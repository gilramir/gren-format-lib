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

It is a DELIBERATE GATE, not part of a default run: 2459 syntax cells become
~83,600 comment cells, at three subprocesses per cell, one of them elm-format.
It is a long sweep. Slice it with --construct/--context plus --comment-kind /
--comment-pos while working on a specific construct, and run it whole after
touching anything in the comment pipeline. A default run prints a line saying it
did not run, so the green never looks broader than it is.

NOT COVERED (deliberate, stated rather than hidden):
  - multi-line string literals: `\"\"\"x\"\"\"` does not parse on one line, so it
    cannot be a one-line atom in this scheme.
  - bare expressions in atom positions (call args, binop operands): a naked
    operator expression there reassociates into a different parse, so bare
    variants run only in value-position contexts; the paren-carrying flat/broken
    variants cover the atom positions.
  - a run of more than TWO comments, and a run split across the gap (one comment
    trailing the previous token, one leading the next). `--comment-runs` sweeps
    every two-member composition, which is where the rules live -- see THE RUN
    AXIS below for why length is not the axis -- and both of these are stated
    here rather than hidden because neither is swept.

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

    ./matrix-syntax.py --comments --comment-runs -j 12   # the RUN axis (slower still)
    ./matrix-syntax.py --comments --comment-mix multi,line --construct binop -v

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
COMMENT_RUN_BASELINE = HERE / "matrix-comment-run-baseline.json"
# Which of the two the comment axis is gating against; set in main(). The run
# axis gets a FILE OF ITS OWN rather than more keys in the existing one: that
# baseline is a reviewed asset of ~25k entries, and a run sweep that is not
# also a single-comment sweep would report every one of them stale.
ACTIVE_COMMENT_BASELINE = COMMENT_BASELINE


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
# PLACEMENTS. Each gap yields six cells:
# {`{- ¤ -}`, `-- ¤`, `{- ¤` .. `-}`} x {trail, lead}.
# `trail` puts the comment immediately after the previous token (keeping the
# gap's whitespace after it), `lead` immediately before the next token. That
# distinction is the point: `CommentRole` classification is exactly the
# trailing-vs-leading decision, and gren diverges from elm-format on purpose in
# one direction (#13) while a divergence in the other has been a real bug twice.
#
# THE THREE KINDS ARE THE AXIS, not a detail of it. Almost every placement rule
# branches on "can this comment share a line", and the three kinds are exactly
# the three answers: a single-line `{- -}` can, a `--` cannot (it runs to
# end-of-line), a multi-line `{- .. -}` cannot (it brings its own newlines).
# `multi` was missing here until 2026-08-05 while `fuzz-idempotency.py` swept all
# three -- the same one-kind-per-gap hole that file's own history records costing
# 401 regressions, left open on the ONE axis with an elm-format oracle. It is not
# hypothetical debt: `detachOwnLineTrailer` (fuzz-idempotency 347 -> 172) is a
# multi-line-block fix, so this axis's "0 failing" across that change was
# evidence of no regression and no evidence of the fix.
#
# A `-- ¤` needs a newline after it, so the lead/trail forms re-indent the next
# token to the column it already had -- the offside structure is unchanged, which
# keeps the cell parseable. A multi-line `{- .. -}` needs the same treatment for
# the same reason: whatever follows its `-}` would otherwise land on the closing
# row at a column the author never wrote.
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
# `multi`'s text is `fuzz-idempotency.py`'s MARKER_MULTI verbatim. The two gates
# probe different things and cannot share code here (that one splices into a
# fixture, this one into a generated cell), but a marker that differs between
# them makes two findings of the same shape look unrelated.
#
# A marker's text must never contain `when` or `is`: `to_elm` replaces those two
# words wherever they appear, and its exactness rests on them occurring nowhere
# in this file's vocabulary but as the keywords. A marker body is part of that
# vocabulary.
COMMENT_KINDS = {
    "block": "{- ¤ -}",
    "line": "-- ¤",
    "multi": "{- ¤\n   second row -}",
}
COMMENT_POSITIONS = ["trail", "lead"]

# ------------------------------------------------------------- THE RUN AXIS
#
# Everything above injects ONE comment per cell. A comment RUN -- two or more in
# the same gap -- is a THIRD axis, and until 2026-08-07 it had never met an
# oracle: `fuzz-idempotency.py --run N` / `--mix` vary it over the corpus and ask
# only "is this a fixed point", and this matrix asks elm-format but injects one
# comment. That is the same shape of hole as the one-kind-per-gap sweep this file
# records costing 401 regressions -- a gate green over the wrong axis reads
# exactly like a gate that is green.
#
# COMPOSITION IS THE AXIS, NOT LENGTH. `--run 3` swept clean over the corpus
# (2026-08-06) because N copies of one kind give every member the same neighbour
# it already had; the rules a run can break are written about a neighbour's
# SHAPE. So `--comment-runs` sweeps all NINE two-member compositions -- the three
# homogeneous ones and the six ordered mixed pairs -- and nothing longer.
#
# The member texts and the join rule are `fuzz-idempotency.py`'s, asserted equal
# to what `run_kind` / `mixed_kind` produce (`_assert_run_text_matches_fuzz`)
# rather than trusted to stay in step: a run spliced differently from the gate
# that sweeps the corpus makes two findings of one shape look unrelated, and the
# labels (`blockx2`, `block+line`) are what `repro.py` already takes.
FUZZ_KINDS = {k[0]: k for k in _fuzz.KINDS}
assert {label: k[1] for label, k in FUZZ_KINDS.items()} == COMMENT_KINDS, (
    "the comment marker texts have drifted from fuzz-idempotency.py's KINDS; a "
    "finding of the same shape would look unrelated between the two gates")

# label -> the member kinds it splices, in order. The three single kinds map to
# themselves, so every consumer takes a run without knowing there is such a thing.
RUN_MEMBERS = {label: [label] for label in COMMENT_KINDS}


def run_label(members):
    """`fuzz-idempotency.py`'s labelling: `blockx2` homogeneous, `block+line`
    mixed. Same strings that gate reports, so a finding here can be handed to
    `repro.py` and to `--mix` without translation."""
    if len(set(members)) == 1:
        return f"{members[0]}x{len(members)}"
    return "+".join(members)


def run_text_at(members, col):
    """One gap's worth of splice text: the members in order, marked `¤1 … ¤n`.

    Two things are load-bearing and neither is a free choice:

      * **The joiner is keyed on the member to its LEFT** -- a `--` swallows the
        rest of its row, so whatever follows one must start a new row, while
        every other boundary joins with a space. (`fuzz-idempotency.mixed_kind`
        says the same thing; the assert below keeps them identical.)
      * **Only the joins are re-indented to `col`, never a member's own body.**
        A newline *between* members is real inter-token whitespace and its column
        is part of the shape being tested; the newline inside `{- ¤\\n   second
        row -}` is comment text, and re-indenting it would rewrite the 22,770
        single-`multi` cells this baseline already holds.

    A one-member run is returned unnumbered, so every existing cell's source --
    and therefore its baseline key and its meaning -- is byte-identical to what
    it was before this axis existed.
    """
    if len(members) == 1:
        return COMMENT_KINDS[members[0]]
    parts = []
    for i, label in enumerate(members):
        parts.append(COMMENT_KINDS[label].replace(MARKER_CH, f"{MARKER_CH}{i + 1}"))
        if i + 1 < len(members):
            parts.append("\n" + " " * col if label == "line" else " ")
    return "".join(parts)


def _assert_run_text_matches_fuzz(members):
    """`run_text_at(members, 0)` is exactly what fuzz-idempotency splices, so the
    two gates cannot drift onto different runs. Called once per composition when
    the axis is selected -- a drift is a startup failure, not a silent one."""
    if len(set(members)) == 1:
        want = _fuzz.run_kind(FUZZ_KINDS[members[0]], len(members))[1]
    else:
        want = _fuzz.mixed_kind(members)[1]
    got = run_text_at(members, 0)
    assert got == want, (f"run text for {members} drifted from fuzz-idempotency:\n"
                         f"  here:  {got!r}\n  there: {want!r}")


def register_run(members):
    """Make `run_label(members)` a usable comment kind. Returns the label."""
    _assert_run_text_matches_fuzz(members)
    label = run_label(members)
    RUN_MEMBERS[label] = list(members)
    return label


# The whole run axis: every two-member composition. The six mixed pairs are
# `--mix-pairs`; the three homogeneous ones are `--run 2`, which that flag
# deliberately excludes and which have no oracle either.
RUN_COMPOSITIONS = [[a, b] for a in COMMENT_KINDS for b in COMMENT_KINDS]

# The marker comment as a SPAN, which is the only form that survives a multi-line
# `{- ¤ .. -}`. Every helper below that deletes or locates the marker uses this;
# the per-line `\{-\s*¤\s*-\}` it replaced silently matched nothing on a
# multi-line marker and left the comment's own words in the text being compared.
# Non-greedy to the first `-}`, DOTALL so the body may cross rows.
_MARKER_SPAN_RE = re.compile(
    r"\{-\s*" + MARKER_CH + r".*?-\}|--[ \t]*" + MARKER_CH + r"[^\n]*",
    re.S,
)


def strip_marker(text, repl=""):
    """`text` with the marker's whole comment replaced by `repl`.

    **Line count is preserved** -- a multi-line marker's own newlines are kept,
    so deleting it can never merge two code lines together. Every caller then
    drops the lines that are left blank, which is the same normalization they
    applied before multi-line markers existed. Without this, `foo {- ¤` / `x -}
    bar` would canonicalize to `foo bar` on one row and compare equal to a
    genuinely different layout.
    """
    def sub(m):
        return repl + "\n" * m.group(0).count("\n")

    return _MARKER_SPAN_RE.sub(sub, text)


def marker_span(text):
    """`(start, end)` offsets of the marker's comment, or None."""
    m = _MARKER_SPAN_RE.search(text)
    return (m.start(), m.end()) if m else None


# A run's members are marked `¤1 … ¤n`; a lone comment is bare `¤` and counts as
# member 1. Every helper below is keyed on that number rather than on the order
# the markers appear in the OUTPUT, because "which member ended up where" is
# exactly the question a run asks -- keying on output order would make a
# reordered run look like an unmoved one.
_MEMBER_RE = re.compile(re.escape(MARKER_CH) + r"(\d+)")


def _member_of(comment_text):
    m = _MEMBER_RE.search(comment_text)
    return int(m.group(1)) if m else 1


def marker_spans(text):
    """`{member number: (start, end)}` for every marker comment in `text`."""
    return {_member_of(m.group(0)): (m.start(), m.end())
            for m in _MARKER_SPAN_RE.finditer(text)}

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

    A multi-line marker is deleted as a span but keeps its own newlines
    (`strip_marker`), so the two sides stay comparable line-for-line however
    each formatter chose to lay the comment's rows out -- which is the point,
    since elm-format re-spaces a multi-line comment's body and gren does not.
    """
    def canon(s):
        out = []
        for ln in strip_marker(s).split("\n"):
            ln = ln.rstrip()
            if collapse_interior and ln.strip():
                ln = " " * (len(ln) - len(ln.lstrip())) + re.sub(r" +", " ", ln.strip())
            if ln.strip():
                out.append(ln)
        return "\n".join(out)
    return canon(gren_out) == canon(elm_out)


def marker_roles(out):
    """`{member number: role}` -- where each marker sits relative to what shares
    its line: alone | leading | trailing | inner.

    Asked of the marker's SPAN rather than of "the line containing ¤", so a
    multi-line `{- ¤ .. -}` is judged by what precedes its `{-` and what follows
    its `-}` -- which is the question -- instead of by whatever happens to share
    the opening row. For a single-line marker the two are the same thing, and
    this returns exactly what the per-line version did.

    `alone` is load-bearing: the "gren stranded the comment alone and elm-format
    did not" shape is never auto-classified, because that is the shape of both
    pairing bugs. Getting it wrong for one comment kind would quietly re-open
    that door for that kind.

    **A sibling member counts as content, not as blank.** In a run, member 1 with
    member 2 beside it reads `inline`/`inner`, not `alone`. That is deliberately
    the conservative direction: a run the two formatters break apart differently
    then shows up as a role disagreement and is refused, where treating a comment
    neighbour as blank would make "both stranded together" and "both beside the
    code" look alike.
    """
    roles = {}
    for member, (start, end) in marker_spans(out).items():
        before = out[out.rfind("\n", 0, start) + 1:start].strip()
        nl = out.find("\n", end)
        after = (out[end:] if nl < 0 else out[end:nl]).strip()
        roles[member] = ("alone" if not before and not after else
                         "leading" if not before else
                         "trailing" if not after else "inner")
    return roles


REASON_INHERITED = "INHERITED"  # prefix: "INHERITED: <the syntax cell's own reason>"


def reason_is_stale(reason):
    """True for a stored reason that must be RECOMPUTED rather than preserved.

    `UNREVIEWED` is the obvious one. The trap is `INHERITED:UNREVIEWED` — a
    comment cell that inherited its base's reason back when the base was itself
    unreviewed. It is not literally `UNREVIEWED`, so the "keep any prior reason"
    rule preserved it verbatim for ever, and the UNREVIEWED counter never saw
    it: 1,963 cells were wearing that label on 2026-08-03 with **not one** of
    their 123 base cells still unreviewed (119 registered — mostly #28 — and 4
    gone from the syntax baseline entirely, i.e. matching elm-format now).

    A stale label that reads as reviewed debt is the failure mode this whole
    baseline exists to prevent, so these recompute against the base's CURRENT
    reason instead. A genuinely reviewed reason (`#22`, `BUG: …`,
    `PENDING-UPSTREAM: …`, `INHERITED:#28`) never contains the token and is
    still preserved.
    """
    return reason == REASON_UNREVIEWED or f"{REASON_INHERITED}:{REASON_UNREVIEWED}" in reason
REASON_UNRECORDED = "#22"  # comment snapped to a canonical side of an unrecorded token
REASON_ELM_REFLOWS = "#23"  # gren kept its comment-free layout; elm-format re-flowed
REASON_COMMENT_ROWS = "#25"  # comment did not move; elm re-spaced its OWN rows

# Tokenizer for the "which tokens did the comment cross" test below. `@C<n>`
# stands in for member n's comment so it takes a slot in the stream. The digits
# are part of the token on purpose: matched separately they would be read as a
# numeric literal and left in the CODE stream, where they would look like a code
# difference between the two formatters at exactly the place the comment sits.
_TOKEN_RE = re.compile(
    r"@C\d*|[A-Za-z_][A-Za-z0-9_.]*|\d+\.?\d*|'[^']*'|\"[^\"]*\"|[()\[\]{},]|[|<>+*/\\=:.-]+")

# The punctuation the Gren parser records a source position for. A comment that
# crossed one of these moved across a boundary the formatter can SEE, so the move
# is a finding, not the forced canonicalization of #22. Everything else that can
# separate two operands -- `=` `:` `|` `,` `->` and the keywords -- is discarded
# by the parser (see docs/elmFormatComparison.md #22).
_RECORDED = {"(", ")", "[", "]", "{", "}"}
_OPERATOR_RE = re.compile(r"^[|<>+*/\\=:.-]+$")
_UNRECORDED_OPS = {"=", ":", "|", "->"}
_UNRECORDED_WORDS = {"if", "then", "else", "when", "case", "of", "is", "let", "in", "as"}


def _marker_slots(out):
    """`({member: index in the paren-free code-token stream}, that stream)`.

    Both formatters emit the same code tokens, so a marker is the only thing that
    can occupy a different slot. For a run each member gets its own index, and
    two members can land in the same slot (a run that stayed together)."""
    body = "\n".join(out.split("\n")[3:])
    body = _MARKER_SPAN_RE.sub(
        lambda m: f" @C{_member_of(m.group(0))} " + "\n" * m.group(0).count("\n"), body)
    slots, code = {}, []
    for t in _TOKEN_RE.findall(body):
        if t in ("(", ")"):
            continue
        if t.startswith("@C"):
            slots[int(t[2:] or 1)] = len(code)
        else:
            code.append(t)
    return slots, code


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

    For a RUN every member must answer the same way: one that crossed a bracket
    or an operator fails the whole cell, so a run the formatters tore apart at a
    boundary gren CAN see is never swept in behind a sibling that only crossed
    an `=`.
    """
    gslots, gcode = _marker_slots(gren_out)
    eslots, ecode = _marker_slots(elm_out)
    if not gslots or gslots.keys() != eslots.keys() or gcode != ecode:
        return False
    moved = False
    for member, gi in gslots.items():
        ei = eslots[member]
        if gi == ei:
            continue
        moved = True
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
    return moved


def _canon_lines(text, drop_marker=False):
    """Right-trimmed non-blank lines with interior runs of spaces squeezed,
    optionally with the marker's comment deleted.

    Both normalizations are needed to compare a commented output against its
    uncommented one: deleting a comment from mid-line leaves the gap it
    occupied, and an own-line comment leaves a blank line. Indentation is kept
    exact -- a cell whose code lands at a different column must still differ."""
    out = []
    for ln in (strip_marker(text) if drop_marker else text).split("\n"):
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


def marker_did_not_move(gren_out, elm_out):
    """True when the marker occupies the SAME slot in both outputs -- the same
    index in the same paren-free code-token stream -- so neither formatter put
    the comment anywhere the other did not.

    This is a strictly stronger statement than "the roles match".
    `marker_roles` compares what shares each comment's line; two formatters can
    agree on that while attaching the comment between different tokens. Slot
    equality leaves nothing about *placement* to differ, which is what makes it
    safe to attribute the whole remaining difference to the comment's own rows.

    Sound in the direction that matters: `_marker_slots` finds no member when it
    cannot find the marker, and any disagreement about the code tokens
    themselves (`gcode != ecode`) fails it too, so an unexplained difference
    still books UNREVIEWED debt.

    For a RUN it means EVERY member kept its slot -- including relative to its
    siblings, since two members that swapped occupy each other's slots and the
    dicts then differ.
    """
    gslots, gcode = _marker_slots(gren_out)
    eslots, ecode = _marker_slots(elm_out)
    return bool(gslots) and gslots == eslots and gcode == ecode


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

    # Every test below is PER MEMBER and unanimous over the run: a family is
    # claimed for the cell only when it holds for each comment in it. One member
    # doing something unreviewed keeps the whole cell UNREVIEWED, which is the
    # only safe direction -- an auto-classification that reads member 1 and
    # generalizes is exactly how a baseline starts freezing bugs as expected
    # output, and a run is the case where the members can differ.
    groles, eroles = marker_roles(gren_out), marker_roles(elm_out)
    if not groles or groles.keys() != eroles.keys():
        # One side is missing a member the other has. `check_comment_cell`'s
        # marker oracle should have failed the cell before parity ran, so this is
        # unreachable in a normal sweep -- but "no members" makes every unanimous
        # test below vacuously true, and a vacuous pass is the one thing this
        # classifier must never do.
        return None
    moved = groles != eroles
    if moved and not unrecorded and any(
        role == "alone" and eroles.get(member) != "alone" for member, role in groles.items()
    ):
        # gren alone / elm beside code is the pairing-bug shape. Never swept,
        # not even when the base explains the code around it.
        return None
    if moved and not unrecorded and not elm_only and not all(
        role == eroles.get(member)
        or (role == "trailing" and eroles.get(member) in ("leading", "alone"))
        for member, role in groles.items()
    ):
        # A comment move we have no reviewed family for. Review it.
        return None

    stripped_matches = comment_stripped_matches(gren_out, elm_out, collapse_interior=unrecorded)

    parts = []
    if not stripped_matches:
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
    elif stripped_matches and marker_did_not_move(gren_out, elm_out):
        # Nothing about the PLACEMENT differs -- same slot, same code tokens, and
        # the code is byte-identical once the comment is deleted. What is left is
        # what elm-format does to the comment's OWN rows: a blank line above an
        # own-row comment in a container, and -- with a multi-line `{- .. -}` --
        # its closing `-}` moved to a row of its own. #25 states exactly that
        # ("what elm-format does to the comment's own rows"); the multi-line half
        # was unreachable until the axis swept that kind on 2026-08-05, and is
        # now the largest family in this baseline.
        #
        # This arm cannot swallow a placement bug: it is reached only when
        # `moved` is False AND the slot is identical, and either of the two
        # `return None` guards above fires first on any comment that moved.
        #
        # `stripped_matches` is required, not incidental: without it the arm
        # would append `#25` to an INHERITED cell whose real difference is the
        # base divergence, attributing to the comment's rows a difference that is
        # not in them.
        parts.append(REASON_COMMENT_ROWS)
    return "+".join(parts) if parts else None


def line_col_of(src, idx):
    """The 0-based column `idx` sits at within its line."""
    return idx - (src.rfind("\n", 0, idx) + 1)


def comment_variant(src, gap, kind, position):
    """`src` with `kind`'s comment -- or, for a run kind, its whole run --
    injected at `gap` (the first char of a whitespace run). Returns the new
    source, or None if the placement is not expressible.

    trail: `prev {- ¤ -}<original whitespace>next`
    lead:  `prev<original whitespace up to the last line>{- ¤ -}next`

    For `-- ¤` a newline must follow the comment, and the next token is
    re-indented to the column it already occupied, so the offside structure --
    and therefore the parse -- is unchanged.

    A multi-line `{- ¤ .. -}` needs the same break for a different reason: it is
    self-delimiting, so nothing is *swallowed*, but its `-}` closes on a row the
    author never wrote, and gluing the next token onto that row puts it at a
    column that decides the offside structure. Both kinds therefore share
    `broken_tail`, and both leave the next token exactly where it was.
    """
    end = gap
    while end < len(src) and src[end] in " \t\r\n":
        end += 1
    if end >= len(src):
        return None
    ws = src[gap:end]
    members = RUN_MEMBERS[kind]
    col = line_col_of(src, end)
    # The column the run itself starts at, which is where its joins are
    # re-indented to: a `trail` run begins one space past the gap, a `lead` run
    # begins where the next token did. A single-comment kind never spans a join,
    # so this changes nothing for the cells that existed before the run axis.
    text = run_text_at(members, line_col_of(src, gap) + 1 if position == "trail" else col)
    # Whether the next token has to start a fresh row after the comment. True for
    # a `--` (it eats the rest of its row) and for a multi-line block (its `-}`
    # closes on a row the source never had). A single-line `{- ¤ -}` is the one
    # kind that can leave the gap's own whitespace alone -- which is exactly the
    # property `commentTextCanRide` names, so the injector and the formatter are
    # branching on the same fact.
    #
    # For a RUN the test is "did ANY member break a row", not "did the last one":
    # a run ending in a single-line `{- ¤n -}` could in principle let the next
    # token ride its closing row, but that row is one the source never had, so
    # the token would land at a column nobody wrote and the offside structure --
    # the thing this injector exists not to disturb -- would be decided by
    # accident. Stated rather than hidden: the "token rides the run's last row"
    # shape is not swept here.
    breaks_row = "\n" in text or "line" in members

    if position == "trail":
        if breaks_row:
            # Keep the original whitespace only if it already broke the line,
            # else synthesize the break at the next token's column.
            tail = ws if "\n" in ws else "\n" + " " * col
            return src[:gap] + " " + text + tail + src[end:]
        return src[:gap] + " " + text + ws + src[end:]

    # lead: the comment goes immediately before the next token.
    if breaks_row:
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
    # Lambdas whose PARAMETER destructures. Every lambda above binds the bare
    # var `q`, and until 2026-08-09 so did every lambda anywhere in this file --
    # so the whole matrix, including the comment axis and its elm-format oracle,
    # could not reach a lambda head that ends in a bracket. That is exactly what
    # `08a8573` fixed: `prevBlockGlueRow` read the head's cached rightmost
    # bracket (the PATTERN's `]`) as "this flow ends in a bracket", so a comment
    # past the `->` glued to a row the output does not have. `\q ->` has no
    # bracket to find and was a fixed point throughout, which is why no gate here
    # ever objected -- a run of any length in the arrow gap of a `\q ->` still
    # cannot produce a bracket pattern. It took a random seed from `fuzzrun.py`
    # to find, and it belongs under an oracle instead.
    #
    # All four parse and round-trip byte-identically under `elm-format` (checked
    # against the binary, not assumed), so `to_elm` needs no extension. The
    # as-pattern is written parenthesized: the bare `Ctor args as name` spelling
    # hits a known compiler-common parser bug, and parens are its documented
    # workaround -- see `README.md`'s Known limitations.
    Construct("lambdaArrayPat", "(\\[ 1 ] -> one)",            True,  "(\\[ 1 ] ->\none)",  True),
    # The pattern alone is NOT enough, and finding that out is the reason this
    # entry exists. `08a8573`'s bug needs a body that is written ON the `->` row
    # (so it parses as a `SoftIndentedBlock`) yet RENDERS multi-line -- only then
    # does a comment in the arrow gap glue to a row the output does not have. A
    # one-row body (`one`, above) lets the comment ride and is stable either way,
    # and a body the author put on the next row parses as an `IndentedBlock`,
    # whose own arm already forces the comment own-line. So `broken` here breaks
    # INSIDE the body rather than at the lambda's `->` -- the one variant that
    # reaches the shape. Verified non-vacuous by rebuilding `08a8573~1`: this
    # cell fails there and passes here; the four pattern constructs above pass on
    # BOTH, which is what "the gate is green" would otherwise have hidden.
    Construct("lambdaArrayPatBody", "(\\[ 1 ] -> [ 0, 1 ])",   True,  "(\\[ 1 ] -> [ 0\n, 1 ])", True),
    Construct("lambdaRecordPat", "(\\{ a, b } -> a)",          True,  "(\\{ a, b } ->\na)", True),
    Construct("lambdaCtorPat",  "(\\(Just q) -> q)",           True,  "(\\(Just q) ->\nq)", True),
    Construct("lambdaAsPat",    "(\\({ a } as whole) -> a)",   True,  "(\\({ a } as whole) ->\na)", True),
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
    # `value_position` is True here for the same reason `backPipeBody`'s is: the
    # operand is the last thing in the expression, so a bare (unparenthesized)
    # lambda / `if` / `when` / `let` stands here legally.
    #
    # It was False until 2026-08-19, and that was a real hole -- no cell in this
    # matrix placed a BARE construct after `|>`, so the `|>`-with-a-bare-lambda
    # bug fixed that day (the operator stranded on a row of its own) had no cell
    # here at all, only the parenthesized twin. Two fixes had to land before it
    # could be flipped: that one, and a mixed `|>`/`<|` chain written on one row
    # coming back across three (an oracle-1 failure this flag exposed).
    #
    # The cells it adds diverge from elm-format on parity and are registered as
    # divergence #34: elm-format wraps a bare non-atomic `|>` operand in parens
    # it adds itself, and gren never introduces a paren. #10 does NOT cover them
    # -- that entry is about parens the AUTHOR wrote and gren keeps, which is the
    # same rule seen from the other side.
    #
    # `pipelineSeed` stays False and is NOT the same case: a bare block there
    # swallows the `|> fn` to its right, so the cell would test a different
    # expression from the one the template names.
    Context("pipelineOperand",  "seed |> {x}",                  True,  True),
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
# `docs/commentAlgorithm.md` §10.
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
    "FORMATTER INTERNAL ERROR",       # Box renderer returned Err
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


def has_no_elm_twin(elm_source, base_source):
    """True if the CELL, not the translation, is what Elm refuses.

    Called only when elm-format has already rejected `elm_source`, to split two
    very different things that arrive looking identical:

      - a `to_elm` bug -- we handed Elm something malformed, and the cell's
        parity is silently not being checked. A failure, loudly.
      - a language ACCEPTANCE difference -- the Gren program in this cell has no
        valid Elm counterpart at all, so there is nothing for `to_elm` to fix and
        no elm-format output that could exist to compare against.

    The whole class here is one difference: **`compiler-common` lets a
    declaration start in any column, where Elm requires column 1.** It is an
    upstream parser bug, not a language difference -- the real Gren compiler
    requires column 1 too (compiler-common#37), so these cells retire when it is
    fixed. Splicing a comment in front of a declaration's name pushes the name
    right, and gren keeps a comment on the row the author wrote it on, so the
    name stays there:

        foo : a
        {- ¤ -} foo =      gren: parses, formats, is a fixed point
            one            elm : "Unable to parse file <STDIN>:5:10"

    The same comment on its own row is accepted by both -- verified in both
    directions. Note gren does NOT accept every such cell: when the annotation's
    last token can take a type-application argument (`foo : Int`), gren swallows
    the next line's name as one and rejects the cell itself. Those never reach
    oracle 4, so the predicate below is only ever asked about the ones gren took.

    The test is differential rather than a shape match: the SAME cell without the
    injected comment must be fine for Elm. That is exactly the claim being made
    ("the comment is what Elm refuses"), and it keeps a real `to_elm` bug loud --
    if the uncommented cell is rejected too, the translation is what is broken.
    The keyword guard covers the one `to_elm` bug this file has actually had: a
    comment defeating the `when … is` pattern so `when` survived into the "Elm"
    source, which is a translation fault a comment triggers and must not be
    excused here.

    REJECTED, explicitly, so nobody re-invents it: translating by moving the
    comment onto its own row. `{- ¤ -} foo =` and `{- ¤ -}` ⏎ `foo =` are
    DIFFERENT PROGRAMS. Asking elm-format about the second and diffing it against
    gren's formatting of the first would manufacture a divergence out of nothing
    -- the same dishonesty as regenerating a fixture to whatever the tool emits.
    """
    if base_source is None:
        # The syntax axis: nothing was injected, so there is no "same cell
        # without the comment" and this class cannot arise.
        return False
    if re.search(r"\b(when|is)\b", elm_source):
        return False
    try:
        bare = subprocess.run([ELM_FORMAT, "--stdin"], input=to_elm(base_source),
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False
    return bare.returncode == 0


def check_parity(source, gren_out, base_source=None):
    """Oracle 4. Returns None if the cell agrees with elm-format, else the diff.

    `base_source` is the cell as it was BEFORE a comment was injected (the
    comment axis passes it; the syntax axis has none). It is used for one thing:
    telling a `to_elm` bug from a cell Elm's own parser refuses -- see
    `has_no_elm_twin`.
    """
    elm_source = to_elm(source)
    try:
        elm = subprocess.run([ELM_FORMAT, "--stdin"], input=elm_source,
                             capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return dict(kind="elm-format-timeout", gren="", elm="")

    if elm.returncode != 0:
        detail = (elm.stderr + elm.stdout).strip()[:400]
        if has_no_elm_twin(elm_source, base_source):
            # Not a failure and not a divergence: oracle 4 has no question to ask
            # here. Counted and printed apart from both.
            return dict(kind="no-elm-twin", gren="", elm=detail)
        # Our translation produced something elm-format will not accept. That is
        # a to_elm bug, not a formatter bug -- surface it rather than skip it.
        return dict(kind="untranslatable", gren="", elm=detail)

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


def predicate_lie_detail(f):
    """One `--audit-predicates` finding, in the direction it was actually made.

    The comment half of that audit is bidirectional -- `claim: false` is the
    OPPOSITE complaint, and the audit's own doc calls it the worse direction --
    so printing "said X breaks" for both read backwards on half of them.
    """
    claimed = "breaks" if f["claim"] else "does not break"
    return f'{f["predicate"]} said {f["boxKind"]} {claimed}, rendered: {f["rendered"]}'


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
                              detail="; ".join(predicate_lie_detail(f) for f in roots[:3]))

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
    atom fills `{x}`. Returns (comment_cells, n_atom_gap_cells, n_ctx_gap_cells,
    n_collapsed) -- the last being trail/lead pairs that came out the same bytes
    (run kinds only; see the dedupe note below).
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
    out, n_atom, n_ctx, n_collapsed = [], 0, 0, 0
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
                seen = {}
                for position in positions:
                    variant_src = comment_variant(source, gap, kind, position)
                    if variant_src is None:
                        continue
                    # `trail` and `lead` COLLAPSE at most gaps: once the comment
                    # forces a row break, "just after the previous token" and
                    # "just before the next one" are the same bytes unless the
                    # gap's own whitespace already held a newline. On the run
                    # axis that is 90% of the sweep -- 186k of 207k cells --
                    # formatted twice under two keys for one input.
                    #
                    # Deduplicated for RUN kinds only. The single-comment axis
                    # has the same redundancy and keeps it: its keys are 25k
                    # reviewed baseline entries, and dropping half of them to
                    # save time would report every one stale. Coverage is
                    # identical either way -- the same distinct sources -- and
                    # the count of dropped cells is printed, never silent.
                    if len(RUN_MEMBERS[kind]) > 1 and variant_src in seen:
                        n_collapsed += 1
                        continue
                    seen[variant_src] = position
                    out.append(dict(construct=construct.name, context=context.name,
                                    variant=variant, ordinal=ordinal, kind=kind,
                                    position=position, source=variant_src,
                                    # The cell BEFORE the injection. Oracle 4
                                    # needs it to tell a to_elm bug from a cell
                                    # Elm's own parser refuses -- see
                                    # `has_no_elm_twin`.
                                    base_source=source,
                                    atom_local=atom_local))
                    if atom_local:
                        n_atom += 1
                    else:
                        n_ctx += 1
    return out, n_atom, n_ctx, n_collapsed


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
        # A dropped or duplicated comment survives every self-consistency check
        # in the repo -- the duplicate reformats to itself. Only a count can see
        # it. For a RUN this is also the only reordering check anywhere on this
        # axis: a run torn across a separator and put back in the wrong order is
        # a stable fixed point, so `fuzz-idempotency.marker_check` (imported, not
        # copied) additionally requires `¤1 … ¤n` to come out in source order.
        bad = _fuzz.marker_check(formatted, len(RUN_MEMBERS[cell["kind"]]))
        if bad:
            return result(kind_result="comment-count", output=formatted, detail=bad)

        try:
            audited = run("--audit-predicates", path)
            findings = json.loads(audited.stdout) if audited.returncode == 0 else []
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            findings = []
        roots = [f for f in findings if not f["propagated"]]
        if roots:
            return result(kind_result="predicate-lie", output=formatted,
                          detail="; ".join(predicate_lie_detail(f) for f in roots[:3]))

        parity = check_parity(source, formatted, cell["base_source"]) if PARITY else None
        return result(kind_result="ok", output=formatted, parity=parity)


def load_baseline():
    if not BASELINE.exists():
        return {}
    return json.loads(BASELINE.read_text())["cells"]


def load_comment_baseline():
    if not ACTIVE_COMMENT_BASELINE.exists():
        return {}
    return json.loads(ACTIVE_COMMENT_BASELINE.read_text())["cells"]


def write_comment_baseline(cells):
    ACTIVE_COMMENT_BASELINE.write_text(json.dumps({
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
    # Cells oracle 4 has no question to ask about: the Gren program has no valid
    # Elm counterpart, so elm-format never sees them. Not failures, and NOT
    # silently folded into the byte-identical figure either -- they are held out
    # of `ran` below so that number keeps meaning "compared, and agreed".
    no_twin = [r for r in results
               if (r.get("parity") or {}).get("kind") == "no-elm-twin"]

    if update:
        syntax_baseline = load_baseline()
        cells = {}
        for key, r in diverging.items():
            prior = baseline.get(key)
            if prior and not reason_is_stale(prior):
                cells[key] = prior
                continue
            family = comment_family(r["parity"]["gren"], r["parity"]["elm"],
                                    syntax_baseline.get(base_parity_key(r)),
                                    (base_pairs or {}).get(base_parity_key(r)))
            cells[key] = family or REASON_UNREVIEWED
        write_comment_baseline(cells)
        print(f"wrote {len(cells)} registered divergences to {ACTIVE_COMMENT_BASELINE.name}")
        return []

    failures = []
    for r in broken:
        failures.append((f'[{r["parity"]["kind"]}] {comment_key(r)}',
                         "to_elm produced source elm-format rejects, and the SAME cell "
                         "without the injected comment is not accepted either (or a Gren "
                         "keyword survived translation) -- so this is the translator, not "
                         f'a language difference: {r["parity"]["elm"]}'))

    ran = {comment_key(r) for r in checked} - {comment_key(r) for r in no_twin}
    for key, r in sorted(diverging.items()):
        if key not in baseline:
            failures.append((f"[comment-parity-new-divergence] {key}",
                             "diverges from elm-format and is not registered in "
                             f"{ACTIVE_COMMENT_BASELINE.name}\n"
                             + "  source:\n"
                             + "\n".join(f"    |{ln}" for ln in r["source"].strip().split("\n")[3:])
                             + "\n" + side_by_side(r["parity"])))
    for key in sorted(baseline):
        if key in ran and key not in diverging:
            failures.append((f"[comment-parity-baseline-stale] {key}",
                             f'registered in {ACTIVE_COMMENT_BASELINE.name} as "{baseline[key]}" but it '
                             "now matches elm-format -- remove the entry"))

    registered = {k: v for k, v in baseline.items() if k in diverging}
    unreviewed = sorted(k for k, v in registered.items() if v == REASON_UNREVIEWED)
    # `REASON_BUG + ":"` anywhere, not just as a prefix: an inherited reason
    # reads "INHERITED: BUG: ...", and a known bug must not go quiet just
    # because the comment cell inherited it from its syntax cell.
    bugs = sorted(k for k, v in registered.items() if REASON_BUG + ":" in v)
    pending = sorted(k for k, v in registered.items() if REASON_PENDING + ":" in v)
    if no_twin:
        # Printed every run, never a bare number: a count labelled only
        # "excluded" is how a coverage hole goes quiet. The breakdown is what
        # would show a NEW class arriving under this heading -- today the whole
        # of it is one shape, a comment in front of a declaration's name.
        shapes = collections.Counter(f'{r["kind"]}.{r["position"]} in {r["context"]}'
                                     for r in no_twin)
        print(f"  {len(no_twin)} cells have NO ELM TWIN -- oracle 4 skipped, not a failure.\n"
              f"     compiler-common lets a declaration start in any column; Elm requires\n"
              f"     column 1, and so does the real Gren compiler (compiler-common#37). And\n"
              f"     gren keeps a comment on the row it was written on, so the name stays\n"
              f"     right of it. Elm's parser refuses the program, so no elm-format output\n"
              f"     exists to compare against. Oracles 1-3 still ran on all {len(no_twin)}.\n"
              f"     Rewriting them to put the comment on its own row would ask Elm about a\n"
              f"     DIFFERENT program -- see `has_no_elm_twin`. Where they are:")
        for shape, count in shapes.most_common(8):
            print(f"       {count:5}  {shape}")
        if len(shapes) > 8:
            print(f"       ... and {len(shapes) - 8} more shapes")
        print()
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
    kinds = (getattr(args, "comment_kinds", None)
             or ([args.comment_kind] if args.comment_kind else list(COMMENT_KINDS)))
    positions = [args.comment_pos] if args.comment_pos else list(COMMENT_POSITIONS)

    comment_cells, n_atom, n_ctx, n_collapsed = enumerate_comment_cells(cells, kinds, positions)
    if not comment_cells:
        sys.exit("no comment cells selected -- check the filters")
    print(f"comment axis: {len(comment_cells)} cells over {len(cells)} syntax cells "
          f"({n_atom} at atom-local gaps, {n_ctx} at context gaps)")
    print(f"  kinds: {', '.join(kinds)}   positions: {', '.join(positions)}")
    if n_collapsed:
        print(f"  {n_collapsed} trail/lead pairs collapsed (identical bytes -- the gap's own "
              f"whitespace held no newline, so both placements write the same source)")
    print()

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
          "except where the comment baseline says otherwise -- or, for the cells counted\n"
          "above, where Elm has no way to express the program at all.")
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
    ap.add_argument("--comment-runs", action="store_true",
                    help="comment axis: inject a RUN of two comments per gap instead of one, "
                         "in every one of the nine compositions. Gates against "
                         f"{COMMENT_RUN_BASELINE.name}")
    ap.add_argument("--comment-mix", action="append", metavar="A,B[,C]",
                    help="comment axis: one explicit run composition (e.g. --comment-mix "
                         "multi,block). Repeatable; a slice of --comment-runs, so it takes "
                         "the same baseline and cannot update it")
    args = ap.parse_args()

    # The run axis replaces the kind list rather than multiplying it: a run
    # SPELLS its own members, so `--comment-kind` would have nothing to filter.
    if (args.comment_runs or args.comment_mix) and args.comment_kind:
        ap.error("--comment-runs/--comment-mix spell their own members; "
                 "they cannot be combined with --comment-kind")
    if (args.comment_runs or args.comment_mix) and not args.comments:
        ap.error("--comment-runs/--comment-mix are part of the comment axis (--comments)")
    if args.comment_runs and args.comment_mix:
        ap.error("--comment-mix is a slice of --comment-runs; pass one or the other")
    global ACTIVE_COMMENT_BASELINE
    run_kinds = []
    if args.comment_runs:
        run_kinds = [register_run(m) for m in RUN_COMPOSITIONS]
    for spec in args.comment_mix or []:
        members = [s.strip() for s in spec.split(",")]
        bad = [m for m in members if m not in COMMENT_KINDS]
        if bad or len(members) < 2:
            ap.error(f"--comment-mix {spec!r}: want two or more of "
                     f"{', '.join(sorted(COMMENT_KINDS))}")
        run_kinds.append(register_run(members))
    if run_kinds:
        ACTIVE_COMMENT_BASELINE = COMMENT_RUN_BASELINE
    args.comment_kinds = run_kinds or None

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
    # `write_baseline` / `write_comment_baseline` write exactly the cells THIS
    # run produced, so a filtered update silently deletes every entry it did not
    # re-derive -- tens of thousands of reviewed reasons, replaced by the handful
    # a `--construct` run happens to cover. Nothing in the file said so until a
    # `--comment-kind multi` run was one keystroke away from doing it.
    selectors = [n for n, v in (("--construct", args.construct),
                                ("--context", args.context),
                                ("--variant", args.variant),
                                ("--comment-kind", args.comment_kind),
                                ("--comment-mix", args.comment_mix),
                                ("--comment-pos", args.comment_pos)) if v]
    if args.update_baseline and selectors:
        sys.exit(f"--update-baseline rewrites the WHOLE baseline from this run's cells, so "
                 f"a filtered run deletes every entry it did not re-derive.\n"
                 f"Drop {', '.join(selectors)}, or update a copy and merge by hand.")

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
