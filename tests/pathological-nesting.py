#!/usr/bin/env python3
"""Pathological/boundary-input sweep: how deep can each construct nest before
something breaks?

qe.md avenue #1 ("boundary/pathological inputs") was untried until now. Every
other gate in this repo — the matrix, both fuzzers, `gen-random.py` — samples
*legal syntax at bounded depth*, because that is what real Gren programs and
random-but-plausible programs look like. None of them stress structural depth
itself. The one historical bug this axis is known to catch (an `O(2^depth)`
render hang in `Box.gren`'s `renderRowState`) was found by exactly this kind of
adversarial input, not by any of the other tools.

For each shape below (parens, list, record, lambda, if-chain, unary-minus,
binop chain, pipeline chain) this script generates `x = <shape nested N deep>`,
runs it through the formatter, and geometrically grows N until something
breaks (crash, hang/timeout, or non-idempotency), then bisects to the exact
boundary depth. At the boundary it also runs `--pre-ast` (parse only, no
formatting) to tell apart two very different findings:

  parse-stage  — the PARSER already fails at this depth (stack overflow is
                 typical: recursive-descent parsers pay one native stack frame
                 per nesting level). This is a `compiler-common` limitation,
                 not a `gren-format-lib` bug — compiler/compiler-common/
                 compiler-node are frozen (see root CLAUDE.md) — but it's the
                 realistic ceiling on how deep a file this formatter will ever
                 be asked to handle, and worth knowing.
  format-stage — parsing succeeds at this depth but `--show` (format /
                 reparse / AST-compare / format again / idempotency-compare)
                 does not. This IS an in-scope, actionable formatter bug: the
                 renderer or one of its passes is less depth-tolerant than the
                 parser it sits on top of.

Usage:
    ./pathological-nesting.py                      # sweep every shape
    ./pathological-nesting.py --shape parens        # just one shape
    ./pathological-nesting.py --shape parens --shape list
    ./pathological-nesting.py --timeout 10          # per-run timeout (default 15s)
    ./pathological-nesting.py --max-depth 50000     # ceiling before giving up
    ./pathological-nesting.py -v                    # print each probe as it runs

Every depth that isn't "ok" has its generated source saved to
`pathological-out/<shape>_<depth>.gren` for follow-up.

Rebuild the `gren-format` app first (`cd ../../gren-format && ./build.sh`) —
this shells out to it.
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "..", "gren-format", "app")
OUT_DIR = os.path.join(HERE, "pathological-out")


# ── Shapes: depth -> expression source ──────────────────────────────────────
#
# Each returns a bare expression (no trailing newline) to be spliced into
# `x =\n    <expr>\n`. Depth is nesting depth, not text length.

def shape_parens(n):
    return "(" * n + "42" + ")" * n


def shape_list(n):
    return "[" * n + "1" + "]" * n


def shape_record(n):
    s = "1"
    for _ in range(n):
        s = "{ a = " + s + " }"
    return s


def shape_lambda(n):
    return "\\a -> " * n + "a"


def shape_if_chain(n):
    s = "0"
    for _ in range(n):
        s = "if True then (" + s + ") else 0"
    return s


def shape_unary_minus(n):
    s = "5"
    for _ in range(n):
        s = "-(" + s + ")"
    return s


def shape_binop_chain(n):
    return " + ".join(["1"] * (n + 1))


def shape_pipeline_chain(n):
    return "1 " + ("|> identity " * n)


SHAPES = {
    "parens": shape_parens,
    "list": shape_list,
    "record": shape_record,
    "lambda": shape_lambda,
    "ifchain": shape_if_chain,
    "unaryminus": shape_unary_minus,
    "binopchain": shape_binop_chain,
    "pipelinechain": shape_pipeline_chain,
}


def make_source(shape_fn, n):
    return "module Fuzz exposing (x)\n\nx =\n    " + shape_fn(n) + "\n"


# ── Probing ──────────────────────────────────────────────────────────────────

def classify(stdout, stderr, code, timed_out):
    if timed_out:
        return "timeout"
    if code == 0:
        return "ok"
    blob = stdout + stderr
    if "Maximum call stack size exceeded" in blob:
        return "stack-overflow"
    if "FAILED TO PARSE" in blob:
        return "parse-reject"
    if "NOT IDEMPOTENT" in blob:
        return "non-idempotent"
    if "Please report this" in blob or "box:" in blob or "unreachable" in blob:
        return "crash"
    return "error"


def run_app(flag, path, timeout):
    try:
        r = subprocess.run(["node", APP, flag, path],
                           capture_output=True, text=True, timeout=timeout)
        return classify(r.stdout, r.stderr, r.returncode, False), r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return "timeout", "(timed out after %ss)" % timeout


def probe(shape_name, flag, shape_fn, n, timeout, verbose):
    """Run `flag` (--show or --pre-ast) at depth n. Returns (bucket, src)."""
    src = make_source(shape_fn, n)
    path = os.path.join(OUT_DIR, "_probe.gren")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(src)
    bucket, detail = run_app(flag, path, timeout)
    if verbose:
        print("  %-14s %-9s n=%-7d %s" % (shape_name, flag, n, bucket), file=sys.stderr)
    return bucket, src


def save_repro(shape_name, n, src):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s_%d.gren" % (shape_name, n))
    with open(path, "w") as f:
        f.write(src)
    return path


def find_boundary(shape_name, flag, shape_fn, start, factor, max_depth, timeout, verbose):
    """Geometrically grow depth until `flag` breaks, then bisect to the exact
    boundary (the smallest depth that is no longer "ok"). Returns
    (last_ok, boundary, bucket, src_at_boundary) — boundary/bucket/src are
    None if nothing broke by max_depth."""
    last_ok = 0
    n = start
    bucket = "ok"
    src = ""
    while True:
        bucket, src = probe(shape_name, flag, shape_fn, n, timeout, verbose)
        if bucket != "ok":
            break
        last_ok = n
        if n >= max_depth:
            return last_ok, None, None, None
        n = min(int(n * factor) + 1, max_depth)

    lo, hi, fail_bucket = last_ok, n, bucket
    while hi - lo > 1:
        mid = (lo + hi) // 2
        b, _ = probe(shape_name, flag, shape_fn, mid, timeout, verbose)
        if b == "ok":
            lo = mid
        else:
            hi = mid
            fail_bucket = b

    _, src = probe(shape_name, flag, shape_fn, hi, timeout, verbose)
    return lo, hi, fail_bucket, src


# A single probe at the exact bisected boundary is noisy: native stack-depth
# crash thresholds fluctuate a few % run to run with unrelated system state,
# so one crossover point can't tell "the formatter breaks earlier than the
# parser" from "they break at the same place and this run landed on the
# coin-flip zone". Requiring the parser's own boundary to clear the
# formatter's by MARGIN filters that noise out.
STAGE_MARGIN = 1.15


def sweep_shape(shape_name, shape_fn, start, factor, max_depth, timeout, verbose):
    """Bisect both --show and --pre-ast boundaries independently and compare
    them (with a margin) to tell a format-stage break from a parse-stage one.
    Returns a result dict."""
    show_last_ok, show_boundary, show_bucket, show_src = find_boundary(
        shape_name, "--show", shape_fn, start, factor, max_depth, timeout, verbose)

    if show_boundary is None:
        return {"shape": shape_name, "status": "no-break",
                "last_ok": show_last_ok, "boundary": None,
                "bucket": None, "stage": None, "repro": None,
                "parse_boundary": None}

    repro_path = save_repro(shape_name, show_boundary, show_src)

    # Independently find where plain parsing (no formatting) breaks, starting
    # the search from the --show boundary so it can climb well past it.
    parse_last_ok, parse_boundary, _parse_bucket, _parse_src = find_boundary(
        shape_name, "--pre-ast", shape_fn, show_boundary, factor, max_depth, timeout, verbose)

    if parse_boundary is None:
        stage = "format"  # parser handled everything up to max_depth; --show didn't
    elif parse_boundary >= show_boundary * STAGE_MARGIN:
        stage = "format"
    else:
        stage = "parse"

    return {"shape": shape_name, "status": "break",
            "last_ok": show_last_ok, "boundary": show_boundary,
            "bucket": show_bucket, "stage": stage, "repro": repro_path,
            "parse_boundary": parse_boundary}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shape", action="append", choices=sorted(SHAPES),
                    help="limit to these shapes (repeatable); default: all")
    ap.add_argument("--start", type=int, default=10,
                    help="starting depth (default 10)")
    ap.add_argument("--factor", type=float, default=2.0,
                    help="geometric growth factor per step (default 2.0)")
    ap.add_argument("--max-depth", type=int, default=20000,
                    help="give up and report clean if no break by this depth (default 20000)")
    ap.add_argument("--timeout", type=float, default=15.0,
                    help="per-run timeout in seconds (default 15)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every probe as it runs")
    args = ap.parse_args()

    if not os.path.exists(APP):
        print("app not found: %s\n(cd ../../gren-format && ./build.sh)" % APP,
              file=sys.stderr)
        return 2

    shapes = args.shape or sorted(SHAPES)
    print("sweeping %d shape(s), start=%d factor=%s max-depth=%d timeout=%ss"
          % (len(shapes), args.start, args.factor, args.max_depth, args.timeout))

    results = []
    for name in shapes:
        print("\n%s:" % name)
        r = sweep_shape(name, SHAPES[name], args.start, args.factor,
                        args.max_depth, args.timeout, args.verbose)
        results.append(r)
        if r["status"] == "no-break":
            print("  clean up to depth %d (no break found)" % r["last_ok"])
        else:
            pb = "no break by max-depth" if r["parse_boundary"] is None else "breaks@%d" % r["parse_boundary"]
            print("  --show breaks at depth %d (clean up to %d): %s, %s-stage"
                  % (r["boundary"], r["last_ok"], r["bucket"], r["stage"]))
            print("  --pre-ast (parse only): %s" % pb)
            print("  repro: %s" % r["repro"])

    print("\n%-16s %-10s %-12s %-14s %-12s %-10s"
          % ("shape", "status", "clean-upto", "bucket", "parse-upto", "stage"))
    n_format_stage = 0
    for r in results:
        if r["status"] == "no-break":
            print("%-16s %-10s %-12d %-14s %-12s %-10s"
                  % (r["shape"], "clean", r["last_ok"], "-", "-", "-"))
        else:
            parse_upto = "inf" if r["parse_boundary"] is None else str(r["parse_boundary"])
            print("%-16s %-10s %-12d %-14s %-12s %-10s"
                  % (r["shape"], "BREAKS@%d" % r["boundary"], r["last_ok"], r["bucket"], parse_upto, r["stage"]))
            if r["stage"] == "format":
                n_format_stage += 1

    print()
    if n_format_stage:
        print("FAIL: %d shape(s) break in the FORMATTER before the parser's own "
              "depth limit — in-scope bug(s), see repro(s) above." % n_format_stage)
        return 1
    print("PASS: every break (if any) is parse-stage — a compiler-common depth "
          "limit, not a gren-format-lib bug. See pathological-out/ for repros.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
