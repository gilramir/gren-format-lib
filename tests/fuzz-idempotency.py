#!/usr/bin/env python3
"""Idempotency fuzzer for `gren format`.

For each input Gren file, insert a block comment into every inter-token
whitespace gap (one at a time), format the result twice, and require the two
formatted outputs to be byte-identical. This systematically surfaces the
"comment shifts on reparse" class of idempotency bugs without anyone having to
hand-place comments (the way KitchenComments' `extremelyCommented` does).

Usage:
    ./fuzz-idempotency.py                       # all testfiles/Formatter/*.formatted.gren
    ./fuzz-idempotency.py path/to/File.gren ... # specific files
    ./fuzz-idempotency.py -j 4                   # run 4 `gren format`s at a time

The gaps of each file are checked concurrently (default 2 jobs; `gren format`
is a subprocess so threads scale with CPUs). Each worker thread gets its own
isolated project dir so concurrent formats never share a file.

A gap whose comment makes the file fail to PARSE is skipped (those are parser
limitations, e.g. a comment between two type variables, not idempotency bugs)
and counted separately. Exit status is non-zero if any non-idempotent gap is
found.
"""

import argparse
import concurrent.futures
import os
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
GREN_FORMAT = os.path.join(HERE, "..", "..", "gren-format", "gren-format.sh")
MARKER = "{- ¤ -}"  # ¤ — unlikely to collide with real comment text

# Each worker thread formats in its own project dir so concurrent `gren format`
# invocations never write the same Fuzz.gren. Created lazily, reused across the
# tasks that land on the thread, and cleaned up with the enclosing base tempdir.
_local = threading.local()


def worker_workdir(base):
    wd = getattr(_local, "workdir", None)
    if wd is None:
        wd = tempfile.mkdtemp(dir=base)
        os.makedirs(os.path.join(wd, "src"))
        with open(os.path.join(wd, "gren.json"), "w") as f:
            f.write('{ "type": "application" }')
        _local.workdir = wd
    return wd


def gap_indices(src):
    """Indices in `src` where a comment may be inserted: the first character of
    each maximal run of whitespace that lies between two code characters and is
    NOT inside a string, char, or comment. Returns them in source order."""
    gaps = []
    i, n = 0, len(src)
    prev_code = False  # saw a code (non-ws) char since the last gap was opened
    run_start = -1  # index of the current normal-state whitespace run, or -1

    def close_run(next_is_code):
        nonlocal run_start
        if run_start != -1 and prev_code and next_is_code:
            gaps.append(run_start)
        run_start = -1

    while i < n:
        c = src[i]
        two = src[i : i + 2]
        three = src[i : i + 3]
        # Skip over non-code spans (comments / literals); they are not gaps and
        # their interior whitespace must not be touched.
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
        # a code character
        close_run(True)
        prev_code = True
        i += 1
    return gaps


def run_show(workdir, source):
    """Write `source` to the worker's Fuzz.gren and run --show. Returns the
    subprocess result."""
    path = os.path.join(workdir, "src", "Fuzz.gren")
    with open(path, "w") as f:
        f.write(source)
    return subprocess.run(
        [GREN_FORMAT, "--show", path], capture_output=True, text=True
    )


def all_gaps_variant(src, gaps):
    """Insert MARKER into every gap simultaneously."""
    parts = []
    prev = 0
    for g in gaps:
        parts.append(src[prev:g])
        parts.append(" " + MARKER)
        prev = g
    parts.append(src[prev:])
    return "".join(parts)


def fast_check(base, src, gaps):
    """Insert MARKER into all gaps at once and run --show.

    Returns:
      "ok"         — idempotent (file is clean, no per-gap work needed)
      "parse-fail" — all-at-once variant didn't parse (fall back to per-gap)
      "fail"       — non-idempotent or other error (fall back to per-gap)
    """
    workdir = worker_workdir(base)
    r = run_show(workdir, all_gaps_variant(src, gaps))
    blob = r.stdout + r.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        return "parse-fail"
    if r.returncode != 0 or not r.stdout.strip():
        return "fail"
    return "ok"


def check_gap(base, src, g):
    """Insert the marker at gap `g` only, run --show, classify the outcome.
    Used in the slow path when fast_check did not return "ok".
    Runs on a pool worker; uses that worker's isolated project dir."""
    workdir = worker_workdir(base)
    r = run_show(workdir, src[:g] + " " + MARKER + src[g:])
    blob = r.stdout + r.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        return ("skip", g, "")
    if r.returncode != 0 or not r.stdout.strip():
        return ("bug", g, r.stderr.strip())
    return ("ok", g, "")


def report_slow_path(base, pool, path, src, gaps, verbose):
    """Per-gap fallback for a file that failed the fast check. Returns bug count."""
    name = os.path.basename(path)
    results = list(pool.map(lambda g: check_gap(base, src, g), gaps))
    bugs, skipped = [], 0
    for r in results:
        if r[0] == "skip":
            skipped += 1
        elif r[0] == "bug":
            _, g, detail = r
            bugs.append((g, detail))
    status = "OK " if not bugs else "BUG"
    print(f"{status} {name}: {len(gaps)} gaps, {skipped} skipped (parser), {len(bugs)} non-idempotent")
    for g, detail in bugs:
        line = src.count("\n", 0, g) + 1
        ctx = (src[max(0, g - 20) : g] + "⟨here⟩" + src[g : g + 20]).replace("\n", "⏎")
        print(f"      gap at line {line}: …{ctx}…")
        if verbose and detail:
            for dl in detail.splitlines()[:20]:
                print("        " + dl)
    return len(bugs)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", action="store_true", help="show the format¹/format² diff per gap")
    ap.add_argument("-j", "--jobs", type=int, default=2, help="concurrent `gren format`s (default 2)")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv[1:])

    files = args.files
    if not files:
        d = os.path.join(HERE, "testfiles", "Formatter")
        files = sorted(
            os.path.join(d, f) for f in os.listdir(d) if f.endswith(".formatted.gren")
        )

    # Precompute gaps (pure Python, no subprocesses).
    file_data = [(path, open(path).read()) for path in files]
    file_data = [(path, src, gap_indices(src)) for path, src in file_data]

    total = 0
    with tempfile.TemporaryDirectory() as base:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            # Phase 1: fast check — all files in parallel, one --show call each.
            fast_outcomes = list(pool.map(
                lambda t: fast_check(base, t[1], t[2]),
                file_data,
            ))

            # Phase 2: report results; fall back to per-gap for any failures.
            for (path, src, gaps), fast in zip(file_data, fast_outcomes):
                name = os.path.basename(path)
                if fast == "ok":
                    print(f"OK  {name}: {len(gaps)} gaps, 0 skipped (parser), 0 non-idempotent")
                else:
                    total += report_slow_path(base, pool, path, src, gaps, args.v)

    print(f"\n{'FAIL' if total else 'PASS'}: {total} non-idempotent gap(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
