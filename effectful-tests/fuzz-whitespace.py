#!/usr/bin/env python3
"""Whitespace-canonicalization fuzzer for `gren format`.

Premise: the formatter should produce *canonical* output that depends only on
the program's meaning, not on the incoming whitespace. So if we perturb the
inter-token whitespace of a source file without changing what it parses to, we
require:

  (1) AST INVARIANCE  — the perturbed file parses to the same AST as the
      original, disregarding source positions (row/col).
  (2) FORMAT INVARIANCE — format(perturbed) is byte-identical to
      format(original).

Whitespace gaps are the maximal runs of spaces/tabs/newlines that lie between
two code characters and are NOT inside a string, char, or comment (same notion
as fuzz-idempotency.py). Perturbations come in modes:

  stretch  — every same-line gap is widened to a varying number of spaces.
             (newline gaps are left alone)   [should be 100% AST-safe]
  indent   — per gap: push one newline gap's continuation line deeper.
  newline  — per gap: inject "\n" + deep indentation into one gap at a time
             (isolates which gaps survive a hard break).

Usage:
    ./fuzz-whitespace.py [--mode stretch|indent|newline] [-v] [FILE ...]
Defaults to mode=stretch over all testfiles/Formatter/*.dirty.gren.
A perturbation that fails to PARSE or changes the AST is reported as
"ast-changed" (the perturbation was illegal, not necessarily a formatter bug).
A perturbation that parses to the same AST but formats differently is a real
"format-drift" finding.
"""

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GREN = os.path.join(HERE, "..", "..", "gren.sh")


def gap_runs(src):
    """Return (start, end) for every whitespace run between two code chars that
    is not inside a string/char/comment. Mirrors fuzz-idempotency.gap_indices but
    keeps the run extent."""
    runs = []
    i, n = 0, len(src)
    prev_code = False
    run_start = -1

    def close_run(i, next_is_code):
        nonlocal run_start
        if run_start != -1 and prev_code and next_is_code:
            runs.append((run_start, i))
        run_start = -1

    while i < n:
        c = src[i]
        two = src[i : i + 2]
        three = src[i : i + 3]
        if two == "--":
            close_run(i, True)
            j = src.find("\n", i)
            i = n if j == -1 else j
            prev_code = True
            continue
        if two == "{-":
            close_run(i, True)
            depth = 0
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
            close_run(i, True)
            j = src.find('"""', i + 3)
            i = n if j == -1 else j + 3
            prev_code = True
            continue
        if c == '"' or c == "'":
            close_run(i, True)
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
        close_run(i, True)
        prev_code = True
        i += 1
    return runs


def col_of(src, idx):
    """0-based column of idx (chars since the last newline)."""
    nl = src.rfind("\n", 0, idx)
    return idx - (nl + 1)


# ---- perturbation builders -------------------------------------------------


def perturb_stretch(src, runs):
    """Whole-file: widen every same-line gap to a varying number of spaces.
    Newline-containing gaps are left untouched."""
    out, last = [], 0
    for k, (s, e) in enumerate(runs):
        run = src[s:e]
        out.append(src[last:s])
        if "\n" in run:
            out.append(run)  # don't touch vertical layout
        else:
            out.append(" " * (1 + (k % 3) + 1))  # 2,3,4,2,3,4,... spaces
        last = e
    out.append(src[last:])
    return ["".join(out)]


def perturb_indent(src, runs):
    """Per-gap: yield one variant per newline-containing gap, where that gap's
    continuation line is pushed a varying 4-6 spaces deeper. (Deepening every
    continuation at once is too blunt — it hits indentation-sensitive anchors
    like `in` and changes the parse — so we do one gap at a time.)"""
    variants = []
    for k, (s, e) in enumerate(runs):
        run = src[s:e]
        if "\n" not in run:
            continue
        tail_nl = run.rfind("\n")
        deepened = run[: tail_nl + 1] + " " * (4 + (k % 3)) + run[tail_nl + 1 :]
        variant = src[:s] + deepened + src[e:]
        variants.append((f"gap@{s}", variant))
    return variants


def perturb_newline_gap(src, runs):
    """Per-gap: yield one variant per same-line gap, where that gap becomes a
    hard newline indented one step deeper than the current line. Returns a list
    of (label, variant)."""
    variants = []
    for k, (s, e) in enumerate(runs):
        run = src[s:e]
        if "\n" in run:
            continue
        indent = col_of(src, s)
        variant = src[:s] + "\n" + " " * (indent + 4) + src[e:]
        variants.append((f"gap@{s}", variant))
    return variants


# ---- harness ---------------------------------------------------------------


def strip_pos(o):
    if isinstance(o, dict):
        return {k: strip_pos(v) for k, v in o.items() if k not in ("start", "end")}
    if isinstance(o, list):
        return [strip_pos(x) for x in o]
    return o


def run_format(workdir, source, flag):
    path = os.path.join(workdir, "src", "Fuzz.gren")
    with open(path, "w") as f:
        f.write(source)
    return subprocess.run(
        [GREN, "format", flag, path], capture_output=True, text=True
    )


def fmt(workdir, source):
    r = run_format(workdir, source, "--show")
    blob = r.stdout + r.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        return None
    return r.stdout if r.stdout.strip() else None


def ast(workdir, source):
    r = run_format(workdir, source, "--pre-ast")
    if "FAILED TO PARSE" in (r.stdout + r.stderr):
        return None
    try:
        return strip_pos(json.loads(r.stdout))
    except Exception:
        return None


def check_file(workdir, path, mode, verbose):
    src = open(path).read()
    runs = gap_runs(src)
    name = os.path.basename(path)

    base_ast = ast(workdir, src)
    base_fmt = fmt(workdir, src)
    if base_ast is None or base_fmt is None:
        print(f"SKIP {name}: original does not parse/format")
        return 0

    if mode == "stretch":
        variants = [("stretch", v) for v in perturb_stretch(src, runs)]
    elif mode == "indent":
        variants = perturb_indent(src, runs)
    else:
        variants = perturb_newline_gap(src, runs)

    ast_changed, drift, ok = 0, [], 0
    for label, variant in variants:
        va = ast(workdir, variant)
        if va is None or va != base_ast:
            ast_changed += 1
            continue
        vf = fmt(workdir, variant)
        if vf != base_fmt:
            drift.append((label, variant, vf))
        else:
            ok += 1

    status = "OK " if not drift else "DRIFT"
    print(
        f"{status} {name}: {len(variants)} variants, {ok} canonical, "
        f"{ast_changed} ast-changed/illegal, {len(drift)} format-drift"
    )
    for label, variant, vf in drift:
        print(f"      {label}: format(perturbed) != format(original)")
        if verbose:
            a = base_fmt.splitlines()
            b = (vf or "<parse/format error>").splitlines()
            for dl in list(
                difflib.unified_diff(a, b, "format(orig)", "format(perturbed)", lineterm="")
            )[:20]:
                print("        " + dl)
    return len(drift)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["stretch", "indent", "newline"], default="stretch")
    ap.add_argument("-v", action="store_true")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv[1:])

    files = args.files
    if not files:
        d = os.path.join(HERE, "testfiles", "Formatter")
        files = sorted(
            os.path.join(d, f) for f in os.listdir(d) if f.endswith(".dirty.gren")
        )

    total = 0
    with tempfile.TemporaryDirectory() as workdir:
        os.makedirs(os.path.join(workdir, "src"))
        with open(os.path.join(workdir, "gren.json"), "w") as f:
            f.write('{ "type": "application" }')
        for path in files:
            total += check_file(workdir, path, args.mode, args.v)
    print(f"\n{'FAIL' if total else 'PASS'}: {total} format-drift finding(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
