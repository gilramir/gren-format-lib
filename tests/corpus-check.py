#!/usr/bin/env python3
"""Real-corpus sweep: run `gren-format --show` over every .gren file in a tree of
real published Gren packages and classify each failure.

This is the standing, repeatable form of the root `CLAUDE.md` "elm-format
comparison" / `qe.md` avenue #1 exercise. Unlike the synthetic gates
(`matrix-syntax.py`, `fuzz-idempotency.py`, `fuzz-whitespace.py`,
`audit-predicates.py`), which each vary ONE axis over a fixed base, real source
varies many axes at once — and every one of the 2026-07-18 scan bugs (A–E) was a
*conjunction* of features (multi-line string × trailing whitespace × nesting;
author-broken record × arrow position; pipe × record arg × else-if; binop ×
comment × bracket operand; call × 3+ multi-line block args) that no single-axis
generator produced. This sweep is the gate that reaches those conjunctions,
because someone already wrote the code.

`--show` internally does parse → format → reparse → AST-compare → format again →
idempotency-compare, so one clean exit per file buys: no crash, AST preserved
(meaning unchanged), idempotency, and "the output parses". Each failure is
bucketed by which of those broke, so the report reads as a work-list.

Usage:
    ./corpus-check.py                      # sweep the default corpus root
    ./corpus-check.py -j 12                # parallelise (default 4)
    ./corpus-check.py /path/to/pkgs        # sweep a different root
    ./corpus-check.py -v                   # print the first error line per failure

The parser class F gap (compiler-common#31, `Ctor arg as name`) is out of scope
for gren-format and is reported separately, not counted as a formatter failure.

Rebuild the `gren-format` app first (`cd ../../gren-format && ./build.sh`) — this
shells out to it.
"""

import argparse
import concurrent.futures
import os
import subprocess
import sys

DEFAULT_ROOT = os.path.expanduser("~/prj/gren-format-preview/pkgs")
APP = os.path.join(os.path.dirname(__file__), "..", "..", "gren-format", "app")


def classify(stdout, stderr, code):
    """Bucket a --show result. Returns (bucket, first_error_line) or ("ok", "")."""
    out = stdout + stderr
    if code == 0:
        return ("ok", "")
    if "FAILED TO PARSE" in out:
        # Not a formatter bug — the parser rejected the source. compiler-common#31
        # (unparenthesized `Ctor arg as name`) is the known class F instance.
        return ("parse", first_real_line(out))
    if "NOT IDEMPOTENT" in out:
        return ("non-idempotent", first_real_line(out))
    if "COULD NOT READ FILE" in out:
        return ("unreadable", first_real_line(out))
    if "Please report this" in out or "box:" in out or "unreachable" in out:
        return ("crash", crash_message(out))
    # AST-comparison failures print a `function '...' ...: 'a' vs 'b'` diff with no
    # banner; anything else non-zero that reached here is an AST mismatch.
    return ("ast-mismatch", first_real_line(out))


def first_real_line(out):
    for line in out.splitlines():
        s = line.strip()
        if s and not s.startswith("--"):
            return s
    return out.splitlines()[0] if out.splitlines() else ""


def crash_message(out):
    # The bug message is the first non-empty line after the "-- ... bug." banner.
    lines = [l for l in out.splitlines() if l.strip()]
    for i, l in enumerate(lines):
        if "Please report" in l and i + 1 < len(lines):
            return lines[i + 1].strip()
    return first_real_line(out)


def check_one(path):
    try:
        r = subprocess.run(
            ["node", APP, "--show", path],
            capture_output=True, text=True, timeout=120,
        )
        bucket, msg = classify(r.stdout, r.stderr, r.returncode)
    except subprocess.TimeoutExpired:
        bucket, msg = ("timeout", "formatting timed out (>120s)")
    return (path, bucket, msg)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=DEFAULT_ROOT,
                    help=f"corpus root to sweep (default {DEFAULT_ROOT})")
    ap.add_argument("-j", "--jobs", type=int, default=4, help="parallel workers")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print the first error line for each failure")
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        print(f"corpus root not found: {args.root}", file=sys.stderr)
        return 2

    files = []
    for dirpath, dirnames, filenames in os.walk(args.root):
        # Skip build caches.
        dirnames[:] = [d for d in dirnames if d != ".gren"]
        for fn in filenames:
            if fn.endswith(".gren"):
                files.append(os.path.join(dirpath, fn))
    files.sort()

    if not files:
        print(f"no .gren files under {args.root}", file=sys.stderr)
        return 2

    print(f"sweeping {len(files)} files under {args.root} (-j {args.jobs}) ...")

    buckets = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for path, bucket, msg in ex.map(check_one, files):
            buckets.setdefault(bucket, []).append((path, msg))

    ok = len(buckets.get("ok", []))
    # In-scope formatter failures: everything except a clean pass and the
    # out-of-scope parser gap.
    in_scope_buckets = [b for b in buckets if b not in ("ok", "parse")]
    n_in_scope = sum(len(buckets[b]) for b in in_scope_buckets)
    n_parse = len(buckets.get("parse", []))

    print(f"\n{ok}/{len(files)} formatted cleanly "
          f"(AST-preserving, idempotent, reparses)")

    order = ["crash", "ast-mismatch", "non-idempotent", "timeout", "unreadable"]
    for bucket in order:
        rows = buckets.get(bucket, [])
        if not rows:
            continue
        print(f"\n{bucket.upper()} — {len(rows)}:")
        for path, msg in rows:
            rel = os.path.relpath(path, args.root)
            print(f"  {rel}")
            if args.verbose and msg:
                print(f"      {msg}")

    if n_parse:
        print(f"\nPARSE (out of scope — parser gap, not a formatter bug) — {n_parse}:")
        for path, msg in buckets["parse"]:
            print(f"  {os.path.relpath(path, args.root)}")

    if n_in_scope:
        print(f"\nFAIL: {n_in_scope} in-scope formatter failure(s)")
        return 1
    print(f"\nPASS: 0 in-scope formatter failures"
          + (f" ({n_parse} out-of-scope parse gap(s) noted)" if n_parse else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
