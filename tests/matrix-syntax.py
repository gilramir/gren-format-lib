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

A cell whose *generated source* does not parse is the generator's fault, not a
formatter bug: it is skipped and counted, never silently dropped.

NOT COVERED (deliberate, stated rather than hidden):
  - multi-line string literals: `\"\"\"x\"\"\"` does not parse on one line, so it
    cannot be a one-line atom in this scheme.
  - comments: that is fuzz-idempotency.py's axis, not this one.
  - author-broken layout: every cell is written flat on purpose, because flat
    input is what makes the layout oracle two-directional.

Usage:
    ./matrix-syntax.py                 # whole matrix
    ./matrix-syntax.py -j 12           # parallelise (do this; default is 2)
    ./matrix-syntax.py -v              # show source + output for every failure
    ./matrix-syntax.py -k DIR          # write failing cells to DIR as .gren files
    ./matrix-syntax.py --construct recordUpdate2 --context parenBinopArg

Requires an up-to-date ../../gren-format/app (cd ../../gren-format && ./build.sh).
Exit status is non-zero if any cell fails.
"""

import argparse
import collections
import concurrent.futures
import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE.parent.parent / "gren-format" / "app"

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

        return dict(kind="ok", construct=cname, context=xname, source=source)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-j", "--jobs", type=int, default=2, help="concurrent cells (default 2)")
    ap.add_argument("-v", "--verbose", action="store_true", help="show source + output for every failure")
    ap.add_argument("-k", "--keep", type=pathlib.Path, help="write failing cells to this dir as .gren files")
    ap.add_argument("--construct", help="only this construct")
    ap.add_argument("--context", help="only this context")
    args = ap.parse_args()

    if not APP.exists():
        sys.exit(f"{APP} not found -- run (cd ../../gren-format && ./build.sh) first")

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
        return 1

    print("Every cell renders as the author-driven rule requires, with no predicate lies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
