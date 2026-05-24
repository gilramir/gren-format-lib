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

A gap whose comment makes the file fail to PARSE is skipped (those are parser
limitations, e.g. a comment between two type variables, not idempotency bugs)
and counted separately. Exit status is non-zero if any non-idempotent gap is
found.
"""

import difflib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GREN = os.path.join(HERE, "..", "..", "gren.sh")
MARKER = "{- ¤ -}"  # ¤ — unlikely to collide with real comment text


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


def fmt(workdir, source):
    """Format `source`; return the output string, or None on parse/format error."""
    path = os.path.join(workdir, "src", "Fuzz.gren")
    with open(path, "w") as f:
        f.write(source)
    r = subprocess.run(
        [GREN, "format", "--show", path], capture_output=True, text=True
    )
    out = r.stdout
    if "FAILED TO PARSE" in (r.stdout + r.stderr) or "Could not format" in (
        r.stdout + r.stderr
    ):
        return None
    if out.strip() == "":
        return None
    return out


def check_file(workdir, path, verbose=False):
    src = open(path).read()
    gaps = gap_indices(src)
    name = os.path.basename(path)
    bugs, skipped = [], 0
    for g in gaps:
        variant = src[:g] + " " + MARKER + src[g:]
        once = fmt(workdir, variant)
        if once is None:
            skipped += 1
            continue
        twice = fmt(workdir, once)
        if twice is None:
            bugs.append((g, "re-format failed", once, ""))
        elif once != twice:
            bugs.append((g, "not idempotent", once, twice))
    status = "OK " if not bugs else "BUG"
    print(f"{status} {name}: {len(gaps)} gaps, {skipped} skipped (parser), {len(bugs)} non-idempotent")
    for g, why, once, twice in bugs:
        # show the source line where the comment was inserted
        line = src.count("\n", 0, g) + 1
        print(f"      gap at line {line} ({why})")
        if verbose:
            diff = difflib.unified_diff(
                once.splitlines(), twice.splitlines(), "format¹", "format²", lineterm=""
            )
            for dl in list(diff)[:14]:
                print("        " + dl)
    return len(bugs)


def main(argv):
    files = [a for a in argv[1:] if a != "-v"]
    if not files:
        d = os.path.join(HERE, "testfiles", "Formatter")
        files = sorted(
            os.path.join(d, f) for f in os.listdir(d) if f.endswith(".formatted.gren")
        )
    total = 0
    with tempfile.TemporaryDirectory() as workdir:
        os.makedirs(os.path.join(workdir, "src"))
        with open(os.path.join(workdir, "gren.json"), "w") as f:
            f.write('{ "type": "application" }')
        for path in files:
            total += check_file(workdir, path, verbose=("-v" in argv))
    print(f"\n{'FAIL' if total else 'PASS'}: {total} non-idempotent gap(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
