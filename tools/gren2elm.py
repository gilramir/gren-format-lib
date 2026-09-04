#!/usr/bin/env python3
"""Convert a Gren source file to approximate Elm syntax for elm-format comparison.

Usage:
    python3 tools/gren2elm.py <input.gren> [output.elm]
    python3 tools/gren2elm.py --normalize <elm-format-output> [output]  # - = stdin

The output is not semantically valid Elm, but should be syntactically acceptable
so elm-format can parse and reformat it.

This is the one piece of the elm-format comparison workflow (root CLAUDE.md,
"elm-format comparison") that is a program rather than a judgement call, which is
why it lives here and the rest of the workflow is prose. Run from
`gren-format-lib/`:

    python3 tools/gren2elm.py F.gren F.elm
    elm-format --stdin < F.elm | python3 tools/gren2elm.py --normalize - > elm.out
    node ../gren-format/app --show F.gren > gren.out
    diff elm.out gren.out

`--normalize` is the second half of that and exists so the diff shows only
divergences nobody has decided about yet. The two formatters spell some literals
differently ON PURPOSE, and each of those differences would otherwise appear on
every file that contains one, in a review whose whole job is spotting the
differences nobody catalogued. See NORMALIZATIONS below.
"""

import re
import sys


def convert_when_is(source):
    """Convert `when EXPR is` to `case EXPR of` (single-line form).

    Also handles multi-line form where `is` appears alone on the next line.
    """
    lines = source.splitlines(keepends=True)
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Single-line: leading whitespace + `when` + expr + `is` at end
        m = re.match(r'^(\s*)when\b(.+)\bis\s*$', line)
        if m:
            result.append(m.group(1) + 'case' + m.group(2) + 'of\n')
            i += 1
            continue
        # Multi-line: `when` on its own line (no `is`), next non-blank line is `is`
        m_open = re.match(r'^(\s*)when\b(.+)$', line)
        if m_open:
            # Peek ahead to see if a following line is just `is`
            j = i + 1
            peeked = []
            while j < len(lines) and lines[j].strip() == '':
                peeked.append(lines[j])
                j += 1
            if j < len(lines) and lines[j].strip() == 'is':
                indent = re.match(r'^(\s*)', lines[j]).group(1)
                result.append(m_open.group(1) + 'case' + m_open.group(2) + '\n')
                result.extend(peeked)
                result.append(indent + 'of\n')
                i = j + 1
                continue
        result.append(line)
        i += 1
    return ''.join(result)


def convert_module_header(source):
    """Ensure the module header uses Elm syntax (already compatible in Gren)."""
    return source


TRANSFORMS = [
    convert_when_is,
    convert_module_header,
]


def convert(source):
    for t in TRANSFORMS:
        source = t(source)
    return source


# --------------------------------------------------------------- NORMALIZE
#
# Rewrites applied to elm-format's OUTPUT, each one a divergence that is already
# decided and written down in gren-format-lib/docs/elmFormatComparison.md. Only
# add an entry here once the divergence has a catalogue number and a fixture --
# this list is for silencing settled questions, and an undecided difference
# silenced here is a difference nobody will ever look at again.
#
# Both entries are literal SPELLING: the two formatters agree on the string's
# value and disagree on how to write it down.
NORMALIZATIONS = [
    # Divergence #35. elm-format escapes U+3000 IDEOGRAPHIC SPACE along with the
    # rest of the Zs category; gren-format writes it as the character, because a
    # full-width space is a full character cell wide and lines up with the
    # ideographs around it. Every other Zs member is escaped by both.
    (r'\u{3000}', '　'),
    # Divergence #9. elm-format EXPANDS a carriage return to its code point (it
    # has named escapes only for \n, \t, \\ and the closing quote); gren-format
    # contracts it to `\r`. The two agree on \n and \t, and on contracting a
    # printable code point (`"\u{0041}"` -> `"A"` in both), so this is the whole
    # of #9's residue in a file that is not a float or an integer literal.
    (r'\u{000D}', r'\r'),
]


def normalize_elm_output(text):
    """Rewrite elm-format's output so a diff against `gren-format --show` shows
    only differences nobody has classified yet.

    The rewrites are applied ONLY inside string and character literals. That is
    not caution for its own sake: a comment mentioning `\\u{000D}` is comment
    TEXT, which neither formatter rewrites, so both sides already carry it
    verbatim -- rewriting one of them is how you invent a diff rather than
    remove one. Real Gren source discusses escapes in prose (the formatter's own
    `LiteralFormat.gren` does it a dozen times), so this is a case that comes up
    rather than a hypothetical.

    The scanner is Elm's lexical structure, not a regex over the file: line
    comments, block comments (which nest), `"..."`, `\"\"\"...\"\"\"` and
    `'x'`, with backslash escapes inside the three literal forms.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if text.startswith('--', i):
            end = text.find('\n', i)
            end = n if end == -1 else end
            out.append(text[i:end])
            i = end

        elif text.startswith('{-', i):
            depth = 0
            start = i
            while i < n:
                if text.startswith('{-', i):
                    depth += 1
                    i += 2
                elif text.startswith('-}', i):
                    depth -= 1
                    i += 2
                    if depth == 0:
                        break
                else:
                    i += 1
            out.append(text[start:i])

        elif text.startswith('"""', i):
            start = i
            i += 3
            while i < n and not text.startswith('"""', i):
                i += 2 if text[i] == '\\' else 1
            i = min(i + 3, n)
            out.append(apply_normalizations(text[start:i]))

        elif ch == '"' or ch == "'":
            quote = ch
            start = i
            i += 1
            while i < n and text[i] != quote:
                i += 2 if text[i] == '\\' else 1
            i = min(i + 1, n)
            out.append(apply_normalizations(text[start:i]))

        else:
            out.append(ch)
            i += 1

    return ''.join(out)


def apply_normalizations(literal):
    for elm_spelling, gren_spelling in NORMALIZATIONS:
        literal = literal.replace(elm_spelling, gren_spelling)
    return literal


def main():
    args = sys.argv[1:]

    if args and args[0] == '--normalize':
        if len(args) < 2:
            print("Usage: gren2elm.py --normalize <elm-format-output|-> [output]",
                  file=sys.stderr)
            sys.exit(1)
        source = sys.stdin.read() if args[1] == '-' else open(args[1]).read()
        result = normalize_elm_output(source)
        if len(args) > 2:
            with open(args[2], 'w') as f:
                f.write(result)
            print(f"Written to {args[2]}")
        else:
            sys.stdout.write(result)
        return

    if not args:
        print("Usage: gren2elm.py <input.gren> [output.elm]", file=sys.stderr)
        print("       gren2elm.py --normalize <elm-format-output|-> [output]",
              file=sys.stderr)
        sys.exit(1)

    input_path = args[0]
    if len(args) > 1:
        output_path = args[1]
    elif input_path.endswith('.gren'):
        output_path = input_path[:-5] + '.elm'
    else:
        output_path = input_path + '.elm'

    with open(input_path) as f:
        source = f.read()

    result = convert(source)

    with open(output_path, 'w') as f:
        f.write(result)

    print(f"Written to {output_path}")


if __name__ == '__main__':
    main()
