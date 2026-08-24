#!/usr/bin/env python3
"""Idempotency fuzzer for `gren format`.

Two passes, both requiring format¹ == format²:

1. Per-gap pass: insert a comment into every inter-token whitespace gap (one at
   a time), in each of the three kinds the formatter distinguishes. Surfaces the
   "comment shifts on reparse" class without anyone hand-placing comments (the
   way KitchenComments' `extremelyCommented` does).

2. End-of-declaration pass: inject an OWN-LINE trailing comment — both the block
   (`{- ¤ -}`) and line (`-- ¤`) form — indented one level below the last line
   of every top-level declaration. The per-gap pass never generates this shape,
   yet it is exactly where the "indented comment past a closing bracket, or deep
   in an inline binop, drifts left on reparse" bug class lives.

**The three kinds are the axis, not a detail.** Placement rules branch on
comment kind everywhere — `commentTextCanRide` is exactly "single-line `{- -}`
or not", and C2's line-leading-separator exception applies to a `--` and a
multi-line `{- … -}` but not to a single-line `{- -}`. Until 2026-08-03 this
pass injected only `{- ¤ -}`, so every rule that fires *only* for the other two
kinds was untested at every gap in the corpus: the `--` at a signature's `->`
was invisible here and invisible to `matrix-syntax.py --comments` (whose
contexts are expression-only), and the fix for it therefore shipped against
fixtures alone.

A `--` runs to end of line, so it cannot simply be spliced into a gap: the pass
breaks the line at the gap and re-indents the tail to the gap's own column,
which is how a person would have to write it too. Where that re-indentation does
not parse the probe is skipped and **counted** — a high skip rate means thin
coverage, so it is reported per file rather than swallowed.

Usage:
    ./fuzz-idempotency.py                       # all testfiles/*/*.formatted.gren, both passes
    ./fuzz-idempotency.py path/to/File.gren ... # specific files
    ./fuzz-idempotency.py -j 4                   # run 4 `gren format`s at a time
    ./fuzz-idempotency.py --decl-ends            # only the end-of-declaration pass
    ./fuzz-idempotency.py --gaps                 # only the per-gap pass
    ./fuzz-idempotency.py --kind line            # only one comment kind (block|multi|line)
    ./fuzz-idempotency.py --gaps --run 2         # a RUN of two comments per gap
    ./fuzz-idempotency.py --gaps --mix-pairs     # every ordered PAIR of two kinds
    ./fuzz-idempotency.py --gaps --mix-triples   # every non-homogeneous TRIPLE

The gaps of each file are checked concurrently (default 2 jobs; `gren format`
is a subprocess so threads scale with CPUs). Each worker thread gets its own
isolated project dir so concurrent formats never share a file.

A gap whose comment makes the file fail to PARSE is skipped (those are parser
limitations, e.g. a comment between two type variables, not idempotency bugs)
and counted separately. Exit status is non-zero if any non-idempotent gap is
found.

Beyond the format¹==format² check, each candidate's output is also checked for
containing the expected number of markers ("¤"). A formatter bug can drop or
duplicate a comment while still being a stable fixed point (the duplicate
persists identically on reformat), which the format¹==format² check alone
cannot see — the marker-count check catches that class directly.

**`--run N` is the second axis of the per-gap pass**, and until 2026-08-06 it
did not exist: every gate in this repo varied *where* a comment goes and none
varied *how many*, so a rule that only misbehaves once a comment's neighbour is
another comment had no probe anywhere. That is not a corner — inside a run the
neighbour a role is classified against IS another comment, which is why
`docs/commentAlgorithm.md` §7 states the run rules (R1-R5) and §8 argues why a
run of N reaches no decision a run of two does not. With `N > 1` the members are marked `¤1 … ¤N`, the kind's label
grows to e.g. `blockx2` (so a finding is still `<fixture>[<kind>]@<gap>` and
`repro.py` still reproduces it), and the marker check gains a REORDERING arm —
a run torn across a separator is a real shape and it is a stable fixed point,
so nothing else here can see it.
"""

import argparse
import concurrent.futures
import json
import os
import pathlib

import subprocess
import sys
import tempfile
import threading

from corpus import add_corpus_argument, corpus_files_for

HERE = os.path.dirname(os.path.abspath(__file__))
GREN_FORMAT = os.path.join(HERE, "..", "..", "gren-format", "gren-format.sh")
MARKER = "{- ¤ -}"  # ¤ — unlikely to collide with real comment text
MARKER_LINE = "-- ¤"  # line-comment form, for the end-of-declaration pass
MARKER_MULTI = "{- ¤\n   second row -}"  # multi-line block form

# Each worker thread formats in its own project dir so concurrent `gren format`
# invocations never write the same Fuzz.gren. Created lazily, reused across the
# tasks that land on the thread, and cleaned up with the enclosing base tempdir.
_local = threading.local()


def worker_workdir(base):
    wd = getattr(_local, "workdir", None)
    if wd is None:
        wd = tempfile.mkdtemp(dir=base)
        os.makedirs(os.path.join(wd, "src"))
        with open(os.path.join(wd, "gren.json"), "w") as f:
            f.write('{ "type": "application" }')
        _local.workdir = wd
    return wd


def gap_indices(src):
    """Indices in `src` where a comment may be inserted: the first character of
    each maximal run of whitespace that lies between two code characters and is
    NOT inside a string, char, or comment. Returns them in source order."""
    gaps = []
    i, n = 0, len(src)
    prev_code = False  # saw a code (non-ws) char since the last gap was opened
    run_start = -1  # index of the current normal-state whitespace run, or -1

    def close_run(next_is_code):
        nonlocal run_start
        if run_start != -1 and prev_code and next_is_code:
            gaps.append(run_start)
        run_start = -1

    while i < n:
        c = src[i]
        two = src[i : i + 2]
        three = src[i : i + 3]
        # Skip over non-code spans (comments / literals); they are not gaps and
        # their interior whitespace must not be touched.
        if two == "--":
            close_run(True)
            j = src.find("\n", i)
            i = n if j == -1 else j
            prev_code = True
            continue
        if two == "{-":
            close_run(True)
            depth, i = 0, i
            while i < n:
                if src[i : i + 2] == "{-":
                    depth += 1
                    i += 2
                elif src[i : i + 2] == "-}":
                    depth -= 1
                    i += 2
                    if depth == 0:
                        break
                else:
                    i += 1
            prev_code = True
            continue
        if three == '"""':
            close_run(True)
            j = src.find('"""', i + 3)
            i = n if j == -1 else j + 3
            prev_code = True
            continue
        if c == '"' or c == "'":
            close_run(True)
            q, i = c, i + 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                elif src[i] == q:
                    i += 1
                    break
                else:
                    i += 1
            prev_code = True
            continue
        if c in " \t\r\n":
            if run_start == -1:
                run_start = i
            i += 1
            continue
        # a code character
        close_run(True)
        prev_code = True
        i += 1
    return gaps


def mask_noncode(src):
    """Return a copy of `src` with the interior of every comment and string/char
    literal replaced by spaces (newlines preserved), so line/column structure can
    be reasoned about without tripping over code-looking text inside literals.
    Uses the same span-skipping as `gap_indices`."""
    out = list(src)
    i, n = 0, len(src)

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        two = src[i : i + 2]
        three = src[i : i + 3]
        if two == "--":
            j = src.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
            continue
        if two == "{-":
            depth, start = 0, i
            while i < n:
                if src[i : i + 2] == "{-":
                    depth += 1
                    i += 2
                elif src[i : i + 2] == "-}":
                    depth -= 1
                    i += 2
                    if depth == 0:
                        break
                else:
                    i += 1
            blank(start, i)
            continue
        if three == '"""':
            j = src.find('"""', i + 3)
            j = n if j == -1 else j + 3
            blank(i, j)
            i = j
            continue
        if src[i] == '"' or src[i] == "'":
            q, start, i = src[i], i, i + 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                elif src[i] == q:
                    i += 1
                    break
                else:
                    i += 1
            blank(start, i)
            continue
        i += 1
    return "".join(out)


def decl_end_positions(src):
    """For each top-level declaration, the (char index, indent) at which to
    inject an own-line trailing comment: right after the last code character of
    the declaration's last non-blank line, indented one level (+4) deeper than
    that line. A top-level declaration is a maximal run of lines beginning at a
    line whose first column holds code; its end is the last non-blank line before
    the next such line (or EOF). This is the shape that exposed the "indented
    comment past a closing bracket / deep in an inline binop drifts left on
    reparse" bug class, which the per-gap pass never generates (that pass only
    glues a comment inline into an existing gap)."""
    masked = mask_noncode(src)
    mlines = masked.split("\n")
    olines = src.split("\n")

    starts, off = [], 0
    for ln in olines:
        starts.append(off)
        off += len(ln) + 1

    n = len(mlines)
    tops = [i for i in range(n) if mlines[i][:1] not in ("", " ", "\t")]

    positions = []
    for k, t in enumerate(tops):
        stop = tops[k + 1] if k + 1 < len(tops) else n
        last = None
        for i in range(t, stop):
            if mlines[i].strip() != "":
                last = i
        if last is None:
            continue
        code_len = len(mlines[last].rstrip())
        insert_pos = starts[last] + code_len
        last_indent = len(olines[last]) - len(olines[last].lstrip())
        positions.append((insert_pos, last_indent + 4))
    return positions


def decl_end_variant(src, positions, comment):
    """Insert an own-line `comment`, indented, at every declaration end at once."""
    parts, prev = [], 0
    for pos, indent in positions:
        parts.append(src[prev:pos])
        parts.append("\n" + " " * indent + comment)
        prev = pos
    parts.append(src[prev:])
    return "".join(parts)


def run_show(workdir, source):
    """Write `source` to the worker's Fuzz.gren and run --show. Returns the
    subprocess result."""
    path = os.path.join(workdir, "src", "Fuzz.gren")
    with open(path, "w") as f:
        f.write(source)
    return subprocess.run(
        [GREN_FORMAT, "--show", path], capture_output=True, text=True
    )


def known_upstream_issue(workdir, source):
    """Name the upstream bug a finding is caused by, or None.

    Some findings are not the formatter's to fix: the parser hands it a tree the
    source did not mean, and any faithful rendering of that tree is wrong. Those
    stay REPORTED and still fail the run — this only puts a name on them, so a
    finding that has already been diagnosed and filed is recognisable on sight
    instead of being re-investigated. Narrowing the gate would be the other
    thing, and is not what this is.

    Today there are three.

    **compiler-common#14** — an expression's argument-indent scope is bumped to
    the *line start* of the row its first term sits on, but only when that term
    is a `Var`/`VarQual` (`Expression.parser`'s `indentationBumped`). A lambda,
    `if`, `when` or `let` skips the bump, so it keeps whatever looser indent the
    enclosing scope had — and a token in between the two columns is refused by
    the inner scope and then swallowed by the outer one as an ARGUMENT of the
    block term itself. `\\q ->` ⏎ `fn one` ⏎ `two` comes out as
    `(\\q -> fn one) two`. Splicing a multi-line comment run into a deeply
    indented call is a way to reach it, because the comment's own continuation
    row is what puts the following token at the in-between column; the same
    layout without the comment is a hard parse error rather than a misparse. The
    real Gren compiler reads it the other way (verified with `gren make`), so the
    tree is simply wrong and no rendering of it can be right. This is the half
    the issue's own text does not cover, already added there as a comment — **do
    not file a new ticket**; see `../../parser-same-column-continuation-bug.md`.

    Its two signals, both read off the parser's own output:

      1. a `call` whose `fn` is a **bare** `lambda` / `if` / `when` / `let`.
         There is no way to write that: a parenthesized lambda arrives as a
         `parens` node wrapping it, so an unwrapped one as a call's function can
         only have been assembled by this bug.
      2. that call's first argument starts on a **different row** than its `fn`
         ends on. This bug is a continuation-row bug, so the spurious argument is
         always on a later row; a same-row shape would be some other failure and
         stays unlabelled.

    **compiler-common#25** — a top-level declaration's `Located.start` is built
    as `{row = name.start.row, col = 1}`, so when the keyword and the name are on
    different rows the recorded "start of the declaration" is neither the keyword
    nor the name. A comment run spliced into a `type ⟨here⟩ alias` /
    `port ⟨here⟩ name` gap pushes the name onto the next row and does exactly
    that; the run is then partitioned by that fabricated point, one member
    hoisted out of the declaration and one kept inside, and the blank-line count
    above the torn run differs between the two formats. Not fixable here — the
    keyword's true row is simply not in the AST. Write-up in
    `../../COMPILER_COMMON_BUG_decl_start_row.md`.

    Its two signals:

      1. some top-level `import` / `type alias` / `type` / `port` has a recorded
         start row whose source line **does not contain that declaration's
         keyword at all**. In any file where the bug is dormant the keyword sits
         at column 1 of that very row, so its absence is the fabricated position
         showing. Deliberately "appears anywhere on the row" rather than "starts
         the row": a comment glued in front (`{- c -} type alias P =`) leaves the
         row correct, and testing the row's *first* token would label that as a
         bug. A keyword that only *looks* present because a comment quotes the
         word leaves the finding unlabelled, which is the safe direction.
      2. the two formats differ **only in whitespace-only lines**. That is how
         this bug surfaces — a blank line appears or disappears above the torn
         run — and it is what separates it from a probe that merely happens to
         contain a keyword/name split while failing for some other reason.

    **compiler-common#35** — a binary `-` whose right operand starts on a later
    row at the operator's own column is parsed as a unary negation, so `10 -` ⏎
    `        3` becomes `Call (10, [Negate 3])`. A `--` written after that `-`
    then renders between the negation and its operand and comes out as `---`,
    swallowing the operator.

    Two signals must agree, because a label that can cover a *different* failure
    is worse than no label:

      1. the AST the parser produced holds a **`negate` whose operand starts on
         a different row than the `-` itself**. A genuine negation cannot look
         like that — the parser requires the operand to follow the `-`
         immediately, and `v = - -- c` ⏎ `3` is a parse error — so the shape
         only exists when this bug produced it. (Read off the parser's own
         output rather than by re-deriving its column rule, which would be a
         mirror of the parser and wrong in its own way.)
      2. the format fails the **AST comparison**. That is how this bug always
         surfaces: the `-` ends up inside the `--`, so the output no longer means
         what the input did. A probe carrying a misparsed negation that fails for
         some *other* reason keeps its finding unlabelled, and gets investigated.
    """
    path = os.path.join(workdir, "src", "Fuzz.gren")
    with open(path, "w") as f:
        f.write(source)
    r = subprocess.run(
        [GREN_FORMAT, "--pre-ast", path], capture_output=True, text=True
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        ast = json.loads(r.stdout)
    except ValueError:
        return None

    found = []
    block_call = []

    def walk(node):
        if isinstance(node, dict):
            value = node.get("value")
            if isinstance(value, dict) and value.get("type") == "call":
                fn = value.get("fn")
                args = value.get("args") or []
                if (
                    isinstance(fn, dict)
                    and isinstance(fn.get("value"), dict)
                    and fn["value"].get("type") in ("lambda", "if", "when", "let")
                    and isinstance(fn.get("end"), dict)
                    and args
                    and isinstance(args[0], dict)
                    and isinstance(args[0].get("start"), dict)
                    and fn["end"]["row"] != args[0]["start"]["row"]
                ):
                    block_call.append(True)
            if (
                isinstance(value, dict)
                and value.get("type") == "negate"
                and isinstance(node.get("start"), dict)
                and isinstance(value.get("expr"), dict)
                and isinstance(value["expr"].get("start"), dict)
                and node["start"]["row"] != value["expr"]["start"]["row"]
            ):
                found.append(True)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(ast)
    shown = run_show(workdir, source)
    blob = shown.stdout + shown.stderr

    if found and "AST MISMATCH" in blob:
        return "compiler-common#35"

    if block_call:
        return "compiler-common#14"

    lines = source.split("\n")
    keyword_row_wrong = any(
        isinstance(decl.get("start"), dict)
        and isinstance(decl["start"].get("row"), int)
        and 1 <= decl["start"]["row"] <= len(lines)
        and keyword not in lines[decl["start"]["row"] - 1]
        for keyword, decl in keyword_declarations(ast)
    )
    if keyword_row_wrong and diff_is_whitespace_only(blob):
        return "compiler-common#25"

    return None


def keyword_declarations(ast):
    """Yield `(keyword, declaration)` for every top-level declaration that is
    introduced by one — the four compiler-common#25 names.

    `type alias` and a union both lead with `type`, which is all the caller
    needs: it asks only whether the recorded row holds the keyword, never which
    one it is.

    **A port is not `module["ports"]`** — it lives under `module["effects"]`,
    whose own `type` says whether this module has ports at all. Reading it off
    the top level silently yielded nothing, so the one `port` finding stayed
    unlabelled while the seven `type` ones were named."""
    module = ast.get("module", ast)
    for field, keyword in (("imports", "import"), ("aliases", "type"), ("unions", "type")):
        for decl in module.get(field) or []:
            if isinstance(decl, dict):
                yield keyword, decl
    effects = module.get("effects")
    if isinstance(effects, dict):
        for decl in effects.get("ports") or []:
            if isinstance(decl, dict):
                yield "port", decl


def diff_is_whitespace_only(blob):
    """True when every line the two formats differ by is whitespace-only.

    Reads the unified diff `--show` prints when it finds the format not
    idempotent. Requires at least one changed line, so a blob carrying no diff
    at all cannot answer yes.

    **Scanning starts at the first `@@` hunk header, and that is load-bearing**:
    the report opens with a `-- FORMATTER NOT IDEMPOTENT ---------- <path>`
    banner, which begins with `-` and is not part of the diff at all. Counting it
    made every answer `False`, because a path is not whitespace."""
    if "FORMATTER NOT IDEMPOTENT" not in blob:
        return False
    lines = blob.split("\n")
    hunk = next((i for i, ln in enumerate(lines) if ln.startswith("@@")), None)
    if hunk is None:
        return False
    changed = [
        ln
        for ln in lines[hunk:]
        if ln[:1] in ("-", "+") and not ln.startswith(("---", "+++"))
    ]
    return bool(changed) and all(not ln[1:].strip() for ln in changed)


def splice_block(src, g, text):
    """A `{- … -}` splices straight into the gap: it self-terminates, so the
    code after it stays where it was (a multi-line one carries its tail onto the
    comment's closing row, which is exactly the shape being tested)."""
    return src[:g] + " " + text + src[g:]


def splice_line(src, g, text):
    """A `--` runs to end of line, so it cannot be spliced inline — everything
    after it on that row would be swallowed. Break the row at the gap instead
    and re-indent the tail to the gap's own column, which is how the comment
    would have to be written by hand:

        foo :  ⏎  ····Int -> String        foo :  ⏎  ····Int -- ¤
                                                     ·······-> String

    Column-aligning the tail (rather than picking a fixed indent) keeps the
    continuation legal for the widest range of gaps; where it still does not
    parse, the caller counts the probe as skipped.

    `text` may itself span rows (a RUN of `--` comments — see `run_text`); its
    own newlines are re-indented to the same column as the tail, so the run
    reads as one block of comment rows rather than dropping to column 0. For a
    single-row text this is exactly what it always did."""
    ws_end = g
    while ws_end < len(src) and src[ws_end] in " \t":
        ws_end += 1
    col = g - (src.rfind("\n", 0, g) + 1)
    pad = " " * col
    return src[:g] + " " + text.replace("\n", "\n" + pad) + "\n" + pad + src[ws_end:]


# (label, comment text, splice fn, may-inject-all-gaps-at-once).
#
# The all-at-once fast path works for both block forms — a `{- … -}` is
# whitespace to the parser, so N of them in one file is still one legal program.
# A `--` breaks its row, so N of them at once would re-indent the whole file;
# the line kind always takes the per-gap path.
KINDS = [
    ("block", MARKER, splice_block, True),
    ("multi", MARKER_MULTI, splice_block, True),
    ("line", MARKER_LINE, splice_line, False),
]

# The largest run `--run` accepts. The members are marked `¤1 … ¤n` and counted
# by substring, so a two-digit index would make `¤1` a prefix of `¤10`; nothing
# the repo asks for a run past three: `--run 3` swept dry, and
# `docs/commentAlgorithm.md` §8.4 says why length adds no new case.
MAX_RUN = 9


def run_text(label, text, n):
    """One gap's worth of splice text: `text` as a RUN of `n` comments.

    A run is its own axis, not more of the same one. Every placement rule here
    decides a comment's role from its neighbours, and inside a run the neighbour
    is another comment — so the role of member k can depend on members 1..k−1
    through a path nobody wrote down. That is the situation rules C1 (one gap is
    one attachment, one role) and C3 (the run rides iff every member rides)
    exist to remove, and until this axis existed nothing in the repo varied it:
    `--comments` injects one comment per cell and this pass injected one per
    gap. See `docs/commentAlgorithm.md` §8 and §10.

    The members are marked `¤1 … ¤n` rather than repeating one marker, which
    buys two things a repeated marker cannot: a finding names WHICH member
    moved, and `marker_check` can tell a dropped comment from a REORDERED run —
    a run torn across a separator (`{- ¤1 -}` ⏎ `, {- ¤2 -} item`) is a real
    shape, and two identical comments make a swap invisible.

    A `--` run joins with a newline because a `--` runs to end of line;
    `splice_line` re-indents those rows to the gap's column."""
    joiner = "\n" if label == "line" else " "
    return joiner.join(text.replace("¤", f"¤{i + 1}") for i in range(n))


def run_kind(kind, n):
    """`kind` with its text widened to a run of `n`, label and all. Returns the
    same 4-tuple shape `KINDS` holds, so every consumer of a kind — including
    `check-decision-stability.py`, which imports this table by path — takes a
    run without knowing there is such a thing."""
    label, text, splice, can_fast = kind
    if n == 1:
        return kind
    return (f"{label}x{n}", run_text(label, text, n), splice, can_fast)


def mixed_kind(labels):
    """A run whose members are DIFFERENT kinds, in the order given.

    **Run LENGTH and run COMPOSITION are two axes, and `--run` only varies the
    first.** Its members are `n` copies of one kind, so every member's
    neighbours have the same shape it does — which is why `--run 3` (2026-08-06)
    found nothing over `--run 2`: going from two members to three adds a
    *second* identical neighbour, where going from one to two had added the
    first neighbour of any sort. The rules a run can break are written about a
    neighbour's SHAPE, not its count: `commentRendersOwnLine` distinguishes a
    multi-line `{- … -}` from a single-line one and from a `--`,
    `FlowPolicy`'s inline arm asks whether the previous comment *glued*, and
    `spanTrailingOwnLine` peels a suffix that mixes both. A homogeneous run
    cannot put those on either side of each other; only this can.

    It is not a hypothetical: `PipelineStepTrailingMultilineComment`'s notes
    record `{- multi⏎line -} {- c -}` as already non-idempotent and reachable by
    no gate here.

    Two things follow from the members differing, both forced rather than
    chosen:

      - **A `--` swallows the rest of its row**, so whatever follows one must
        start a new row, while a block-to-anything boundary joins with a space.
        The joiner is therefore per-boundary, keyed on the member to its LEFT —
        `run_text`'s single joiner is the homogeneous special case of this.
      - **One `--` anywhere forces the whole run onto `splice_line`** (the
        gap's tail has to move below the run) and off the all-at-once fast
        path, exactly as the pure `line` kind is.

    Returns the same 4-tuple `KINDS` holds, labelled `a+b`, so every consumer —
    `report_slow_path`, `fast_check`, `marker_check`, and `repro.py` /
    `check-decision-stability.py` importing this module by path — takes a mixed
    run without knowing there is such a thing."""
    by_label = {k[0]: k for k in KINDS}
    members = [by_label[l] for l in labels]
    parts = []
    for i, (label, text, _splice, _fast) in enumerate(members):
        parts.append(text.replace("¤", f"¤{i + 1}"))
        if i + 1 < len(members):
            parts.append("\n" if label == "line" else " ")
    has_line = any(m[0] == "line" for m in members)
    return (
        "+".join(labels),
        "".join(parts),
        splice_line if has_line else splice_block,
        not has_line,
    )


def mix_sequences(length):
    """Every ordered sequence of `length` kinds that is not all one kind.

    Homogeneous sequences are excluded because `--run N` already sweeps them,
    and at n=3 it swept DRY (2026-08-06) — so this is the whole of what a run of
    `length` members can be that `--run` cannot say.

    **At length 3 the new shape is a member with a comment on BOTH sides.** A
    pair gives every member exactly one neighbour; `--mix-pairs` therefore
    sweeps every *boundary* that can exist, and a triple adds no new boundary at
    all — what it adds is a member whose left and right neighbours are both
    comments, which is what the rules are actually written about
    (`commentRendersOwnLine` separates the three kinds, `FlowPolicy`'s inline arm
    asks whether the PREVIOUS comment glued, `spanTrailingOwnLine` peels a
    SUFFIX). `a,b,a` is kept for the same reason `--run 3` is not enough: its
    middle member has two neighbours where a `--run 3` member's are the same kind
    as itself.

    24 sequences at length 3, against `--mix-pairs`' 6, so budget for four times
    the sweep."""
    seqs = []
    labels = [k[0] for k in KINDS]

    def build(prefix):
        if len(prefix) == length:
            if len(set(prefix)) > 1:
                seqs.append(prefix)
            return
        for l in labels:
            build(prefix + [l])

    build([])
    return seqs


def marker_check(out, n):
    """None if the run came through the format intact, else what went wrong.

    Three failures, and only the first is visible to format¹ == format²: a
    formatter can drop, duplicate or reorder a comment and still be a stable
    fixed point."""
    total = out.count("¤")
    if total != n:
        return (
            f"expected {n} '¤' marker(s) in the output, found {total} "
            "(dropped or duplicated comment)"
        )
    if n == 1:
        return None
    offsets = []
    for i in range(1, n + 1):
        c = out.count(f"¤{i}")
        if c != 1:
            return f"marker ¤{i} appears {c} times in the output, expected once"
        offsets.append(out.index(f"¤{i}"))
    if offsets != sorted(offsets):
        return f"the run came back out of source order (¤1…¤{n} at {offsets})"
    return None


def all_gaps_variant(src, gaps, text=MARKER):
    """Insert `text` into every gap simultaneously."""
    parts = []
    prev = 0
    for g in gaps:
        parts.append(src[prev:g])
        parts.append(" " + text)
        prev = g
    parts.append(src[prev:])
    return "".join(parts)


def fast_check(base, src, gaps, text=MARKER, per_gap=1):
    """Insert `text` into all gaps at once and run --show.

    `per_gap` is how many markers one splice carries (the run length), since
    the whole-file marker count is the only thing here that has to know.

    Returns:
      "ok"         — idempotent (file is clean, no per-gap work needed)
      "parse-fail" — all-at-once variant didn't parse (fall back to per-gap)
      "fail"       — non-idempotent, wrong marker count, or other error
                     (fall back to per-gap)
    """
    workdir = worker_workdir(base)
    r = run_show(workdir, all_gaps_variant(src, gaps, text))
    blob = r.stdout + r.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        return "parse-fail"
    if r.returncode != 0 or not r.stdout.strip():
        return "fail"
    if r.stdout.count("¤") != len(gaps) * per_gap:
        return "fail"
    return "ok"


def decl_ranges(src):
    """(start, end) char offsets of each top-level declaration — the same
    "maximal run of lines starting at one whose first column holds code"
    `decl_end_positions` uses, kept as spans so gaps can be bucketed by the
    declaration they sit in."""
    masked = mask_noncode(src)
    mlines = masked.split("\n")
    olines = src.split("\n")
    starts, off = [], 0
    for ln in olines:
        starts.append(off)
        off += len(ln) + 1
    n = len(mlines)
    tops = [i for i in range(n) if mlines[i][:1] not in ("", " ", "\t")]
    out = []
    for k, t in enumerate(tops):
        stop = tops[k + 1] if k + 1 < len(tops) else n
        end = starts[stop - 1] + len(olines[stop - 1]) if stop <= len(olines) else len(src)
        out.append((starts[t], min(end, len(src))))
    return out


def splice_pair(src, ga, ka, gb, kb):
    """Two comments at two DIFFERENT gaps, `ga` before `gb`.

    The LATER one goes in first, so the earlier offset is still valid when the
    second splice runs. (`splice_line` rewrites the tail of the row it breaks,
    which is why this cannot be done in the other order.)

    The two markers are NUMBERED `¤1`/`¤2` for the same reason a run's are:
    `marker_check` can then tell a dropped comment from a swapped pair, and two
    identical markers would make a swap invisible. `¤1` is the earlier gap, so
    the output is required to keep them in source order."""
    _la, ta, sa, _fa = ka
    _lb, tb, sb, _fb = kb
    return sa(sb(src, gb, tb.replace("¤", "¤2")), ga, ta.replace("¤", "¤1"))


def check_pair(base, src, ga, ka, gb, kb):
    """One PAIR probe: a comment at `ga` and another at `gb`, both inside one
    declaration. Same classification as `check_gap`."""
    workdir = worker_workdir(base)
    markers = 2

    r = run_show(workdir, splice_pair(src, ga, ka, gb, kb))
    blob = r.stdout + r.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        return ("skip", (ga, gb), "")
    if r.returncode != 0 or not r.stdout.strip():
        return ("bug", (ga, gb), r.stderr.strip())
    bad = marker_check(r.stdout, markers)
    if bad:
        return ("bug", (ga, gb), bad)
    return ("ok", (ga, gb), "")


def pair_probes(src, gaps, cap, rng):
    """Every ordered pair of gaps WITHIN one declaration, capped per
    declaration.

    Scoped to a declaration because the whole corpus all-pairs is not a
    tractable sweep — 20,874 gaps for one kind means ~2x10^8 pairs — and
    because the bug class this axis exists for is local: an outer construct
    whose own row is broken by something nested inside it. Both comments have
    to land in the same declaration to interact at all.

    `cap` subsamples a declaration whose pair count exceeds it, with a seeded
    `rng` so a run is reproducible and a finding replays."""
    out = []
    for lo, hi in decl_ranges(src):
        inside = [g for g in gaps if lo <= g < hi]
        pairs = [(a, b) for i, a in enumerate(inside) for b in inside[i + 1:]]
        if cap and len(pairs) > cap:
            pairs = rng.sample(pairs, cap)
            pairs.sort()
        out += pairs
    return out


def report_pairs(base, pool, path, src, gaps, verbose, ka, kb, cap, rng, registry=None):
    """Pair pass over one file for one ordered kind pair. Returns
    (bug count, known-upstream count)."""
    name = os.path.basename(path)
    label = f"{ka[0]}+{kb[0]}"
    probes = pair_probes(src, gaps, cap, rng)
    results = list(pool.map(lambda p: check_pair(base, src, p[0], ka, p[1], kb), probes))
    bugs, skipped = [], 0
    for kind, gp, detail in results:
        if kind == "skip":
            skipped += 1
        elif kind == "bug":
            bugs.append((gp, detail))
    known = list(
        pool.map(
            lambda gd: known_upstream_issue(
                worker_workdir(base), splice_pair(src, gd[0][0], ka, gd[0][1], kb)
            ),
            bugs,
        )
    )
    known_count = sum(1 for k in known if k)
    if registry is not None:
        for (gp, _d), issue in zip(bugs, known):
            registry[f"{name}[pair:{label}]@{gp[0]},{gp[1]}"] = issue
    tail = f", {known_count} known upstream" if known_count else ""
    status = "OK " if not bugs else "BUG"
    print(f"{status} {name} [pair {label}]: {len(probes)} pairs, {skipped} skipped (parser), {len(bugs)} non-idempotent{tail}")
    for (gp, detail), issue in zip(bugs, known):
        la = src.count("\n", 0, gp[0]) + 1
        lb = src.count("\n", 0, gp[1]) + 1
        mark = f"  [known: {issue}]" if issue else ""
        print(f"      lines {la}+{lb} (gaps {gp[0]},{gp[1]})"
              f": …{(src[max(0, gp[0]-14):gp[0]] + '⟨A⟩' + src[gp[0]:gp[0]+10]).replace(chr(10), '⏎')}…"
              f" / …{(src[max(0, gp[1]-14):gp[1]] + '⟨B⟩' + src[gp[1]:gp[1]+10]).replace(chr(10), '⏎')}…{mark}")
        if verbose and detail:
            for dl in detail.splitlines()[:20]:
                print("        " + dl)
    return len(bugs), known_count


def check_gap(base, src, g, splice=splice_block, text=MARKER, markers=1):

    """Insert one comment (or one RUN of them) at gap `g`, run --show, classify
    the outcome. Runs on a pool worker; uses that worker's isolated project
    dir."""
    workdir = worker_workdir(base)
    r = run_show(workdir, splice(src, g, text))
    blob = r.stdout + r.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        return ("skip", g, "")
    if r.returncode != 0 or not r.stdout.strip():
        return ("bug", g, r.stderr.strip())
    bad = marker_check(r.stdout, markers)
    if bad:
        return ("bug", g, bad)
    return ("ok", g, "")


def report_slow_path(base, pool, path, src, gaps, verbose, kind=KINDS[0], registry=None):
    """Per-gap pass over one file for one comment kind. Returns
    (bug count, known-upstream count).

    `registry` collects every finding as `{repro label: issue-or-None}` for the
    known-upstream baseline — the label is exactly what `repro.py` takes."""

    label, text, splice, _fast = kind
    name = os.path.basename(path)
    markers = text.count("¤")
    results = list(
        pool.map(lambda g: check_gap(base, src, g, splice, text, markers), gaps)
    )
    bugs, skipped = [], 0
    for r in results:
        if r[0] == "skip":
            skipped += 1
        elif r[0] == "bug":
            _, g, detail = r
            bugs.append((g, detail))
    # Only findings pay for this second parse, so it costs ~one run per reported
    # bug rather than one per probe.
    known = list(
        pool.map(
            lambda gd: known_upstream_issue(
                worker_workdir(base), splice(src, gd[0], text)
            ),
            bugs,
        )
    )
    known_count = sum(1 for k in known if k)
    if registry is not None:
        for (g, _detail), issue in zip(bugs, known):
            registry[f"{name}[{label}]@{g}"] = issue
    tail = f", {known_count} known upstream" if known_count else ""

    status = "OK " if not bugs else "BUG"
    print(f"{status} {name} [{label}]: {len(gaps)} gaps, {skipped} skipped (parser), {len(bugs)} non-idempotent{tail}")
    for (g, detail), issue in zip(bugs, known):
        line = src.count("\n", 0, g) + 1
        ctx = (src[max(0, g - 20) : g] + "⟨here⟩" + src[g : g + 20]).replace("\n", "⏎")
        mark = f"  [known: {issue}]" if issue else ""
        print(f"      gap at line {line}: …{ctx}…{mark}")
        if verbose and detail:
            for dl in detail.splitlines()[:20]:
                print("        " + dl)
    return len(bugs), known_count


def check_one_decl_end(base, src, pos, indent, comment):
    """Inject `comment` at a single declaration end, run --show, classify.
    Returns ("ok"|"skip"|"bug", detail)."""
    workdir = worker_workdir(base)
    variant = decl_end_variant(src, [(pos, indent)], comment)
    r = run_show(workdir, variant)
    blob = r.stdout + r.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        return ("skip", "")
    if r.returncode != 0 or not r.stdout.strip():
        return ("bug", r.stderr.strip())
    if r.stdout.count("¤") != 1:
        return ("bug", f"expected exactly one '¤', found {r.stdout.count('¤')} (dropped or duplicated comment)")
    return ("ok", "")


def report_decl_ends(base, pool, path, src, verbose):
    """Inject an own-line trailing comment (block, then line form) at every
    top-level declaration end and require each to be idempotent. Returns the bug
    count. Localises per-declaration so each failure names its line."""
    name = os.path.basename(path)
    positions = decl_end_positions(src)
    if not positions:
        print(f"OK  {name}: 0 declaration ends")
        return 0

    bugs = []
    skipped = 0
    for comment, label in ((MARKER, "block"), (MARKER_LINE, "line")):
        results = list(
            pool.map(
                lambda pi: check_one_decl_end(base, src, pi[0], pi[1], comment),
                positions,
            )
        )
        for (pos, _indent), (kind, detail) in zip(positions, results):
            if kind == "skip":
                skipped += 1
            elif kind == "bug":
                bugs.append((pos, label, detail))

    status = "OK " if not bugs else "BUG"
    print(f"{status} {name}: {len(positions)} declaration ends x2 (block/line), {skipped} skipped (parser), {len(bugs)} non-idempotent")
    for pos, label, detail in bugs:
        line = src.count("\n", 0, pos) + 1
        ctx = (src[max(0, pos - 24) : pos] + "⟨+" + label + " comment⟩").replace("\n", "⏎")
        print(f"      after line {line} ({label}): …{ctx}")
        if verbose and detail:
            for dl in detail.splitlines()[:20]:
                print("        " + dl)
    return len(bugs)


KNOWN_BASELINE = pathlib.Path(__file__).resolve().parent / "idempotency-known-baseline.json"

#: The corpus half the default sweep covers, and therefore the half
#: `idempotency-known-baseline.json` was written from. Both halves: the
#: dirty inputs reach rule families the fixed points cannot (see `corpus.py`).
DEFAULT_CORPUS = "both"


def check_known_baseline(registry, full_sweep, update):
    """Gate the KNOWN-upstream findings against a registered set. Returns True
    if the run should fail on drift.

    An unlabelled finding already fails on its own count. This is the other
    half: a finding that `known_upstream_issue` classifies as upstream is only
    tolerated if it was registered as such, so a regression cannot hide behind
    an automatic classification, and a registered finding that has stopped
    reproducing is reported rather than silently kept.

    That distinction is what this gate lacked. It exited non-zero on ANY
    finding, so it ran permanently red and "27 findings, 19 of them known" read
    exactly like "19 findings, all known" -- the eight that belonged to the
    if/when header bug sat in the summary line for weeks looking like the
    upstream ones. Only the default full sweep is gated: `--run`, `--mix*`,
    `--kind` and a file subset each probe a different set of gaps, so their
    findings are not this baseline's to hold.
    """
    if update:
        if not full_sweep:
            print("\nrefusing to write the baseline from a partial run "
                  "(--kind/--run/--mix/file arguments): it would drop every "
                  "finding those flags did not probe")
            return True
        KNOWN_BASELINE.write_text(json.dumps({
            "_comment": [
                "Findings this gate has diagnosed as an UPSTREAM parser bug, so",
                "they fail nothing until it is fixed. Key is the label repro.py",
                "takes: <fixture>[<kind>]@<byte offset>. Value is the issue.",
                "",
                "A finding that is NOT here fails the run even when it classifies",
                "as upstream -- register it deliberately or fix it. An entry here",
                "that no longer reproduces fails too, so a fix cannot leave a",
                "stale exemption behind. Regenerate with",
                "./fuzz-idempotency.py --update-known-baseline.",
            ],
            "findings": {k: v for k, v in sorted(registry.items()) if v},
        }, indent=2) + "\n")
        n = sum(1 for v in registry.values() if v)
        print(f"\nwrote {n} registered upstream finding(s) to {KNOWN_BASELINE.name}")
        return False

    if not full_sweep:
        return False

    observed = {k: v for k, v in registry.items() if v}
    registered = (
        json.loads(KNOWN_BASELINE.read_text())["findings"]
        if KNOWN_BASELINE.exists()
        else {}
    )
    unregistered = sorted(set(observed) - set(registered))
    stale = sorted(set(registered) - set(observed))
    for key in unregistered:
        print(f"\nUNREGISTERED upstream finding: {key}  [{observed[key]}]"
              f"\n    It classifies as an upstream parser bug, but nothing says it is"
              f"\n    expected. Reproduce it (`./repro.py {key.split('[')[0]} "
              f"{key.split('[')[1].split(']')[0]} {key.split('@')[1]}`), then fix it"
              f"\n    or register it with --update-known-baseline.")
    for key in stale:
        print(f"\nSTALE baseline entry: {key}  [{registered[key]}]"
              f"\n    Registered as an upstream finding, but it no longer reproduces."
              f"\n    Drop it with --update-known-baseline.")
    if unregistered or stale:
        print(f"\n{len(unregistered)} unregistered, {len(stale)} stale "
              f"against {KNOWN_BASELINE.name}")
        return True
    return False


def main(argv):
    ap = argparse.ArgumentParser()

    ap.add_argument("-v", action="store_true", help="show the format¹/format² diff per gap")
    ap.add_argument("-j", "--jobs", type=int, default=2, help="concurrent `gren format`s (default 2)")
    ap.add_argument("--gaps", action="store_true", help="run only the per-gap pass (skip the end-of-declaration pass)")
    ap.add_argument("--decl-ends", action="store_true", help="run only the end-of-declaration pass (skip the per-gap pass)")
    ap.add_argument("--kind", action="append", choices=[k[0] for k in KINDS],
                    help="restrict the per-gap pass to one comment kind (repeatable; default all three)")
    ap.add_argument("--run", type=int, default=1, metavar="N",
                    help=f"inject a RUN of N comments per gap instead of one (1-{MAX_RUN}); "
                         "the per-gap pass only")
    ap.add_argument("--mix", action="append", metavar="A,B[,C]",
                    help="inject a run of DIFFERENT kinds per gap, in the order given "
                         "(e.g. --mix multi,block). Repeatable; --mix-pairs is the "
                         "whole ordered cross-product. Replaces the default kind list, "
                         "and spells its own length, so it takes neither --kind nor --run")
    ap.add_argument("--mix-pairs", action="store_true",
                    help="every ordered pair of two DIFFERENT kinds (6 sequences)")
    ap.add_argument("--mix-triples", action="store_true",
                    help="every ordered TRIPLE that is not all one kind (24 "
                         "sequences) — the first shape where a member has a "
                         "comment on BOTH sides of it")
    ap.add_argument("--pairs", action="store_true",
                    help="the PAIR pass: two comments at two DIFFERENT gaps of "
                         "one declaration (every other multi-comment mode puts "
                         "its comments in ONE gap). Replaces the per-gap and "
                         "end-of-declaration passes")
    ap.add_argument("--pair-kinds", action="append", metavar="A,B",
                    help="ordered kind pair for --pairs (default block,multi "
                         "and block,line — a riding comment first, a "
                         "row-breaking one after it). Repeatable")
    ap.add_argument("--pair-cap", type=int, default=400, metavar="N",
                    help="max pairs probed per declaration (default 400; 0 = "
                         "no cap). Sampling is seeded, so a run replays")
    ap.add_argument("--pair-seed", type=int, default=1, help="seed for --pair-cap sampling")
    ap.add_argument("--update-known-baseline", action="store_true",

                    help=f"rewrite {KNOWN_BASELINE.name} from this run's findings "
                         "(full sweep only)")
    add_corpus_argument(ap, default=DEFAULT_CORPUS)
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv[1:])

    if not 1 <= args.run <= MAX_RUN:
        ap.error(f"--run must be between 1 and {MAX_RUN}")
    mixes = list(args.mix or [])
    if args.mix_pairs:
        mixes += [",".join(s) for s in mix_sequences(2)]
    if args.mix_triples:
        mixes += [",".join(s) for s in mix_sequences(3)]
    if mixes and (args.kind or args.run != 1):
        ap.error("--mix/--mix-pairs replaces the kind list and spells its own "
                 "run length; it cannot be combined with --kind or --run")
    run_gaps = not args.decl_ends and not args.pairs
    run_decl_ends = not args.gaps and not args.pairs
    pair_kinds = []
    if args.pairs:
        by_label = {k[0]: k for k in KINDS}
        specs = args.pair_kinds or ["block,multi", "block,line"]
        for spec in specs:
            labels = [s.strip() for s in spec.split(",")]
            if len(labels) != 2 or any(l not in by_label for l in labels):
                ap.error(f"--pair-kinds {spec!r}: want exactly two of "
                         f"{', '.join(by_label)}")
            pair_kinds.append((by_label[labels[0]], by_label[labels[1]]))
    elif args.pair_kinds:
        ap.error("--pair-kinds needs --pairs")

    if mixes:
        seqs = []
        for spec in mixes:
            labels = [s.strip() for s in spec.split(",")]
            bad = [l for l in labels if l not in {k[0] for k in KINDS}]
            if bad or len(labels) < 2:
                ap.error(f"--mix {spec!r}: want two or more of "
                         f"{', '.join(k[0] for k in KINDS)}")
            seqs.append(labels)
        kinds = [mixed_kind(labels) for labels in seqs]
    else:
        kinds = [
            run_kind(k, args.run) for k in KINDS if not args.kind or k[0] in args.kind
        ]

    # The baseline holds the findings of the DEFAULT sweep and only those: any
    # flag that narrows or changes which gaps are probed takes the run out of
    # its scope. `-v` and `-j` do not, being output and concurrency only.
    #
    # `--corpus` narrows it like any other: the half a run does not sweep
    # contributes no findings, so every baseline entry from that half would
    # report as STALE and the run would fail for having been asked a smaller
    # question. `DEFAULT_CORPUS` is what the baseline was written from.
    full_sweep = (
        not args.files
        and not args.kind
        and args.run == 1
        and not mixes
        and not args.pairs
        and run_gaps
        and run_decl_ends
        and args.corpus == DEFAULT_CORPUS
    )


    files = args.files
    if not files:
        files = corpus_files_for(args.corpus)


    # Precompute gaps (pure Python, no subprocesses).
    file_data = [(path, open(path).read()) for path in files]
    file_data = [(path, src, gap_indices(src)) for path, src in file_data]

    total = 0
    known_total = 0
    registry = {}
    with tempfile.TemporaryDirectory() as base:

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            if run_gaps:
                # Per-gap pass, once per comment kind. A kind that can be
                # injected into every gap at once gets the fast check first (one
                # --show per file), falling back to per-gap only where that
                # fails; a `--` has no such shortcut and always goes per-gap.
                for label, text, splice, can_fast in kinds:
                    print(f"== per-gap comment pass [{label}] ==")
                    kind = (label, text, splice, can_fast)
                    if can_fast:
                        per_gap = text.count("¤")
                        fast_outcomes = list(pool.map(
                            lambda t: fast_check(base, t[1], t[2], text, per_gap),
                            file_data,
                        ))
                    else:
                        fast_outcomes = ["slow"] * len(file_data)
                    for (path, src, gaps), fast in zip(file_data, fast_outcomes):
                        name = os.path.basename(path)
                        if fast == "ok":
                            print(f"OK  {name} [{label}]: {len(gaps)} gaps, 0 skipped (parser), 0 non-idempotent")
                        else:
                            bugs, known = report_slow_path(base, pool, path, src, gaps, args.v, kind, registry)

                            total += bugs
                            known_total += known

            if pair_kinds:
                import random
                for ka, kb in pair_kinds:
                    print(f"== comment PAIR pass [{ka[0]}+{kb[0]}] ==")
                    rng = random.Random(args.pair_seed)
                    for path, src, gaps in file_data:
                        bugs, known = report_pairs(
                            base, pool, path, src, gaps, args.v, ka, kb,
                            args.pair_cap, rng, registry)
                        total += bugs
                        known_total += known

            if run_decl_ends:

                # End-of-declaration pass: inject an own-line trailing comment
                # (block and line form) after every top-level declaration.
                print("\n== end-of-declaration comment pass ==")
                for path, src, _gaps in file_data:
                    total += report_decl_ends(base, pool, path, src, args.v)

    # The known-upstream count is subtracted from nothing: those findings are
    # real and still counted. What it changes is the EXIT STATUS, and that is
    # the whole point of the baseline below -- see `check_known_baseline`.
    unlabelled = total - known_total
    tail = ""
    if known_total:
        tail = f" (+{known_total} known upstream, waiting on a parser fix — see the `known: …` marks above)"
    print(f"\n{'FAIL' if unlabelled else 'PASS'}: {unlabelled} unlabelled finding(s){tail}")

    drift = check_known_baseline(registry, full_sweep, args.update_known_baseline)
    return 1 if (unlabelled or drift) else 0



if __name__ == "__main__":
    sys.exit(main(sys.argv))
