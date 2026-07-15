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

ORACLES (nothing here needs a human to eyeball output):

1. Layout, both directions. Gren's layout is author-driven -- there is no page
   width and no fitter, so a construct written flat renders flat unless its own
   content forces a break. Every cell is generated on ONE line. Therefore:
       flat construct in flat context   => body MUST be exactly one line
       otherwise (if/when/let anywhere) => body MUST break
   Over-approximation (pre-breaking something that renders inline) is exactly
   the RecordUpdate bug class, and it fails the first assertion.
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
  - author-broken layout: every cell is written flat on purpose, because flat
    input is what makes oracle 1 two-directional. (Oracle 4 does not need flat
    input -- elm-format answers for any shape -- so generating broken variants
    is now possible. Not done yet.)

Usage:
    ./matrix-syntax.py                 # whole matrix
    ./matrix-syntax.py -j 12           # parallelise (do this; default is 2)
    ./matrix-syntax.py -v              # show source + output for every failure
    ./matrix-syntax.py -k DIR          # write failing cells to DIR as .gren files
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
# forever. Two things push back. A reason of REASON_UNREVIEWED is counted and
# reported loudly on every run, so the debt is never silent; and a reviewed
# entry is expected to name a catalogue number, which makes registering a
# divergence a documentation decision rather than a keystroke.
ELM_FORMAT = "elm-format"
PARITY = True  # set from --no-parity / elm-format availability in main()

REASON_UNREVIEWED = "UNREVIEWED"
REASON_PARENS = "README divergence #10 -- gren-format keeps redundant parens"


def to_elm(source):
    """Translate a generated cell to Elm. Exact for this file's vocabulary only."""
    return re.sub(r"\bwhen\s+(\S+)\s+is\b", r"case \1 of", source)


def parens_only_difference(gren_out, elm_out):
    """True if the two outputs are identical once every paren is deleted.

    Sound in the direction that matters: if deleting parens does NOT reconcile
    them, something other than paren elision differs. Newlines and indentation
    are left alone, so a cell that also breaks differently is not swept up --
    that is the whole point, e.g. `seed |> (when ...)` keeps its parens in both
    formatters and diverges only in layout, so it is correctly NOT matched here.
    """
    return re.sub(r"[()]", "", gren_out) == re.sub(r"[()]", "", elm_out)

# (name, atom, flat) -- `atom` must be usable anywhere an atom is expected, so
# anything that is not already delimited carries its own parens. `flat` is the
# documented truth, not observed behavior: True renders on one line when written
# on one line; False always breaks no matter how it was written.
CONSTRUCTS = [
    ("intLit",          "1",                                   True),
    ("floatLit",        "1.5",                                 True),
    ("charLit",         "'c'",                                 True),
    ("stringLit",       '"s"',                                 True),
    ("varRef",          "one",                                 True),
    ("fieldAccess",     "rec.fld",                             True),
    ("accessor",        ".fld",                                True),
    ("recordEmpty",     "{}",                                  True),
    ("recordLit1",      "{ a = 1 }",                           True),
    ("recordLit2",      "{ a = 1, b = 2 }",                    True),
    ("recordNested",    "{ a = { b = 1 } }",                   True),
    ("recordUpdate1",   "{ rec | a = 1 }",                     True),
    ("recordUpdate2",   "{ rec | a = 1, b = 2 }",              True),
    ("updateNested",    "{ rec | a = { b = 1 } }",             True),
    ("arrayEmpty",      "[]",                                  True),
    ("arrayNums",       "[ 1, 2, 3 ]",                         True),
    ("arrayRecords",    "[ { a = 1 }, { a = 2 } ]",            True),
    ("arrayUpdates",    "[ { rec | a = 1 }, { rec | a = 2 } ]", True),
    ("call",            "(fn one two)",                        True),
    ("qualifiedCall",   "(Array.map fn items)",                True),
    ("ctor",            "(Just one)",                          True),
    ("negate",          "(-one)",                              True),
    ("binop",           "(one + two)",                         True),
    ("binopMixedPrec",  "(one + two * three)",                 True),
    ("append",          "(items ++ rest)",                     True),
    ("pipeline",        "(items |> fn)",                       True),
    ("backPipe",        "(fn <| one)",                         True),
    ("lambda",          "(\\q -> q + one)",                    True),
    ("lambdaRecord",    "(\\q -> { q | a = 1 })",              True),
    ("lambdaLiteral",   "(\\q -> { a = q })",                  True),
    ("whenExpr",        "(when sel is Just w -> w)",           False),
    ("ifExpr",          "(if cond then one else two)",         False),
    ("letExpr",         "(let q = one in q)",                  False),
]

# (name, template, flat) -- `flat` is whether the context itself keeps its
# content on one line. if/when/let contexts always break.
CONTEXTS = [
    ("top",             "{x}",                        True),
    ("callArgFirst",    "fn {x}",                     True),
    ("callArgMid",      "fn a {x} last",              True),
    ("callArgLast",     "fn a {x}",                   True),
    ("nestedCallArg",   "fn (gn {x}) last",           True),
    ("parenBinopArg",   "fn ({x} |> gn) last",        True),
    ("parenBackPipeArg", "fn (gn <| {x}) last",       True),
    ("pipelineSeed",    "{x} |> fn",                  True),
    ("pipelineOperand", "seed |> {x}",                True),
    ("pipelineStep",    "seed |> fn {x}",             True),
    ("pipelineLast",    "seed |> fn |> gn {x}",       True),
    ("backPipeBody",    "fn <| {x}",                  True),
    ("lambdaBody",      "\\q -> {x}",                 True),
    ("recordField",     "{ fld = {x} }",              True),
    ("recordFieldMulti", "{ fld = {x}, other = 2 }",  True),
    ("updateField",     "{ rec | fld = {x} }",        True),
    ("updateFieldMulti", "{ rec | fld = {x}, other = 2 }", True),
    ("arrayItem",       "[ {x} ]",                    True),
    ("arrayItemMulti",  "[ {x}, other ]",             True),
    ("binopLhs",        "{x} ++ tail",                True),
    ("binopRhs",        "head ++ {x}",                True),
    ("letBinding",      "let bnd = {x} in bnd",       False),
    ("whenBranch",      "when sel is Just w -> {x}",  False),
    ("ifThen",          "if cond then {x} else other", False),
    ("ifElse",          "if cond then other else {x}", False),
]

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
    body = context_template.replace("{x}", construct_atom)
    return f"module M exposing (..)\n\n\nv = {body}\n"


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
    (cname, atom, cflat), (xname, template, xflat) = cell
    source = source_for(atom, template)
    expect_flat = cflat and xflat

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "M.gren"
        path.write_text(source)

        try:
            shown = run("--show", path)
        except subprocess.TimeoutExpired:
            return dict(kind="timeout", construct=cname, context=xname, source=source, detail="--show timed out")

        if shown.returncode != 0:
            out = shown.stderr + shown.stdout
            if GENERATOR_FAULT in out:
                return dict(kind="skipped", construct=cname, context=xname, source=source,
                            detail="generated source does not parse")
            for title in BUG_TITLES:
                if title in out:
                    return dict(kind=title, construct=cname, context=xname, source=source,
                                detail=out.strip()[:600])
            return dict(kind="unknown-error", construct=cname, context=xname, source=source,
                        detail=out.strip()[:600])

        formatted = shown.stdout
        body = body_lines(formatted)
        if body is None:
            return dict(kind="no-body", construct=cname, context=xname, source=source,
                        detail="could not locate `v =` in output", output=formatted)

        try:
            audited = run("--audit-predicates", path)
            findings = json.loads(audited.stdout) if audited.returncode == 0 else []
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            findings = []

        if findings:
            roots = [f for f in findings if not f["propagated"]]
            if roots:
                return dict(kind="predicate-lie", construct=cname, context=xname, source=source,
                            output=formatted,
                            detail="; ".join(f'{f["predicate"]} said {f["boxKind"]} breaks, '
                                             f'rendered: {f["rendered"]}' for f in roots[:3]))

        is_flat = len(body) == 1
        if expect_flat and not is_flat:
            return dict(kind="broke-when-flat", construct=cname, context=xname, source=source,
                        output=formatted,
                        detail="written on one line and nothing forces a break, but the body broke "
                               f"across {len(body)} lines")
        if not expect_flat and is_flat:
            return dict(kind="flat-when-should-break", construct=cname, context=xname, source=source,
                        output=formatted, detail="an if/when/let is involved, so the body must break")

        # Parity runs only on cells that satisfy oracles 1-3. A cell that
        # already violates a truth would diverge from elm-format too, and
        # reporting it twice buys nothing -- fix the truth first.
        parity = check_parity(source, formatted) if PARITY else None
        return dict(kind="ok", construct=cname, context=xname, source=source,
                    output=formatted, parity=parity)


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
    return f'{result["construct"]}/{result["context"]}'


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

    constructs = [c for c in CONSTRUCTS if not args.construct or c[0] == args.construct]
    contexts = [x for x in CONTEXTS if not args.context or x[0] == args.context]
    if not constructs or not contexts:
        sys.exit("no cells selected -- check --construct/--context names")

    cells = [(c, x) for c in constructs for x in contexts]
    print(f"{len(constructs)} constructs x {len(contexts)} contexts = {len(cells)} cells\n")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for r in pool.map(check_cell, cells):
            results.append(r)

    by_kind = collections.Counter(r["kind"] for r in results)
    failures = [r for r in results if r["kind"] not in ("ok", "skipped")]

    if args.keep and failures:
        args.keep.mkdir(parents=True, exist_ok=True)
        for r in failures:
            (args.keep / f'{r["construct"]}__{r["context"]}.gren').write_text(r["source"])
        print(f"wrote {len(failures)} failing cells to {args.keep}\n")

    if failures:
        shown = failures if args.verbose else failures[:10]
        for r in shown:
            print(f'FAIL [{r["kind"]}] {r["construct"]} in {r["context"]}')
            print(f'  {r["detail"]}')
            print(f'  source: {r["source"].strip().splitlines()[-1]}')
            if args.verbose and r.get("output"):
                print("  output:")
                for line in r["output"].rstrip().split("\n"):
                    print(f"    |{line}")
            print()
        if len(failures) > len(shown):
            print(f"... and {len(failures) - len(shown)} more failures (-v to see all)\n")

    print(f"{len(cells)} cells: {by_kind['ok']} ok, {len(failures)} failing, "
          f"{by_kind['skipped']} skipped (generated source does not parse)\n")

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
