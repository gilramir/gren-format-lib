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

NOT COVERED (deliberate, stated rather than hidden):
  - multi-line string literals: `\"\"\"x\"\"\"` does not parse on one line, so it
    cannot be a one-line atom in this scheme.
  - comments: that is fuzz-idempotency.py's axis, not this one.
  - bare expressions in atom positions (call args, binop operands): a naked
    operator expression there reassociates into a different parse, so bare
    variants run only in value-position contexts; the paren-carrying flat/broken
    variants cover the atom positions.

Usage:
    ./matrix-syntax.py                 # whole matrix (all variants)
    ./matrix-syntax.py -j 12           # parallelise (do this; default is 2)
    ./matrix-syntax.py -v              # show source + output for every failure
    ./matrix-syntax.py -k DIR          # write failing cells to DIR as .gren files
    ./matrix-syntax.py --variant broken --variant bareBroken   # author-broken only
    ./matrix-syntax.py --construct recordUpdate2 --context parenBinopArg
    ./matrix-syntax.py --no-parity     # skip oracle 4 (elm-format not installed)
    ./matrix-syntax.py --update-baseline   # rewrite the parity baseline

Requires an up-to-date ../../gren-format/app (cd ../../gren-format && ./build.sh).
Oracle 4 additionally requires `elm-format` on PATH.
Exit status is non-zero if any cell fails.
"""

import argparse
import collections
import concurrent.futures
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
ELM_FORMAT = "elm-format"
PARITY = True  # set from --no-parity / elm-format availability in main()

REASON_UNREVIEWED = "UNREVIEWED"
REASON_BUG = "BUG"  # prefix: "BUG: <what is wrong>"
REASON_PARENS = "README divergence #10 -- gren-format keeps redundant parens"


def to_elm(source):
    """Translate a generated cell to Elm. Exact for this file's vocabulary only."""
    return re.sub(r"\bwhen\s+(\S+)\s+is\b", r"case \1 of", source)


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
Context = collections.namedtuple("Context", "name template flat value_position")

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
    # collapses it back to one line (the #22 rule) exactly as it does a
    # single-field record -- this is the array witness that #22 is one
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
    cells = []
    for c in constructs:
        for x in contexts:
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


def source_for(construct_atom, context_template):
    body = substitute(context_template, construct_atom)
    return f"module M exposing (..)\n\n\nv = {body}\n"


def substitute(template, atom):
    """Put `atom` where `{x}` is. A multi-line atom keeps its continuation lines
    aligned under the column `{x}` lands in (4 for the `v = ` prefix + the
    offset of `{x}` in the template), so every continuation is indented past the
    top-level `v` and the source parses. The atom's own relative indentation is
    preserved on top of that base -- the formatter re-flows it regardless; all
    that matters here is that it is valid and spans rows."""
    idx = template.index("{x}")
    before, after = template[:idx], template[idx + 3:]
    if "\n" not in atom:
        return before + atom + after
    col = 4 + idx  # len("v = ") == 4
    lines = atom.split("\n")
    glued = lines[0] + "".join("\n" + " " * col + ln for ln in lines[1:])
    return before + glued + after


def body_lines(formatted):
    """The rendered decl body: everything after the `v =` line."""
    lines = formatted.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("v ="):
            body = lines[i + 1:]
            while body and not body[-1].strip():
                body.pop()
            return body
    return None


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


def check_cell(cell):
    construct, context, variant = cell
    cname, xname = construct.name, context.name
    atom = variant_atom(construct, variant)
    source = source_for(atom, context.template)
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
        body = body_lines(formatted)
        if body is None:
            return result(kind="no-body", detail="could not locate `v =` in output", output=formatted)

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
        if flat_input:
            is_flat = len(body) == 1
            if expect_flat and not is_flat:
                return result(kind="broke-when-flat", output=formatted,
                              detail="written on one line and nothing forces a break, but the body "
                                     f"broke across {len(body)} lines")
            if not expect_flat and is_flat:
                return result(kind="flat-when-should-break", output=formatted,
                              detail="an if/when/let is involved, so the body must break")

        # Parity runs only on cells that satisfy oracles 1-3. A cell that
        # already violates a truth would diverge from elm-format too, and
        # reporting it twice buys nothing -- fix the truth first.
        parity = check_parity(source, formatted) if PARITY else None
        return result(kind="ok", output=formatted, parity=parity)


def load_baseline():
    if not BASELINE.exists():
        return {}
    return json.loads(BASELINE.read_text())["cells"]


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
                    help="rewrite matrix-parity-baseline.json from this run")
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

    constructs = [c for c in CONSTRUCTS if not args.construct or c.name == args.construct]
    contexts = [x for x in CONTEXTS if not args.context or x.name == args.context]
    variants = args.variant or VARIANTS
    if not constructs or not contexts:
        sys.exit("no cells selected -- check --construct/--context names")

    cells = enumerate_cells(constructs, contexts, variants)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
