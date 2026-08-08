#!/usr/bin/env python3
"""A full census of a predicate class over the run axis, by claim direction.

**The class it was written for is closed** (2026-08-08): the 8,527
`commentEndsItsLine` findings that were 100% of what `--comment-runs` reported
went to 0 when the audit was re-grained from the comment to the RUN, and not one
of them was a layout bug. This census is what settled that, and the shape of its
answer is the transferable part: the claim direction turned out to be a **pure
function of the run composition**, with no construct or context dependence — a
grain mismatch looks like that, and a layout bug does not. Kept as the
instrument for the next such class, not as a live work-list.

`_run_predicate_sample.py` sampled 285 cells first and said "one family, both
claim directions, always a `--` in the run" — enough to refuse to call them
bugs, not enough to work from. This reads EVERY failing cell and tallies the
whole space.

Runs against the cells `matrix-syntax.py -k` wrote out, so it needs no sweep:

    python3 _run_predicate_census.py <keep-dir> [-j N]

Prints, per axis: claim direction, run composition, the (parent / comment) box
kinds, and the construct/context. `--json OUT` writes the per-cell rows so a
follow-up can slice them without re-running.
"""
import argparse
import collections
import concurrent.futures
import json
import pathlib
import subprocess
import sys

APP = pathlib.Path(__file__).resolve().parent.parent.parent / "gren-format" / "app"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keepdir", type=pathlib.Path)
    ap.add_argument("-j", "--jobs", type=int, default=10)
    ap.add_argument("--json", type=pathlib.Path)
    args = ap.parse_args()

    files = sorted(args.keepdir.glob("*.gren"))
    print(f"{len(files)} cells in {args.keepdir}, -j {args.jobs}\n")

    def one(path):
        try:
            r = subprocess.run(["node", str(APP), "--audit-predicates", str(path)],
                               capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return None
        if r.returncode != 0:
            return None
        try:
            findings = json.loads(r.stdout)
        except json.JSONDecodeError:
            return None
        roots = [f for f in findings if not f["propagated"]]
        if not roots:
            return None
        # `construct__context@variant#gN.kind.position.gren`
        stem = path.stem
        head, _, tail = stem.partition("#")
        cc, _, variant = head.partition("@")
        construct, _, context = cc.partition("__")
        bits = tail.split(".")
        return {
            "cell": stem,
            "construct": construct,
            "context": context,
            "variant": variant,
            "gap": bits[0] if bits else "",
            "kind": bits[1] if len(bits) > 1 else "",
            "position": bits[2] if len(bits) > 2 else "",
            "findings": roots,
        }

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for out in pool.map(one, files):
            if out:
                rows.append(out)

    findings = [f for r in rows for f in r["findings"]]
    print(f"{len(rows)} cells carry a root finding, {len(findings)} findings total\n")

    def tally(title, pairs):
        print(f"--- {title}")
        for k, n in collections.Counter(pairs).most_common(14):
            print(f"  {n:6}  {k}")
        print()

    tally("claim (True = promised a break that did not happen)",
          [f["claim"] for f in findings])
    tally("predicate", [f["predicate"] for f in findings])
    tally("run composition (cells)", [r["kind"] for r in rows])
    tally("box kind: parent / comment", [f["boxKind"] for f in findings])
    tally("construct (cells)", [r["construct"] for r in rows])
    tally("context (cells)", [r["context"] for r in rows])
    tally("variant (cells)", [r["variant"] for r in rows])

    # The question that decides the whole class: within one composition, is the
    # claim direction consistent? A composition that produces BOTH directions
    # cannot be one scoping mistake.
    print("--- claim direction per composition (cells)")
    per = collections.defaultdict(collections.Counter)
    for r in rows:
        for f in r["findings"]:
            per[r["kind"]][f["claim"]] += 1
    for kind, c in sorted(per.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {kind:14} True={c[True]:6}  False={c[False]:6}")
    print()

    if args.json:
        args.json.write_text(json.dumps(rows, indent=1) + "\n")
        print(f"wrote {len(rows)} rows to {args.json}")


if __name__ == "__main__":
    sys.exit(main())
