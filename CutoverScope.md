# Change-1 Cutover Scope

Scoping document for retiring the legacy `Render.Doc` renderer in favour of the
`Render.Box` renderer (`MakeRenderBox.makePrettyLineBox`). Written after tranche
26 (commit `3afd9ea`). This is the plan to get from "Box covers 90%, guarded by
Doc" to "Box is the only renderer".

## Where we are

`MakeRender.makePrettyLine` is a **self-verifying strangler**: it renders each
top-level item via BOTH Box and Doc and only *uses* the Box output when it is
byte-identical to Doc; anything else falls back to Doc. Cutover = delete the Doc
renderer and this equality guard, making `makePrettyLineBox` the sole path.

Corpus census (all `.formatted.gren` fixtures, per top-level root child):

| bucket | count | meaning |
|---|---|---|
| **ok** | 2250 | Box renders and byte-matches Doc |
| **err** | ~245 | Box returns `Err` → construct not ported yet |
| **mismatch** | ~7 | Box renders `Ok` but differs from Doc → a divergence |
| total | 2502 | |

(1138 of the 2250 are top-level `EmptyLine`; the real frontier is ~1112 / ~1364
non-EmptyLine items covered.)

To cut over, **every** item must render via Box — either byte-matching Doc, or
via an *intentional* divergence we consciously adopt (and regenerate the fixture
for). So the work is: (A) drive `err` → 0 by porting constructs, (B) resolve the
`mismatch` items as adopt-vs-fix decisions, (C) delete Doc + guard.

## Phase A — port the remaining `err` constructs (~245 items)

Ranked by item count (biggest ROI first). Each is a strangler tranche exactly
like tranches 1–26: port in `MakeRenderBox`, keep `trust-Box-always` at the known
5 failures, run both fuzzers, measure, commit.

1. **Exposing-list paren form — 49 items.** `module … exposing (a, b, c)` and
   `import … exposing (…)`. Currently `makeLiteralBox`/`bracketPunc` has no
   `ListParen`, so any real exposing list Errs. The list is *already sorted and
   grouped* in the LPT (`Formatter.Logical.SortSymbols`), so this is a rendering
   port only — mirror `MakeRender.makeExposingLineDoc` / `makeExposingListDoc`
   (a bracketed group `( … )` with `,`-prefixed continuation, all-or-nothing
   vertical). **Biggest single unlock left.**

2. **Non-leaf function/port signatures — 29 items.** `makeSignatureBox` handles
   only flat *leaf* signatures; a signature with a nested type (record, paren,
   function type) Errs (`box: non-leaf signature node`). Needs the type-node
   renderer (already exists for type aliases — `makeTypeAliasBody`) wired into
   the signature segments.

3. **`construct not ported` in `makePBox` — 23 items.** The unhandled `LPBox`
   expression types are **`Glue`** (postfix accessor glued after a bracketed
   base — mirror `makeGlueDoc`), **`AlignedFlow`** (`R.align` of an inline flow),
   and in-flow **`DocComment`**. Split by which dominates once (1) and (2) land.

4. **Doc comments (`StDocComment`) — 14 items.** Top-level `{-| … -}`. The
   dispatch Errs (`stype not ported`). Needs single-line + multi-line
   normalization (can reuse `multiLineBlockCommentBox`'s reindent for the body;
   doc comments have their own leading-`|` rule — check `makeCommentLineDoc`'s
   doc-comment arm).

5. **Multi-line forward-pipeline steps — 9 items.** `renderPipelineStep`
   requires single-line steps; a step with a multi-line argument (which
   elm-format relocates) falls back. Mirror the Doc's `renderPipelineStepNode`
   relocation.

6. **Comment-bearing unions — 6 items.** `makeUnionBodyBox` Errs on a comment
   among variants; needs the same comment-sibling handling as
   `renderWhenBranchesBox` (tranche 24).

7. **Comment tail — ~20 items, ~+1 each.** The long tail already seen in tranches
   20–26: `non-standalone multi-line block comment in flow` (4),
   `multi-line block comment not ported` in container contexts (4),
   `mid-flow own-line block comment` (4), `multi-line OpAndRhs` (3),
   `comments not ported (tranche 1)` in a funcDecl header (3),
   `multi-line when-pattern` (1), `multi-line prefix-glue` (1),
   `multi-line operand in non-vertical binop` (1),
   `inline token gluing to a preceding block` (1 — a `{…} =` record-pattern arg).
   Diminishing returns; do last, or leave for post-cutover polish if the item is
   genuinely rare.

## Phase B — resolve the ~7 `mismatch` divergences (product decisions)

These are items where Box renders `Ok` but diverges from Doc. Each must be an
*intentional* adopt (regenerate the fixture) or a Box bug to fix.

- **`BareIfListItem` (B1) — ADOPT.** Box lines `else` up under `if` and doesn't
  over-indent a bare `if` list item; Doc has the bug. Box is correct per
  elm-format. At cutover, regenerate the fixture to Box's output.
- **`UniformRecordArray` + `UniformUpdateArray` (2) — DECIDE.** An array whose
  items are records (or record updates) couples them: all inline or all expanded
  (the `P.choice` coupling in `MakePretty`). Box's `E.groupBox` decides per item,
  so it can leave a mixed layout. This is the ONE genuine *structural* gap — Box
  has no whole-array choice. Options: (a) accept per-item Box behaviour (simpler,
  regenerate fixtures), or (b) add an array-level "all children forced vertical
  together" pass in `makeLiteralBox`. Recommend (a) unless elm-format actually
  couples (verify against elm-format first — see README `elm-format comparison`).
- **~4 remaining mismatch items** (undercounted by the per-fixture sweep because
  they sit in fixtures that *also* Err — `KitchenSink` nested-record-type,
  `KitchenComments` comment-placement, possibly 2 more). **Review individually**
  once Phase A shrinks those fixtures' err items: each is either a Box bug to fix
  (e.g. the nested-record-type under-indent) or another adopt. Do NOT cut over
  until each of the 7 is explicitly classified.

## Phase C — the cutover itself

Once `err = 0` and every `mismatch` is classified/adopted:

1. Regenerate every fixture whose adopted divergence changes its output
   (`BareIfListItem`, the uniform-array pair if we adopt per-item, etc.).
2. In `MakeRender.makePrettyLine`, drop the dual-render guard — call
   `makePrettyLineBox` directly and surface its `Err` (no Doc fallback).
3. Delete the Doc renderer: `makePrettyLineDoc` and its whole `makePDoc` /
   `buildFlowDoc*` subtree in `MakeRender.gren`, plus `Render/Doc.gren` if nothing
   else needs it. (Check the `--render-doc` debug flag and `lptToRenderDocJson`.)
4. Delete the strangler scaffolding: the coverage-instrumentation recipe, the
   `tests/src/Change1/` slice modules + the "Change 1 slice" guard test.
5. Full gate sweep: effectful, both fuzzers, and an `elm-format` audit pass on a
   real corpus to confirm the adopted divergences match elm.

**Gating product decision (unchanged from `Change1Scope.md`):** cutover adopts
elm-format's width-dependent indentation (Tab tab-stops + prefix exact-width
padding), reversing the old uniform-4 preference. That reversal is already baked
into every ported Box construct; cutover just makes it the only behaviour.

## Effort estimate

Phase A is the bulk: ~7 tranches, front-loaded (exposing-lists + signatures alone
retire ~78 of the ~245 err items). Phases B and C are small but require the
elm-format verification and the fixture regeneration. The `err` count is the
honest burndown metric — instrument `makePrettyResult` to print
`ok=/err=/mismatch=` per file and sweep the corpus after each tranche.
