# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **An invisible character no longer loses the `\u{...}` escape that made it
  visible.** A string literal was rebuilt with every code point from U+0080 up
  written out verbatim, so `"\u{FEFF}"` came back as a byte order mark with
  nothing to see between the quotes, `"\u{200B}"` as a zero-width space, and
  `"\u{00A0}"` as something no reader, `grep` or diff can tell from a plain
  space. A char literal did the opposite and kept the escape, so the two rules
  disagreed with each other.

### Changed

- **One rule now decides how every literal is written**, shared by `"..."`,
  `"""..."""` and `'x'` so they cannot drift apart again. A code point is
  written as itself when it is printable, and keeps a `\u{...}` escape when it
  is not: control characters, format characters (the byte order mark, the
  zero-width space, the bidi controls, an emoji sequence's zero-width joiner),
  every space separator but U+0020 and U+3000, the line and paragraph
  separators, surrogates, private-use code points and noncharacters. This is
  elm-format's rule, with one deliberate exemption: elm-format escapes U+3000
  IDEOGRAPHIC SPACE along with the rest of the space separators, and
  gren-format writes it as itself, because a full-width space is a full
  character cell wide and is the space of CJK text — escaping it is what would
  hide it. See divergence #35 in `docs/elmFormatComparison.md`. Letters, marks,
  numbers, punctuation and symbols are unaffected — `"é"`, `"日本"` and `"😀"`
  come back as themselves, as before.
- **A char literal no longer escapes every non-ASCII character**, which is the
  half of that rule that changes existing output: `'☃'` and `'é'` are now
  written as themselves rather than as `'\u{2603}'` and `'\u{00e9}'`, the same
  way a string has always written them.
- **`\u{...}` escapes are written in uppercase hex** — `\u{001B}`, not
  `\u{001b}` — matching elm-format. The case you wrote is not recoverable
  either way, since the parser hands the formatter a decoded value in which
  `\u{001B}`, `\u{001b}` and a raw ESC byte are one and the same character; see
  divergence #9 in `docs/elmFormatComparison.md`.


## [1.1.0] - 2026-08-26

### Added

- **`Formatter.RenderTree` and `Formatter.RenderTree.Json` are exposed.** The
  first is the barrier between the formatter's two stages: `lower` copies the
  Logical Printing Tree with every source position taken off, so no stage-two
  code can re-derive a layout decision from the author's rows — the rule that
  used to be a lint script is now a type error. The second serialises that tree
  for the CLI's new `--rt` flag, including the four author-intent booleans
  `lower` computes, which appear in no other dump.

### Fixed

- **Import sorting no longer misplaces a comment written inside an `import`
  statement**, and a unit's row extent now spans its leading comments, so a
  comment above an import sorts with the import it belongs to rather than being
  left behind.
- **Two non-idempotencies** found by sweeping the dirty half of the fixture
  corpus for the first time: files whose second format differed from the first.

### Changed

- The render stage must not access (row, col) positions from the Logical
  Printing Tree. Remove the remaining static-analysis enforcement script
  `tests/check-render-invariant.py`, and replace with a RenderTree
  type that does not provide (row, col). Nothing about the formatter's
  output changes; see `docs/testing.md`.


## [1.0.1] - 2026-08-22

The exposed API is unchanged from 1.0.0. Everything here is formatter
behaviour: the `<|` and `|>` chain layouts, and the comment placements around
them.

### Changed

- **`<|` chains have a stated layout, R1–R3** (settled decision SD5, divergence
  #33). A lambda after `<|` keeps its head on the operator's row rather than
  being pushed below it (R1); continuation steps of a chain align with each
  other (R2); and the body that closes a chain always takes a row of its own
  (R3).
- **A bare `if` / `when` / `let` / lambda operand after `|>` keeps its head on
  the operator's row** (divergence #34). elm-format parenthesizes such an
  operand; gren-format does not add parens, so the head glues to the `|>`.
- **A mixed `|>` / `<|` chain follows the author's rows**, like every other
  chain, instead of being laid out independently of what was written.

### Fixed

- `|>` with a bare lambda operand no longer strands the operator alone on its
  row.
- `|>` with a multi-line string operand no longer strands the operator.
- A `|>` / `<|` step no longer flattens a break the author wrote.
- A `{- c -}` written before a lambda item no longer force-breaks the bracket
  around it (`[ 0, {- c -} \x -> 1 ]` stays on one line), and no longer moves
  the comment to a row of its own. The author's own broken spelling of the same
  array is preserved too — before, both spellings rendered identically.
- A `--` ending a `<|` chain no longer sends the chain vertical. Nothing is
  appended after the final body's row, so its trailing comment swallows
  nothing; `|>`, `+` and `++` already behaved this way at every chain length.
- A comment leading an `if` / `when` header no longer makes the formatter
  refuse the file with `unreachable: multi-line non-paren unclassified
  soft-glue item`. Relatedly, an `if` whose condition cannot fit beside such a
  comment now puts the comment on its own row, so the output reparses — it
  previously emitted a file no parser accepts.
- A single-line `{- … -}` comment no longer breaks a flat mixed `|>` / `<|`
  chain.

### Documentation

- Divergence catalogue entries **#33** (lambda after `<|`) and **#34** (bare
  operand after `|>`) added; the catalogue's entry↔fixture index is at 34/34.
- Divergence **#31** is recorded as an upstream parser bug
  (`gren-lang/compiler-common#37`, a declaration that doesn't start in column 1),
  not a deliberate choice, and is described in `docs/knownLimitations.md`.
- `docs/settledDecisions.md` entries are numbered **SD1–SD5**, and SD5 (`<|`
  lambda head) is condensed from a decision record into the rule it settled on.

## [1.0.0] - 2026-08-15

- First release. The formatter library behind the `gren-format` CLI: the
  `Formatter` entry point, the logical and render stages, the two audit
  modules, and the three AST utilities (`Compiler.Ast.Compare`,
  `Compiler.Ast.Source.Json`, `Compiler.Parse.Context.Json`) moved here out of
  `compiler-common`.
