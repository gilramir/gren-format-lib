# CLAUDE.md — gren-format-lib

`gilramir/gren-format-lib` is a Gren **package** (`platform: common`) holding the
formatter library. All formatter source is in `src/Formatter/`. It also hosts
three AST utilities in `src/Compiler/`, moved here from `compiler-common`
because only this tooling uses them: `Compiler.Ast.Compare` (semantic AST
equality), `Compiler.Ast.Source.Json`, `Compiler.Parse.Context.Json`.

The consumer is `../gren-format/` — the standalone CLI, and the only thing that
builds an executable. This package is a library; `tests/` is a separate Gren app
that depends on it locally.

## Build & check

```bash
cd gren-format-lib
devbox run -- gren make Formatter          # module NAME, not a file path
```

## Tests

The main gate — fixtures, AST equivalence, idempotency, plus the
render-invariant and divergence-index checks:

```bash
cd gren-format-lib/tests && ./run-tests.sh
```

It recompiles against `src/` directly, so editing formatter source and re-running
it is enough. Fixtures live one directory per suite under `tests/testfiles/`;
add `<Name>.dirty.gren` + `<Name>.formatted.gren`, then an
`assertPrettyIn fsPerm "<SuiteDir>"` line in `tests/src/Test/Formatter/Format.gren`.

The other gates (all in `tests/`, all documented in
[`docs/testing.md`](docs/testing.md) — what each guards against, what it checks,
where its code lives):

| Gate | What it covers |
|---|---|
| `fuzz-idempotency.py` | a comment in every inter-token gap; `--run N` / `--mix-pairs` for runs at ONE gap; `--pairs` for two comments at TWO gaps |
| `check-decision-stability.py` | *which* layout decision moved between two formats (same flags) |
| `repro.py <fixture> <kind> <gap>` | reproduce one finding from either gate's label |
| `matrix-syntax.py` | construct × context matrix; `--comments` adds the comment axis |
| `gen-random.py` / `fuzzrun.py` | random-module property testing; long/distributed sweeps |
| `fuzz-whitespace.py` | output survives whitespace perturbation of the input |
| `audit-predicates.py` | predicates claiming a break the renderer does not emit |
| `check-render-invariant.py` | `Render/*` never re-derives placement from source rows (also run by `run-tests.sh`) |
| `fuzz-project.py` | the modes that WRITE FILES (no-arg project run, paths, `--remove-unused-imports`) |
| `corpus-check.py` | `--show` over real published packages — the one gate whose inputs nobody here chose |
| `bench-scaling.py` | timing only, never fails — how a shape scales; see the 2026-08-11 measurement in `docs/testing.md` |
| `pathological-nesting.py` / `pathological-other.py` | the edges: nesting depth (bisected to the boundary), and size/shape/unicode/CRLF probes |

Pass `-j 12` — this machine has 16 cores and the fuzzers default to `-j 2`.

The five `tests/_run_*.py` are **instruments, not gates** — they answer a
question (is this pile of findings a bug, or is the instrument asking the wrong
one?) and guard nothing. Nothing runs them automatically and their exit status
means nothing; `docs/testing.md` says what each answers and which part is meant
to be reused.

**Rebuild the CLI first** (`cd ../gren-format && ./build.sh`): every python gate
shells out to the built `../gren-format/app`, so a stale binary tests the wrong
code. Never rebuild while a fuzzer is running.

A finding whose cause is a known upstream parser bug is labelled
`[known: compiler-common#NN]` and still counted — gates label, never subtract.
Editing a probe or narrowing coverage to recover a green is the one thing not to
do here.

`fuzz-idempotency.py` exits non-zero on an **unlabelled** finding, not on any
finding: the labelled ones are registered in `tests/idempotency-known-baseline.json`
(keyed by the label `repro.py` takes) and forgiven. An upstream-classifying
finding that is *not* registered fails, and so does a registered one that has
stopped reproducing — so a regression cannot hide behind the automatic
classification, and a fix cannot leave a stale exemption. Re-register with
`--update-known-baseline` after a deliberate change. Before this the gate failed
on any finding, ran permanently red, and hid eight findings of a real bug among
the upstream ones for weeks.

## Inspecting formatter internals

```bash
node ../gren-format/app --show       MyFile.gren   # formatted output (+ all checks) to stdout
node ../gren-format/app --show-first MyFile.gren   # first pass only — for non-idempotent cases
node ../gren-format/app --lpt        MyFile.gren   # Logical Printing Tree as JSON
node ../gren-format/app --box        MyFile.gren   # the Box tree per declaration
node ../gren-format/app --pre-ast / --pre-context / --post-ast / --post-context
node ../gren-format/app --decisions  MyFile.gren   # which decisions differed between two formats
```

`--lpt` is the most useful flag for comment-placement and layout bugs.

## Architecture

Pipeline: `Src.Module + Ctx.Context → LPT → Box → String`

```
Formatter                          entry point: prettyPrint
    Formatter.Logical              runs lptFromAst, then the finishing passes
        …MakeLogical               AST → LPT (+ InsertExpressions / InsertPatterns /
                                   InsertTypes / LPTHelpers / BinopPrecedence)
        …Comments                  re-attaches comments from the parse context
        …SortSymbols               sorts exposing lists + import groups
        …VerticalSpace             blank lines between top-level items
    Formatter.Render               maps each RootBox child through the Box renderer
        …MakeRenderBox             LPT → Box: recursive dispatch + per-construct renderers
                                   (+ BinopLayout / CommentBox / FlowAssembly /
                                    NodeClassify / BoxOps — knot-free helpers only,
                                    since Gren forbids circular imports)
        …BackwardPipeline          the `<|` cluster; re-enters the recursion via a
                                   `Renderers` record instead of importing back
        …Box                       elm-format's Box IR (Line/Box, Tab tab stops, prefix)
        …ElmStructure              port of the two ElmStructure.hs combinators we use
        …FlowPolicy                shared inline/break decision layer

Formatter.Audit                    off to the side, not in the pipeline
    …DecisionTrace                 --decisions: which layout decision moved
    …PredicateAgreement            --audit-predicates: a predicate claiming a
                                   break the renderer never emits
```

Three things to hold onto:

- Layout is **author-driven, not fit-driven** — no page width, no layout search.
  Each shape knows whether it renders inline or vertical, decided from the author's
  original rows (`forceVertical`). Indent step is 4 (`grenIndent`).
- Every top-level declaration becomes exactly one `OriginalRows` node directly
  under `RootBox`; comments and blank lines are added as *siblings* afterwards.
- A comment's placement is decided **once**, in `Comments.gren`, and stored as a
  `CommentRole`. `Render/*` must never re-derive it from source rows —
  `check-render-invariant.py` enforces that.

## Documentation

- [`docs/index.md`](docs/index.md) — the documentation index, and the long-form
  prose that used to be the bulk of the README (the packages site stores only
  `README.md`, so it is deliberately short and links here). Every link in the
  README must be an absolute URL for the same reason.
- [`docs/formatterRules.md`](docs/formatterRules.md) — what every rule does, with
  examples. Read first when reasoning about behaviour.
- [`docs/commentHandling.md`](docs/commentHandling.md) — comments, for the
  reader: what the parser hands over, the decide-once model, and the normative
  C1–C7 rules. [`docs/commentAlgorithm.md`](docs/commentAlgorithm.md) is the same
  ground for someone editing the code.
- [`docs/howItWorks.md`](docs/howItWorks.md) — a tour of the pipeline.
- [`docs/elmFormatComparison.md`](docs/elmFormatComparison.md) — every deliberate
  divergence from elm-format. `tests/testfiles/Divergence/` pins each entry.
- [`docs/knownLimitations.md`](docs/knownLimitations.md) — what fails, and why it
  is not ours to fix.
- [`docs/testing.md`](docs/testing.md) and
  [`docs/fuzzTesting.md`](docs/fuzzTesting.md) — the gates, in full.
- [`docs/addingSyntax.md`](docs/addingSyntax.md) — how to teach the formatter
  a new piece of Gren syntax.
- [`docs/llm/`](docs/llm/) — written for a model, not a reader:
  [`attic.md`](docs/llm/attic.md) holds approaches **tried and backed out**,
  work deferred and the measurement that settled it, and designs never built.
  Check it before redesigning a comment-placement or glue rule.
