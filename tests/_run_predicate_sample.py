#!/usr/bin/env python3
"""Which way does the run axis's `predicate-lie` pile actually point?

The first run-axis sweep reported 8,527 of them, which is 96% of its failures --
far too many to read one at a time, and far too many to report as bugs without
knowing whether they are one shape. `flowCommentFindings` is bidirectional
(`claim: true` = the predicate promised a break the flow did not take;
`claim: false` = the flow took a break the predicate did not promise, which the
audit's own doc calls the worse direction), so the split by claim and by run
composition is the thing that says whether this is a formatter bug or the audit
being asked a question outside the scope it was written for.

    python3 _run_predicate_sample.py [stride] [-j N]
"""
import collections
import concurrent.futures
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def main():
    stride = int(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else 60
    jobs = int(sys.argv[sys.argv.index("-j") + 1]) if "-j" in sys.argv else 6

    spec = importlib.util.spec_from_file_location("m", HERE / "matrix-syntax.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    kinds = [m.register_run(members) for members in m.RUN_COMPOSITIONS]
    cells = m.enumerate_cells(m.CONSTRUCTS + m.TYPE_CONSTRUCTS,
                              m.CONTEXTS + m.TYPE_CONTEXTS, m.VARIANTS)
    run_cells, _, _, _ = m.enumerate_comment_cells(cells, kinds, m.COMMENT_POSITIONS)
    sample = run_cells[::stride]
    print(f"{len(sample)} run cells sampled from {len(run_cells)} (every {stride}th), -j {jobs}\n")

    def one(cell):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "M.gren"
            path.write_text(cell["source"])
            try:
                r = m.run("--audit-predicates", path)
            except subprocess.TimeoutExpired:
                return cell["kind"], []
            if r.returncode != 0:
                return cell["kind"], []
            try:
                findings = json.loads(r.stdout)
            except json.JSONDecodeError:
                return cell["kind"], []
            return cell["kind"], [f for f in findings if not f["propagated"]]

    by_claim = collections.Counter()
    by_kind = collections.Counter()
    by_predicate = collections.Counter()
    boxkinds = collections.Counter()
    cells_with = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for kind, findings in pool.map(one, sample):
            if findings:
                cells_with += 1
                by_kind[kind] += 1
            for f in findings:
                by_claim[f["claim"]] += 1
                by_predicate[f["predicate"]] += 1
                boxkinds[f["boxKind"]] += 1

    print(f"{cells_with} of {len(sample)} sampled cells carry a root finding\n")
    print("by claim (True = promised a break that did not happen; "
          "False = broke without promising):")
    for k, n in by_claim.most_common():
        print(f"  {n:6}  claim={k}")
    print("\nby predicate:")
    for k, n in by_predicate.most_common():
        print(f"  {n:6}  {k}")
    print("\nby run composition (cells, not findings):")
    for k, n in by_kind.most_common():
        print(f"  {n:6}  {k}")
    print("\nby box kind (parent / comment):")
    for k, n in boxkinds.most_common(12):
        print(f"  {n:6}  {k}")


if __name__ == "__main__":
    sys.exit(main())
