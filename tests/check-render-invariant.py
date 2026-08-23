#!/usr/bin/env python3
"""Enforce the last un-typed half of the comment/layout architecture invariant.

    After Comments.gren runs, no code in Render/* reads source rows or positions
    to make a layout or comment-placement decision.

Layout is decided from author-intent flags (captured at LPT build) and the
*rendered box shape* (`isSingleLine`/`allSingles`), never re-derived from source
rows at render time.

**Most of this is now the type checker's job.** `Formatter.RenderTree.lower`
turns the LPT's `LPNode` -- whose record caches seven source-position fields --
into a position-free `RenderNode`, and every module under `src/Formatter/Render/`
takes that instead. The eight row/position accessors this script used to
enumerate do not typecheck there any more, so there is nothing left for an
allowlist to allow: the five genuinely-structural row reads it used to permit are
precomputed by `lower` and read back as booleans.

That enumeration was the script's real weakness, not its redundancy. `ACCESSOR`
named eight functions; `lpnBracketStart` was not among them and *was* called in
`Render/NodeClassify.gren`, and the only reason no unreviewed violation existed
was that the call happened to sit inside an allowlisted function. Same exposure
for `lpnBracketEndExact` / `lpnBracketEndElastic` / `lpnWithBracketStart`. A type
error cannot be under-enumerated.

What the type checker does NOT yet catch is a position read straight off a
`Located` payload inside an `LPShape`, which `RenderNode` still carries as-is:

    when rnShape node is
        UnbreakableText loc -> loc.start.row     -- compiles; must not exist

Render only ever reads `.value` off those payloads. This script is the gate on
that one remaining spelling, and it goes away when `LPShape` gets a mirrored,
`Located`-free `RenderShape` (tier 2 of the refactor; see the module doc on
`Formatter.RenderTree`).

Comment- and string-aware: matches only real code (a `{- .start.row -}` in a doc
comment does not count).
"""

import re
import sys
from pathlib import Path

RENDER_DIR = Path(__file__).resolve().parent.parent / "src" / "Formatter" / "Render"

# A source position read off a `Located` payload carried by an `LPShape`.
ACCESSOR = re.compile(r"\.(start|end)\.(row|col)\b")


def mask_comments_and_strings(src: str) -> str:
    """Replace comment and string CONTENT with spaces, preserving newlines and
    code positions, so ACCESSOR only matches real code."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i : i + 2]
        three = src[i : i + 3]
        if two == "--":  # line comment to EOL
            while i < n and src[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if two == "{-":  # block comment, nesting
            depth = 1
            out.append("  ")
            i += 2
            while i < n and depth > 0:
                if src[i : i + 2] == "{-":
                    depth += 1
                    out.append("  ")
                    i += 2
                elif src[i : i + 2] == "-}":
                    depth -= 1
                    out.append("  ")
                    i += 2
                else:
                    out.append("\n" if src[i] == "\n" else " ")
                    i += 1
            continue
        if three == '"""':  # triple-quoted string
            out.append("   ")
            i += 3
            while i < n and src[i : i + 3] != '"""':
                out.append("\n" if src[i] == "\n" else " ")
                i += 1
            out.append("   ")
            i += 3
            continue
        if c == '"':  # string literal
            out.append(" ")
            i += 1
            while i < n and src[i] != '"':
                if src[i] == "\\":
                    out.append("  ")
                    i += 2
                    continue
                out.append(" ")
                i += 1
            out.append(" ")
            i += 1
            continue
        if c == "'":  # char literal
            out.append(" ")
            i += 1
            while i < n and src[i] != "'":
                if src[i] == "\\":
                    out.append("  ")
                    i += 2
                    continue
                out.append(" ")
                i += 1
            out.append(" ")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


DEF_RE = re.compile(r"^([a-z][A-Za-z0-9_]*)\b")


def enclosing_function(lines, idx):
    """Nearest top-level definition at or above line `idx` (0-based)."""
    for j in range(idx, -1, -1):
        m = DEF_RE.match(lines[j])
        if m:
            return m.group(1)
    return "<top-level>"


def main():
    violations = []
    # Recursive: `Render/` is flat today, and a new subdirectory must not
    # silently fall outside the gate.
    for path in sorted(RENDER_DIR.rglob("*.gren")):
        raw = path.read_text()
        masked = mask_comments_and_strings(raw)
        raw_lines = raw.splitlines()
        masked_lines = masked.splitlines()
        for i, code in enumerate(masked_lines):
            if ACCESSOR.search(code):
                fn = enclosing_function(masked_lines, i)
                violations.append((path.name, i + 1, fn, raw_lines[i].strip()))

    if violations:
        print("FAIL: render-invariant \u2014 source position read in Render/* "
              "(layout must come from author flags + rendered box shape, not rows).")
        print("      Placement is the stored CommentRole (see its docstring in\n"
              "      Logical/LogicalPrintingTree.gren); verticality is the rendered box.\n")
        for name, ln, fn, text in violations:
            print(f"  {name}:{ln}  in `{fn}`")
            print(f"      {text}")
        print(f"\n{len(violations)} violation(s). If a render decision genuinely "
              "needs a source row, precompute\nit as a boolean in "
              "`Formatter.RenderTree.lower` and read the flag here instead.")
        return 1

    print("PASS: 0 render-invariant violations "
          "(the accessors are a type error now; this gates the `Located` payloads).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
