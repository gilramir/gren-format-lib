#!/usr/bin/env python3
"""What the run axis's elm-format debt would look like, measured before a
baseline is written for it.

`--update-baseline` refuses a filtered run (it rewrites the whole file), so
there is no way to ask "how much of this classifies, and into what" without
sweeping all ~200k run cells first. This samples them instead: format each cell
with both formatters, run `comment_family`, and print the distribution.

    python3 _run_parity_sample.py [stride] [-j N]

Not a gate -- an instrument for deciding whether the run axis can carry a
per-cell baseline at all.
"""
import collections
import concurrent.futures
import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def main():
    stride = int(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else 200
    jobs = int(sys.argv[sys.argv.index("-j") + 1]) if "-j" in sys.argv else 8

    spec = importlib.util.spec_from_file_location("m", HERE / "matrix-syntax.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    kinds = [m.register_run(members) for members in m.RUN_COMPOSITIONS]
    cells = m.enumerate_cells(m.CONSTRUCTS + m.TYPE_CONSTRUCTS,
                              m.CONTEXTS + m.TYPE_CONTEXTS, m.VARIANTS)
    run_cells, _, _, _ = m.enumerate_comment_cells(cells, kinds, m.COMMENT_POSITIONS)
    sample = run_cells[::stride]
    print(f"{len(sample)} run cells sampled from {len(run_cells)} (every {stride}th), -j {jobs}\n")

    syntax_baseline = json.loads((HERE / "matrix-parity-baseline.json").read_text())["cells"]

    def one(cell):
        r = m.check_comment_cell(cell)
        p = r.get("parity") or {}
        if r["kind_result"] != "ok":
            return (f"!{r['kind_result']}", None)
        if p.get("kind") != "divergence":
            return (p.get("kind", "?"), None)
        base = syntax_baseline.get(m.base_parity_key(cell))
        family = m.comment_family(p["gren"], p["elm"], base, None)
        return (family or "UNREVIEWED", (m.comment_key(cell), p) if not family else None)

    tally, unreviewed = collections.Counter(), []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for label, sample_cell in pool.map(one, sample):
            tally[label] += 1
            if sample_cell:
                unreviewed.append(sample_cell)

    for label, n in tally.most_common():
        print(f"  {n:6}  {label}")
    print(f"\n{sum(tally.values())} cells; "
          f"{tally['identical']} byte-identical to elm-format, "
          f"{len(unreviewed)} would book UNREVIEWED debt")

    for key, p in unreviewed[:6]:
        print(f"\n--- UNREVIEWED {key}")
        print(m.side_by_side(p))


if __name__ == "__main__":
    sys.exit(main())
