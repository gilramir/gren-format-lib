#!/usr/bin/env python3
"""Differential: the N-marker classifiers must answer EXACTLY as the one-marker
ones did on single-comment cells.

The marker helpers were rewritten from "find THE marker" to "find every member
`¤1 … ¤n`" so the run axis could be classified at all. `marker_role` feeds the
"gren stranded it alone, never auto-classify" guard, so a silent change there
re-opens the pairing-bug door -- the same reason the span-based rewrite was
proved behaviour-identical before it landed.

Samples single-comment cells, formats each with gren-format and elm-format
once, and asks both the pre-change and post-change `comment_family` for a
verdict. Any disagreement is printed and the exit status is non-zero.

    python3 _classifier_differential.py [stride] [-j N]

Not a gate -- a one-off proof, kept next to the change it justifies.
"""
import concurrent.futures
import importlib.util
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    stride = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    jobs = int(sys.argv[sys.argv.index("-j") + 1]) if "-j" in sys.argv else 4

    old_path = HERE / "_matrix_before_run_axis.py"
    if not old_path.exists():
        sys.exit(f"{old_path.name} not found -- "
                 "git show HEAD:tests/matrix-syntax.py > tests/_matrix_before_run_axis.py")
    old = load(old_path, "matrix_old")
    new = load(HERE / "matrix-syntax.py", "matrix_new")

    cells = new.enumerate_cells(new.CONSTRUCTS + new.TYPE_CONSTRUCTS,
                                new.CONTEXTS + new.TYPE_CONTEXTS, new.VARIANTS)
    comment_cells, _, _, _ = new.enumerate_comment_cells(
        cells, list(new.COMMENT_KINDS), new.COMMENT_POSITIONS)
    sample = comment_cells[::stride]
    print(f"{len(sample)} single-comment cells sampled from {len(comment_cells)} "
          f"(every {stride}th), -j {jobs}")

    syntax_baseline = json.loads((HERE / "matrix-parity-baseline.json").read_text())["cells"]

    def one(cell):
        r = new.check_comment_cell(cell)
        if r["kind_result"] != "ok" or not r.get("parity"):
            return None
        p = r["parity"]
        if p.get("kind") not in ("divergence", "identical"):
            return None
        if p.get("kind") != "divergence":
            return None
        base = syntax_baseline.get(new.base_parity_key(cell))
        return (new.comment_key(cell),
                old.comment_family(p["gren"], p["elm"], base, None),
                new.comment_family(p["gren"], p["elm"], base, None))

    if "--prove-nonvacuous" in sys.argv:
        # Zero disagreements over a sample that exercises nothing looks exactly
        # like zero over a sample that agrees. Break what the comparison watches
        # -- the per-member role, which feeds the "gren stranded it alone" guard
        # -- and the run must go RED. If it stays green the sample is vacuous and
        # the 0 above means nothing.
        real = new.marker_roles
        new.marker_roles = lambda out: {m: ("leading" if r == "trailing" else r)
                                        for m, r in real(out).items()}
        print("!! --prove-nonvacuous: marker_roles is deliberately broken; "
              "this run MUST report disagreements\n")

    checked = disagree = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for out in pool.map(one, sample):
            if out is None:
                continue
            key, a, b = out
            checked += 1
            if a != b:
                disagree += 1
                print(f"DISAGREE {key}\n  before: {a!r}\n  after:  {b!r}")

    print(f"\n{checked} diverging cells classified by both; {disagree} disagreements")
    return 1 if disagree else 0


if __name__ == "__main__":
    sys.exit(main())
