#!/usr/bin/env python3
"""Construct x context syntax matrix for `gren format`.

The fixture corpus reaches only the syntax somebody thought to write, and both
fuzzers perturb only *comments* and *whitespace* over that fixed corpus --
neither varies syntax. So a bug needing a conjunction of features has no fixture,
because nobody had a reason to write one. The RecordUpdate over-approximation
lived for months in exactly that gap: it needed a record update, inside parens
that survive elision, inside a pipeline-step relocation.

This closes that hole by brute force: it embeds every expression form in every
context and checks each cell. It is the syntax axis the fuzzers do not have.

LAYOUT VARIANTS. Each construct-in-context is generated in up to four variants
(see VOCABULARY below): `flat` (the original one-line form), `broken` (the same
atom pre-broken across rows), and -- in value-position contexts only -- their
un-parenthesized `bareFlat` / `bareBroken` cousins. The author-broken variants
are the axis the flat-only matrix lacked: they exercise the multi-line render
path (`forceVertical`) even when the output later collapses, which is where the
2026-07-18 dogfooding crash lived (a record-literal field holding a multi-line
binop -- a shape that needs a NAKED value broken across rows, so the paren-
carrying atoms could never reach it).

ORACLES (nothing here needs a human to eyeball output):

1. Layout, both directions -- FLAT-INPUT VARIANTS ONLY (`flat`, `bareFlat`).
   Gren's layout is author-driven -- no page width, no fitter -- so a construct
   written flat renders flat unless its own content forces a break:
       flat construct in flat context   => body MUST be exactly one line
       otherwise (if/when/let anywhere) => body MUST break
   Over-approximation (pre-breaking something that renders inline) is exactly
   the RecordUpdate bug class, and it fails the first assertion. This is a
   flat-INPUT truth, so it does not run on `broken`/`bareBroken`: a broken input
   has no local layout truth (gren collapses a broken-but-fitting binop), which
   is why the author-broken variants rely on oracles 2-4 instead.
2. `--show` internally does parse -> render -> reparse -> AST-compare -> render
   again -> idempotency-compare, so a clean exit also buys AST equivalence,
   idempotency, and "the output parses". Each failure title is reported as its
   own class.
3. `--audit-predicates` over every cell -- the predicate/renderer agreement
   audit (see audit-predicates.py), now over generated syntax instead of only
   the corpus.
4. elm-format parity. Gren is a fork of Elm, so on shared constructs the two
   formatters should agree byte-for-byte. Every cell is translated to Elm and
   run through `elm-format --stdin`, and the two outputs are diffed. Translating
   *real* Gren source to Elm is lossy hand work, which is what makes the parity
   audit in the root CLAUDE.md a manual exercise -- but the cells here are built
   from a vocabulary we authored, and across all of it the only Gren-vs-Elm
   difference is `when X is` -> `case X of`. See ELM_PARITY below.

Oracles 1-3 are truths: a violation is a bug, full stop. Oracle 4 is NOT --
gren-format diverges from elm-format on purpose in places (README's "Divergence
catalogue"), so parity is gated against a reviewed baseline instead. See
ELM_PARITY.

A cell whose *generated source* does not parse is the generator's fault, not a
formatter bug: it is skipped and counted, never silently dropped.

THE COMMENT AXIS (`--comments`). Until 2026-07-31 comments were excluded here
and left to fuzz-idempotency.py. That left a hole at the *intersection*: this
script varies syntax and asks elm-format, the fuzzers vary comments and ask only
"is it stable?" -- so a comment *placement* divergence from elm-format was
invisible to every gate in the repo. It is stable, AST-equivalent and idempotent;
nothing ever asked elm-format what it thought. That hole hid the broken-call and
broken-binop leading-`{- -}` pairing divergences (7c20e15, cd774f5), and it was
not slow-acting: 7c20e15 was hand-checked against elm-format and gated the same
day, and still shipped a second divergence in the shapes its author did not think
to type. Manual parity checking scales with imagination; an oracle over generated
input does not.

`--comments` closes it by crossing the two axes: it takes each syntax cell,
injects ONE comment into an inter-token gap, and runs oracles 2-4 on the result.
Four placements per gap (`{- -}` / `--`, each trailing the previous token or
leading the next one), because trailing-vs-leading is precisely what the
`CommentRole` classifier decides -- sweeping one end would test half of it. See
COMMENT_AXIS below for the gap scoping and the separate baseline.

It is a DELIBERATE GATE, not part of a default run: 1738 syntax cells become
~38,600 comment cells, about 11 minutes at -j 12 on a 16-core box (three
subprocesses per cell, one of them elm-format). Slice it with --construct/--context
plus --comment-kind / --comment-pos while working on a specific construct, and
run it whole after touching anything in the comment pipeline. A default run
prints a line saying it did not run, so the green never looks broader than it is.

NOT COVERED (deliberate, stated rather than hidden):
  - multi-line string literals: `\"\"\"x\"\"\"` does not parse on one line, so it
    cannot be a one-line atom in this scheme.
  - bare expressions in atom positions (call args, binop operands): a naked
    operator expression there reassociates into a different parse, so bare
    variants run only in value-position contexts; the paren-carrying flat/broken
    variants cover the atom positions.
  - more than one comment per cell: a comment RUN (`{- a -} {- b -}`, or a `--`
    followed by a `{- -}`) has its own rules -- all-or-nothing pairing -- and
    this axis does not generate one. fuzz-idempotency.py's all-gaps-at-once pass
    does, but without the elm-format oracle. Still a hole; smaller than the one
    this closed.

Usage:
    ./matrix-syntax.py                 # whole matrix (all variants)
    ./matrix-syntax.py -j 12           # parallelise (do this; default is 2)
    ./matrix-syntax.py -v              # show source + output for every failure
    ./matrix-syntax.py -k DIR          # write failing cells to DIR as .gren files
    ./matrix-syntax.py --variant broken --variant bareBroken   # author-broken only
    ./matrix-syntax.py --construct recordUpdate2 --context parenBinopArg
    ./matrix-syntax.py --no-parity     # skip oracle 4 (elm-format not installed)
    ./matrix-syntax.py --update-baseline   # rewrite the parity baseline

    ./matrix-syntax.py --comments -j 12          # the comment axis (slow; see above)
    ./matrix-syntax.py --comments --construct binop --context top -v
    ./matrix-syntax.py --comments --comment-kind block --comment-pos lead
    ./matrix-syntax.py --comments --update-baseline   # rewrite the COMMENT baseline

Requires an up-to-date ../../gren-format/app (cd ../../gren-format && ./build.sh).
Oracle 4 additionally requires `elm-format` on PATH.
Exit status is non-zero if any cell fails.
"""

import argparse
import collections
import concurrent.futures
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE.parent.parent / "gren-format" / "app"
BASELINE = HERE / "matrix-parity-baseline.json"
COMMENT_BASELINE = HERE / "matrix-comment-baseline.json"


def _load_sibling(filename, name):
    """Import a hyphenated sibling script as a module (no package to import from)."""
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The gap tokenizer is fuzz-idempotency.py's, imported rather than copied: it is
# literal- and comment-aware (this vocabulary has `'c'` and `"s"` atoms, so a
# naive scanner would find gaps inside them), and two copies would drift.
_fuzz = _load_sibling("fuzz-idempotency.py", "fuzz_idempotency")
gap_indices = _fuzz.gap_indices

# ---------------------------------------------------------------- ELM_PARITY
#
# Oracle 4 (see module docstring) diffs each cell against `elm-format --stdin`.
# Two things make it different from the other three.
#
# TRANSLATION. Every atom and template in this file was authored here, and the
# whole vocabulary is valid Elm except for `when X is` (Elm: `case X of`).
# Record updates, array/list literals, accessors, lambdas, negation and the
# module header are already byte-identical. So the translator is one regex, and
# it is exact for this vocabulary rather than approximate the way translating
# real source would be. Adding a construct or context that is NOT valid Elm
# means extending `to_elm` -- if you cannot, give the cell no Elm twin rather
# than letting a bad translation report a fake divergence.
#
# BASELINE. gren-format diverges from elm-format deliberately in places (README
# "Divergence catalogue"), so a diff is not automatically a bug and this cannot
# be pass/fail. Instead every diverging cell must be REGISTERED in
# matrix-parity-baseline.json with a reason, and the gate fires on:
#   - a cell that diverges and is not registered        -> new divergence
#   - a cell that is registered and no longer diverges  -> baseline is stale
#     (someone fixed it, or the entry was always wrong -- either way, resolve it)
#
# The hazard here is the one in the effectful suite's fixtures: a baseline entry
# that is really a bug freezes it as expected output and the gate stays green
# forever. Three things push back. A reason of REASON_UNREVIEWED is counted and
# reported loudly on every run, so the debt is never silent; a reviewed entry is
# expected to name a catalogue number, which makes registering a divergence a
# documentation decision rather than a keystroke; and a divergence reviewed and
# found to be a genuine BUG is registered with a REASON_BUG prefix, which is
# ALSO reported loudly -- being understood is not the same as being acceptable,
# and a known bug must not go quiet just because someone wrote down what it is.
# A fourth, REASON_PENDING, is the same idea for a divergence whose cause is
# upstream in the parser: reported every run, but its work-list is not ours.
ELM_FORMAT = "elm-format"
PARITY = True  # set from --no-parity / elm-format availability in main()

REASON_UNREVIEWED = "UNREVIEWED"
REASON_BUG = "BUG"  # prefix: "BUG: <what is wrong>"
# A divergence we have diagnosed and cannot fix here: the cause is upstream, in
# the parser gren-format is built on. Format: "PENDING-UPSTREAM:<issue>: <what>".
# Reported loudly on every run for the same reason REASON_BUG is -- it is parked,
# not accepted -- but listed apart from BUG because the work-list is somebody
# else's. The come-back trigger needs no bookkeeping: when the upstream fix lands
# and the compiler-common dependency is bumped, these cells stop diverging and the
# existing `parity-baseline-stale` check fails until the entry is removed.
REASON_PENDING = "PENDING-UPSTREAM"
REASON_PARENS = "README divergence #10 -- gren-format keeps redundant parens"


def to_elm(source):
    """Translate a generated cell to Elm. Exact for this file's vocabulary only.

    The two keywords are replaced INDEPENDENTLY rather than matched as one
    `when <expr> is` pattern. The pattern form broke the moment the comment axis
    existed: it required `when` and `is` to be separated by a single non-space
    token, so `when sel {- c -} is` did not match, the `when` survived into the
    "Elm" source, and elm-format rejected it -- reported (loudly, as designed) as
    `untranslatable` rather than as a fake divergence.

    Replacing the keywords one at a time is exact for the same reason the pattern
    was: this file authors the whole vocabulary, `when` and `is` appear in it
    ONLY as the two keywords of a `when` expression, and the injected comments
    carry a `¤` marker, so neither keyword can occur inside a comment or a string
    literal here.
    """
    return re.sub(r"\bis\b", "of", re.sub(r"\bwhen\b", "case", source))


def parens_only_difference(gren_out, elm_out):
    """True if the two outputs are identical once every redundant paren is gone.

    Sound in the direction that matters: if deleting parens does NOT reconcile
    them, something other than paren elision differs. Real-content newlines and
    indentation are left alone, so a cell that also breaks differently is not
    swept up -- that is the whole point, e.g. `seed |> (when ...)` keeps its
    parens in both formatters and diverges only in layout, so it is correctly
    NOT matched here.

    The one normalization: when gren keeps a redundant paren that elm strips and
    gren rendered it multi-line, the `(` / `)` each sit on their own line, so
    deleting the paren CHARACTER leaves a blank line elm never had. Those
    paren-emptied blank lines are dropped from both sides before comparing.
    Content lines are never merged -- a `|>` step or token that lands on a
    different line still differs after the blank-line drop -- so #20 and genuine
    layout bugs are still not reconciled. This keeps the multi-line #10 family
    (redundant paren, now across rows) auto-classifiable instead of drowning the
    UNREVIEWED list, without weakening the decisive "does elm have FEWER parens"
    test the reviewer applies.
    """
    def canon(s):
        lines = (ln.rstrip() for ln in re.sub(r"[()]", "", s).split("\n"))
        return "\n".join(ln for ln in lines if ln.strip())
    return canon(gren_out) == canon(elm_out)

# --------------------------------------------------------------- COMMENT_AXIS
#
# `--comments` crosses the syntax axis with the comment axis (module docstring).
# One comment per cell, injected into an inter-token gap of the generated source.
#
# PLACEMENTS. Each gap yields four cells: {`{- ¤ -}`, `-- ¤`} x {trail, lead}.
# `trail` puts the comment immediately after the previous token (keeping the
# gap's whitespace after it), `lead` immediately before the next token. That
# distinction is the point: `CommentRole` classification is exactly the
# trailing-vs-leading decision, and gren diverges from elm-format on purpose in
# one direction (#13) while a divergence in the other has been a real bug twice.
# A `-- ¤` needs a newline after it, so the lead/trail forms re-indent the next
# token to the column it already had -- the offside structure is unchanged, which
# keeps the cell parseable.
#
# GAP SCOPING. Sweeping every gap of every cell is ~4x the cells for little
# gain: a gap in the CONTEXT template (`when sel is Just w -> {x}`) does not
# depend on which atom fills `{x}`. So atom-local gaps -- those touching the
# construct's own span -- run for every cell, and context gaps run once per
# context, on the first selected construct's flat variant. Both counts are
# printed; nothing is capped silently.
#
# ORACLES. 1 (the flat/break layout truth) does NOT run: a comment may legally
# force a break, so "written flat, renders flat" is not a truth here. 2 (--show:
# crash / AST / idempotency / output parses) and 3 (the predicate audit) run
# unchanged, and are still truths. 4 (parity) runs against its own baseline,
# matrix-comment-baseline.json, since the cell keys differ. Plus one oracle the
# syntax axis has no use for: the output must contain the marker EXACTLY once --
# a formatter can drop or duplicate a comment and still be a stable fixed point,
# which no diff-against-itself check can see.
MARKER_CH = "¤"
COMMENT_KINDS = {"block": "{- ¤ -}", "line": "-- ¤"}
COMMENT_POSITIONS = ["trail", "lead"]

# Auto-classified comment-parity family, the counterpart of REASON_PARENS.
# Reasons here are SHORT TAGS, not prose: the syntax baseline has 571 entries and
# can afford a sentence each, this one has ~25k and the prose would be 2.7MB of
# repetition nobody can scan. The legend goes in the file's `_comment` block.
REASON_TRAILING = "#13"


def short_tag(reason):
    """`README divergence #21 -- ...` -> `#21`. Anything without a catalogue
    number (notably a `BUG: ...` entry) is kept verbatim, so it stays loud."""
    m = re.search(r"README divergence (#\d+)", reason)
    return m.group(1) if m else reason


def comment_stripped_matches(gren_out, elm_out, collapse_interior=False):
    """True if the two outputs agree once the marker's comment is deleted --
    i.e. the ONLY difference is where the comment sits. Blank lines left behind
    by deleting a comment that was alone on its line are dropped from both sides;
    no content line is ever merged, so a cell that ALSO breaks differently still
    differs here.

    `collapse_interior` additionally squeezes runs of spaces *within* a line,
    leaving its indentation untouched. Needed only when the two formatters put
    the comment at different points of the SAME line (`{ f = {- c -} 1 }` vs
    `{ f {- c -} = 1 }`): deleting it leaves the gap it occupied in different
    places, which is the comment's position showing through, not a code
    difference. Indentation is still compared exactly, so a cell whose code
    lands at a different column still differs.
    """
    def canon(s):
        out = []
        for ln in s.split("\n"):
            if MARKER_CH in ln:
                ln = re.sub(r"\{-\s*" + MARKER_CH + r"\s*-\}|--\s*" + MARKER_CH + r"\s*$", "", ln)
            ln = ln.rstrip()
            if collapse_interior and ln.strip():
                ln = " " * (len(ln) - len(ln.lstrip())) + re.sub(r" +", " ", ln.strip())
            if ln.strip():
                out.append(ln)
        return "\n".join(out)
    return canon(gren_out) == canon(elm_out)


def marker_role(out):
    """Where the marker sits on its line: alone | leading | trailing | inner."""
    for ln in out.split("\n"):
        if MARKER_CH not in ln:
            continue
        stripped = ln.strip()
        body = re.sub(r"\{-\s*" + MARKER_CH + r"\s*-\}|--\s*" + MARKER_CH + r"\s*$", "", stripped).strip()
        if not body:
            return "alone"
        if stripped.startswith(("{-", "--")):
            return "leading"
        if stripped.endswith(("-}",)) or re.search(r"--\s*" + MARKER_CH + r"\s*$", stripped):
            return "trailing"
        return "inner"
    return None


REASON_INHERITED = "INHERITED"  # prefix: "INHERITED: <the syntax cell's own reason>"
REASON_UNRECORDED = "#22"  # comment snapped to a canonical side of an unrecorded token
REASON_ELM_REFLOWS = "#23"  # gren kept its comment-free layout; elm-format re-flowed

# Tokenizer for the "which tokens did the comment cross" test below. `@C` stands
# in for the marker's comment so it takes a slot in the stream.
_TOKEN_RE = re.compile(
    r"@C|[A-Za-z_][A-Za-z0-9_.]*|\d+\.?\d*|'[^']*'|\"[^\"]*\"|[()\[\]{},]|[|<>+*/\\=:.-]+")

# The punctuation the Gren parser records a source position for. A comment that
# crossed one of these moved across a boundary the formatter can SEE, so the move
# is a finding, not the forced canonicalization of #22. Everything else that can
# separate two operands -- `=` `:` `|` `,` `->` and the keywords -- is discarded
# by the parser (see docs/elmFormatComparison.md #22).
_RECORDED = {"(", ")", "[", "]", "{", "}"}
_OPERATOR_RE = re.compile(r"^[|<>+*/\\=:.-]+$")
_UNRECORDED_OPS = {"=", ":", "|", "->"}
_UNRECORDED_WORDS = {"if", "then", "else", "when", "case", "of", "is", "let", "in", "as"}


def _marker_slot(out):
    """The marker's index in the output's paren-free code-token stream, plus that
    stream. Both formatters emit the same code tokens, so the marker is the only
    thing that can occupy a different slot."""
    body = "\n".join(out.split("\n")[3:])
    body = re.sub(r"\{-\s*" + MARKER_CH + r"\s*-\}|--\s*" + MARKER_CH + r"[ \t]*", " @C ", body)
    toks = [t for t in _TOKEN_RE.findall(body) if t not in ("(", ")")]
    return (toks.index("@C") if "@C" in toks else None), [t for t in toks if t != "@C"]


def crossed_only_unrecorded_tokens(gren_out, elm_out):
    """True when the two formatters put the marker on opposite sides of tokens
    the parser does not record a position for, and nothing else moved.

    This is divergence #22, and it is the one comment-position family that is
    FORCED rather than chosen: `x {- c -} = y` and `x = {- c -} y` reach the
    formatter as the same three positions, so gren-format canonicalizes to one
    side and exactly one of the two authorings then differs from elm-format.

    Soundness rests on the crossed span: if it contains a bracket or a binary
    operator -- the only separators the parser DOES position -- gren had the fact
    it needed and a move across it is a real finding, so this returns False and
    the cell stays UNREVIEWED.
    """
    gi, gcode = _marker_slot(gren_out)
    ei, ecode = _marker_slot(elm_out)
    if gi is None or ei is None or gi == ei or gcode != ecode:
        return False
    span = gcode[min(gi, ei):max(gi, ei)]
    if not span:
        return False
    for t in span:
        if t in _RECORDED:
            return False
        if _OPERATOR_RE.match(t) and t not in _UNRECORDED_OPS:
            return False
        if t.isidentifier() and t not in _UNRECORDED_WORDS:
            return False
    return True


def _canon_lines(text, drop_marker=False):
    """Right-trimmed non-blank lines with interior runs of spaces squeezed,
    optionally with the marker's comment deleted.

    Both normalizations are needed to compare a commented output against its
    uncommented one: deleting a comment from mid-line leaves the gap it
    occupied, and an own-line comment leaves a blank line. Indentation is kept
    exact -- a cell whose code lands at a different column must still differ."""
    out = []
    for ln in text.split("\n"):
        if drop_marker and MARKER_CH in ln:
            ln = re.sub(r"\{-\s*" + MARKER_CH + r"\s*-\}|--\s*" + MARKER_CH + r"\s*$", "", ln)
        ln = ln.rstrip()
        if ln.strip():
            out.append(" " * (len(ln) - len(ln.lstrip())) + re.sub(r" +", " ", ln.strip()))
    return out


def only_elm_reflowed(gren_out, elm_out, base_pair):
    """True when gren-format emitted **exactly the layout it emits for this cell
    with no comment in it at all**, and elm-format did not.

    This is the machine-checkable form of catalogue #23 (and of #12 / #16 / #18,
    its instances): a comment is something to place, not a reason to re-lay-out
    working code. When it holds, whatever extra structure the outputs differ by
    was introduced by elm-format alone -- gren cannot be the one that moved the
    code, because its code is byte-identical to the comment-free rendering.

    It is deliberately asymmetric. "Both sides re-flowed and elm ended up with
    more lines" is NOT this rule: a `{- c -}` in a broken call defeats gren's own
    fn/arg0 glue, so gren re-flows too, and that second difference has never been
    reviewed. Requiring gren's side to be untouched is what keeps such a cell out.

    A `--` cell where the comment legitimately forces gren to break also fails
    this -- gren's layout did change -- and stays UNREVIEWED. That is the honest
    outcome: there the claim "gren is right" rests on which re-flow is correct,
    not on gren having done nothing."""
    if not base_pair:
        return False
    base_gren, base_elm = base_pair
    return (_canon_lines(gren_out, drop_marker=True) == _canon_lines(base_gren)
            and _canon_lines(elm_out, drop_marker=True) != _canon_lines(base_elm))


def comment_family(gren_out, elm_out, base_reason, base_pair=None):
    """Auto-classify a comment-parity divergence, or None to leave it UNREVIEWED.

    Two things can differ, and they are judged separately:

      * the comment's POSITION -- two families are auto-classified. #13: gren
        kept it trailing the token it was written after where elm-format re-homed
        it to lead the next one; deliberate and long-standing. #22: the two put
        it on opposite sides of a token the parser records no position for
        (`crossed_only_unrecorded_tokens`), which is forced rather than chosen --
        both authorings reach gren-format as the same three positions, so one of
        them must differ from elm-format whatever side is picked.
      * the LAYOUT around it -- #23, when gren-format emitted exactly its
        comment-free rendering of the cell and elm-format did not
        (`only_elm_reflowed`, which needs `base_pair`). Then the structural
        difference is elm's alone, which is provable rather than argued.
      * everything else -- if the outputs still differ once the comment is
        deleted from both, the underlying syntax cell diverges on its own, and
        `base_reason` (its entry in the SYNTAX baseline) says why. Adding a
        comment to a cell that already keeps a redundant paren must not book a
        second, separate debt for the same #10; it is registered as INHERITED.
        A base cell that agrees with elm-format but whose commented form does
        not is a genuine new finding and stays UNREVIEWED.

    Deliberately NOT auto-classified, whatever else is going on: a divergence
    where gren stranded the comment ALONE on its own line and elm-format did
    not. That is the shape of both pairing bugs (7c20e15, cd774f5) -- gren put a
    leading `{- -}` on its own row where elm-format keeps it on the following
    term's line -- so a classifier that swept "the comment is somewhere else"
    into one family would have frozen the very bug this axis was built to find.
    Reclassifying is not a formality; the same warning applies here as on the
    parens baseline.
    """
    unrecorded = crossed_only_unrecorded_tokens(gren_out, elm_out)
    elm_only = only_elm_reflowed(gren_out, elm_out, base_pair)

    gren_role, elm_role = marker_role(gren_out), marker_role(elm_out)
    moved = gren_role != elm_role
    if moved and gren_role == "alone" and not unrecorded:
        # gren alone / elm beside code is the pairing-bug shape. Never swept,
        # not even when the base explains the code around it.
        return None
    if moved and not unrecorded and not elm_only and not (
        gren_role == "trailing" and elm_role in ("leading", "alone")
    ):
        # A comment move we have no reviewed family for. Review it.
        return None

    parts = []
    if not comment_stripped_matches(gren_out, elm_out, collapse_interior=unrecorded):
        # The outputs differ by more than where the comment sits. Either the
        # underlying cell already diverges (INHERITED), or gren emitted its
        # comment-free layout and the extra structure is elm's alone (#23). With
        # neither, something unexplained happened -- review it.
        if base_reason:
            parts.append(f"{REASON_INHERITED}:{short_tag(base_reason)}")
        elif not elm_only:
            return None
    if unrecorded:
        parts.append(REASON_UNRECORDED)
    elif elm_only:
        parts.append(REASON_ELM_REFLOWS)
    elif moved:
        parts.append(REASON_TRAILING)
    return "+".join(parts) if parts else None


def line_col_of(src, idx):
    """The 0-based column `idx` sits at within its line."""
    return idx - (src.rfind("\n", 0, idx) + 1)


def comment_variant(src, gap, kind, position):
    """`src` with one comment injected at `gap` (the first char of a whitespace
    run). Returns the new source, or None if the placement is not expressible.

    trail: `prev {- ¤ -}<original whitespace>next`
    lead:  `prev<original whitespace up to the last line>{- ¤ -}next`

    For `-- ¤` a newline must follow the comment, and the next token is
    re-indented to the column it already occupied, so the offside structure --
    and therefore the parse -- is unchanged.
    """
    end = gap
    while end < len(src) and src[end] in " \t\r\n":
        end += 1
    if end >= len(src):
        return None
    ws = src[gap:end]
    text = COMMENT_KINDS[kind]
    col = line_col_of(src, end)

    if position == "trail":
        if kind == "line":
            # `-- ¤` must end its line; anything after it on that row would be
            # swallowed. Keep the original whitespace only if it already broke
            # the line, else synthesize the break at the next token's column.
            tail = ws if "\n" in ws else "\n" + " " * col
            return src[:gap] + " " + text + tail + src[end:]
        return src[:gap] + " " + text + ws + src[end:]

    # lead: the comment goes immediately before the next token.
    if kind == "line":
        return src[:gap] + ws + text + "\n" + " " * col + src[end:]
    return src[:gap] + ws + text + " " + src[end:]

# ---------------------------------------------------------------- VOCABULARY
#
# A construct is embedded in a context, in one of four LAYOUT VARIANTS:
#
#   flat        the paren-carrying atom, on one line       (the original matrix)
#   broken      the paren-carrying atom, pre-broken across rows
#   bareFlat    the atom with its outer parens stripped, on one line
#   bareBroken  the bare atom, pre-broken across rows
#
# `broken`/`bareBroken` are the author-broken axis: they feed the formatter
# input that already spans rows, so the multi-line render path (`forceVertical`)
# runs even when the output later collapses. That path is where the 2026-07-18
# dogfooding crash lived (a record-literal field holding a multi-line binop),
# and it is invisible to the flat-only matrix.
#
# `bare*` matters because the paren-carrying atoms route a multi-line operand
# through the *handled* `ParenBlock` arm; the crash was on the BARE form in a
# value position (`{ fld = a || b }` across rows). So bare variants run only in
# value-position contexts (`value_position=True`), where a naked expression is
# both valid and the shape a real author writes.

Construct = collections.namedtuple("Construct", "name atom flat broken paren_wrapped")

# name, template, flat, value_position, kind, header
#   kind    "expr" -- `{x}` is an EXPRESSION and the template is a `v = <body>`
#           body; "type" -- `{x}` is a TYPE and the template is a whole
#           declaration. The two axes have disjoint vocabularies: an expression
#           cannot stand in a signature and a type cannot stand in a call
#           argument, so constructs and contexts are paired by `kind`.
#   header  the module line (+ `v = ` for expression contexts), or None for the
#           kind's default. A `port` context needs `port module`.
Context = collections.namedtuple(
    "Context", "name template flat value_position kind header",
    defaults=("expr", None),
)

# name, atom, flat, broken, paren_wrapped
#   atom          usable anywhere an atom is expected, so anything not already
#                 delimited carries its own parens (one line).
#   flat          documented truth, not observed behavior: True renders on one
#                 line when written on one line; False always breaks.
#   broken        the atom pre-broken across rows (paren-carrying, valid in
#                 EVERY context), or None if the atom cannot meaningfully break.
#                 The bare-broken form is derived by stripping the outer parens.
#   paren_wrapped True when the atom is `( expr )` -- the outer parens are
#                 exactly its first/last char, so a value-position `bare` form is
#                 `atom[1:-1]`. `if`/`when`/`let` are paren_wrapped (so they get
#                 a bare value-position form) but have broken=None: their flat
#                 atom already renders multi-line, so a broken *input* variant
#                 adds parser risk (branch/binding offside) for little gain.
CONSTRUCTS = [
    Construct("intLit",        "1",                            True,  None,                 False),
    Construct("floatLit",      "1.5",                          True,  None,                 False),
    Construct("charLit",       "'c'",                          True,  None,                 False),
    Construct("stringLit",     '"s"',                          True,  None,                 False),
    Construct("varRef",        "one",                          True,  None,                 False),
    Construct("fieldAccess",   "rec.fld",                      True,  None,                 False),
    Construct("accessor",      ".fld",                         True,  None,                 False),
    Construct("recordEmpty",   "{}",                           True,  None,                 False),
    Construct("recordLit1",    "{ a = 1 }",                    True,  "{ a =\n1 }",         False),
    Construct("recordLit2",    "{ a = 1, b = 2 }",             True,  "{ a = 1\n, b = 2 }", False),
    Construct("recordNested",  "{ a = { b = 1 } }",            True,  "{ a =\n{ b = 1 } }", False),
    Construct("recordUpdate1", "{ rec | a = 1 }",              True,  "{ rec\n| a = 1 }",   False),
    Construct("recordUpdate2", "{ rec | a = 1, b = 2 }",       True,  "{ rec\n| a = 1\n, b = 2 }", False),
    Construct("updateNested",  "{ rec | a = { b = 1 } }",      True,  "{ rec\n| a = { b = 1 } }", False),
    Construct("arrayEmpty",    "[]",                           True,  None,                 False),
    # A single-item array. Its `broken` form has no gap BETWEEN items, so gren
    # collapses it back to one line (the #21 rule) exactly as it does a
    # single-field record -- this is the array witness that #21 is one
    # container-wide rule, not record-specific.
    Construct("arrayOne",      "[ 1 ]",                        True,  "[ 1\n]",             False),
    Construct("arrayNums",     "[ 1, 2, 3 ]",                  True,  "[ 1\n, 2\n, 3 ]",    False),
    Construct("arrayRecords",  "[ { a = 1 }, { a = 2 } ]",     True,  "[ { a = 1 }\n, { a = 2 } ]", False),
    Construct("arrayUpdates",  "[ { rec | a = 1 }, { rec | a = 2 } ]", True, "[ { rec | a = 1 }\n, { rec | a = 2 } ]", False),
    # A doubly-parenthesized atom. Every OTHER atom here carries at most the one
    # paren layer it needs, so nothing else in the matrix exercises redundant
    # NESTING -- gren never strips either layer, in any position (README #10).
    Construct("doubleParen",   "((one))",                      True,  None,                 False),
    Construct("call",          "(fn one two)",                 True,  "(fn one\ntwo)",      True),
    Construct("qualifiedCall", "(Array.map fn items)",         True,  "(Array.map fn\nitems)", True),
    Construct("ctor",          "(Just one)",                   True,  "(Just\none)",        True),
    Construct("negate",        "(-one)",                       True,  None,                 True),
    Construct("binop",         "(one + two)",                  True,  "(one\n+ two)",       True),
    Construct("binopMixedPrec", "(one + two * three)",         True,  "(one\n+ two * three)", True),
    Construct("append",        "(items ++ rest)",              True,  "(items\n++ rest)",   True),
    Construct("pipeline",      "(items |> fn)",                True,  "(items\n|> fn)",     True),
    Construct("backPipe",      "(fn <| one)",                  True,  "(fn\n<| one)",       True),
    Construct("lambda",        "(\\q -> q + one)",             True,  "(\\q ->\nq + one)",  True),
    Construct("lambdaRecord",  "(\\q -> { q | a = 1 })",       True,  "(\\q ->\n{ q | a = 1 })", True),
    Construct("lambdaLiteral", "(\\q -> { a = q })",           True,  "(\\q ->\n{ a = q })", True),
    Construct("whenExpr",      "(when sel is Just w -> w)",    False, None,                 True),
    Construct("ifExpr",        "(if cond then one else two)",  False, None,                 True),
    Construct("letExpr",       "(let q = one in q)",           False, None,                 True),
]

# name, template, flat, value_position
#   flat            whether the context itself keeps its content on one line;
#                   if/when/let contexts always break.
#   value_position  True when `{x}` sits where a naked (un-parenthesized)
#                   expression is valid AND is the "= value" / branch-body /
#                   element shape a real author writes broken. Bare variants run
#                   only here; an atom position (call arg, binop operand) would
#                   reassociate a naked operator expression into a different
#                   parse, so those stay False and are covered by the paren-
#                   carrying flat/broken variants instead.
CONTEXTS = [
    Context("top",              "{x}",                          True,  True),
    Context("callArgFirst",     "fn {x}",                       True,  False),
    Context("callArgMid",       "fn a {x} last",                True,  False),
    Context("callArgLast",      "fn a {x}",                     True,  False),
    Context("nestedCallArg",    "fn (gn {x}) last",             True,  False),
    Context("parenBinopArg",    "fn ({x} |> gn) last",          True,  False),
    Context("parenBackPipeArg", "fn (gn <| {x}) last",          True,  False),
    Context("pipelineSeed",     "{x} |> fn",                    True,  False),
    Context("pipelineOperand",  "seed |> {x}",                  True,  False),
    Context("pipelineStep",     "seed |> fn {x}",               True,  False),
    Context("pipelineLast",     "seed |> fn |> gn {x}",         True,  False),
    Context("backPipeBody",     "fn <| {x}",                    True,  True),
    Context("lambdaBody",       "\\q -> {x}",                   True,  True),
    Context("recordField",      "{ fld = {x} }",                True,  True),
    Context("recordFieldMulti", "{ fld = {x}, other = 2 }",     True,  True),
    Context("updateField",      "{ rec | fld = {x} }",          True,  True),
    Context("updateFieldMulti", "{ rec | fld = {x}, other = 2 }", True, True),
    Context("arrayItem",        "[ {x} ]",                      True,  True),
    Context("arrayItemMulti",   "[ {x}, other ]",               True,  True),
    Context("binopLhs",         "{x} ++ tail",                  True,  False),
    Context("binopRhs",         "head ++ {x}",                  True,  False),
    Context("letBinding",       "let bnd = {x} in bnd",         False, True),
    Context("whenBranch",       "when sel is Just w -> {x}",    False, True),
    Context("ifThen",           "if cond then {x} else other",  False, True),
    Context("ifElse",           "if cond then other else {x}",  False, True),
]

# ---------------------------------------------------------------------------
# The TYPE axis: type constructs in declaration contexts.
#
# Everything above embeds an EXPRESSION in an expression context, so the whole
# of Gren's declaration syntax -- signatures, type aliases, unions, ports, a
# `let` binding's annotation -- had no cell here at all. That is one half of
# what hid the signature-`->` comment rule (the other half was
# `fuzz-idempotency.py` sweeping only one comment kind); see
# `docs/commentRunTesting.md`.
#
# Same namedtuples, same variants, same four oracles. The vocabularies are
# disjoint and `enumerate_cells` pairs them by `kind`, because a type cannot
# stand in a call argument and an expression cannot stand in a signature.
#
# The atom convention is the expression axis's: `atom` is usable wherever an
# ATOM is expected, so anything applied or arrow-joined carries its own parens,
# and `paren_wrapped` marks the ones whose bare form (`atom[1:-1]`) is legal in
# a type's "value position" -- a slot that accepts a non-atomic type.
TYPE_CONSTRUCTS = [
    Construct("tyName",       "Int",                      True,  None,                        False),
    Construct("tyVar",        "a",                        True,  None,                        False),
    Construct("tyApp",        "(Array Int)",              True,  "(Array\nInt)",              True),
    Construct("tyAppNested",  "(Array (Maybe Int))",      True,  "(Array\n(Maybe Int))",      True),
    Construct("tyFn",         "(Int -> Int)",             True,  "(Int\n-> Int)",             True),
    Construct("tyFn3",        "(Int -> String -> Bool)",  True,  "(Int\n-> String\n-> Bool)", True),
    Construct("tyRecord0",    "{}",                       True,  None,                        False),
    Construct("tyRecord1",    "{ a : Int }",              True,  "{ a :\nInt }",              False),
    Construct("tyRecord2",    "{ a : Int, b : String }",  True,  "{ a : Int\n, b : String }", False),
    Construct("tyRecordNest", "{ a : { b : Int } }",      True,  "{ a :\n{ b : Int } }",      False),
    Construct("tyExtRecord",  "{ r | a : Int }",          True,  "{ r\n| a : Int }",          False),
]

# A type context's template is a WHOLE declaration, not a `v = ` body, so each
# carries its own trailing definition where the parser needs one.
#
# `flat` is False for the templates that are themselves written across rows --
# the author broke the signature at a `->`, so the canonical output is the
# per-segment shape and oracle 1's "stayed on one line" question does not
# apply. `value_position` is True where the slot accepts a NON-atomic type
# (`foo : {x}` takes `Int -> Int` bare; `Array {x}` does not).
TYPE_CONTEXTS = [
    Context("sigSole",        "foo : {x}\nfoo =\n    one",                     True,  True,  "type"),
    Context("sigFirstArg",    "foo : {x} -> Int\nfoo q =\n    one",            True,  False, "type"),
    Context("sigLastArg",     "foo : Int -> {x}\nfoo q =\n    one",            True,  True,  "type"),
    Context("sigMidArg",      "foo : Int -> {x} -> Bool\nfoo q r =\n    one",  True,  False, "type"),
    Context("sigTypeAppArg",  "foo : Array {x}\nfoo =\n    one",               True,  False, "type"),
    Context("sigBrokenFirst", "foo :\n    {x}\n    -> Int\nfoo q =\n    one",  False, False, "type"),
    Context("sigBrokenLast",  "foo :\n    Int\n    -> {x}\nfoo q =\n    one",  False, True,  "type"),
    Context("aliasBody",      "type alias T =\n    {x}",                       True,  True,  "type"),
    Context("aliasArrow",     "type alias T =\n    {x}\n    -> Int",           False, False, "type"),
    Context("aliasField",     "type alias T =\n    { fld : {x} }",             True,  True,  "type"),
    Context("unionPayload",   "type U\n    = A {x}",                           True,  False, "type"),
    Context("unionPayload2",  "type U\n    = A {x}\n    | B Int",              False, False, "type"),
    Context("letSig",         "v =\n    let\n        bnd : {x}\n        bnd =\n            one\n    in\n    bnd",
                                                                               True,  True,  "type"),
    Context("portArg",        "port send : {x} -> Cmd msg",                    True,  False, "type",
            "port module M exposing (..)\n\n\n"),
    Context("portResult",     "port send : Int -> {x}",                        True,  True,  "type",
            "port module M exposing (..)\n\n\n"),
]

# The four layout variants. `flat_input` variants keep oracle 1 (the flat/break
# two-directional check); the author-broken ones drop it -- a broken input has
# no local layout truth (gren collapses a broken-but-fitting binop), so they
# lean on oracles 2-4 instead.
VARIANTS = ["flat", "broken", "bareFlat", "bareBroken"]
FLAT_INPUT_VARIANTS = {"flat", "bareFlat"}


def strip_outer_parens(multiline):
    """Drop the outer `(` / `)` from a paren-wrapped (possibly multi-line) atom."""
    lines = multiline.split("\n")
    if lines[0].startswith("("):
        lines[0] = lines[0][1:]
    if lines[-1].endswith(")"):
        lines[-1] = lines[-1][:-1]
    return "\n".join(lines)


def variant_atom(construct, variant):
    """The atom string for this construct in this variant, or None if the
    variant does not apply (atom cannot break, or is not paren-wrapped)."""
    if variant == "flat":
        return construct.atom
    if variant == "broken":
        return construct.broken
    if variant == "bareFlat":
        return construct.atom[1:-1] if construct.paren_wrapped else None
    if variant == "bareBroken":
        if construct.paren_wrapped and construct.broken is not None:
            return strip_outer_parens(construct.broken)
        return None
    return None


def enumerate_cells(constructs, contexts, variants):
    """Every applicable (construct, context, variant) triple. A variant is
    skipped when the construct has no atom for it, and bare variants are skipped
    outside value-position contexts."""
    type_names = {c.name for c in TYPE_CONSTRUCTS}
    cells = []
    for c in constructs:
        c_kind = "type" if c.name in type_names else "expr"
        for x in contexts:
            # The two vocabularies are disjoint: a type cannot stand in a call
            # argument and an expression cannot stand in a signature.
            if x.kind != c_kind:
                continue
            for v in variants:
                atom = variant_atom(c, v)
                if atom is None:
                    continue
                if v in ("bareFlat", "bareBroken") and not x.value_position:
                    continue
                cells.append((c, x, v))
    return cells

# --show error titles. "FAILED TO PARSE" means the generated source was invalid
# (our fault); every other title is a formatter bug.
GENERATOR_FAULT = "FAILED TO PARSE"
BUG_TITLES = [
    "Could not format this file",       # Box renderer returned Err
    "AST MISMATCH AFTER FORMATTING",    # format changed meaning
    "FORMATTER NOT IDEMPOTENT",         # format(format(x)) != format(x)
    "COULD NOT PARSE FORMATTED OUTPUT",  # emitted invalid Gren
]


MODULE_LINE = "module M exposing (..)\n\n\n"
HEADER = MODULE_LINE + "v = "


def context_header(context):
    """The text a context's template is appended to. Explicit when the context
    sets one (a `port` declaration needs `port module`), otherwise the kind's
    default: expression templates are a `v = ` body, type templates are whole
    declarations."""
    if context.header is not None:
        return context.header
    return HEADER if context.kind == "expr" else MODULE_LINE


def source_for(construct_atom, context):
    body, _span = substitute(context.template, construct_atom, context_header(context))
    return context_header(context) + body + "\n"


def source_and_atom_span(construct_atom, context):
    """`source_for`, plus the atom's [start, end) offsets in the returned source.
    The comment axis uses the span to tell atom-local gaps from context ones."""
    header = context_header(context)
    body, (lo, hi) = substitute(context.template, construct_atom, header)
    return header + body + "\n", (len(header) + lo, len(header) + hi)


def substitute(template, atom, header=HEADER):
    """Put `atom` where `{x}` is; return (body, (atom_start, atom_end)) with the
    offsets measured in the returned body. A multi-line atom keeps its
    continuation lines aligned under the COLUMN `{x}` lands in, so every
    continuation is indented past the declaration's own column and the source
    parses. The atom's own relative indentation is preserved on top of that base
    -- the formatter re-flows it regardless; all that matters here is that it is
    valid and spans rows.

    The column is the offset of `{x}` within its own line of the template, plus
    the width of the header's last line (4 for `v = `, 0 for a whole-declaration
    template). A type template is itself multi-line, so taking `{x}`'s index in
    the whole string -- which is what this did while every template was one line
    -- would indent continuations by the length of everything above them."""
    idx = template.index("{x}")
    before, after = template[:idx], template[idx + 3:]
    if "\n" not in atom:
        return before + atom + after, (idx, idx + len(atom))
    line_start = template.rfind("\n", 0, idx) + 1
    base = len(header.split("\n")[-1]) if line_start == 0 else 0
    col = base + (idx - line_start)
    lines = atom.split("\n")
    glued = lines[0] + "".join("\n" + " " * col + ln for ln in lines[1:])
    return before + glued + after, (idx, idx + len(glued))


def body_lines(formatted, context=None):
    """The rendered lines oracle 1 measures.

    For an expression cell that is the decl body -- everything after the `v =`
    line -- so "the atom stayed flat" is exactly "one line".

    For a type cell the atom sits inside a whole declaration that has its own
    line count, so there is no single-line answer to compare against. The
    measure there is every non-blank line after the module header, and oracle 1
    asks whether the formatter ADDED rows relative to the flat source rather
    than whether it produced one."""
    lines = formatted.split("\n")
    if context is None or context.kind == "expr":
        for i, line in enumerate(lines):
            if line.startswith("v ="):
                body = lines[i + 1:]
                while body and not body[-1].strip():
                    body.pop()
                return body
        return None
    body = [ln for ln in lines[1:] if ln.strip()]
    return body or None


def run(app_args, path):
    return subprocess.run(
        ["node", str(APP), app_args, str(path)],
        capture_output=True, text=True, timeout=120,
    )


def check_parity(source, gren_out):
    """Oracle 4. Returns None if the cell agrees with elm-format, else the diff."""
    elm_source = to_elm(source)
    try:
        elm = subprocess.run([ELM_FORMAT, "--stdin"], input=elm_source,
                             capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return dict(kind="elm-format-timeout", gren="", elm="")

    if elm.returncode != 0:
        # Our translation produced something elm-format will not accept. That is
        # a to_elm bug, not a formatter bug -- surface it rather than skip it.
        return dict(kind="untranslatable", gren="", elm=(elm.stderr + elm.stdout).strip()[:400])

    # Compare in Elm's token space: gren's output goes through the same
    # translation, so `when`/`case` is not itself reported as a divergence.
    gren_elm = to_elm(gren_out).strip()
    elm_out = elm.stdout.strip()
    if gren_elm == elm_out:
        return None
    return dict(kind="divergence", gren=gren_elm, elm=elm_out)


def base_output_pair(cell):
    """The uncommented cell's own two outputs, in Elm token space -- the same
    pair `check_parity` compares. Keyed by `base_parity_key` so a comment cell
    can ask "did the comment change anything the uncommented cell did not
    already do?" (see `explained_by_base`)."""
    construct, context, variant = cell
    source = source_for(variant_atom(construct, variant), context)
    key = f"{construct.name}/{context.name}" + ("" if variant == "flat" else "@" + variant)
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "M.gren"
        path.write_text(source)
        try:
            shown = run("--show", path)
        except subprocess.TimeoutExpired:
            return key, None
        if shown.returncode != 0:
            return key, None
    try:
        elm = subprocess.run([ELM_FORMAT, "--stdin"], input=to_elm(source),
                             capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return key, None
    if elm.returncode != 0:
        return key, None
    return key, (to_elm(shown.stdout).strip(), elm.stdout.strip())


def check_cell(cell):
    construct, context, variant = cell
    cname, xname = construct.name, context.name
    atom = variant_atom(construct, variant)
    source = source_for(atom, context)
    flat_input = variant in FLAT_INPUT_VARIANTS
    expect_flat = flat_input and construct.flat and context.flat

    def result(**kw):
        return dict(construct=cname, context=xname, variant=variant, source=source, **kw)

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "M.gren"
        path.write_text(source)

        try:
            shown = run("--show", path)
        except subprocess.TimeoutExpired:
            return result(kind="timeout", detail="--show timed out")

        if shown.returncode != 0:
            out = shown.stderr + shown.stdout
            if GENERATOR_FAULT in out:
                return result(kind="skipped", detail="generated source does not parse")
            for title in BUG_TITLES:
                if title in out:
                    return result(kind=title, detail=out.strip()[:600])
            return result(kind="unknown-error", detail=out.strip()[:600])

        formatted = shown.stdout
        body = body_lines(formatted, context)
        if body is None:
            return result(kind="no-body", detail="could not locate the declaration in output", output=formatted)

        try:
            audited = run("--audit-predicates", path)
            findings = json.loads(audited.stdout) if audited.returncode == 0 else []
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            findings = []

        if findings:
            roots = [f for f in findings if not f["propagated"]]
            if roots:
                return result(kind="predicate-lie", output=formatted,
                              detail="; ".join(f'{f["predicate"]} said {f["boxKind"]} breaks, '
                                               f'rendered: {f["rendered"]}' for f in roots[:3]))

        # Oracle 1 (the flat/break two-directional check) is a *flat-input*
        # truth, so it runs only on flat_input variants. An author-broken
        # variant has no local layout truth -- gren collapses a broken-but-
        # fitting binop -- so it leans on oracles 2-4 (crash/AST/idempotency,
        # predicate audit, elm-format parity) instead.
        if flat_input and context.kind == "expr":
            is_flat = len(body) == 1
            if expect_flat and not is_flat:
                return result(kind="broke-when-flat", output=formatted,
                              detail="written on one line and nothing forces a break, but the body "
                                     f"broke across {len(body)} lines")
            if not expect_flat and is_flat:
                return result(kind="flat-when-should-break", output=formatted,
                              detail="an if/when/let is involved, so the body must break")
        elif flat_input and expect_flat:
            # Type axis. The declaration has its own line count, so "flat" is
            # not "one line" -- it is "the formatter added no rows". A type has
            # no `if`/`when`/`let`, so there is no must-break half to check.
            want = len([ln for ln in source.split("\n")[len(context_header(context).split("\n")) - 1:]
                        if ln.strip()])
            if len(body) > want:
                return result(kind="broke-when-flat", output=formatted,
                              detail=f"written across {want} lines with nothing forcing a break, "
                                     f"but the declaration rendered across {len(body)}")

        # Parity runs only on cells that satisfy oracles 1-3. A cell that
        # already violates a truth would diverge from elm-format too, and
        # reporting it twice buys nothing -- fix the truth first.
        parity = check_parity(source, formatted) if PARITY else None
        return result(kind="ok", output=formatted, parity=parity)


def enumerate_comment_cells(cells, kinds, positions):
    """Every (syntax cell, gap, comment kind, position) the comment axis runs.

    Atom-local gaps (those touching the construct's own span) run for every
    cell; context gaps run once per context, on the first selected construct's
    flat variant, because a gap in the context template does not depend on which
    atom fills `{x}`. Returns (comment_cells, n_atom_gap_cells, n_ctx_gap_cells).
    """
    # The representative that carries the context gaps is fixed to the FIRST
    # construct of the full vocabulary in its flat variant, NOT to the first
    # selected cell. It has to be filter-independent: a cell's baseline key must
    # mean the same thing in a `--construct binop` slice as in a whole run, or
    # every slice reports the context gaps it happens to inherit as brand-new
    # divergences. A slice that excludes the representative simply sweeps no
    # context gaps -- the whole run covers them.
    #
    # One representative per AXIS: the expression contexts' gaps are swept with
    # the first expression construct, the type contexts' with the first type
    # construct. A single global representative would sweep no context gaps at
    # all on the axis it does not belong to, since `enumerate_cells` never pairs
    # an expression construct with a type context.
    reps = {"expr": (CONSTRUCTS[0].name, "flat"),
            "type": (TYPE_CONSTRUCTS[0].name, "flat")}
    out, n_atom, n_ctx = [], 0, 0
    for construct, context, variant in cells:
        atom = variant_atom(construct, variant)
        source, (lo, hi) = source_and_atom_span(atom, context)
        is_rep = (construct.name, variant) == reps[context.kind]
        # The module line, whose length varies: a `port` context's header is
        # `port module …`.
        header_end = len(context_header(context)) - (4 if context.kind == "expr" else 0)
        for ordinal, gap in enumerate(gap_indices(source)):
            if gap < header_end:
                # The module header is fixed boilerplate here, identical in every
                # cell -- it is not one of this matrix's two axes, and header
                # comments are already the corpus fuzzers' ground. Skipping it
                # also keeps gap ordinals (and so baseline keys) counting from
                # the declaration.
                continue
            end = gap
            while end < len(source) and source[end] in " \t\r\n":
                end += 1
            atom_local = end >= lo and gap <= hi
            if not atom_local and not is_rep:
                continue
            for kind in kinds:
                for position in positions:
                    variant_src = comment_variant(source, gap, kind, position)
                    if variant_src is None:
                        continue
                    out.append(dict(construct=construct.name, context=context.name,
                                    variant=variant, ordinal=ordinal, kind=kind,
                                    position=position, source=variant_src,
                                    atom_local=atom_local))
                    if atom_local:
                        n_atom += 1
                    else:
                        n_ctx += 1
    return out, n_atom, n_ctx


def comment_key(cell):
    return (f'{cell["construct"]}/{cell["context"]}@{cell["variant"]}'
            f'#g{cell["ordinal"]}.{cell["kind"]}.{cell["position"]}')


def base_parity_key(cell):
    """The SYNTAX-baseline key of the cell this comment cell was built from, so
    a divergence the uncommented cell already has is inherited rather than
    re-registered as fresh comment debt."""
    suffix = "" if cell["variant"] == "flat" else "@" + cell["variant"]
    return f'{cell["construct"]}/{cell["context"]}{suffix}'


def check_comment_cell(cell):
    """Oracles 2, 3 and 4 over one comment cell, plus the marker-count check.
    Oracle 1 does not apply -- a comment may legally force a break."""
    source = cell["source"]

    def result(**kw):
        return dict(cell, **kw)

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "M.gren"
        path.write_text(source)

        try:
            shown = run("--show", path)
        except subprocess.TimeoutExpired:
            return result(kind_result="timeout", detail="--show timed out")

        if shown.returncode != 0:
            out = shown.stderr + shown.stdout
            if GENERATOR_FAULT in out:
                # A comment the PARSER rejects in that gap (e.g. between two type
                # variables) -- a known parser limitation, not a formatter bug.
                return result(kind_result="skipped", detail="commented source does not parse")
            for title in BUG_TITLES:
                if title in out:
                    return result(kind_result=title, detail=out.strip()[:600])
            return result(kind_result="unknown-error", detail=out.strip()[:600])

        formatted = shown.stdout
        seen = formatted.count(MARKER_CH)
        if seen != 1:
            # A dropped or duplicated comment survives every self-consistency
            # check in the repo -- the duplicate reformats to itself. Only a
            # count can see it.
            return result(kind_result="comment-count", output=formatted,
                          detail=f"exactly one comment went in, {seen} came out")

        try:
            audited = run("--audit-predicates", path)
            findings = json.loads(audited.stdout) if audited.returncode == 0 else []
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            findings = []
        roots = [f for f in findings if not f["propagated"]]
        if roots:
            return result(kind_result="predicate-lie", output=formatted,
                          detail="; ".join(f'{f["predicate"]} said {f["boxKind"]} breaks, '
                                           f'rendered: {f["rendered"]}' for f in roots[:3]))

        parity = check_parity(source, formatted) if PARITY else None
        return result(kind_result="ok", output=formatted, parity=parity)


def load_baseline():
    if not BASELINE.exists():
        return {}
    return json.loads(BASELINE.read_text())["cells"]


def load_comment_baseline():
    if not COMMENT_BASELINE.exists():
        return {}
    return json.loads(COMMENT_BASELINE.read_text())["cells"]


def write_comment_baseline(cells):
    COMMENT_BASELINE.write_text(json.dumps({
        "_comment": [
            "Registered elm-format parity divergences on the COMMENT axis --",
            "see COMMENT_AXIS in matrix-syntax.py. Key is",
            "construct/context@variant#g<gap>.<block|line>.<trail|lead>.",
            "The matrix fails on a cell that diverges and is NOT listed, and on a cell listed",
            "here that no longer diverges. Regenerate with ./matrix-syntax.py --comments",
            "--update-baseline.",
            "",
            "REASON TAGS (short on purpose -- 25k prose entries would be unscannable):",
            "  #NN            a README divergence-catalogue entry, about where the COMMENT",
            "                 sits. #13 is the only one auto-classified: gren keeps a comment",
            "                 trailing the token it was written after, elm-format re-homes it",
            "                 to lead the next one.",
            "  INHERITED:#NN  the UNCOMMENTED cell already diverges for #NN (look it up in",
            "                 matrix-parity-baseline.json); the comment itself sits where",
            "                 elm-format puts it. Not fresh comment debt.",
            "  a+b            both apply.",
            "  UNREVIEWED     not yet reviewed. May be a real bug frozen as expected output.",
            "  PENDING-UPSTREAM:<issue>: <what>",
            "                 diagnosed, and the cause is upstream in the parser rather than in",
            "                 this formatter. Reported on every run. Clears by itself: when the",
            "                 fix ships the cell stops diverging and the stale-entry check fires.",
            "",
            "A divergence where gren strands the comment ALONE on its own line is NEVER",
            "auto-classified, whatever else is going on: that is the shape of the two pairing",
            "bugs this axis was built to find (7c20e15, cd774f5).",
        ],
        "cells": dict(sorted(cells.items())),
    }, indent=2) + "\n")


def report_comment_parity(results, baseline, update, verbose=False, base_pairs=None):
    """Oracle 4 over the comment axis, gated against COMMENT_BASELINE.
    Mirrors report_parity; separate because the keys and the auto-classifier
    differ. Returns (failures, ...)."""
    checked = [r for r in results if r["kind_result"] == "ok"]
    diverging = {comment_key(r): r for r in results
                 if (r.get("parity") or {}).get("kind") == "divergence"}
    broken = [r for r in results
              if (r.get("parity") or {}).get("kind") in ("untranslatable", "elm-format-timeout")]

    if update:
        syntax_baseline = load_baseline()
        cells = {}
        for key, r in diverging.items():
            prior = baseline.get(key)
            if prior and prior != REASON_UNREVIEWED:
                cells[key] = prior
                continue
            family = comment_family(r["parity"]["gren"], r["parity"]["elm"],
                                    syntax_baseline.get(base_parity_key(r)),
                                    (base_pairs or {}).get(base_parity_key(r)))
            cells[key] = family or REASON_UNREVIEWED
        write_comment_baseline(cells)
        print(f"wrote {len(cells)} registered divergences to {COMMENT_BASELINE.name}")
        return []

    failures = []
    for r in broken:
        failures.append((f'[{r["parity"]["kind"]}] {comment_key(r)}',
                         f'to_elm produced source elm-format rejects: {r["parity"]["elm"]}'))

    ran = {comment_key(r) for r in checked}
    for key, r in sorted(diverging.items()):
        if key not in baseline:
            failures.append((f"[comment-parity-new-divergence] {key}",
                             "diverges from elm-format and is not registered in "
                             f"{COMMENT_BASELINE.name}\n"
                             + "  source:\n"
                             + "\n".join(f"    |{ln}" for ln in r["source"].strip().split("\n")[3:])
                             + "\n" + side_by_side(r["parity"])))
    for key in sorted(baseline):
        if key in ran and key not in diverging:
            failures.append((f"[comment-parity-baseline-stale] {key}",
                             f'registered in {COMMENT_BASELINE.name} as "{baseline[key]}" but it '
                             "now matches elm-format -- remove the entry"))

    registered = {k: v for k, v in baseline.items() if k in diverging}
    unreviewed = sorted(k for k, v in registered.items() if v == REASON_UNREVIEWED)
    # `REASON_BUG + ":"` anywhere, not just as a prefix: an inherited reason
    # reads "INHERITED: BUG: ...", and a known bug must not go quiet just
    # because the comment cell inherited it from its syntax cell.
    bugs = sorted(k for k, v in registered.items() if REASON_BUG + ":" in v)
    pending = sorted(k for k, v in registered.items() if REASON_PENDING + ":" in v)
    if ran:
        print(f"comment parity: {len(ran) - len(diverging)}/{len(ran)} cells byte-identical to "
              f"elm-format, {len(registered)} registered divergences")
        for reason, count in collections.Counter(registered.values()).most_common():
            print(f"  {count:5}  {reason if len(reason) <= 110 else reason[:107] + '...'}")
        if unreviewed:
            print(f"\n  !! {len(unreviewed)} UNREVIEWED divergence(s) -- each one may be a real bug\n"
                  f"     frozen as expected output. Establish a reason or fix it:")
            for key in unreviewed[:40]:
                print(f"       {key}")
            if len(unreviewed) > 40:
                print(f"       ... and {len(unreviewed) - 40} more")
        if bugs:
            print(f"\n  !! {len(bugs)} known BUG(s) registered -- reviewed, not deliberate,\n"
                  f"     still wrong. These are a work-list, not a decision:")
            for key in bugs:
                print(f"       {key}: {registered[key][len(REASON_BUG) + 2:]}")
        if pending:
            print(f"\n  !! {len(pending)} divergence(s) PENDING-UPSTREAM -- diagnosed, not fixable\n"
                  f"     here. They clear when the upstream fix ships and this baseline\n"
                  f"     reports them stale:")
            for key in pending:
                print(f"       {key}: {registered[key][len(REASON_PENDING) + 1:]}")
        if verbose:
            print("\n  registered divergences in full:\n")
            for key, r in sorted(diverging.items()):
                print(f'  --- {key}  [{baseline.get(key, "?")}]')
                print("  source:")
                for ln in r["source"].strip().split("\n")[3:]:
                    print(f"    |{ln}")
                print(side_by_side(r["parity"]) + "\n")
        elif unreviewed:
            print("\n     (-v shows each divergence beside elm-format's output)")
        print()
    return failures


def run_comment_axis(cells, args):
    """The `--comments` mode: enumerate comment cells, run oracles 2-4, report."""
    kinds = [args.comment_kind] if args.comment_kind else list(COMMENT_KINDS)
    positions = [args.comment_pos] if args.comment_pos else list(COMMENT_POSITIONS)

    comment_cells, n_atom, n_ctx = enumerate_comment_cells(cells, kinds, positions)
    if not comment_cells:
        sys.exit("no comment cells selected -- check the filters")
    print(f"comment axis: {len(comment_cells)} cells over {len(cells)} syntax cells "
          f"({n_atom} at atom-local gaps, {n_ctx} at context gaps)")
    print(f"  kinds: {', '.join(kinds)}   positions: {', '.join(positions)}\n")

    # The UNCOMMENTED cells' own output pairs. Only needed when writing the
    # baseline, where `explained_by_base` asks whether a comment changed
    # anything its cell did not already do -- ~4% more work on top of 38k
    # comment cells, and the difference between an INHERITED reason that is the
    # whole story and one that only covers part of it.
    base_pairs = {}
    if args.update_baseline and PARITY:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for key, pair in pool.map(base_output_pair, cells):
                if pair:
                    base_pairs[key] = pair

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for r in pool.map(check_comment_cell, comment_cells):
            results.append(r)

    by_kind = collections.Counter(r["kind_result"] for r in results)
    failures = [r for r in results if r["kind_result"] not in ("ok", "skipped")]

    if args.keep and failures:
        args.keep.mkdir(parents=True, exist_ok=True)
        for r in failures:
            (args.keep / f'{comment_key(r).replace("/", "__")}.gren').write_text(r["source"])
        print(f"wrote {len(failures)} failing cells to {args.keep}\n")

    shown = failures if args.verbose else failures[:10]
    for r in shown:
        print(f'FAIL [{r["kind_result"]}] {comment_key(r)}')
        print(f'  {r["detail"]}')
        print("  source:")
        for line in r["source"].strip().split("\n")[3:]:
            print(f"    |{line}")
        if args.verbose and r.get("output"):
            print("  output:")
            for line in r["output"].rstrip().split("\n"):
                print(f"    |{line}")
        print()
    if len(failures) > len(shown):
        print(f"... and {len(failures) - len(shown)} more failures (-v to see all)\n")

    print(f'{len(comment_cells)} comment cells: {by_kind["ok"]} ok, {len(failures)} failing, '
          f'{by_kind["skipped"]} skipped (commented source does not parse)\n')
    if failures:
        for kind, count in collections.Counter(r["kind_result"] for r in failures).most_common():
            print(f"  {kind}: {count}")
        print()

    parity_failures = []
    if PARITY:
        parity_failures = report_comment_parity(results, load_comment_baseline(),
                                                args.update_baseline, verbose=args.verbose,
                                                base_pairs=base_pairs)
        for title, detail in (parity_failures if args.verbose else parity_failures[:10]):
            print(f"FAIL {title}")
            print(f"  {detail}\n")
        if len(parity_failures) > 10 and not args.verbose:
            print(f"... and {len(parity_failures) - 10} more parity failures (-v to see all)\n")

    if failures or parity_failures:
        return 1
    print("Every comment cell formats, preserves its comment exactly once, is idempotent\n"
          "and AST-equivalent, tells no predicate lies, and sits where elm-format puts it\n"
          "except where the comment baseline says otherwise.")
    return 0


def write_baseline(cells):
    BASELINE.write_text(json.dumps({
        "_comment": [
            "Registered elm-format parity divergences -- see ELM_PARITY in matrix-syntax.py.",
            "A cell listed here diverges from elm-format on purpose (or is UNREVIEWED debt).",
            "The matrix fails on a cell that diverges and is NOT listed, and on a cell listed",
            "here that no longer diverges. Regenerate with ./matrix-syntax.py --update-baseline.",
            "Every UNREVIEWED entry may be a real bug frozen as expected output. Replace the",
            "reason with a README divergence-catalogue number once reviewed, or fix the bug.",
        ],
        "cells": dict(sorted(cells.items())),
    }, indent=2) + "\n")


def parity_key(result):
    # Flat cells keep the original unsuffixed key so the existing baseline (all
    # flat) still matches; author-broken/bare variants carry an `@variant` tag.
    variant = result["variant"]
    suffix = "" if variant == "flat" else "@" + variant
    return f'{result["construct"]}/{result["context"]}{suffix}'


def report_parity(results, baseline, update, verbose=False):
    """Gate oracle 4 against the baseline. Returns (failures, exit_nonzero)."""
    # Parity only ran on cells that passed oracles 1-3, so only those can be
    # judged -- a cell that failed earlier must not be called stale-in-baseline.
    checked = [r for r in results if r["kind"] == "ok"]
    diverging = {parity_key(r): r for r in results if (r.get("parity") or {}).get("kind") == "divergence"}
    broken = [r for r in results if (r.get("parity") or {}).get("kind") in ("untranslatable", "elm-format-timeout")]

    if update:
        cells = {}
        for key, r in diverging.items():
            # Keep a reason already reviewed; classify the rest as far as we
            # honestly can and leave the remainder as visible debt.
            prior = baseline.get(key)
            if prior and prior != REASON_UNREVIEWED:
                cells[key] = prior
            elif parens_only_difference(r["parity"]["gren"], r["parity"]["elm"]):
                cells[key] = REASON_PARENS
            else:
                cells[key] = REASON_UNREVIEWED
        write_baseline(cells)
        print(f"wrote {len(cells)} registered divergences to {BASELINE.name}")
        return [], False

    failures = []
    for r in broken:
        failures.append((f'[{r["parity"]["kind"]}] {parity_key(r)}',
                         f'to_elm produced source elm-format rejects: {r["parity"]["elm"]}'))

    ran = {parity_key(r) for r in checked}
    for key, r in sorted(diverging.items()):
        if key not in baseline:
            failures.append((f"[parity-new-divergence] {key}",
                             "diverges from elm-format and is not registered in "
                             f"{BASELINE.name}\n" + side_by_side(r["parity"])))
    for key in sorted(baseline):
        if key in ran and key not in diverging:
            failures.append((f"[parity-baseline-stale] {key}",
                             f'registered in {BASELINE.name} as "{baseline[key]}" but it now '
                             "matches elm-format -- remove the entry"))

    registered = {k: v for k, v in baseline.items() if k in diverging}
    unreviewed = [k for k, v in registered.items() if v == REASON_UNREVIEWED]
    bugs = sorted(k for k, v in registered.items() if v.startswith(REASON_BUG))
    pending = sorted(k for k, v in registered.items() if v.startswith(REASON_PENDING))
    if registered:
        print(f'parity: {len(ran) - len(diverging)}/{len(ran)} cells byte-identical to elm-format, '
              f"{len(registered)} registered divergences")
        for reason, count in collections.Counter(registered.values()).most_common():
            print(f"  {count:4}  {reason}")
        if unreviewed:
            print(f"\n  !! {len(unreviewed)} UNREVIEWED divergence(s) -- each one may be a real bug\n"
                  f"     frozen as expected output. Establish a reason or fix it:")
            for key in sorted(unreviewed):
                print(f"       {key}")
        if bugs:
            # Reviewed and known-wrong. Still printed every run: writing down what
            # a bug is does not make it acceptable, and a baseline entry is the
            # easiest place in this repo for one to go quiet.
            print(f"\n  !! {len(bugs)} known BUG(s) registered -- reviewed, not deliberate,\n"
                  f"     still wrong. These are a work-list, not a decision:")
            for key in bugs:
                print(f"       {key}: {registered[key][len(REASON_BUG) + 2:]}")
        if pending:
            print(f"\n  !! {len(pending)} divergence(s) PENDING-UPSTREAM -- diagnosed, not fixable\n"
                  f"     here. They clear when the upstream fix ships and this baseline\n"
                  f"     reports them stale:")
            for key in pending:
                print(f"       {key}: {registered[key][len(REASON_PENDING) + 1:]}")
        if verbose:
            print("\n  registered divergences in full:\n")
            for key, r in sorted(diverging.items()):
                print(f'  --- {key}  [{baseline.get(key, "?")}]')
                print(side_by_side(r["parity"]) + "\n")
        elif unreviewed:
            print("\n     (-v shows each divergence next to elm-format's output)")
        print()
    return failures, bool(failures)


def side_by_side(parity):
    out = ["  gren-format:"]
    out += [f"    |{ln}" for ln in parity["gren"].split("\n")]
    out += ["  elm-format:"]
    out += [f"    |{ln}" for ln in parity["elm"].split("\n")]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-j", "--jobs", type=int, default=2, help="concurrent cells (default 2)")
    ap.add_argument("-v", "--verbose", action="store_true", help="show source + output for every failure")
    ap.add_argument("-k", "--keep", type=pathlib.Path, help="write failing cells to this dir as .gren files")
    ap.add_argument("--construct", help="only this construct")
    ap.add_argument("--context", help="only this context")
    ap.add_argument("--variant", choices=VARIANTS, action="append",
                    help="only this layout variant (repeatable); default is all four")
    ap.add_argument("--no-parity", action="store_true",
                    help="skip oracle 4 (the elm-format parity diff)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="rewrite the parity baseline from this run (--comments: the comment one)")
    ap.add_argument("--comments", action="store_true",
                    help="run the COMMENT axis instead of the plain syntax matrix (slow)")
    ap.add_argument("--comment-kind", choices=sorted(COMMENT_KINDS),
                    help="comment axis: only this comment kind (default both)")
    ap.add_argument("--comment-pos", choices=COMMENT_POSITIONS,
                    help="comment axis: only this placement (default both)")
    args = ap.parse_args()

    if not APP.exists():
        sys.exit(f"{APP} not found -- run (cd ../../gren-format && ./build.sh) first")

    global PARITY
    PARITY = not args.no_parity
    if PARITY and not shutil.which(ELM_FORMAT):
        # Loud, never silent: an oracle that quietly stops running is worse than
        # one that was never added, because the green means less than it looks.
        print(f"!! {ELM_FORMAT} not on PATH -- ORACLE 4 (elm-format parity) IS NOT RUNNING.\n"
              f"   Install it, or pass --no-parity to say so on purpose.\n")
        PARITY = False
    if args.update_baseline and not PARITY:
        sys.exit("--update-baseline needs the parity oracle")

    all_constructs = CONSTRUCTS + TYPE_CONSTRUCTS
    all_contexts = CONTEXTS + TYPE_CONTEXTS
    constructs = [c for c in all_constructs if not args.construct or c.name == args.construct]
    contexts = [x for x in all_contexts if not args.context or x.name == args.context]
    variants = args.variant or VARIANTS
    if not constructs or not contexts:
        sys.exit("no cells selected -- check --construct/--context names")

    cells = enumerate_cells(constructs, contexts, variants)
    if args.comments:
        return run_comment_axis(cells, args)

    per_variant = collections.Counter(v for _, _, v in cells)
    breakdown = ", ".join(f"{per_variant[v]} {v}" for v in variants if per_variant[v])
    print(f"{len(cells)} cells ({breakdown})\n")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for r in pool.map(check_cell, cells):
            results.append(r)

    by_kind = collections.Counter(r["kind"] for r in results)
    failures = [r for r in results if r["kind"] not in ("ok", "skipped")]

    if args.keep and failures:
        args.keep.mkdir(parents=True, exist_ok=True)
        for r in failures:
            (args.keep / f'{r["construct"]}__{r["context"]}__{r["variant"]}.gren').write_text(r["source"])
        print(f"wrote {len(failures)} failing cells to {args.keep}\n")

    if failures:
        shown = failures if args.verbose else failures[:10]
        for r in shown:
            print(f'FAIL [{r["kind"]}] {r["construct"]} in {r["context"]} ({r["variant"]})')
            print(f'  {r["detail"]}')
            print("  source:")
            for line in r["source"].strip().split("\n")[3:]:
                print(f"    |{line}")
            if args.verbose and r.get("output"):
                print("  output:")
                for line in r["output"].rstrip().split("\n"):
                    print(f"    |{line}")
            print()
        if len(failures) > len(shown):
            print(f"... and {len(failures) - len(shown)} more failures (-v to see all)\n")

    skipped_by_variant = collections.Counter(r["variant"] for r in results if r["kind"] == "skipped")
    skip_note = ""
    if skipped_by_variant:
        skip_note = " [" + ", ".join(f"{n} {v}" for v, n in skipped_by_variant.most_common()) + "]"
    print(f"{len(cells)} cells: {by_kind['ok']} ok, {len(failures)} failing, "
          f"{by_kind['skipped']} skipped (generated source does not parse){skip_note}\n")

    if failures:
        for kind, count in collections.Counter(r["kind"] for r in failures).most_common():
            print(f"  {kind}: {count}")
        print()

    parity_failures = []
    if PARITY:
        parity_failures, _ = report_parity(results, load_baseline(), args.update_baseline,
                                           verbose=args.verbose)
        for title, detail in (parity_failures if args.verbose else parity_failures[:10]):
            print(f"FAIL {title}")
            print(f"  {detail}\n")
        if len(parity_failures) > 10 and not args.verbose:
            print(f"... and {len(parity_failures) - 10} more parity failures (-v to see all)\n")

    if failures or parity_failures:
        return 1

    if PARITY:
        print("Every cell renders as the author-driven rule requires, with no predicate lies,\n"
              "and diverges from elm-format only where the baseline says it should.")
    else:
        print("Every cell renders as the author-driven rule requires, with no predicate lies.")
    # Never silent: this run varied SYNTAX only. A comment-placement divergence
    # from elm-format is invisible to it, which is exactly how two of them
    # shipped -- so say so on every green run rather than letting the green look
    # broader than it is.
    print("\nNote: comments were not varied in this run. `--comments` crosses this matrix\n"
          "      with the comment axis (the gate that would have caught 7c20e15 / cd774f5).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
