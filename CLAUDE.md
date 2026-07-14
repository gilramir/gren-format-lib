# CLAUDE.md — gren-format-lib

`gilramir/gren-format-lib` is a Gren **package** (`platform: common`) that holds
the Gren formatter library. The formatter is consumed by:

- `gren-format/` — the standalone `gren-format` CLI (primary consumer)
- `compiler/` — the legacy `gren format` subcommand of the Haskell front-end

All formatter source lives in `src/Formatter/`. The package also hosts three
AST utility modules in `src/Compiler/`, moved here out of `compiler-common`
because only the gren-format tooling uses them:

- `Compiler.Ast.Compare` (`compareModules`) — semantic AST equality, used to
  verify a format preserves meaning
- `Compiler.Ast.Source.Json` (`encodeModule`) — JSON encoder for the source AST
- `Compiler.Parse.Context.Json` (`encodeContext`) — JSON encoder for parse context

All three are re-exposed by the package so the `gren-format` CLI and this
package's `tests/` can import them (their module names are unchanged).

## Sibling repos (expected at `../`)

| Path | Role |
|---|---|
| `../compiler-common/` | Shared AST + parse types (`Compiler.Ast.Source`, `Compiler.Parse.Context`) — no longer modified/rebuilt, read-only |
| `../gren-format/` | Standalone CLI that imports this package |

`compiler/`, `compiler-common/`, and `compiler-node/` are no longer being
modified or rebuilt. There is no top-level `gren.sh` wrapper anymore (removed).
Build and typecheck this package with **devbox** — `devbox.json` pins a
`gren@0.6` package that resolves to a version-compatible published Gren
compiler.

## Build & check

Compile a module to surface type errors (use the **module name**, not a file path):

```bash
cd gren-format-lib
devbox run -- gren make Formatter
```

The package itself has no runnable app — it is a library. The `tests/` directory
is a separate Gren application that depends on this package locally.

## Tests

### Effectful suite (main gate)

```bash
cd gren-format-lib/tests
./run-tests.sh     # builds tests/app via devbox, then runs it
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

**Rebuild the `gren-format` app first** (`cd ../../gren-format && ./build.sh`) —
fuzzers invoke `../../gren-format/gren-format.sh` as a subprocess, so they
require an up-to-date binary. Run after any change to comment handling, and
after adding any comment-bearing fixture.

### Whitespace-canonicalization fuzzer

Perturbs inter-token whitespace and requires `format(perturbed) == format(original)`.

```bash
cd gren-format-lib/tests
python3 fuzz-whitespace.py                 # default: stretch mode
python3 fuzz-whitespace.py --mode indent   # modes: stretch | indent
python3 fuzz-whitespace.py -j 12           # parallelise
```

This machine has 16 cores; both fuzzers default to `-j 2`. Use `-j 12` for a
fast whole-corpus sweep.

## Inspecting formatter internals

Both the standalone CLI and the legacy `gren format` subcommand accept debug flags:

```bash
node ../gren-format/app --show       MyFile.gren   # formatted output to stdout
node ../gren-format/app --pre-ast    MyFile.gren   # parsed AST + context as JSON
node ../gren-format/app --pre-context MyFile.gren   # just the parse Context (comments) as JSON
node ../gren-format/app --lpt        MyFile.gren   # Logical Printing Tree as JSON
node ../gren-format/app --box        MyFile.gren   # the Box tree each decl renders to, as a JSON array
node ../gren-format/app --check      MyFile.gren   # format, verify ASTs match
```

`--lpt` is the most useful debug flag for comment-placement and layout bugs.

## Formatter architecture

Pipeline: `Src.Module + Ctx.Context → LPT → Box → String`

```
Formatter                              entry point: prettyPrint
    Formatter.Logical.MakeLogical      AST → LogicalPrintingTree
        Formatter.Logical.InsertExpressions   expressions (one insert* per form)
        Formatter.Logical.InsertPatterns      patterns
        Formatter.Logical.InsertTypes         types
        Formatter.Logical.LPTHelpers          construction helpers (mkText*, plainAcross, …)
        Formatter.Logical.BinopPrecedence     operator fixity table
        Formatter.Logical.Comments            re-attaches comments from parse context
        Formatter.Logical.SortSymbols         sorts exposing lists + import groups
        Formatter.Logical.VerticalSpace       inserts blank lines between top-level items
    Formatter.Render                   render-stage entry (module Formatter.Render, file Render.gren): maps each RootBox child through the Box renderer, joins with newlines
        Formatter.Render.MakeRenderBox LPT → Box — recursive core: dispatch (one builder per LPBox constructor) + per-construct renderers
            Formatter.Render.BinopLayout   pure binop-chain layout assembly
            Formatter.Render.CommentBox    comment-node rendering (line / block / doc)
            Formatter.Render.FlowAssembly  FlowItem / SoftGlueAlignment types + pure flow-layout helpers
            Formatter.Render.NodeClassify  boolean predicates / structural queries over LPT nodes
            Formatter.Render.BoxOps        low-level Box / Line manipulation helpers
        Formatter.Render.Box           elm-format's Box IR (Line/Box, Tab tab-stops, prefix)
        Formatter.Render.FlowPolicy    shared inline/break decision layer
```

The Box renderer is the **sole backend** — the earlier `Formatter.Render.Doc`
renderer and the self-verifying Box/Doc guard were deleted at the full cutover.

`Render/MakeRenderBox.gren` was the whole Box renderer; its knot-free helpers
have been split into five sibling modules — `BinopLayout`, `CommentBox`,
`FlowAssembly`, `NodeClassify`, `BoxOps` — leaving `MakeRenderBox` as the
mutually-recursive dispatch (`makePBox`) plus the per-construct renderers. Gren
forbids circular imports, so only functions that never transitively reach the
`makePBox`/`buildFlowBox` recursion could move out. Import DAG:
`MakeRenderBox` → all five; `BinopLayout`/`CommentBox`/`FlowAssembly` →
`BoxOps`, `NodeClassify`; `BoxOps` and `NodeClassify` depend on neither.

Layout is **author-driven, not fit-driven**: there is no page width and no
layout search. Each box already knows whether it renders inline or vertical —
decided from the author's original source rows (`forceVertical`). Indent step:
**4** spaces (`grenIndent`, in `Render/MakeRenderBox.gren`).

**Key invariant:** every top-level declaration becomes exactly one `OriginalRows`
node directly under `RootBox`. Comments and blank lines are inserted as sibling
`OriginalRows` nodes by `Comments` and `VerticalSpace` after the tree is built.

## Authoritative documentation

- `README.md` (the "Gren Formatter Rules" section) — what every formatting
  rule does, with worked examples for every construct. Read first when
  reasoning about formatter behavior.
- `DEVELOPER.md` — orientation guide for extending the formatter
  with new syntax: the full checklist, position rules, comment-attachment
  hazards, and the "things to worry about" section.
