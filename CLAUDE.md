# CLAUDE.md — gren-format-lib

`gilramir/gren-format-lib` is a Gren **package** (`platform: common`) that holds
the Gren formatter library. The formatter is consumed by:

- `gren-format/` — the standalone `gren-format` CLI (primary consumer)
- `compiler/` — the legacy `gren format` subcommand of the Haskell front-end

All formatter source lives in `src/Formatter/`.

## Sibling repos (expected at `../`)

| Path | Role |
|---|---|
| `../compiler-common/` | Shared AST + parse types (`Compiler.Ast.Source`, `Compiler.Parse.Context`) |
| `../compiler/` | Haskell front-end; provides `../gren.sh` for builds |
| `../gren-format/` | Standalone CLI that imports this package |

`../gren.sh` (one directory up from this repo) is the compiler wrapper used for
all build and format commands.

## Build & check

Compile a module to surface type errors (use the **module name**, not a file path):

```bash
cd gren-format-lib
../gren.sh make Formatter.PrettyPrinter
```

The package itself has no runnable app — it is a library. The `tests/` directory
is a separate Gren application that depends on this package locally.

## Tests

### Effectful suite (main gate)

```bash
cd gren-format-lib/tests
./run-tests.sh     # builds tests/app via `../../gren.sh make Main --output=app`, then runs it
```

`run-tests.sh` recompiles the test harness against the formatter source directly
(the `tests/` app depends on `..` locally), so editing formatter source and
re-running `run-tests.sh` is enough — no separate library build step.

Test cases are in `tests/src/Test/Formatter/Format.gren`. Each calls:

```gren
assertPretty fsPerm "description" "FileBaseName"
```

which performs three checks:
1. **Formatting** — `format(testfiles/Formatter/<FileBaseName>.dirty.gren)` is
   byte-equal to `testfiles/Formatter/<FileBaseName>.formatted.gren`
2. **AST equivalence** — re-parsing the formatted output yields a semantically
   equal AST (catches formatting that changes meaning)
3. **Idempotency** — re-formatting the `.formatted` file changes neither the
   `Module` nor the comment/blank-line `Context`

**To add a test:** write both `<Name>.dirty.gren` and `<Name>.formatted.gren` in
`tests/testfiles/Formatter/`, then add an `assertPretty` line in `Format.gren`.
Generate the `.formatted` with:
```bash
node ../../gren-format/app --show <Name>.dirty.gren > testfiles/Formatter/<Name>.formatted.gren
```
Read it before trusting it — confirm the output is actually canonical.

### Idempotency fuzzer

Inserts a `{- ¤ -}` block comment into every inter-token gap, formats twice,
and requires byte-identical output. The safety net for comment-shift bugs.

```bash
cd gren-format-lib/tests
python3 fuzz-idempotency.py -j 12                                      # whole corpus
python3 fuzz-idempotency.py -v testfiles/Formatter/Foo.formatted.gren  # one file
```

**Rebuild the compiler app first** — fuzzers invoke `../../gren.sh format` as a
subprocess, so they require an up-to-date binary. Run after any change to comment
handling, and after adding any comment-bearing fixture.

### Whitespace-canonicalization fuzzer

Perturbs inter-token whitespace and requires `format(perturbed) == format(original)`.

```bash
cd gren-format-lib/tests
python3 fuzz-whitespace.py --mode newline   # modes: stretch | indent | newline
python3 fuzz-whitespace.py -j 12           # parallelise
```

This machine has 16 cores; both fuzzers default to `-j 2`. Use `-j 12` for a
fast whole-corpus sweep.

## Inspecting formatter internals

Both the standalone CLI and the legacy `gren format` subcommand accept debug flags:

```bash
node ../gren-format/app --show    MyFile.gren   # formatted output to stdout
node ../gren-format/app --pre-ast MyFile.gren   # parsed AST + context as JSON
node ../gren-format/app --lpt     MyFile.gren   # Logical Printing Tree as JSON
node ../gren-format/app --pex     MyFile.gren   # PrettyExpressive Doc as JSON
node ../gren-format/app --check   MyFile.gren   # format, verify ASTs match
```

`--lpt` is the most useful debug flag for comment-placement and layout bugs.

## Formatter architecture

Pipeline: `Src.Module + Ctx.Context → LPT → PrettyExpressive Doc → String`

```
Formatter.PrettyPrinter        entry point: prettyPrint/2
    Formatter.MakeLogical      AST → LogicalPrintingTree
        Formatter.InsertExpressions   expressions
        Formatter.InsertPatterns      patterns
        Formatter.InsertTypes         types
        Formatter.LPTHelpers          shared helpers (mkText, mkTextFromLocString, …)
        Formatter.Comments            re-attaches comments from parse context
        Formatter.VerticalSpace       inserts blank lines between top-level items
    Formatter.MakePretty       LPT → PrettyExpressive Doc → String
```

Page width: **80** columns (`costFactory.pageWidth`; `computationWidth` is 100).
Indent step: **4** spaces (`grenIndent`). All in `MakePretty.gren`.

**Key invariant:** every top-level declaration becomes exactly one `OriginalRows`
node directly under `RootBox`. Comments and blank lines are inserted as sibling
`OriginalRows` nodes by `Comments` and `VerticalSpace` after the tree is built.

## Authoritative documentation

- `src/Formatter/README.md` — what every formatting rule does, with worked
  examples for every construct. Read first when reasoning about formatter behavior.
- `src/Formatter/DEVELOPER.md` — orientation guide for extending the formatter
  with new syntax: the full checklist, position rules, comment-attachment
  hazards, and the "things to worry about" section.
