#!/usr/bin/env python3
"""The run axis's UNREVIEWED parity cells, grouped by the DISAGREEMENT, for review.

`_run_parity_sample.py` says how much debt a run-axis parity baseline would book
and how it splits by composition. It does not say what the debt *is*, and the
per-composition split is what makes that worth asking separately: agreement runs
from 89% (`line+multi`) down to 35% (`multix2`) across compositions built from
the same two comment kinds.

This samples a chosen set of compositions, keeps the cells `comment_family`
cannot label, and buckets them on the **disagreement** rather than on the cell --
reusing `triage-comment-parity.py`'s own `shape`/`disagreement`, so a group here
is the same unit `--interview` would ask about. Names and literals are flattened
and the surrounding context dropped, so the same question asked in a call
argument, a record field and a pipeline step is ONE group with a count.

    python3 _run_parity_review.py                        # the four families under review
    python3 _run_parity_review.py --kind multix2 --per-kind 150
    python3 _run_parity_review.py --groups 6 --seed 3

Not a gate, and it writes nothing: a verdict belongs in
`comment-review.jsonl` via `triage-comment-parity.py --interview`, which needs a
baseline to walk. This is the instrument for deciding whether to write one.
"""
import argparse
import collections
import concurrent.futures
import importlib.util
import json
import pathlib
import random
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent

# The compositions the 2026-08-08 sample singled out: the multi-bearing ones
# (0% byte-identical, 57-63% UNREVIEWED), the `line+multi` control that
# classifies at 89% despite holding the same two kinds as `multi+line`, and
# `block+line`, which is 50% UNREVIEWED with no multi-line comment in it at all.
DEFAULT_KINDS = ["multi+line", "line+multi", "multix2", "block+line"]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", action="append", dest="kinds")
    ap.add_argument("--per-kind", type=int, default=80)
    ap.add_argument("--groups", type=int, default=4, help="groups shown per composition")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("-j", "--jobs", type=int, default=12)
    args = ap.parse_args()
    kinds_wanted = args.kinds or DEFAULT_KINDS

    m = load("matrix-syntax")
    t = load("triage-comment-parity")
    if shutil.which(m.ELM_FORMAT) is None:
        sys.exit(f"{m.ELM_FORMAT} not on PATH: every cell would read as identical")

    for members in m.RUN_COMPOSITIONS:
        m.register_run(members)
    cells = m.enumerate_cells(m.CONSTRUCTS + m.TYPE_CONSTRUCTS,
                              m.CONTEXTS + m.TYPE_CONTEXTS, m.VARIANTS)
    run_cells, _, _, _ = m.enumerate_comment_cells(
        cells, [k for k in m.RUN_MEMBERS if len(m.RUN_MEMBERS[k]) > 1], m.COMMENT_POSITIONS)

    rng = random.Random(args.seed)
    by_kind = collections.defaultdict(list)
    for c in run_cells:
        by_kind[c["kind"]].append(c)
    unknown = [k for k in kinds_wanted if k not in by_kind]
    if unknown:
        sys.exit(f"no such composition(s): {unknown}; have {sorted(by_kind)}")

    sample = []
    for k in kinds_wanted:
        pool = by_kind[k]
        sample += rng.sample(pool, min(args.per_kind, len(pool)))
    print(f"{len(sample)} cells: {args.per_kind}/composition over {kinds_wanted}, seed {args.seed}\n")

    syntax_baseline = json.loads((HERE / "matrix-parity-baseline.json").read_text())["cells"]
    by_base_key = {}
    for cell in cells:
        construct, context, variant = cell
        suffix = "" if variant == "flat" else "@" + variant
        by_base_key[f"{construct.name}/{context.name}{suffix}"] = cell

    needed = sorted({m.base_parity_key(c) for c in sample} & by_base_key.keys())
    base_pairs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for key, pair in pool.map(lambda k: m.base_output_pair(by_base_key[k]), needed):
            if pair:
                base_pairs[key] = pair

    def one(cell):
        r = m.check_comment_cell(cell)
        p = r.get("parity")
        if r["kind_result"] != "ok" or p is None or p.get("kind") != "divergence":
            label = ("identical" if p is None and r["kind_result"] == "ok"
                     else f'!{r["kind_result"]}' if r["kind_result"] != "ok" else p["kind"])
            return cell["kind"], label, None
        base_key = m.base_parity_key(cell)
        family = m.comment_family(p["gren"], p["elm"], syntax_baseline.get(base_key),
                                  base_pairs.get(base_key))
        row = dict(key=m.comment_key(cell), source=cell["source"],
                   gren=p["gren"], elm=p["elm"])
        return cell["kind"], family or "UNREVIEWED", row

    labels = collections.defaultdict(collections.Counter)
    unreviewed = collections.defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for kind, label, row in pool.map(one, sample):
            labels[kind][label] += 1
            if label == "UNREVIEWED":
                unreviewed[kind].append(row)

    for kind in kinds_wanted:
        n = sum(labels[kind].values())
        print("=" * 78)
        print(f"{kind}   {len(unreviewed[kind])}/{n} UNREVIEWED")
        print("=" * 78)
        print("  labels: " + ", ".join(f"{lbl} {c}" for lbl, c in labels[kind].most_common(8)))
        groups = collections.defaultdict(list)
        for row in unreviewed[kind]:
            groups[t.disagreement(row)].append(row)
        ranked = sorted(groups.values(), key=lambda rs: -len(rs))
        print(f"  {len(ranked)} distinct disagreements among them; "
              f"top {min(args.groups, len(ranked))}:\n")
        for i, rs in enumerate(ranked[: args.groups], 1):
            share = 100 * len(rs) // max(len(unreviewed[kind]), 1)
            print(f"  --- group {i}: {len(rs)} cells ({share}% of this composition's debt)")
            print(f"      e.g. {rs[0]['key']}")
            t.show_example(rs[0], indent="      ")
        print()


if __name__ == "__main__":
    sys.exit(main())
