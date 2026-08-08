#!/usr/bin/env python3
"""What the run axis's elm-format debt would look like, measured before a
baseline is written for it.

`--update-baseline` refuses a filtered run (it rewrites the whole file), so
there is no way to ask "how much of this classifies, and into what" without
sweeping all ~114k run cells first. This samples them instead: format each cell
with both formatters, run `comment_family`, and print the distribution -- overall
and **per run composition**, which is the cut that decides whether one kind is
the whole debt (the multi-line kind was, on the single-comment axis).

    python3 _run_parity_sample.py [1-in-N] [-j N] [--no-base-pairs] [--seed S]

**The sample is a seeded RANDOM one, not every Nth cell.** `run_cells` is ordered
with the comment kind innermost, so it has a period of 18 `(kind, position)`
slots and any stride sharing a factor with 18 lands on the same few kinds for
ever: `[::60]` drew 0 of 113,796 cells from `multix2`, `block+multi` and
`line+multi` while reporting a per-composition table as if it had covered them.
An aliased sample reads exactly like a representative one.

**It computes the uncommented cells' output pairs by default**, for the base keys
the sample touches, because `comment_family` needs them to attribute #23
(`only_elm_reflowed`) at all. Without them every #23 cell reads as UNREVIEWED and
the headline number is inflated -- which is what this instrument printed before
2026-08-08. `--no-base-pairs` skips that (faster, and the difference between the
two runs is exactly the #23 share).

Not a gate -- an instrument for deciding whether the run axis can carry a
per-cell baseline at all.
"""
import collections
import concurrent.futures
import importlib.util
import json
import pathlib
import random
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent


def main():
    stride = int(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else 200
    jobs = int(sys.argv[sys.argv.index("-j") + 1]) if "-j" in sys.argv else 8
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 0
    want_base = "--no-base-pairs" not in sys.argv

    spec = importlib.util.spec_from_file_location("m", HERE / "matrix-syntax.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # `PARITY` is only set from the flags inside `main()`, so an importer must
    # check the tool itself -- without elm-format every cell would read as
    # identical, which is the one wrong answer this instrument must not print.
    if shutil.which(m.ELM_FORMAT) is None:
        sys.exit(f"{m.ELM_FORMAT} not on PATH: every cell would read as identical")

    kinds = [m.register_run(members) for members in m.RUN_COMPOSITIONS]
    cells = m.enumerate_cells(m.CONSTRUCTS + m.TYPE_CONSTRUCTS,
                              m.CONTEXTS + m.TYPE_CONTEXTS, m.VARIANTS)
    run_cells, _, _, _ = m.enumerate_comment_cells(cells, kinds, m.COMMENT_POSITIONS)
    sample = random.Random(seed).sample(run_cells, max(1, len(run_cells) // stride))
    print(f"{len(sample)} run cells sampled from {len(run_cells)} "
          f"(1 in {stride}, seed {seed}), -j {jobs}")

    syntax_baseline = json.loads((HERE / "matrix-parity-baseline.json").read_text())["cells"]

    # The syntax cell each sampled comment cell was built from, by base key, so
    # `base_output_pair` can be asked for just the ones this sample needs.
    by_base_key = {}
    for cell in cells:
        construct, context, variant = cell
        suffix = "" if variant == "flat" else "@" + variant
        by_base_key[f"{construct.name}/{context.name}{suffix}"] = cell

    base_pairs = {}
    if want_base:
        needed = sorted({m.base_parity_key(c) for c in sample} & by_base_key.keys())
        print(f"computing {len(needed)} uncommented base pairs first (for #23 attribution)")
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            for key, pair in pool.map(lambda k: m.base_output_pair(by_base_key[k]), needed):
                if pair:
                    base_pairs[key] = pair
        print(f"  {len(base_pairs)}/{len(needed)} usable\n")
    else:
        print("  (--no-base-pairs: #23 cells will read as UNREVIEWED)\n")

    def one(cell):
        r = m.check_comment_cell(cell)
        p = r.get("parity")
        if r["kind_result"] != "ok":
            return (cell["kind"], f"!{r['kind_result']}", None)
        # `check_parity` returns None when the cell AGREES with elm-format --
        # there is no `kind="identical"`. Reading one is how this instrument
        # reported "0 byte-identical" for a run axis that is ~27% identical, and
        # `_run_predicate_parity.py` filed the same cells as "no verdict".
        if p is None:
            return (cell["kind"], "identical", None)
        if p.get("kind") != "divergence":
            return (cell["kind"], p["kind"], None)
        base_key = m.base_parity_key(cell)
        family = m.comment_family(p["gren"], p["elm"], syntax_baseline.get(base_key),
                                  base_pairs.get(base_key))
        return (cell["kind"], family or "UNREVIEWED",
                (m.comment_key(cell), p) if not family else None)

    tally, unreviewed = collections.Counter(), []
    per_kind = collections.defaultdict(collections.Counter)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for kind, label, sample_cell in pool.map(one, sample):
            tally[label] += 1
            per_kind[kind][bucket(label)] += 1
            if sample_cell:
                unreviewed.append(sample_cell)

    print("By outcome:")
    for label, n in tally.most_common():
        print(f"  {n:6}  {label}")

    print("\nBy run composition:")
    cols = ["identical", "classified", "UNREVIEWED", "not checked"]
    print("  " + "composition".ljust(14) + "".join(c.rjust(13) for c in cols) + "     n")
    for kind in sorted(per_kind, key=lambda k: -sum(per_kind[k].values())):
        row = per_kind[kind]
        n = sum(row.values())
        cells_out = "".join(f"{row[c]:6} {100*row[c]//max(n,1):3}%".rjust(13) for c in cols)
        print("  " + kind.ljust(14) + cells_out + f"  {n:6}")

    total = sum(tally.values())
    print(f"\n{total} cells; {tally['identical']} byte-identical to elm-format, "
          f"{len(unreviewed)} would book UNREVIEWED debt "
          f"({100*len(unreviewed)//max(total,1)}%)")
    print(f"extrapolated over {len(run_cells)} run cells: "
          f"~{len(unreviewed)*len(run_cells)//max(total,1):,} UNREVIEWED, "
          f"~{(total-tally['identical']-len(unreviewed))*len(run_cells)//max(total,1):,} "
          f"auto-classified, ~{tally['identical']*len(run_cells)//max(total,1):,} identical")

    for key, p in unreviewed[:6]:
        print(f"\n--- UNREVIEWED {key}")
        print(m.side_by_side(p))


def bucket(label):
    if label == "identical":
        return "identical"
    if label == "UNREVIEWED":
        return "UNREVIEWED"
    if label.startswith("!") or label in ("untranslatable", "elm-format-timeout", "?"):
        return "not checked"
    return "classified"


if __name__ == "__main__":
    sys.exit(main())
