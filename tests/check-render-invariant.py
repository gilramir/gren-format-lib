#!/usr/bin/env python3
"""Enforce the comment/layout architecture invariant (see comment-arch.md, §7).

    After Comments.gren runs, no code in Render/* reads source rows or positions
    to make a layout or comment-placement decision.

Layout is decided from author-intent flags (captured at LPT build) and the
*rendered box shape* (`isSingleLine`/`allSingles`), never re-derived from source
rows at render time. This check fails if any `src/Formatter/Render/*.gren`
function reads a row/position accessor, UNLESS that function is on the
allowlist below — a small set of genuinely-structural, non-decision uses.

Adding a new row-read in Render/* is almost always a regression toward the
oscillation/crash class this architecture eliminated. If you truly need one,
add the function to ALLOWED with a one-line justification — that makes it a
conscious, reviewed choice rather than an accident.

Comment- and string-aware: matches only real code (a `{- lpnLastPos -}` in a
doc comment does not count).
"""

import re
import sys
from pathlib import Path

RENDER_DIR = Path(__file__).resolve().parent.parent / "src" / "Formatter" / "Render"

# Row / position accessors that would re-derive a layout decision from source rows.
ACCESSOR = re.compile(
    r"\b(lpnFirstPos|lpnLastPos|lpnLastBracketEnd|lpnMinRow|lpnMaxRow"
    r"|firstRowInSubtree|lastRowInSubtree|subtreeRowRange)\b"
    r"|\.(start|end)\.(row|col)\b"
)

# Functions permitted to read a row/position accessor, with the reason each is
# NOT a comment-placement / verticality decision.
ALLOWED = {
    "nodeStartRow": "structural: the start row of a node, for fn+arg0 glue",
    "nodesShareStartRow": "structural: do fn and arg0 share a source row (glue on the opening line)",
    "segFirstRow": "signature-segment layout: a `->` segment's true start row",
    "segLastRow": "signature-segment layout: a `->` segment's true last row",
    "isElidedArrow": "structural: a zero-width synthesized `->` (start == end), no row *comparison*",
    "assembleBrokenWithComments": "a `lastRow >= 0` 'previous entry is real content' guard; the glue call itself is role-based",
    "makeUnionBodyBox": "author-intent: did the author write the union variants across rows (flat vs vertical)",
}


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
    for path in sorted(RENDER_DIR.glob("*.gren")):
        raw = path.read_text()
        masked = mask_comments_and_strings(raw)
        raw_lines = raw.splitlines()
        masked_lines = masked.splitlines()
        for i, code in enumerate(masked_lines):
            if ACCESSOR.search(code):
                fn = enclosing_function(masked_lines, i)
                # `import`/`module` exposing lists name accessors as text, not
                # as a decision — those aren't code that reads a row.
                if fn in ("import", "module"):
                    continue
                if fn not in ALLOWED:
                    violations.append((path.name, i + 1, fn, raw_lines[i].strip()))

    if violations:
        print("FAIL: render-invariant — new source-row read in Render/* "
              "(layout must come from author flags + rendered box shape, not rows).")
        print("      See comment-arch.md §7 and docs/commentHandling.md.\n")
        for name, ln, fn, text in violations:
            print(f"  {name}:{ln}  in `{fn}`")
            print(f"      {text}")
        print(f"\n{len(violations)} violation(s). If a use is genuinely structural, "
              "add its function to ALLOWED in this script with a reason.")
        return 1

    print(f"PASS: 0 render-invariant violations "
          f"({len(ALLOWED)} allowlisted structural row-reads).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
