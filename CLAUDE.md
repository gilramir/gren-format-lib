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
| `../gren-format/` | Standalone CLI that imports this package |

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

### Construct × context syntax matrix

The corpus reaches only the syntax somebody thought to write, and both fuzzers
perturb *comments* and *whitespace* over that fixed corpus — **neither varies
syntax**. A bug needing a conjunction of features therefore has no fixture. This
is the syntax axis: it embeds every expression form in every context (825 cells)
and checks each one.

```bash
cd gren-format-lib/tests
./matrix-syntax.py -j 12                                  # whole matrix
./matrix-syntax.py -v                                     # source + output per failure
./matrix-syntax.py --construct recordUpdate1 --context parenBinopArg
./matrix-syntax.py -k /tmp/failing                        # write failing cells out as .gren
./matrix-syntax.py --no-parity                            # skip oracle 4
./matrix-syntax.py --update-baseline                      # rewrite the parity baseline
```

**Rebuild the `gren-format` app first** — it shells out to it. Oracle 4 also
needs `elm-format` on PATH; without it the matrix says so loudly and runs the
other three rather than quietly reporting a thinner green.

Oracles 1–3 need no human review:

1. **Layout, both directions.** Layout is author-driven — no page width, no
   fitter — so a construct written flat renders flat unless its content forces a
   break. Every cell is generated on ONE line, so: a flat construct in a flat
   context **must** stay one line; anything involving `if`/`when`/`let` **must**
   break. Over-approximation (pre-breaking something that renders inline) fails
   the first; a construct that stops breaking fails the second.
2. `--show` internally does parse → render → reparse → AST-compare → render
   again → idempotency-compare, so a clean exit also buys AST equivalence,
   idempotency, and "the output parses". Each failure title is its own class.
3. `--audit-predicates` on every cell (see below), over generated syntax rather
   than only the corpus.

**Oracle 4 — elm-format parity.** Gren is a fork of Elm, so on shared constructs
the two formatters should agree byte-for-byte. Every cell is translated to Elm
and diffed against `elm-format --stdin`. Translating *real* Gren source to Elm is
lossy hand work — which is why the audit in the root `CLAUDE.md` is a manual
exercise — but the cells are built from a vocabulary this script authors, and
across all of it the only Gren-vs-Elm difference is `when X is` → `case X of`.
The translator is therefore one regex, and it is *exact* for that vocabulary
rather than approximate. A construct or context that is not valid Elm must
extend `to_elm`, or be given no Elm twin; a bad translation reports a fake
divergence.

Unlike 1–3, **oracle 4 is not a truth**: gren-format diverges from elm-format on
purpose (README "Divergence catalogue"), so it is gated against a reviewed
baseline in `matrix-parity-baseline.json`. Each diverging cell is registered with
a reason, and the matrix fails on a cell that diverges *unregistered*, or a
registered cell that *no longer* diverges (fixed, or the entry was always wrong).

The hazard is the fixtures' hazard — a baseline entry that is really a bug
freezes it as expected output. Three things push back: a reason of `UNREVIEWED`
is counted and printed on every run, so the debt is never silent; a reviewed
entry is expected to name a catalogue number, making registration a documentation
decision rather than a keystroke; and a divergence reviewed and found to be a
genuine bug gets a `BUG:` reason, which is **also** printed every run — being
understood is not the same as being acceptable, and a baseline entry is the
easiest place in this repo for a known bug to go quiet.

Current state: **850/850 pass oracles 1–3**; 596/850 are byte-identical to
elm-format, with 254 registered divergences — 251 redundant parens (#10), 3
pipeline-`|>` alignment (#20), **0 UNREVIEWED**, and **0 known BUGs**. Use
`-v` to see each divergence beside elm-format's output.
`docs/redundantParens.md` is the reader-facing write-up of the #10 family,
every example verified against both formatters. gren-format never strips a
redundant paren, in any position, including call arguments — the former
one-layer-only call-argument stripping (and its `doubleParen/callArg*`
inconsistency) was removed entirely 2026-07-15.
`whenExpr/pipelineOperand` (a `(when …)` direct pipeline operand stranding the
`|>`) and `*/parenBinopArg` (a doubled `((if/when/let ...)` call argument
anchoring `else`/`in`/its inner `)` to the OUTER paren instead of the inner
one) were both fixed the same day, in `Render/MakeRenderBox.gren`:
`isMultilineLambdaParenBlockAnyBodyBox` now recognizes a ParenBlock whose sole
child is a `WhenFlow`, and `parenGenericFallbackBox` now checks
`parenContentLeadsWithBlockParen` (descends through `Pipeline`/`Binop` to the
leftmost operand) to pick the padded paren-wrap when that operand is itself a
paren-wrapped `if`/`when`/`let`.

**Reclassifying is not a formality.** When the 46 UNREVIEWED were reviewed, two
weaker tests both got it wrong: "same tokens once parens are deleted" cleared 45
of 46, and "does it still diverge with the parens stripped from the source"
cleared 39 — but the source-stripped form takes a different code path, so it
answers a different question. The decisive test is whether **elm's output has
fewer parens than gren's**: if elm keeps the same parens, the divergence cannot
be about parens. That found 4 cells where both formatters agree on the parens and
only the layout differs — real bugs that a blanket reclassification would have
frozen as expected output, including one already known.

Deliberately not covered, and stated in the script rather than hidden: multi-line
string literals (`"""x"""` does not parse on one line, so it cannot be a one-line
atom), comments (that is `fuzz-idempotency.py`'s axis), and author-broken layout
(flat input is what makes oracle 1 two-directional — though oracle 4 does not
need flat input, since elm-format answers for any shape, so generating broken
variants is now possible; not done yet).

### Predicate/renderer agreement audit

Every other check in this repo is a **self-consistency** check — fixture diff,
AST equivalence, idempotency, both fuzzers. Output that is wrongly laid out but
deterministic, AST-equivalent and idempotent passes all of them. This audit is
the missing oracle: it checks the layout predicates against the renderer itself.

Several predicates in `Render/NodeClassify.gren` answer "does this subtree force
a hard break?" *before* rendering, so callers can lay out the code around it.
Each is a hand-written mirror of the renderer, and nothing forces them to agree.
The audit checks, per LPT node:

    predicate node == True   ==>   the node's own box renders multi-line

```bash
cd gren-format-lib/tests
./audit-predicates.py -j 12                              # whole corpus
./audit-predicates.py -v testfiles/Formatter/Foo.formatted.gren
```

**Rebuild the `gren-format` app first** — it shells out to `--audit-predicates`.

Findings are split into **root** and **propagated**: `subtreeHasVerticalBox`'s
fallback arm is `Array.any subtreeHasVerticalBox children`, so one wrong answer
at a leaf makes every ancestor wrong too. Only root findings are a work-list;
propagated ones disappear when the node below them is fixed.

Under-approximation is deliberately not reported — these predicates claim only
the *unconditional* breaks, and a node can still break for reasons they do not
model (most often the author's own `forceVertical` layout).

The audit itself is `src/Formatter/Audit/PredicateAgreement.gren`.

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
node ../gren-format/app --show-first MyFile.gren   # shows first formatting, to help debug non-idempotent cases
node ../gren-format/app --pre-ast    MyFile.gren   # parsed AST + context as JSON
node ../gren-format/app --pre-context MyFile.gren   # just the parse Context (comments) as JSON
node ../gren-format/app --lpt        MyFile.gren   # Logical Printing Tree as JSON
node ../gren-format/app --box        MyFile.gren   # the Box tree each decl renders to, as a JSON array
```

`--lpt` is the most useful debug flag for comment-placement and layout bugs.

## Formatter architecture

Pipeline: `Src.Module + Ctx.Context → LPT → Box → String`

```
Formatter                              entry point: prettyPrint
    Formatter.Logical                  logical-stage entry (module Formatter.Logical, file Logical.gren): runs lptFromAst then the comment/sort/blank-line passes
        Formatter.Logical.MakeLogical    AST → LogicalPrintingTree (lptFromAst — one OriginalRows per declaration)
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
