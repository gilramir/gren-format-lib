#!/usr/bin/env python3
"""Idempotency fuzzer for `gren format`.

Two passes, both requiring format¹ == format²:

1. Per-gap pass: insert a block comment into every inter-token whitespace gap
   (one at a time). Surfaces the "comment shifts on reparse" class without
   anyone hand-placing comments (the way KitchenComments' `extremelyCommented`
   does). This pass only ever glues a comment *inline* into an existing gap.

2. End-of-declaration pass: inject an OWN-LINE trailing comment — both the block
   (`{- ¤ -}`) and line (`-- ¤`) form — indented one level below the last line
   of every top-level declaration. The per-gap pass never generates this shape,
   yet it is exactly where the "indented comment past a closing bracket, or deep
   in an inline binop, drifts left on reparse" bug class lives.

Usage:
    ./fuzz-idempotency.py                       # all testfiles/Formatter/*.formatted.gren, both passes
    ./fuzz-idempotency.py path/to/File.gren ... # specific files
    ./fuzz-idempotency.py -j 4                   # run 4 `gren format`s at a time
    ./fuzz-idempotency.py --decl-ends            # only the end-of-declaration pass
    ./fuzz-idempotency.py --gaps                 # only the per-gap pass

The gaps of each file are checked concurrently (default 2 jobs; `gren format`
is a subprocess so threads scale with CPUs). Each worker thread gets its own
isolated project dir so concurrent formats never share a file.

A gap whose comment makes the file fail to PARSE is skipped (those are parser
limitations, e.g. a comment between two type variables, not idempotency bugs)
and counted separately. Exit status is non-zero if any non-idempotent gap is
found.

Beyond the format¹==format² check, each candidate's output is also checked for
containing the expected number of markers ("¤"). A formatter bug can drop or
duplicate a comment while still being a stable fixed point (the duplicate
persists identically on reformat), which the format¹==format² check alone
cannot see — the marker-count check catches that class directly.
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
MARKER_LINE = "-- ¤"  # line-comment form, for the end-of-declaration pass

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


def mask_noncode(src):
    """Return a copy of `src` with the interior of every comment and string/char
    literal replaced by spaces (newlines preserved), so line/column structure can
    be reasoned about without tripping over code-looking text inside literals.
    Uses the same span-skipping as `gap_indices`."""
    out = list(src)
    i, n = 0, len(src)

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        two = src[i : i + 2]
        three = src[i : i + 3]
        if two == "--":
            j = src.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
            continue
        if two == "{-":
            depth, start = 0, i
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
            blank(start, i)
            continue
        if three == '"""':
            j = src.find('"""', i + 3)
            j = n if j == -1 else j + 3
            blank(i, j)
            i = j
            continue
        if src[i] == '"' or src[i] == "'":
            q, start, i = src[i], i, i + 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                elif src[i] == q:
                    i += 1
                    break
                else:
                    i += 1
            blank(start, i)
            continue
        i += 1
    return "".join(out)


def decl_end_positions(src):
    """For each top-level declaration, the (char index, indent) at which to
    inject an own-line trailing comment: right after the last code character of
    the declaration's last non-blank line, indented one level (+4) deeper than
    that line. A top-level declaration is a maximal run of lines beginning at a
    line whose first column holds code; its end is the last non-blank line before
    the next such line (or EOF). This is the shape that exposed the "indented
    comment past a closing bracket / deep in an inline binop drifts left on
    reparse" bug class, which the per-gap pass never generates (that pass only
    glues a comment inline into an existing gap)."""
    masked = mask_noncode(src)
    mlines = masked.split("\n")
    olines = src.split("\n")

    starts, off = [], 0
    for ln in olines:
        starts.append(off)
        off += len(ln) + 1

    n = len(mlines)
    tops = [i for i in range(n) if mlines[i][:1] not in ("", " ", "\t")]

    positions = []
    for k, t in enumerate(tops):
        stop = tops[k + 1] if k + 1 < len(tops) else n
        last = None
        for i in range(t, stop):
            if mlines[i].strip() != "":
                last = i
        if last is None:
            continue
        code_len = len(mlines[last].rstrip())
        insert_pos = starts[last] + code_len
        last_indent = len(olines[last]) - len(olines[last].lstrip())
        positions.append((insert_pos, last_indent + 4))
    return positions


def decl_end_variant(src, positions, comment):
    """Insert an own-line `comment`, indented, at every declaration end at once."""
    parts, prev = [], 0
    for pos, indent in positions:
        parts.append(src[prev:pos])
        parts.append("\n" + " " * indent + comment)
        prev = pos
    parts.append(src[prev:])
    return "".join(parts)


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
      "fail"       — non-idempotent, wrong marker count, or other error
                     (fall back to per-gap)
    """
    workdir = worker_workdir(base)
    r = run_show(workdir, all_gaps_variant(src, gaps))
    blob = r.stdout + r.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        return "parse-fail"
    if r.returncode != 0 or not r.stdout.strip():
        return "fail"
    if r.stdout.count("¤") != len(gaps):
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
    count = r.stdout.count("¤")
    if count != 1:
        return ("bug", g, f"expected exactly one '¤' in output, found {count} (dropped or duplicated comment)")
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


def check_one_decl_end(base, src, pos, indent, comment):
    """Inject `comment` at a single declaration end, run --show, classify.
    Returns ("ok"|"skip"|"bug", detail)."""
    workdir = worker_workdir(base)
    variant = decl_end_variant(src, [(pos, indent)], comment)
    r = run_show(workdir, variant)
    blob = r.stdout + r.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        return ("skip", "")
    if r.returncode != 0 or not r.stdout.strip():
        return ("bug", r.stderr.strip())
    if r.stdout.count("¤") != 1:
        return ("bug", f"expected exactly one '¤', found {r.stdout.count('¤')} (dropped or duplicated comment)")
    return ("ok", "")


def report_decl_ends(base, pool, path, src, verbose):
    """Inject an own-line trailing comment (block, then line form) at every
    top-level declaration end and require each to be idempotent. Returns the bug
    count. Localises per-declaration so each failure names its line."""
    name = os.path.basename(path)
    positions = decl_end_positions(src)
    if not positions:
        print(f"OK  {name}: 0 declaration ends")
        return 0

    bugs = []
    skipped = 0
    for comment, label in ((MARKER, "block"), (MARKER_LINE, "line")):
        results = list(
            pool.map(
                lambda pi: check_one_decl_end(base, src, pi[0], pi[1], comment),
                positions,
            )
        )
        for (pos, _indent), (kind, detail) in zip(positions, results):
            if kind == "skip":
                skipped += 1
            elif kind == "bug":
                bugs.append((pos, label, detail))

    status = "OK " if not bugs else "BUG"
    print(f"{status} {name}: {len(positions)} declaration ends x2 (block/line), {skipped} skipped (parser), {len(bugs)} non-idempotent")
    for pos, label, detail in bugs:
        line = src.count("\n", 0, pos) + 1
        ctx = (src[max(0, pos - 24) : pos] + "⟨+" + label + " comment⟩").replace("\n", "⏎")
        print(f"      after line {line} ({label}): …{ctx}")
        if verbose and detail:
            for dl in detail.splitlines()[:20]:
                print("        " + dl)
    return len(bugs)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", action="store_true", help="show the format¹/format² diff per gap")
    ap.add_argument("-j", "--jobs", type=int, default=2, help="concurrent `gren format`s (default 2)")
    ap.add_argument("--gaps", action="store_true", help="run only the per-gap pass (skip the end-of-declaration pass)")
    ap.add_argument("--decl-ends", action="store_true", help="run only the end-of-declaration pass (skip the per-gap pass)")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv[1:])
    run_gaps = not args.decl_ends
    run_decl_ends = not args.gaps

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
            if run_gaps:
                # Per-gap pass: fast check all files in parallel (one --show each),
                # then fall back to per-gap for any failures.
                print("== per-gap comment pass ==")
                fast_outcomes = list(pool.map(
                    lambda t: fast_check(base, t[1], t[2]),
                    file_data,
                ))
                for (path, src, gaps), fast in zip(file_data, fast_outcomes):
                    name = os.path.basename(path)
                    if fast == "ok":
                        print(f"OK  {name}: {len(gaps)} gaps, 0 skipped (parser), 0 non-idempotent")
                    else:
                        total += report_slow_path(base, pool, path, src, gaps, args.v)

            if run_decl_ends:
                # End-of-declaration pass: inject an own-line trailing comment
                # (block and line form) after every top-level declaration.
                print("\n== end-of-declaration comment pass ==")
                for path, src, _gaps in file_data:
                    total += report_decl_ends(base, pool, path, src, args.v)

    print(f"\n{'FAIL' if total else 'PASS'}: {total} non-idempotent finding(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
