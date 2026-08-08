#!/usr/bin/env python3
"""Do the run axis's `commentEndsItsLine` cells actually LAY OUT wrong?

The predicate audit is a self-consistency check: it asks whether
`commentEndsItsLine` still agrees with `FlowPolicy.decide`'s own assembly.
A disagreement there is real bookkeeping debt, but it does not by itself say the
output is wrong -- in the one case traced by hand (2026-08-07) the per-comment
attribution was wrong while the layout was right, because `commentBreaksFlowRow`
folds with `any` and a `--` in the run made it True regardless.

elm-format is the oracle that can tell those apart, and this axis has it:

  * gren byte-identical to elm-format on a cell whose predicate disagrees
    => the disagreement is internal. The work is the AUDIT's scope.
  * gren diverging there, in the comment's own rows
    => a layout claim to review. The work is the FORMATTER's.

Neither answer is assumed. Cells that diverge for a reason the comment matrix
already registers (a redundant paren, an unrecorded separator) are reported
separately, because inheriting a base divergence says nothing either way.

    python3 _run_predicate_parity.py <keep-dir> [stride] [-j N]

Needs `elm-format` on PATH. Prints the split and a handful of each kind.
"""
import argparse
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
    ap = argparse.ArgumentParser()
    ap.add_argument("keepdir", type=pathlib.Path)
    ap.add_argument("stride", nargs="?", type=int, default=20)
    ap.add_argument("-j", "--jobs", type=int, default=8)
    ap.add_argument("--show", type=int, default=4, help="examples to print per bucket")
    args = ap.parse_args()

    spec = importlib.util.spec_from_file_location("m", HERE / "matrix-syntax.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for members in m.RUN_COMPOSITIONS:
        m.register_run(members)

    syntax_baseline = json.loads((HERE / "matrix-parity-baseline.json").read_text())["cells"]
    files = sorted(args.keepdir.glob("*.gren"))[:: args.stride]
    print(f"{len(files)} cells sampled (every {args.stride}th), -j {args.jobs}\n")

    def one(path):
        source = path.read_text()
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "M.gren"
            p.write_text(source)
            try:
                shown = m.run("--show", p)
            except subprocess.TimeoutExpired:
                return None
            if shown.returncode != 0:
                return None
            audited = m.run("--audit-predicates", p)
            try:
                roots = [f for f in json.loads(audited.stdout) if not f["propagated"]]
            except (json.JSONDecodeError, ValueError):
                roots = []
            if not roots:
                return None
            parity = m.check_parity(source, shown.stdout)
            return (path.stem, roots, parity)

    buckets = collections.defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for out in pool.map(one, files):
            if not out:
                continue
            stem, roots, parity = out
            head, _, _tail = stem.partition("#")
            cc, _, variant = head.partition("@")
            construct, _, context = cc.partition("__")
            variant = variant or "flat"
            kind = (parity or {}).get("kind")
            if kind == "identical":
                buckets["AGREES with elm-format (audit scope)"].append(out)
            elif kind == "divergence":
                # "Only the comment rows differ" is NOT the same as "unreviewed":
                # gren keeping a `--` on the row the author wrote it on, where
                # elm-format re-homes it, is catalogue #13 and is registered for
                # the single-comment axis already. Ask the real classifier --
                # generalized to runs on 2026-08-07 -- rather than a proxy.
                base = syntax_baseline.get(m.base_parity_key(
                    {"construct": construct, "context": context, "variant": variant}))
                family = m.comment_family(parity["gren"], parity["elm"], base, None)
                if family:
                    buckets[f"DIVERGES, auto-classified: {family}"].append(out)
                else:
                    buckets["DIVERGES, UNREVIEWED (the actual work-list)"].append(out)
            else:
                buckets[f"no verdict ({kind})"].append(out)

    total = sum(len(v) for v in buckets.values())
    print(f"{total} sampled cells carry a root finding\n")
    for name, rows in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(rows):5}  {name}")
    print()

    for name, rows in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        if not rows or not name.startswith("DIVERGES, UNREVIEWED"):
            continue
        print(f"--- examples: {name}\n")
        for stem, roots, parity in rows[: args.show]:
            print(f"  {stem}   [{', '.join(str(f['claim']) for f in roots)}]")
            print(m.side_by_side(parity) + "\n")


if __name__ == "__main__":
    sys.exit(main())
