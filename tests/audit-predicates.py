#!/usr/bin/env python3
"""Render-truth audit for the formatter's layout predicates.

Several predicates answer "does this subtree force a hard break?" *before*
anything is rendered, so callers can lay out the code around it. Each is a
hand-written mirror of what the renderer actually does, and nothing forces the
two to agree. When a predicate over-approximates -- claims a break the renderer
never emits -- callers commit to a vertical shape for content that lands on one
line.

No other check can see that. The resulting output is still deterministic,
AST-equivalent, idempotent, and stable under both fuzzers; only the layout is
wrong. This driver runs the `--audit-predicates` flag over the corpus and
aggregates what it finds.

The property checked, per LPT node:

    predicate(node) == True   ==>   node's own box renders multi-line

Under-approximation is not reported -- these predicates only claim the
unconditional breaks, and a node can still break for reasons they deliberately
do not model (most often the author's own row layout).

Usage:
    ./audit-predicates.py                       # all testfiles/Formatter/*.formatted.gren
    ./audit-predicates.py path/to/File.gren ... # specific files
    ./audit-predicates.py -j 12                 # 12 audits at a time
    ./audit-predicates.py -v                    # list every finding, not just the summary

Requires an up-to-date `../../gren-format/app` (cd ../../gren-format && ./build.sh).
Exit status is non-zero if any finding is reported.
"""

import argparse
import collections
import concurrent.futures
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE.parent.parent / "gren-format" / "app"
CORPUS = HERE / "testfiles" / "Formatter"


def audit(path):
    """Return (path, findings, error). A file that will not parse yields []."""
    try:
        proc = subprocess.run(
            ["node", str(APP), "--audit-predicates", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return path, [], "timed out"

    if proc.returncode != 0:
        # A file the formatter cannot parse or render tells us nothing about the
        # predicates; report it so a corpus-wide regression can't hide here.
        first = (proc.stderr or proc.stdout).strip().splitlines()
        return path, [], first[0] if first else f"exit {proc.returncode}"

    try:
        return path, json.loads(proc.stdout), None
    except json.JSONDecodeError as e:
        return path, [], f"bad JSON: {e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=pathlib.Path)
    ap.add_argument("-j", "--jobs", type=int, default=2, help="concurrent audits (default 2)")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every finding")
    args = ap.parse_args()

    if not APP.exists():
        sys.exit(f"{APP} not found -- run (cd ../../gren-format && ./build.sh) first")

    files = args.files or sorted(CORPUS.glob("*.formatted.gren"))
    if not files:
        sys.exit("no input files")

    findings = []   # (path, finding)
    errors = []     # (path, message)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for path, found, error in pool.map(audit, files):
            if error:
                errors.append((path, error))
            for f in found:
                findings.append((path, f))

    # Group by the thing that has to be fixed: a predicate lying about a box
    # kind. The per-site list is evidence; this is the actual work-list.
    by_kind = collections.Counter((f["predicate"], f["boxKind"]) for _, f in findings)

    roots = [(p, f) for p, f in findings if not f["propagated"]]
    by_root_kind = collections.Counter((f["predicate"], f["boxKind"]) for _, f in roots)

    if args.verbose:
        for path, f in findings:
            tag = "propagated" if f["propagated"] else "ROOT      "
            print(f'{tag} {path.name}:{f["row"]}:{f["col"]}  {f["predicate"]} said {f["boxKind"]} '
                  f'breaks, but it rendered: {f["rendered"]}')
        if findings:
            print()

    print(f"{len(files)} files audited, {len(findings)} findings "
          f"({len(roots)} root, {len(findings) - len(roots)} propagated from a node below)\n")

    if by_root_kind:
        # The work-list: a root finding is a predicate answering wrongly from its
        # own arm, not echoing a descendant. Fixing these collapses the rest.
        width = max(len(f"{p} / {k}") for p, k in by_root_kind)
        print("Root causes, by (predicate, box kind):")
        for (predicate, kind), count in by_root_kind.most_common():
            echoed = by_kind[(predicate, kind)] - count
            suffix = f"  (+{echoed} propagated)" if echoed else ""
            print(f"  {predicate + ' / ' + kind:<{width}}  {count:>4} sites{suffix}")
        print()

    propagated_only = {k: v for k, v in by_kind.items() if k not in by_root_kind}
    if propagated_only:
        print("Only ever propagated (no fix of their own -- they echo a node below):")
        for (predicate, kind), count in sorted(propagated_only.items(), key=lambda kv: -kv[1]):
            print(f"  {predicate} / {kind}: {count} sites")
        print()

    if errors:
        print(f"{len(errors)} file(s) could not be audited:")
        for path, message in errors:
            print(f"  {path.name}: {message}")
        print()

    if findings:
        print("Each finding is a predicate promising a break the renderer does not emit.")
        print("Callers laying out code around these nodes are working from a false answer.")
        return 1

    print("No over-approximations: every predicate agrees with the renderer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
