#!/usr/bin/env python3
"""Measure how formatting time scales with the number of injected block comments.

Inserts N evenly-spaced {- ¤ -} block comments into KitchenSink.formatted.gren
(or a specified file), runs the formatter, and records wall-clock time.
N is varied from 0 to max_gaps by STEP.

Three pipeline stages can be timed independently via --stage:

  show   (default) — full pipeline: parse → LPT → Doc → resolve × 2 + idem check
  pex              — parse → LPT → Doc construction (no resolution, no idem check)
  lpt              — parse → LPT only (no Doc, no resolution)

Running all three lets you isolate where time goes:
  • If lpt ≈ pex ≈ show: all time is in parsing/LPT construction.
  • If pex ≈ show but >> lpt: Doc construction dominates.
  • If show >> pex: PrettyExpressive resolution dominates.
  • If show grows super-linearly while pex is linear: resolution is the bottleneck.

Note on --show:  --show runs the formatter *twice* internally (idempotency check),
so its time is roughly 2× a single format pass.  If the N-comment version triggers
a multi-comment idempotency interaction (a known limitation), --show returns rc=1
with empty stdout; those data points are marked FAIL in the table.  Use --stage pex
or --stage lpt to cover the full N range without that limitation.

Usage:
    ./bench-scaling.py                            # show stage, KitchenSink.formatted.gren
    ./bench-scaling.py --stage lpt                # LPT-only timing, full range
    ./bench-scaling.py --stage pex                # Doc-construction timing, full range
    ./bench-scaling.py --stage show               # full pipeline (limited range)
    ./bench-scaling.py --all-stages               # run all three and print merged table
    ./bench-scaling.py --step 50                  # finer resolution
    ./bench-scaling.py --reps 5                   # more repetitions per N
    ./bench-scaling.py path/to/File.gren          # different input file
    ./bench-scaling.py --max 400                  # stop at N=400

Output: tab-separated rows (n_comments, median_ms, min_ms, max_ms, status, note).
"""

import argparse
import os
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GREN_FORMAT = os.path.join(HERE, "..", "..", "gren-format", "gren-format.sh")
MARKER = "{- ¤ -}"

DEFAULT_FILE = os.path.join(HERE, "testfiles", "Formatter", "KitchenSink.formatted.gren")

STAGES = ("lpt", "pex", "show")


# ── Gap finder (copy of fuzz-idempotency.py's gap_indices) ──────────────────

def gap_indices(src):
    """Indices in `src` where a block comment may be inserted: the first
    character of each maximal whitespace run between two code tokens, excluding
    string/char/comment interiors."""
    gaps = []
    i, n = 0, len(src)
    prev_code = False
    run_start = -1

    def close_run(next_is_code):
        nonlocal run_start
        if run_start != -1 and prev_code and next_is_code:
            gaps.append(run_start)
        run_start = -1

    while i < n:
        c = src[i]
        two = src[i : i + 2]
        three = src[i : i + 3]
        if two == "--":
            close_run(True)
            j = src.find("\n", i)
            i = n if j == -1 else j
            prev_code = True
            continue
        if two == "{-":
            close_run(True)
            depth, i = 0, i
            while i < n:
                if src[i : i + 2] == "{-":
                    depth += 1
                    i += 2
                elif src[i : i + 2] == "-}":
                    depth -= 1
                    i += 2
                    if depth == 0:
                        break
                else:
                    i += 1
            prev_code = True
            continue
        if three == '"""':
            close_run(True)
            j = src.find('"""', i + 3)
            i = n if j == -1 else j + 3
            prev_code = True
            continue
        if c == '"' or c == "'":
            close_run(True)
            q, i = c, i + 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                elif src[i] == q:
                    i += 1
                    break
                else:
                    i += 1
            prev_code = True
            continue
        if c in " \t\r\n":
            if run_start == -1:
                run_start = i
            i += 1
            continue
        close_run(True)
        prev_code = True
        i += 1
    return gaps


# ── Comment injector ─────────────────────────────────────────────────────────

def inject_comments(src, chosen_gaps):
    """Insert ' {- ¤ -}' at each index in chosen_gaps (must be sorted asc)."""
    parts = []
    prev = 0
    for g in chosen_gaps:
        parts.append(src[prev:g])
        parts.append(" " + MARKER)
        prev = g
    parts.append(src[prev:])
    return "".join(parts)


def pick_gaps(gaps, n):
    """Pick n gaps spread evenly across the gap list."""
    if n == 0:
        return []
    if n >= len(gaps):
        return gaps
    step = len(gaps) / n
    return [gaps[int(i * step)] for i in range(n)]


# ── Formatter runner ─────────────────────────────────────────────────────────

def time_format(workdir, source, stage):
    """Write source to workdir/src/Fuzz.gren and time a formatter run.

    stage is one of 'lpt', 'pex', or 'show'.

    Returns (elapsed_ms, status) where status is:
      "ok"       — succeeded (rc=0, stdout nonempty)
      "non-idem" — show-stage idempotency self-check failed (rc≠0, "NOT IDEMPOTENT"
                   in stderr).  Timed data is NOT usable: --show emits nothing to
                   stdout and aborts after the second format pass, so elapsed time
                   is the cost of TWO format passes plus the diff, not one.
      "fail"     — parse failure or empty output
    """
    path = os.path.join(workdir, "src", "Fuzz.gren")
    with open(path, "w") as f:
        f.write(source)

    flag = f"--{stage}={path}"
    t0 = time.perf_counter()
    r = subprocess.run(
        [GREN_FORMAT, flag],
        capture_output=True,
        text=True,
    )
    elapsed = (time.perf_counter() - t0) * 1000  # ms

    blob = r.stdout + r.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob or not r.stdout.strip():
        if "NOT IDEMPOTENT" in blob:
            return elapsed, "non-idem"
        return elapsed, "fail"
    if r.returncode == 0:
        return elapsed, "ok"
    return elapsed, "fail"


# ── Single-stage benchmark ────────────────────────────────────────────────────

def run_stage(src, gaps, stage, steps, reps, workdir):
    """Run one stage and return list of (n, med, mn, mx, status, note) rows."""
    rows = []
    for n in steps:
        chosen = pick_gaps(gaps, n)
        modified = inject_comments(src, chosen)

        times = []
        statuses = []
        for _ in range(reps):
            ms, status = time_format(workdir, modified, stage)
            times.append(ms)
            statuses.append(status)

        # For 'show': non-idem times are not reliable (includes two passes + diff).
        # For 'lpt'/'pex': non-idem cannot happen (no idempotency check).
        if stage == "show":
            usable = [(t, s) for t, s in zip(times, statuses) if s == "ok"]
            if not usable:
                rows.append((n, None, None, None, "fail", "(--show returned no output)"))
                continue
            any_non_idem = any(s == "non-idem" for s in statuses)
            note = "* some reps hit multi-comment idem interaction" if any_non_idem else ""
        else:
            usable = [(t, s) for t, s in zip(times, statuses) if s != "fail"]
            if not usable:
                rows.append((n, None, None, None, "fail", "(formatter produced no output)"))
                continue
            note = ""

        usable_times = [t for t, _ in usable]
        med = statistics.median(usable_times)
        mn = min(usable_times)
        mx = max(usable_times)
        rows.append((n, med, mn, mx, "ok", note))
    return rows


# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", default=DEFAULT_FILE,
                    help="Gren source file to benchmark (default: KitchenSink.formatted.gren)")
    ap.add_argument("--stage", choices=STAGES, default="show",
                    help="pipeline stage to time: lpt | pex | show (default: show)")
    ap.add_argument("--all-stages", action="store_true",
                    help="run all three stages and emit a merged table")
    ap.add_argument("--step", type=int, default=100,
                    help="increment in number of injected comments (default 100)")
    ap.add_argument("--max", type=int, default=None,
                    help="stop at this many comments (default: all gaps)")
    ap.add_argument("--reps", type=int, default=3,
                    help="timing repetitions per N (default 3; median is reported)")
    args = ap.parse_args(argv[1:])

    src = open(args.file).read()
    gaps = gap_indices(src)
    max_n = args.max if args.max is not None else len(gaps)
    max_n = min(max_n, len(gaps))

    stages_to_run = list(STAGES) if args.all_stages else [args.stage]

    print(f"File:   {os.path.basename(args.file)}", file=sys.stderr)
    print(f"Gaps:   {len(gaps)}", file=sys.stderr)
    print(f"Range:  0 .. {max_n} step {args.step}", file=sys.stderr)
    print(f"Reps:   {args.reps}", file=sys.stderr)
    print(f"Stages: {', '.join(stages_to_run)}", file=sys.stderr)
    print(file=sys.stderr)

    steps = list(range(0, max_n + 1, args.step))
    if not steps or steps[-1] < max_n:
        steps.append(max_n)

    with tempfile.TemporaryDirectory() as base:
        workdir = tempfile.mkdtemp(dir=base)
        os.makedirs(os.path.join(workdir, "src"))
        with open(os.path.join(workdir, "gren.json"), "w") as f:
            f.write('{ "type": "application" }')

        if args.all_stages:
            # Collect all stages, then print a merged table.
            all_rows = {}  # stage -> list of rows
            for stage in stages_to_run:
                print(f"Stage: {stage}", file=sys.stderr)
                all_rows[stage] = run_stage(src, gaps, stage, steps, args.reps, workdir)
                print(file=sys.stderr)

            # Print merged header + data
            print("n_comments\t" + "\t".join(f"{s}_ms" for s in stages_to_run) + "\tnote")
            for idx, n in enumerate(steps):
                parts = [str(n)]
                note_parts = []
                for stage in stages_to_run:
                    row = all_rows[stage][idx]
                    _, med, mn, mx, status, note = row
                    if med is None:
                        parts.append("FAIL")
                    else:
                        parts.append(f"{med:.1f}")
                        if note:
                            note_parts.append(f"[{stage}] {note}")
                parts.append("; ".join(note_parts))
                print("\t".join(parts))
                # stderr progress
                vals = []
                for stage in stages_to_run:
                    row = all_rows[stage][idx]
                    _, med, _, _, status, _ = row
                    vals.append(f"{stage}={med:.0f}ms" if med else f"{stage}=FAIL")
                print(f"  N={n:5d}  " + "  ".join(vals), file=sys.stderr)
        else:
            stage = args.stage
            print(f"n_comments\tmedian_ms\tmin_ms\tmax_ms\tstatus\tnote")
            rows = run_stage(src, gaps, stage, steps, args.reps, workdir)
            for n, med, mn, mx, status, note in rows:
                if med is None:
                    print(f"{n}\tFAIL\t-\t-\t{status}\t{note}", flush=True)
                    print(f"  N={n:5d}  FAIL", file=sys.stderr)
                else:
                    print(f"{n}\t{med:.1f}\t{mn:.1f}\t{mx:.1f}\t{status}\t{note}", flush=True)
                    print(f"  N={n:5d}  median={med:7.1f}ms  [{mn:.0f}, {mx:.0f}]",
                          file=sys.stderr, flush=True)


if __name__ == "__main__":
    main(sys.argv)
