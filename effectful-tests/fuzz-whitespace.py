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
import concurrent.futures
import difflib
import json
import os
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
GREN = os.path.join(HERE, "..", "..", "gren.sh")

# Each worker thread formats in its own project dir so concurrent `gren format`
# invocations never write the same Fuzz.gren. Created lazily, reused across the
# tasks that land on the thread, cleaned up with the enclosing base tempdir.
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


def comment_fingerprint(src):
    """For each comment, in source order: (text, shares_line_with_prev_token).

    A trailing comment that *shares a source line* with the preceding code token
    is placed inline (`token {- c -}`); one on its own line is placed standalone
    (and may be re-homed to the enclosing or top level). That is a deliberate,
    meaning-bearing distinction the formatter preserves (see the formatter
    README, "Comment placement"). So a whitespace perturbation that flips this
    for any comment has changed comment *placement*, not merely layout — it is
    not a valid format-invariance test, and is discarded like an AST change.

    Comment interiors are never perturbed (gap_runs skips them), so the text is
    stable; only the `shares_line` flag can move, and only when a newline is
    added/removed between a comment and the token before it."""
    fps = []
    i, n = 0, len(src)
    last_code = -1  # index just past the last non-ws, non-comment code char

    def shares_line(comment_start):
        return last_code != -1 and "\n" not in src[last_code:comment_start]

    while i < n:
        two = src[i : i + 2]
        three = src[i : i + 3]
        if two == "--":
            j = src.find("\n", i)
            end = n if j == -1 else j
            fps.append((src[i:end].rstrip(), shares_line(i)))
            i = end
            continue
        if two == "{-":
            start, depth = i, 0
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
            fps.append((src[start:i], shares_line(start)))
            continue
        if three == '"""':
            j = src.find('"""', i + 3)
            i = n if j == -1 else j + 3
            last_code = i
            continue
        if src[i] == '"' or src[i] == "'":
            q, i = src[i], i + 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                elif src[i] == q:
                    i += 1
                    break
                else:
                    i += 1
            last_code = i
            continue
        if src[i] in " \t\r\n":
            i += 1
            continue
        i += 1
        last_code = i
    return fps


def container_layout_fingerprint(src):
    """For each `[ ... ]` array literal and `{ ... }` record / record-update, in
    source order: whether the author laid its items across multiple rows
    (signal B — some item starts on a row below where the previous item ended).

    The formatter now treats this as a meaning-bearing layout choice: a
    container written on one line stays inline (if it fits), while one the
    author spread across rows is kept one-item-per-line regardless of fit (see
    `Src.ArrayLiteral` / `Src.Record` / `Src.Update` in InsertExpressions). So a
    whitespace perturbation that flips this for any container has changed layout
    *intent*, not merely whitespace — like a comment-placement flip, it is
    discarded rather than counted as drift.

    Only the top-level commas of each container delimit its items; nested
    brackets, parens, strings, chars and comments are skipped (their interiors
    belong to one item and are never perturbed anyway). A container with fewer
    than two items has no inter-item gap and is always False. `{ }` is also used
    for record types/patterns, which do not follow the author-layout rule, but
    they are stable under whitespace anyway, so including them only over-counts
    harmlessly. Comment chars do not extend an item's row span — a
    comment-bearing container is forced vertical by a separate rule and its
    comment moves are already guarded by comment_fingerprint."""
    fps = []
    i, n = 0, len(src)
    row = 1
    stack = []  # frames; '['/'{' frames are dicts tracking items, '(' is a marker

    def note(r):
        # Record a code char at row r into the innermost item-tracking frame.
        if stack and isinstance(stack[-1], dict):
            fr = stack[-1]
            if fr["cur_first"] is None:
                fr["cur_first"] = r
            fr["cur_last"] = r

    def close_item(fr):
        if fr["cur_first"] is not None:
            fr["items"].append((fr["cur_first"], fr["cur_last"]))
            fr["cur_first"] = fr["cur_last"] = None

    while i < n:
        c = src[i]
        two = src[i : i + 2]
        three = src[i : i + 3]
        if c == "\n":
            row += 1
            i += 1
            continue
        if two == "--":
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if two == "{-":
            depth = 0
            while i < n:
                if src[i] == "\n":
                    row += 1
                    i += 1
                elif src[i : i + 2] == "{-":
                    depth += 1
                    i += 2
                elif src[i : i + 2] == "-}":
                    depth -= 1
                    i += 2
                    if depth == 0:
                        break
                else:
                    i += 1
            continue
        if three == '"""' or c == '"' or c == "'":
            # A string/char literal is item content: note its first and last row.
            note(row)
            if three == '"""':
                i += 3
                while i < n and src[i : i + 3] != '"""':
                    if src[i] == "\n":
                        row += 1
                    i += 1
                i += 3
            else:
                q, i = c, i + 1
                while i < n:
                    if src[i] == "\\":
                        i += 2
                    elif src[i] == q:
                        i += 1
                        break
                    elif src[i] == "\n":
                        row += 1
                        i += 1
                    else:
                        i += 1
            note(row)
            continue
        if c in " \t\r":
            i += 1
            continue
        # A code character.
        if c == "[" or c == "{":
            note(row)  # the bracket belongs to the enclosing item, if any
            stack.append({"items": [], "cur_first": None, "cur_last": None})
            i += 1
            continue
        if c == "]" or c == "}":
            if stack and isinstance(stack[-1], dict):
                fr = stack.pop()
                close_item(fr)
                spans = any(
                    fr["items"][k][1] < fr["items"][k + 1][0]
                    for k in range(len(fr["items"]) - 1)
                )
                fps.append(spans)
            note(row)
            i += 1
            continue
        if c == "(":
            note(row)
            stack.append(c)
            i += 1
            continue
        if c == ")":
            if stack and not isinstance(stack[-1], dict):
                stack.pop()
            note(row)
            i += 1
            continue
        if c == "," and stack and isinstance(stack[-1], dict):
            close_item(stack[-1])
            i += 1
            continue
        note(row)
        i += 1
    return fps


def _union_variants_span_rows(block):
    """Within one `type … = …` declaration block, whether a newline appears
    after the union's `=` at bracket depth 0 — i.e. the variants are laid out
    across rows. Trailing blank lines are ignored; brackets/strings/comments are
    skipped (payload records/lists and their interiors don't count)."""
    block = block.rstrip()
    i, n = 0, len(block)
    depth = 0
    seen_eq = False
    while i < n:
        c = block[i]
        two = block[i : i + 2]
        three = block[i : i + 3]
        if two == "--":
            j = block.find("\n", i)
            i = n if j == -1 else j
            continue
        if two == "{-":
            d = 0
            while i < n:
                if block[i : i + 2] == "{-":
                    d += 1
                    i += 2
                elif block[i : i + 2] == "-}":
                    d -= 1
                    i += 2
                    if d == 0:
                        break
                else:
                    i += 1
            continue
        if three == '"""':
            j = block.find('"""', i + 3)
            i = n if j == -1 else j + 3
            continue
        if c == '"' or c == "'":
            q, i = c, i + 1
            while i < n:
                if block[i] == "\\":
                    i += 2
                elif block[i] == q:
                    i += 1
                    break
                else:
                    i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == "=" and not seen_eq:
            seen_eq = True
        elif depth == 0 and c == "\n" and seen_eq:
            return True
        i += 1
    return False


def union_layout_fingerprint(src):
    """For each `type` (not `type alias`) declaration, whether its `|`-separated
    variants span rows (signal B for unions). The union variant list now follows
    the author's layout — inline `= A | B | C` when written on one line, one
    variant per line otherwise — so a whitespace perturbation that flips this is
    intended layout, not drift (like the bracket containers above). Unions are
    not bracket-delimited, so they need their own fingerprint."""
    fps = []
    n = len(src)
    # Top-level declaration starts: a non-whitespace char at column 0.
    starts = []
    at_line_start = True
    for i in range(n):
        if at_line_start and src[i] not in " \t\r\n":
            starts.append(i)
        at_line_start = src[i] == "\n"
    starts.append(n)
    for k in range(len(starts) - 1):
        block = src[starts[k] : starts[k + 1]]
        head = block.lstrip()
        if not (head == "type" or head.startswith("type ") or head.startswith("type\n")):
            continue
        after = head[4:].lstrip()
        if after == "alias" or after.startswith("alias ") or after.startswith("alias\n"):
            continue
        fps.append(_union_variants_span_rows(block))
    return fps


def _signature_segments_span_rows(block):
    """Within one top-level signature block (`name : TYPE` or `port name :
    TYPE`), whether the `->`-delimited segments of the type span rows — i.e. two
    consecutive top-level segments start on different rows (signal B for
    signatures). Mirrors `forceVertical` in `makeSignaturePrettyDoc`: only the
    *segment* rows matter, so a newline right after the `:` with the segments
    themselves still on one line (`foo :\\n    A -> B`) is NOT spanning.

    Walks at bracket depth 0; brackets/strings/comments are skipped (a
    parenthesised `(a -> b)` is one segment, its inner `->` doesn't split).
    Rows are relative to the block, which is enough to compare."""
    i, n = 0, len(block)
    row = 1
    depth = 0
    seen_colon = False
    seg_first_rows = []
    cur_first = None

    def close_seg():
        nonlocal cur_first
        seg_first_rows.append(cur_first)
        cur_first = None

    while i < n:
        c = block[i]
        two = block[i : i + 2]
        three = block[i : i + 3]
        if c == "\n":
            row += 1
            i += 1
            continue
        if two == "--":
            j = block.find("\n", i)
            i = n if j == -1 else j
            continue
        if two == "{-":
            d = 0
            while i < n:
                if block[i] == "\n":
                    row += 1
                    i += 1
                elif block[i : i + 2] == "{-":
                    d += 1
                    i += 2
                elif block[i : i + 2] == "-}":
                    d -= 1
                    i += 2
                    if d == 0:
                        break
                else:
                    i += 1
            continue
        if three == '"""':
            i += 3
            while i < n and block[i : i + 3] != '"""':
                if block[i] == "\n":
                    row += 1
                i += 1
            i += 3
            continue
        if c == '"' or c == "'":
            q, i = c, i + 1
            while i < n:
                if block[i] == "\\":
                    i += 2
                elif block[i] == q:
                    i += 1
                    break
                elif block[i] == "\n":
                    row += 1
                    i += 1
                else:
                    i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if not seen_colon:
            # Skip the header (`name` / `port name`) up to its depth-0 `:`.
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif depth == 0 and c == ":":
                seen_colon = True
            i += 1
            continue
        # Past the colon: walk the type, splitting at depth-0 `->`.
        if depth == 0 and two == "->":
            close_seg()
            i += 2
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if cur_first is None:
            cur_first = row
        i += 1
    if cur_first is not None:
        close_seg()
    return any(
        seg_first_rows[k] != seg_first_rows[k + 1]
        for k in range(len(seg_first_rows) - 1)
    )


def signature_layout_fingerprint(src):
    """For each top-level function/port signature, whether its `->`-segments span
    rows. Signatures now follow the author's layout — inline `name : A -> B` when
    written on one line and it fits, one-segment-per-line otherwise (each `->`
    leading its line) — so a whitespace perturbation that flips this is intended
    layout, not drift. Signatures are not bracket-delimited, so they need their
    own fingerprint (cf. unions).

    A signature block is a top-level (column-0) declaration that has a `:` at
    bracket depth 0 but no `=` at depth 0 — distinguishing it from the function
    definition, `type alias`, and `type` blocks that all carry a depth-0 `=`."""
    fps = []
    n = len(src)
    starts = []
    at_line_start = True
    for i in range(n):
        if at_line_start and src[i] not in " \t\r\n":
            starts.append(i)
        at_line_start = src[i] == "\n"
    starts.append(n)
    for k in range(len(starts) - 1):
        block = src[starts[k] : starts[k + 1]]
        if _has_depth0_colon_no_eq(block):
            fps.append(_signature_segments_span_rows(block))
    return fps


def _has_depth0_colon_no_eq(block):
    """True if `block` has a `:` at bracket depth 0 occurring before any depth-0
    `=` — the shape of a signature (`name : …` / `port name : …`), not a
    definition / alias / union (which carry a depth-0 `=`)."""
    i, n = 0, len(block)
    depth = 0
    while i < n:
        c = block[i]
        two = block[i : i + 2]
        three = block[i : i + 3]
        if two == "--":
            j = block.find("\n", i)
            i = n if j == -1 else j
            continue
        if two == "{-":
            d = 0
            while i < n:
                if block[i : i + 2] == "{-":
                    d += 1
                    i += 2
                elif block[i : i + 2] == "-}":
                    d -= 1
                    i += 2
                    if d == 0:
                        break
                else:
                    i += 1
            continue
        if three == '"""':
            j = block.find('"""', i + 3)
            i = n if j == -1 else j + 3
            continue
        if c == '"' or c == "'":
            q, i = c, i + 1
            while i < n:
                if block[i] == "\\":
                    i += 2
                elif block[i] == q:
                    i += 1
                    break
                else:
                    i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == ":":
            return True
        elif depth == 0 and c == "=":
            return False
        i += 1
    return False


def pipeline_layout_fingerprint(src):
    """For each `|>` / `<|` pipeline operator, whether its step spans rows — i.e.
    a newline appears in the gap between the previous item and the step's body
    (the operator sitting in that gap). Pipelines now follow the author's layout:
    inline `seed |> a |> b` when written on one line and it fits, one step per
    line otherwise. So a whitespace perturbation that flips a pipeline's
    one-line/multi-line layout is intended layout, not drift. Pipelines are not
    bracket-delimited, so they need their own fingerprint (cf. unions, signatures).

    Operators inside comments/strings are skipped. Comments do not count as code,
    matching the formatter's signal B (which uses AST token rows). A
    comment-bearing pipeline is always rendered vertical regardless, so any
    over-count here only over-discards harmlessly (cf. the container note)."""
    fps = []
    pending = []  # prev-code-row for each `|>`/`<|` awaiting its next code char
    last_code_row = None
    i, n, row = 0, len(src), 1

    def resolve(next_row):
        for prev in pending:
            fps.append(prev is not None and next_row > prev)
        pending.clear()

    while i < n:
        c = src[i]
        two = src[i : i + 2]
        three = src[i : i + 3]
        if c == "\n":
            row += 1
            i += 1
            continue
        if two == "--":
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if two == "{-":
            d = 0
            while i < n:
                if src[i] == "\n":
                    row += 1
                    i += 1
                elif src[i : i + 2] == "{-":
                    d += 1
                    i += 2
                elif src[i : i + 2] == "-}":
                    d -= 1
                    i += 2
                    if d == 0:
                        break
                else:
                    i += 1
            continue
        if three == '"""' or c == '"' or c == "'":
            # A string/char literal is code: it resolves a pending op (its first
            # row) and becomes the new last code token (its last row).
            resolve(row)
            start_row = row
            if three == '"""':
                i += 3
                while i < n and src[i : i + 3] != '"""':
                    if src[i] == "\n":
                        row += 1
                    i += 1
                i += 3
            else:
                q, i = c, i + 1
                while i < n:
                    if src[i] == "\\":
                        i += 2
                    elif src[i] == q:
                        i += 1
                        break
                    elif src[i] == "\n":
                        row += 1
                        i += 1
                    else:
                        i += 1
            last_code_row = row
            continue
        if c in " \t\r":
            i += 1
            continue
        if two == "|>" or two == "<|":
            pending.append(last_code_row)
            i += 2
            continue
        # Any other code char resolves pending ops and advances the code row.
        resolve(row)
        last_code_row = row
        i += 1
    return fps


def layout_fingerprint(src):
    """Combined author-layout signature: bracket containers (lists, records,
    record updates, record types, patterns), union variant lists, function/port
    type signatures, and `|>`/`<|` pipelines. A perturbation that leaves this
    unchanged is not an intended layout flip."""
    return (
        container_layout_fingerprint(src),
        union_layout_fingerprint(src),
        signature_layout_fingerprint(src),
        pipeline_layout_fingerprint(src),
    )


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


def classify_variant(base, base_ast, base_fmt, base_fp, base_layout, label, variant):
    """Classify one perturbed variant. Runs on a pool worker, in that worker's
    isolated project dir. Returns (kind, label, vf) where kind is one of
    ast / comment / layout / drift / ok."""
    wd = worker_workdir(base)
    va = ast(wd, variant)
    if va is None or va != base_ast:
        return ("ast", label, None)
    # A perturbation that flips any comment's inline/own-line placement has
    # changed comment meaning, not just whitespace (see comment_fingerprint);
    # the formatter rightly reflects that, so it is not a drift bug.
    if comment_fingerprint(variant) != base_fp:
        return ("comment", label, None)
    # Likewise, a perturbation that flips whether a container (list, record, or
    # record update) is laid out across rows has changed layout intent, which the
    # formatter now honours (one-line-if-fits vs one-item-per-line); not a bug.
    if layout_fingerprint(variant) != base_layout:
        return ("layout", label, None)
    vf = fmt(wd, variant)
    if vf != base_fmt:
        return ("drift", label, vf)
    return ("ok", label, None)


def check_file(base, pool, path, mode, verbose):
    src = open(path).read()
    runs = gap_runs(src)
    name = os.path.basename(path)

    wd0 = worker_workdir(base)
    base_ast = ast(wd0, src)
    base_fmt = fmt(wd0, src)
    if base_ast is None or base_fmt is None:
        print(f"SKIP {name}: original does not parse/format")
        return 0
    base_fp = comment_fingerprint(src)
    base_layout = layout_fingerprint(src)

    if mode == "stretch":
        variants = [("stretch", v) for v in perturb_stretch(src, runs)]
    elif mode == "indent":
        variants = perturb_indent(src, runs)
    else:
        variants = perturb_newline_gap(src, runs)

    # pool.map preserves input order, so reporting stays deterministic.
    results = list(
        pool.map(
            lambda lv: classify_variant(base, base_ast, base_fmt, base_fp, base_layout, lv[0], lv[1]),
            variants,
        )
    )
    ast_changed = sum(1 for r in results if r[0] == "ast")
    comment_moved = sum(1 for r in results if r[0] == "comment")
    layout_moved = sum(1 for r in results if r[0] == "layout")
    ok = sum(1 for r in results if r[0] == "ok")
    drift = [(label, vf) for (kind, label, vf) in results if kind == "drift"]

    status = "OK " if not drift else "DRIFT"
    print(
        f"{status} {name}: {len(variants)} variants, {ok} canonical, "
        f"{ast_changed} ast-changed/illegal, {comment_moved} comment-placement, "
        f"{layout_moved} container-layout, {len(drift)} format-drift"
    )
    for label, vf in drift:
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
    ap.add_argument("-j", "--jobs", type=int, default=2, help="concurrent `gren format`s (default 2)")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv[1:])

    files = args.files
    if not files:
        d = os.path.join(HERE, "testfiles", "Formatter")
        files = sorted(
            os.path.join(d, f) for f in os.listdir(d) if f.endswith(".dirty.gren")
        )

    total = 0
    with tempfile.TemporaryDirectory() as base:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for path in files:
                total += check_file(base, pool, path, args.mode, args.v)
    print(f"\n{'FAIL' if total else 'PASS'}: {total} format-drift finding(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
