# Comment/Layout Architecture Plan: stop re-deriving, start storing

**Status:** Change A (Phases 1–3) AND Change B landed in full — the plan is
complete, including the final phase's enforcement gate. No `Render/*` code
re-derives a comment-placement or verticality decision from source rows; layout
is decided once from author-intent flags and the rendered box shape. All gates
green, every fixture byte-identical (2026-07-19), on branch `comments`. This
document is a self-contained plan meant to be handed to an implementer with no
prior conversation context.

**What this document is now.** It is the *rationale and bug history* behind the
architecture — why placement is stored rather than re-derived, and what it cost
to learn that. It is not the normative statement of comment behaviour, and it is
not a description of the current code:

- **What the formatter is trying to do** — rules C1–C6, with worked examples —
  lives in [`docs/commentHandling.md`](docs/commentHandling.md#the-rules-in-english).
  Those rules were written down 2026-08-01, twelve days after this plan landed;
  nothing below states them.
- **What the code does today** is the source. `CommentRole`'s docstring in
  `src/Formatter/Logical/LogicalPrintingTree.gren` is the authority on the roles
  — the set has grown from the five §5.1 proposed to **seven**, and §5.1 is kept
  as written for the record rather than updated in place. See
  [Roles since the plan](#roles-since-the-plan-2026-08-01) for the delta and the
  fixed-point argument each new role owes.

Read this document for *why*; read those two for *what*.

## Progress log

- **Change B (observe rendered shape) — DONE.** Most of Change B was already in
  place: the verticality decisions observe the rendered box, not a mirror
  predicate — bracketed literals via `ElmStructure.groupBox`'s `B.allSingles`,
  record updates via `contentVertical = Array.any (not << isSingleLine)
  fieldBoxes` (the `27e8903` crash site), and binop chains via
  `anyOperandRendersMultiline` (`7dfa132`). `checkContentVertical` is *not* a pure
  predicate but the sound author-vs-synthesized flag §8 describes: it gates
  whether the renderer consults the rendered box shape (`.isBlock` in the
  `AcrossOrVertical` arm, `isSingleLine innerBox` in `parenGenericFallbackBox`) —
  synthesized wraps opt out. Work done:
  - Retired the **redundant** pure binop predicate: `makeBinopBox` OR'd
    `bracketOperandForcesVertical` (a source-row shape predicate) with the
    render-based `anyOperandRendersMultiline`; the former's every hit also renders
    multi-line, so dropping it was byte-identical. Deleted it plus
    `operandIsMultilineBracketLiteral` / `operandCommentForcesOpen`, and the now
    dead `nodeSpansRows`.
  - **Retired `subtreeHasVerticalBox` / `bracketOpenGate` by restructuring
    `stepBodyBox`** — its sole consumer, `isMultilineContentParenBlockBox`, was
    used in two pipeline-step sites: (a) the trigger split, where it is subsumed
    by the render-based `flowChildForcesVerticalBox`; and (b) the trigger-renderer
    selection, where it picked `makeMultilineParenArgBox` — which turns out to be
    byte-identical to `makePBox`'s own paren path
    (`makeParenBlockBox → parenGenericFallbackBox → wrapParenVerticalPadded
    (buildFlowBoxInline 0 …)`), so the trigger renders via its own box and the
    special renderer is redundant. Deleted `subtreeHasVerticalBox`,
    `bracketOpenGate`, `isMultilineContentParenBlockBox`, and
    `makeMultilineParenArgBox`.
  - The `audit-predicates` gate now covers only `isMultilineLambdaParenBlockBox`
    — a structural lambda-shape query (paren + lambda head + `IndentedBlock`
    second child), *not* a shape prediction from rows — so nothing it audits can
    lie about verticality anymore. **§8's "`subtreeHasVerticalBox` and the
    `subtreeHasComment`-as-layout-input family are gone" is met.**

## Progress log (Change A)

- **Phase 3 (cut the remaining comment sites over) — DONE.** Every remaining
  comment-*placement* site now reads the stored `CommentRole` instead of
  re-deriving trailing/leading from source rows:
  - `commentBracketListBox` (bracket-list items) + `literalCommentsRideFlatLine`
    (flat-vs-open gate = "every comment child is `RidesInline`").
  - `makeUnionBodyVerticalBox` (union variants) and `renderWhenBranchesBox`
    (when branches) — the latter guarded on `pending` so a same-row comment run
    leading a branch stays together.
  - `makeCommentLineBox` (top-level comment runs); `commentNodeToBox` simplified
    to return `Box`.
  - The classifier gained a permissive **bracket branch** (matching
    `commentBracketListBox`'s any-item same-row rule) and permissive glue-rows
    for **union-variant** `AcrossOrVertical` (`= Ctor`/`| Ctor`) and **`WhenBranch`**
    predecessors — the three "list-like" contexts whose glue rule is looser than
    the generic flow's.
  - `RidesInline` lost its `sameRowNext` requirement (it is observed only by
    `literalCommentsRideFlatLine`; every other consumer glues it like
    `TrailsPrevious`). The unused `InsideBracketClose` role was removed —
    `CommentRole` is now 4 constructors.
  - `--lpt` prints each comment's role. **End state at the time:** a grep of
    `Render/*` for row accessors returned only Change-B shape-prediction
    (`nodeSpansRows`, …), signature-segment layout, `isElidedArrow`'s zero-width
    check, and one `lpnMaxRow >= 0` "has real content" guard — **no
    comment-placement row re-derivation remains.** (Change B has since deleted
    the shape-prediction hits too, and `tests/check-render-invariant.py` now
    enforces the grep — see [Where it ended
    up](#where-it-ended-up-2026-08-01).)

- **Phase 2 (cut the flow paths over to roles) — DONE.** All three flow paths
  the plan names now take the comment glue/own-line decision from the stored
  `CommentRole`, not source rows:
  - `FlowPolicy.decide`'s `LineCommentItem`/`BlockCommentItem` arms +
    `commentPlacement` (a comment glues iff `TrailsPrevious`/`RidesInline`).
  - `MakeRenderBox.factsFor` — its ~90-line per-box-kind `startRow`/`startRowLine`
    table is gone; non-comment items are just `LeafItem`.
  - `FlowAssembly.assembleBrokenWithComments` (broken binop / broken call) —
    glues via `commentGlues` (role); `FlowItem.startRow` removed.
  - The dead row machinery is deleted: `FlowState` is `{ separator }`;
    `ItemFacts` carries no rows; `lastRenderedRow`/`nodeStartRow` gone.
  - The classifier gained faithful per-box-kind glue-row helpers
    (`prevLineGlueRow`/`prevBlockGlueRow`/`bracketRendersMultiline`) reproducing
    `factsFor`'s tables from pristine rows. A same-row block comment after a
    multi-line `ParenBlock` glues onto its `)` — unifying the broken-flow rule
    (glued after any multi-line item) with the generic rule byte-identically.
  Bugs found+fixed while driving to green: glue-at-`FirstItem` must be `AsFirst`;
  `nodeIsElided` scoped to the zero-width `->` only (the `let … in` trailing
  comment must stay own-line); single- vs multi-line bracket block-glue.

- **Phase 1 (Change A foundation) — DONE.**
  - Added `CommentRole` (`TrailsPrevious | LeadsOwnLine | RidesInline |
    InsideBracketClose | Standalone`) to `LogicalPrintingTree`; changed the
    `SingleLineComment` / `BlockComment` constructors to carry
    `{ loc, role }`; added `commentRole` accessor and `lpnSetCommentRole`.
    Every consumer updated (mechanical `{ loc }` destructure).
  - Implemented the classifier in `Comments.gren` (`classifyCommentKind` +
    `isBracketContainerBox` / `lastRenderedRow` / `nodeIsElided` /
    `blockGlueAllowed`), assigning the role at the single splice point in
    `insertAmongChildren` (plus `Standalone` for a fresh top-level slot and
    `InsideBracketClose` for a bracket-container child). Roles are decided from
    the pristine parse rows, §5.3.
- **Phase 2, construct 1 (binop) — DONE.**
  `BinopLayout.splitTrailingOwnLineComments` now decides own-line vs inline from
  `commentRole == LeadsOwnLine` instead of the `contentRow` row arithmetic
  (deleted). This is the plan's cross-check done as *direct consumption*: driving
  the idempotency + gen-random sweeps to green surfaced and fixed two classifier
  bugs — (a) a comment after a *multi-line* block comment must lead its own line
  (FlowPolicy's `AlreadyTerminated`), (b) a binop trailing comment's reference
  row is the last *non-comment* operand (mirrors `contentRow`), so binop
  containers get their own branch in `classifyCommentKind`. All gates
  byte-identical afterward.
- **Written when Phase 4+ (Change B) was still outstanding**, and left in place
  as the record of that moment — Change B has since landed in full (see the
  first progress entry, which is chronologically *later* than this one). The
  Change-A goal — no `Render/*` comment-placement decision reads source rows —
  is met. Sites that were *already* row-free (kind/structure only) and so needed
  no change: `stepNeedsCommentedLayout` (pipeline), `pairLeadingRecordComments`,
  `pairTypeRecordComments` (since renamed `pairLeadingComments`, and it now
  reads the stored role too), `typeHasCommentBracket` (all classify by comment
  kind / bracket structure, not rows). The remaining row-reads in `Render/*` are
  Change B's target (shape prediction) plus genuinely-structural uses.

## Where it ended up (2026-08-01)

The plan is a year-zero snapshot; this is the scorecard against §8's definition
of done, twelve days on. Four met, one met differently, one only half met.

| §8 wanted | Today |
|---|---|
| All Phase-0 gates green, fixtures byte-identical, parity baseline unchanged | **Met**, and the gate set has grown — the syntax matrix gained a comment axis (`--comments`, ~38.5k cells) that crosses syntax with comments and asks elm-format. It found 424 hard failures on its first run, all fixed. |
| `FlowState` / `ItemFacts` / `FlowItem` have no row fields | **Met.** |
| Near-zero row-read decision sites in `Render/*` (from ~150, `MakeRenderBox` ~98 alone) | **Met, and enforced** — `tests/check-render-invariant.py` greps `Render/*` and fails on any row/position accessor outside a named allowlist of structural uses, each carrying its reason. `run-tests.sh` runs it first. |
| `NodeClassify` keeps only structural queries; `subtreeHasVerticalBox` and the `subtreeHasComment`-as-layout-input family are gone | **Half met.** `subtreeHasVerticalBox` is gone. `subtreeHasComment` is not — it has five callers in `MakeRenderBox` (binop chains ×2, signature segments ×2, pipeline). See below. |
| `checkContentVertical` gone or reduced to "synthesized parens opt out" | **Met by reduction**, not deletion. It is exactly the author-vs-synthesized gate now: a genuine `Src.Parens` consults its rendered content shape, a formatter-synthesized wrap does not. |
| Every classifier arm documented with the fixture that pins it | **Met** — `classifyCommentKind`'s doc comment names a fixture per branch. |

**Why `subtreeHasComment` is allowed to survive.** It is not a shape prediction.
It answers "is there a comment anywhere in this subtree", which routes to a
different *renderer* — it never claims a subtree will break. Crucially it is
reparse-invariant in a way a row-read is not: reformatting moves rows, but it
neither adds nor removes comments, so the second format routes the same way. The
family §8 wanted gone is `subtreeHasComment`-**as-layout-input** — "there is a
comment here, therefore this breaks" — and that is gone. A presence test used to
pick a code path is a different thing, and the enforcement gate agrees: it greps
for row accessors, and this reads none.

The one cost is real and worth naming: a routing test forks the comment-bearing
and comment-free paths, which is exactly what
`feedback_fallback_layout_consistency` (§9) warns about. Every one of the four
C4 bugs found in interview rounds 3–5 was a fork like this drifting — the
comment path disagreeing with the comment-free path about indent or grouping.
The routing is sound; keeping the two arms in agreement is a standing obligation,
and the test for it is in
[C4](docs/commentHandling.md#c4--a-comment-changes-where-the-rows-fall-and-nothing-else):
render the construct again with the comment deleted and compare.

## Roles since the plan (2026-08-01)

§5.1 proposed five roles. There are now **seven**, and the churn is itself the
evidence for the architecture: each addition was one classifier arm plus one
renderer arm, with no per-site fixed-point argument to redo.

`InsideBracketClose` was **removed** — a comment inside a bracket container is
not a distinct role, since the classifier's bracket branch tags it
`TrailsPrevious` / `RidesInline` / `LeadsOwnLine` like any other, which is all
`commentBracketListBox` needs. Three were **added**:

| Role | Added for | Example |
|---|---|---|
| `LeadsNext` | rule **C2** — the comment belongs to the sibling *after* a separator the parser did not record | `[ 1 {- c -}` ⏎ `, 2 ]` → `[ 1` ⏎ `, {- c -} 2` ⏎ `]` |
| `TrailsHead` | a container's **head** is not one of its children, so `TrailsPrevious` has nothing to reach — today exactly a record update's base | `{ rec -- c` ⏎ `\| a = 1 }` keeps the `--` on the base's row |
| `LeadsInline` | the front-of-line mirror of `TrailsPrevious`: a block comment glued before a declaration's first token, travelling with it through the import sort | `{- c -} import Qux` |

Each owes the §5.4 fixed-point argument, and `LeadsNext`'s is the one that bit:
it renders on the **next** item's row, so a classifier keying only on "same row
as the previous item" reads its own output back as an own-row comment and drops
it to its own line. `[ 1 {- c -}` ⏎ `, 2 ]` oscillated exactly that way. The rule
keys on **either** adjacent item's row. This is §5.4's obligation restated: a role
is a fixed point only when the row it is *decided from* equals a row it can
*render on*.

The current role set, with each one's meaning and the reparse argument, is the
`CommentRole` docstring in `src/Formatter/Logical/LogicalPrintingTree.gren`. The
renderer-side consumer table is in
[`docs/commentHandling.md`](docs/commentHandling.md#the-renderer-side--consuming-the-role).

---

**Original plan status:** proposed. Kept below verbatim.

**Scope:** `gren-format-lib` only (`src/Formatter/`). No behavior changes are
intended — every current fixture, fuzzer, matrix cell, and parity-baseline
entry must still pass byte-identically. This is a refactor that changes *where
two questions are answered*, not what the answers are.

---

## 1. The problem

The week of 2026-07-17..19 produced eight separate fixes for comment-related
bugs (crashes and idempotency oscillations). Each fix was small, correct, and
local — and each was the same fix, re-proven for one more code path. The root
cause is architectural and has two faces:

The LPT (`Formatter.Logical.LogicalPrintingTree`) stores comments as
**positioned sibling leaves** (`SingleLineComment`/`BlockComment` nodes carrying
only a `Located String`), and stores layout intent as **source-row metadata**
(cached `firstPos`/`lastPos`/`minRow`/`maxRow`/`lastBracketEnd` bounds, plus
`forceVertical` flags). Consequently the render layer must *re-derive*, at
render time, two facts that could have been decided once:

1. **The comment's role.** "Does this comment trail the previous item on its
   line, lead the next item on its own line, sit inside a bracket before the
   close, or stand alone?" — answered by comparing the comment's source row
   against "the row the previous item ended on", where that row is
   reconstructed differently per box kind, in at least eight independent
   places (§3).

2. **The subtree's rendered shape.** "Will this node render multi-line?" —
   answered by hand-written mirror predicates over source rows and node kinds
   (`Render/NodeClassify.gren`: `nodeSpansRows`, `subtreeHasComment`,
   `subtreeHasVerticalBox`, …) that nothing forces to agree with the renderer.
   The `audit-predicates.py` gate exists *only* because this divergence is
   otherwise invisible.

Both re-derivations read **source rows**, and source rows are only globally
consistent on the first parse. The moment formatting moves anything, every
row-based render decision must be independently proven a reparse fixed point —
or it oscillates (col N ↔ col 0 comment drift) or crashes (a code path assumed
the shape a predicate promised). Every one of the eight fixes was such a proof,
for one arm.

### The invariant this plan establishes

> **After `Formatter.Logical.Comments` runs, no code in `Render/*` reads
> source rows or positions to make a layout or comment-placement decision.**
>
> The only source-derived layout inputs are (a) `forceVertical`-style author
> intent flags, captured at LPT build time, and (b) comment **roles**, captured
> at comment-insertion time. Everything else the renderer needs, it observes
> from already-rendered `Box` values (`isSingleLine`).

Author-intent flags are sound fixed points by design: flat output reparses
flat, vertical output reparses vertical. Roles are sound fixed points if the
classifier's rule matches the renderer's output (§5.4 states the proof
obligation once, instead of once per render arm).

What the invariant buys. A render-time row test asks "is the comment on the same
row as the thing before it?" of rows that the *previous* format already moved —
so the answer can flip on every pass. Schematically, the shape of the bug this
plan removes:

```gren
-- format 1                -- format 2                -- format 3
[ 1                        [ 1                        [ 1
  {- c -}                  , {- c -} 2                  {- c -}
, 2 ]                      ]                          , 2
                                                      ]
```

Neither pass is wrong on its own terms; they disagree because each reads the
previous pass's output as if it were the author's. Storing the answer once, from
the rows the author actually wrote, is what makes format 2 equal format 1. (All
three shapes are stable today — this is the disease, not a current output.)

---

## 2. Evidence: the two bug classes

All commits are in `gren-format-lib`. Read these diffs first; they are the
concrete shape of the disease.

### Class 1 — comment-role re-derivation at render time

| Commit | What it patched |
|---|---|
| `6f1a6df` | Split `FlowPolicy.FlowState.prevRow` into `prevRowBlock`/`prevRowLine` — a threaded two-row state machine so a trailing `--` can glue in strictly more places than a trailing `{- -}`. |
| `f55d4f9` | Invented `lastRenderedRow` (max of `lpnLastPos` and `lpnLastBracketEnd`) in `MakeRenderBox` and plumbed it into three separate `ItemFacts` arms (`AcrossOrVertical` bracket branch, its `Nothing` branch, new `ParenBlock` arm). Also fixed `BinopLayout.splitTrailingOwnLineComments` to distinguish same-row from later-row `--`. |
| `034503c` | Gave `EmptyBracketed` a `lastPos` in `selfBoxBounds` so `subtreeEndsBefore` works for `( {} ) -- c`. |
| `1f66117` | Trailing comment on a paren-based postfix access (`Glue`) crashed. |
| `d0f05c2` (half) | `stepNeedsCommentedLayout` made row-stable for backward `<|` bodies with inline block comments. |

Every fix teaches one more render site the correct answer to "what line did the
previous item really end on". The question itself is the bug: it is asked at
render time, from source rows, per box kind — when it could be answered once at
comment-insertion time (where rows are pristine and the neighbor nodes are in
hand) and **stored**.

### Class 2 — predicted shape diverging from rendered shape

| Commit | What it patched |
|---|---|
| `7dfa132` | Paren binop operand chain-break decided from the **rendered box** (`anyOperandRendersMultiline` + `isSingleLine`) instead of `nodeSpansRows`/`subtreeHasComment`. The commit comment in `BinopLayout.operandCommentForcesOpen`'s new `ParenBlock -> False` arm states the principle explicitly: the pure predicate *cannot see* what the render can. |
| `01745b2` | Record-field lambda with always-breaking body oscillated glue ↔ drop; fixed with a content test (`exprAlwaysBreaks`) instead of a row-derived `IndentedBlock` choice. |
| `27e8903`, `6021c73` | Record-literal / record-update fields with comment-led multi-line binop values **crashed** ("unreachable soft-glue") — a code path assumed the flat shape a predicate promised. |
| `d0f05c2` (half) | Backward `<|` flat/broken decision. |

The project already learned the lesson locally (see the memory note from the
`7dfa132` sweep): *an under-approximating "renders multiline" predicate
oscillates — render and check `isSingleLine`.* This plan makes that the rule
instead of the exception.

---

## 3. Inventory: where the re-derivation lives today

Sites that re-derive **comment role** from rows (Class 1 surface area):

1. `Render/FlowPolicy.gren` — `FlowState { prevRowBlock, prevRowLine, prevElided }`
   threaded state machine; `decide`'s `LineCommentItem` / `BlockCommentItem`
   arms; `commentPlacement`.
2. `Render/MakeRenderBox.gren` `assembleFlowImpl` — constructs `FP.ItemFacts`
   per box kind: `startRow` / `startRowLine` per arm, `lastRenderedRow`,
   `bracketFacts`.
3. `Render/MakeRenderBox.gren` `assembleFlow` / `flowItems` — builds
   `FlowAssembly.FlowItem { startRow, comment : Maybe {…} }`.
4. `Render/FlowAssembly.gren` — `assembleBrokenWithComments`,
   `assembleBrokenCall`, `shouldGlueBox`, `pairTypeRecordComments` (all consume
   `startRow` and same-row comparisons).
5. `Render/BinopLayout.gren` — `splitTrailingOwnLineComments` (row compare vs
   `contentRow`), `operandCommentForcesOpen`, `bracketOpenGate`.
6. `Render/MakeRenderBox.gren` `commentBracketListBox` — comment placement
   inside bracketed lists.
7. Pipeline comment-peeling in the `Pipeline`/`PipelineStep` renderers
   (`stepNeedsCommentedLayout` etc.).
8. `Render/NodeClassify.gren` — `literalCommentsRideFlatLine`,
   `subtreeHasMultilineBlockComment`, `typeHasCommentBracket`.
9. `Render/FlowPolicy.gren` — `pairLeadingRecordComments`.

Sites that **predict rendered shape** from rows/kinds (Class 2 surface area):

- `Render/NodeClassify.gren`: `subtreeHasVerticalBox`, `subtreeHasComment`,
  `nodeSpansRows`, `rowsSpanMultiple`, `fieldValueDropsToOwnLine`,
  `signatureForceVertical` (partially), `isMultilineLambdaParenBlockBox`,
  `isMultilineContentParenBlockBox`.
- The `checkContentVertical` flags on `AcrossOrVertical` and `ParenBlock` in
  `LogicalPrintingTree.gren` — these exist purely as idempotency backstops for
  the predicates' under-approximation, and their long doc comments are a tour
  of the problem.
- `Render/MakeRenderBox.gren`: `softBlockChildForcesVerticalBox` consumers,
  `exprAlwaysBreaks` (the `01745b2` fix — already content-based, keep),
  `anyOperandRendersMultiline` (the `7dfa132` fix — already render-based, this
  is the model to generalize).

Quantitative smell: `MakeRenderBox.gren` alone has ~98 textual references to
row/position accessors (`lpnLastPos`, `.row`, `startRow`,
`lpnLastBracketEnd`, …). Target end-state: near zero (§8).

---

## 4. Reference architecture: elm-format (verified in its source)

Checked directly in `../elm-format/elm-format-lib/src/` — do the same before
trusting any claim here (project rule: verify against elm-format source, don't
curve-fit).

1. **Comments are attachment slots, not positioned siblings**
   (`AST/V0_16.hs`): every commentable position is typed —
   `C2 'BeforeTerm 'AfterTerm a` (comments before/after a term),
   `C1 'Inside ()` (inside empty brackets), and crucially `C1Eol`/`C2Eol`,
   which carry a dedicated **end-of-line comment field** (`Maybe String`) on
   the node itself. A trailing `--` is *data on its host*. Sequences carry
   `'BeforeSeparator`/`'AfterSeparator` slots. After parsing, source positions
   are discarded entirely; the renderer contains zero row arithmetic for
   comments.

2. **Multiline-ness is author-forced or observed, never predicted**
   (`AST/V0_16.hs` `newtype ForceMultiline`, `Box.hs:166` `allSingles`):
   layout inputs are a `ForceMultiline Bool` stored in the node (author
   intent, captured at parse) plus `allSingles`/`isLine` checks on
   already-rendered child Boxes. There is no mirror-predicate layer.

gren-format cannot discard positions entirely — `Comments.gren` needs them to
weave the parse-context comment stream back in, and idempotency checking
compares parse Contexts. But positions can and should stop crossing the
Logical→Render boundary as *decision inputs*.

---

## 5. Change A — comment roles, decided once in `Comments.gren`

### 5.1 The new type

> **Superseded.** Five roles were proposed; there are seven today, and
> `InsideBracketClose` is not among them. See [Roles since the
> plan](#roles-since-the-plan-2026-08-01); the live list is `CommentRole`'s
> docstring in the source. The proposal is kept as written below.

In `Formatter/Logical/LogicalPrintingTree.gren`:

```gren
type CommentRole
    = -- Glues to the end of the previous sibling's LAST RENDERED LINE,
      -- preceded by one space. For `--` this is always safe (runs to EOL).
      -- For `{- -}` this is only assigned where the block-glue rules allow
      -- (see 5.4). Renders `<prev-last-line> <comment>`.
      TrailsPrevious
      -- Renders on its own line at the current flow/body indent, before the
      -- next sibling. The default for own-row comments.
    | LeadsOwnLine
      -- Inline block comment that rides in the middle of a flat flow without
      -- forcing a break (`f {- k -} x`). Only ever a single-line `{- -}`.
    | RidesInline
      -- Sits between a container's last item and its closing bracket
      -- (placed there by commentInsideTrailingBracket /
      -- commentInsideEmptyBracket descent).
    | InsideBracketClose
      -- A top-level detached comment: its own OriginalRows at column 1.
      -- (Existing behavior of findOrCreateOrigRow; named so the renderer
      -- can stop inferring it.)
    | Standalone
```

Change the comment leaf constructors to carry it:

```gren
| SingleLineComment { loc : Located String, role : CommentRole }
| BlockComment { loc : Located String, role : CommentRole }
```

(`DocComment` keeps its current shape — doc comments are top-level-only and
already unambiguous.)

This is a **breaking change to the constructor shape**, which is good: the
compiler will enumerate every consumer, and each consumer is exactly a site
from the §3 inventory. Do not add a parallel constructor; change the existing
one so no site can silently keep using the old path.

### 5.2 Where classification happens

`Formatter/Logical/Comments.gren` already computes everything needed at the
moment it picks an insertion point:

- `insertAmongChildren node row col children` knows the comment's `(row, col)`,
  the previous sibling (the "last child that ends before the comment"), and the
  next sibling.
- `commentInsideTrailingBracket` / `commentInsideEmptyBracket` already decide
  the inside-bracket case.
- `findOrCreateOrigRow` already decides the top-level `Standalone` case.
- `boxKeepsTrailingCommentOutside` and `nextSiblingIsBoundary` already encode
  the boundary rules.

The change: at the point where the comment node is spliced into a child array,
compute its role and construct the leaf with it. The classification reads
**pristine parse rows** — the one place row arithmetic is legitimate, because
the whole point of `Comments.gren` is to interpret the original source.

### 5.3 Classification rules

These rules must reproduce today's *output* exactly (the gate: every fixture
byte-identical). They are a consolidation of the rules currently scattered
across the §3 sites — port them, don't reinvent them:

- **`--` (line comment):**
  - Same row as the previous sibling's last *rendered-relevant* position — the
    max of its last token and its closing bracket (this is exactly
    `f55d4f9`'s `lastRenderedRow`, computed here once, from pristine rows) —
    → `TrailsPrevious`.
  - Trailing an elided/synthesized token (`->`, `=`, `:` — the `prevElided`
    case in `FlowPolicy.decide`): → `TrailsPrevious` regardless of row (the
    synthesized token has no position; a row test would flip on reformat).
  - Otherwise → `LeadsOwnLine`.
  - A `--` can never be `RidesInline` (it consumes the rest of the line).

- **`{- -}` single-line block comment:**
  - Same row as the previous sibling **and** the previous sibling is a kind
    the block-glue rules allow (a positioned leaf token; a multi-line
    bracket's closing `]`/`}` — the cases where today's `prevRowBlock` gets a
    real row in `FlowPolicy`): → `TrailsPrevious`.
  - Same row as previous *and* same row as next, inside a flow that stays flat
    (both neighbors on the comment's row): → `RidesInline`. This is the
    kind-aware "inline `{- -}` rides the flat line" behavior
    (fixtures + memory `project_inline_comment_rides_flat_line`).
  - After a plain-token call, a lone `{}`/`[]`, a single-line bracket
    (`{ a = 1 } {- c -}`): → `LeadsOwnLine` — the deliberate cases where a
    block comment does NOT glue but a `--` does (`6f1a6df`'s two-row split,
    now expressed as two classification arms instead of threaded state).
  - Multi-line `{- … -}`: never `RidesInline`; `TrailsPrevious` only in the
    positions that glue today (see `FlowAssembly.assembleBrokenWithComments`'s
    doc: a multi-line comment glued after a single-line item reindents under
    it); else `LeadsOwnLine`.

- **Inside-bracket descent** (`commentInsideTrailingBracket` /
  `commentInsideEmptyBracket` fired): → `InsideBracketClose`.

- **Top level** (`findOrCreateOrigRow` created a fresh `OriginalRows`):
  → `Standalone`. The existing never-attach-trailing-at-top-level rule
  (README/`trailing-comment-drift.md` §13) is unchanged.

Where a rule above is ambiguous against current behavior, the current
behavior wins — recover it from the relevant §3 site and the fixture corpus,
and document the arm with the fixture name that pins it.

### 5.4 The fixed-point proof (once, here, instead of per render arm)

The idempotency argument the eight fixes each made locally becomes one global
argument. **The obligation, stated once:** a role is a fixed point when the row
the classifier *decides it from* is a row the renderer can *put the comment on*.
Every argument below is that sentence instantiated, and every violation of it —
including the one found after this plan landed — is a case where the two rows
were different.

- `TrailsPrevious` renders as `<prev item's last rendered line> <comment>`.
  On reparse, the comment sits on the same row as the previous item's last
  token (for `--`) or closing bracket — which is precisely the classifier's
  `TrailsPrevious` condition. Fixed point.

  ```gren
  [ apple -- the red one     -- decided from: apple's row
  , banana                   -- rendered on:  apple's row       same row ✓
  ]
  ```
- `LeadsOwnLine` renders the comment on its own row at the flow indent. On
  reparse its row differs from both neighbors → classified `LeadsOwnLine`
  again. Fixed point.

  ```gren
  [ apple                    -- decided from: a row of its own
  -- about banana            -- rendered on:  a row of its own  same row ✓
  , banana
  ]
  ```
- `RidesInline` renders mid-line between two same-row tokens → reparse sees
  the same → fixed point.

  ```gren
  f {- k -} x                -- both neighbours on the comment's row, before
                             -- and after ✓
  ```
- `InsideBracketClose` renders before the close bracket, inside → the descent
  guard re-fires. Fixed point (this is what `lpnBracketNode` already
  guarantees). *(This role was later removed — the bracket branch tags such a
  comment like any other.)*
- `Standalone` renders at column 1 — trivially stable (cannot drift).

**The role added later that failed this test, and how.** `LeadsNext` (rule C2)
renders the comment glued to the front of the **following** item — so it lands
on a row the classifier was not looking at:

```gren
-- you wrote                -- gren-format
[ 1 {- c -}                 [ 1
, 2 ]                       , {- c -} 2
                            ]
--  decided from: item 1's row
--  rendered on:  item 2's row      DIFFERENT ROW ✗
```

Reparsed, the comment is on item 2's row, so a classifier keying only on "same
row as the previous item" calls it an own-row comment and drops it to its own
line — the col-N ↔ col-0 oscillation this whole plan exists to remove, arriving
through a brand-new role. The fix is to key on **either** adjacent item's row,
which restores the invariant: the decision row is now a set that contains the
render row.

Any future comment bug is then a *classifier* bug with exactly one place to
fix, and `fuzz-idempotency.py` remains the gate that proves it. That has held —
every comment-placement bug since has been one classifier arm or one renderer
arm, never a fixed-point argument to redo across sites.

### 5.5 Render-side consumption (what each §3 site becomes)

The renderer's uniform contract:

- `TrailsPrevious` → take the previous item's **rendered Box**; if its last
  line is a `Line`, append `Space ++ commentLine`; if the previous box is
  multi-line, glue onto its last line via the existing `BoxOps` machinery
  (this operation exists today inside `assembleBrokenWithComments` — extract
  it as a single shared helper, e.g. `glueTrailingComment : Box -> Box -> Box`).
  **No row test.** The glue target is whatever line the box actually ends on —
  a render-side fact, correct even when layout moved things.
- `LeadsOwnLine` → own-line at the current flow indent, `MustBreak` after a
  `--`. Existing `CommentBox` rendering unchanged.
- `RidesInline` → an ordinary inline flow item.
- `InsideBracketClose` → the bracket renderers place it before the close
  (existing `commentBracketListBox` behavior, minus its row tests).
- `Standalone` → unchanged top-level path.

Then, site by site:

- **`FlowPolicy.gren`:** `FlowState` loses `prevRowBlock`/`prevRowLine`
  (keep `prevElided` only if classification hasn't fully absorbed the elided
  case; the goal is to drop it too). `ItemFacts.LeafItem` loses `startRow` /
  `startRowLine`. `LineCommentItem`/`BlockCommentItem` gain
  `role : CommentRole` and their arms become pattern matches on the role.
  `commentPlacement` loses its row parameter. Most of `decide`'s comment logic
  collapses; `FlowPolicy` remains the owner of *separator/join* policy
  (`NextSeparator`, `Placement`, `BlockKind`), which is genuinely render-side.
- **`assembleFlowImpl` (`MakeRenderBox`):** the per-box-kind `ItemFacts`
  construction — the `bracketFacts` plumbing, `lastRenderedRow`, the
  `ParenBlock` arm from `f55d4f9`, the long "every other flow-item box kind"
  comment — deletes. Items are just `{ box, isBlock, commentRole }`.
- **`FlowAssembly.gren`:** `FlowItem.startRow` deletes; `shouldGlueBox`,
  `assembleBrokenWithComments`, `assembleBrokenCall`,
  `pairTypeRecordComments` switch from row compares to role matches.
- **`BinopLayout.splitTrailingOwnLineComments`:** becomes a partition on
  role (`LeadsOwnLine` vs `TrailsPrevious`) — the `contentRow` parameter
  deletes.
- **`commentBracketListBox`**, pipeline comment-peeling,
  `literalCommentsRideFlatLine`, `pairLeadingRecordComments`: same treatment —
  match on role.

### 5.6 What Change A does NOT touch

- The top-level detach rule and `VerticalSpace`'s column-based
  detached-vs-leading distinction (works on original columns, pre-render —
  legitimate).
- The `let` last-binding trailing comment routing below `in` (a deliberate
  divergence — [#20](docs/elmFormatComparison.md#divergence-20) at the time of
  writing; do not re-open).
- The where-block `--` escape (parser gives byte-identical AST+Context for
  both layouts — unfixable here, don't try).
- `Comments.gren`'s descent/boundary logic itself — it is the *right* place
  for row arithmetic and keeps its `lpnBracketNode`/`lastBracketEnd`
  machinery. Change A makes that machinery **private to the Logical stage**:
  once no `Render/*` module reads `lpnLastBracketEnd`/`bracketEndExact`,
  consider removing them from the module's exposing list to enforce it.

---

## 6. Change B — observe rendered shape, don't predict it

### 6.1 The rule

Wherever a renderer must decide layout based on whether a child *will* be
multi-line, it renders the child first and checks the Box:

```gren
-- the pattern (already in the codebase, from 7dfa132):
childBoxResult = makePrettyLineBox child
decision = when childBoxResult is
    Ok box -> not (isSingleLine box)
    Err _ -> …propagate…
```

The decision inputs become exactly two:

- **author intent** — the `forceVertical` flags captured at LPT build
  (sound fixed points; keep as-is), and
- **observed shape** — `isSingleLine` of the already-rendered child Box.

Everything in between — `nodeSpansRows`, `subtreeHasComment`-as-layout-input,
`subtreeHasVerticalBox`, `checkContentVertical` backstops — is a prediction
layer that exists only because decisions were made before rendering, and it
goes away.

What a lying predicate looks like from outside. The caller lays out the code
*around* a child on the strength of the prediction, so an under-approximation
does not merely mis-indent — it commits to a layout the child then contradicts:

```gren
-- the predicate said "this operand is one line", so the chain stays flat …
x = alpha ++ (if p then one else two)

-- … but the operand renders multi-line, and the flat assembly has nowhere to
-- put it. Best case the next format disagrees with this one; worst case the
-- assembler hits a shape it was promised could not occur and returns `Err`
-- ("unreachable soft-glue", the 27e8903 / 6021c73 crashes).
```

`audit-predicates.py` exists only because this is otherwise invisible: the
output is deterministic, AST-equivalent and often idempotent, so every
self-consistency gate in the repo passes it. Render the child and ask
`isSingleLine` and the question cannot be answered wrongly.

### 6.2 Box reuse is mandatory, not optional

`makePBox` is bottom-up recursive; the natural refactor is: render children
once, keep the `(node, box)` pairs, make decisions from the boxes, assemble
from the same boxes. **Never render a child once to test and again to use.**
This codebase has already had an O(2^depth) blowup in Box rendering
(`renderRowState`, the self-format hang — see memory
`project_selfformat_hang_bug`); a decide-then-rerender pattern reintroduces
that risk at the tree level. `FlowItem` already carries `{ node, box }` —
extend that shape to the sites that currently consult predicates before
rendering.

Caveat: a few children render *differently by context* (e.g. `inlineStart`
flags into `assembleFlow`, `buildFlowBox 0` vs `buildFlowBox grenIndent`).
Where the deciding render and the final render would differ in parameters,
either (a) decide from the context-free part (`isSingleLine` is usually
invariant across those parameters — verify per site), or (b) restructure so
the context is known before the single render. Do not silently render twice.

### 6.3 Migration order (per-construct, each independently gated)

Convert one decision site at a time, in this order (roughly: most recently
patched first, since those have fresh fixtures):

1. Binop chain-break (`anyOperandRendersMultiline`) — **already done**
   (`7dfa132`); it is the model.
2. Record-literal / record-update field values (`renderRecordFieldBox` /
   `literalItemRenderer`, the `27e8903`/`6021c73` crash sites): replace the
   remaining shape predicates with a rendered-box check; the "unreachable
   soft-glue" `Err` arms should become genuinely unreachable and can then be
   asserted as such.
3. Lambda/`SoftIndentedBlock` body drop (`01745b2`'s `exprAlwaysBreaks` —
   already content-based; finish by making it box-based).
4. `ParenBlock` content verticality: replace the `checkContentVertical`
   render-side fallback chain with an `isSingleLine` check of the rendered
   content; the `checkContentVertical` flag on the LPT then only gates
   *whether synthesized parens opt out* and may shrink to that meaning (or a
   rename, e.g. `authorParens : Bool`).
5. `AcrossOrVertical`/call-argument flows (`checkContentVertical` there,
   `softBlockChildForcesVerticalBox` consumers).
6. When/if/let and pipeline step layout checks.
7. Last: `subtreeHasVerticalBox` itself — once no caller remains, delete it
   and its propagated-finding machinery from the audit.

### 6.4 Effect on the predicate audit

`audit-predicates.py` checks `predicate node == True ⟹ node renders
multi-line`. Every predicate replaced by an `isSingleLine` observation becomes
true *by construction*. Keep the audit running throughout the migration (it is
the only oracle that sees predicate lies); when a predicate's last caller is
gone, delete the predicate and drop it from
`src/Formatter/Audit/PredicateAgreement.gren`. End-state: the audit either
retires or shrinks to whatever genuinely predictive predicates remain (ideally
none).

### 6.5 Interaction with Change A

Do A first. After A, "does this subtree contain a comment?" stops being a
layout question in most places (a `LeadsOwnLine` comment forces its flow
vertical *through its rendered box being multi-line*, not through
`subtreeHasComment`), so B's conversions get simpler. The kind-aware
knowledge — inline `{- -}` rides, `--` breaks — then lives in exactly one
place: the classifier (A) plus `CommentBox`'s rendering (a `--` box is
`MustBreak`; a single-line `{- -}` box is a plain line).

---

## 7. Migration plan (phases, each landing green)

The cutover discipline that worked for Box/Doc (run both, assert agreement,
then delete the old side) applies here.

**Phase 0 — freeze a baseline.**
Run and record: `run-tests.sh` (all fixtures), `fuzz-idempotency.py -j 12`,
`fuzz-whitespace.py -j 12` (both modes), `matrix-syntax.py -j 12`,
`audit-predicates.py -j 12`, `corpus-check.py`, `gen-random.py -n 2000 -j 12`.
All must be clean/known before starting. Every phase below re-runs the
relevant subset; a phase that changes any fixture byte is a bug in the phase.

**Phase 1 — add roles, verify against the render layer's answers.**
- Add `CommentRole`; change the two comment constructors (compiler finds all
  consumers; initially each consumer ignores the role).
- Implement classification in `Comments.gren` (§5.3).
- **Cross-check instead of cutover:** in the flow paths, compute the decision
  both ways — today's row arithmetic and the stored role — and on
  disagreement return an `Err` naming node kind + rows + both answers (an
  `Err` fails the format loudly; the fuzzers and matrix will find every
  disagreement). Ship nothing user-visible in this phase.
- Drive the corpus + fuzzers + matrix + `gen-random.py` until zero
  disagreements. Each disagreement is either a classifier bug (fix the
  classifier) or an existing latent render bug (fix it now, with a fixture —
  this phase will likely surface a few more `f55d4f9`-class arms).

**Phase 2 — cut the flow paths over to roles.**
- `FlowPolicy` / `assembleFlowImpl` / `FlowAssembly` consume roles; delete
  `prevRowBlock`/`prevRowLine`, `startRow`/`startRowLine` from `ItemFacts` and
  `FlowItem`, `lastRenderedRow`, the cross-check scaffolding.
- Extract the single `glueTrailingComment` helper (§5.5).
- Full gate sweep.

**Phase 3 — cut the remaining comment sites over.**
- `BinopLayout.splitTrailingOwnLineComments`, `commentBracketListBox`,
  pipeline peeling, `literalCommentsRideFlatLine`,
  `pairLeadingRecordComments`, `typeHasCommentBracket`.
- Full gate sweep. After this phase, grep `Render/` for `\.row` /
  `lpnLastPos` / `lpnLastBracketEnd`: remaining hits should be only Class 2
  (shape prediction) sites and genuinely non-decision uses (debug JSON).

**Phase 4..n — Change B, one construct per phase (§6.3 order).**
- Each phase: convert, delete the predicate's caller, run the full sweep
  including `audit-predicates.py`.

**Final phase — enforce the invariant.**
- Remove `lpnLastBracketEnd`/`bracketEndExact` (and, if possible,
  `lpnFirstPos`/`lpnLastPos`) from `LogicalPrintingTree`'s exposing list, or
  at minimum add a CI grep that fails on new row-reads in `Render/*`.
- Update `DEVELOPER.md`'s "adding a construct" checklist: new constructs
  declare comment-glue behavior by *classifier arm* (if nonstandard) and never
  add render-side row logic.

Each phase is a separate commit (or few), on the current branch (project rule:
no auto task branches).

---

## 8. Definition of done

- All Phase-0 gates green; **every fixture byte-identical** to baseline; the
  matrix parity baseline unchanged (0 new divergences, 0 disappeared
  registered ones).
- `FlowState` has no row fields; `ItemFacts`/`FlowItem` have no row fields.
- `grep -rE 'lpnLastPos|lpnFirstPos|lpnLastBracketEnd|\.row' src/Formatter/Render/'`
  returns (near-)zero decision sites — target from ~150 today, MakeRenderBox
  ~98 alone.
- `NodeClassify` retains only structural queries that are not shape
  predictions (`isArrowLeaf`, `leafValue`, `splitSignatureSegments`, …);
  `subtreeHasVerticalBox` and the `subtreeHasComment`-as-layout-input family
  are gone.
- `checkContentVertical` is gone or reduced to "synthesized parens opt out".
- The classifier's arms are each documented with the fixture that pins them.

Scored against the code as it stands: [Where it ended
up](#where-it-ended-up-2026-08-01).

## 9. Pitfalls and standing decisions (read before coding)

- **Deliberate divergences from elm-format are not bugs.** The catalogue is
  [`docs/elmFormatComparison.md`](docs/elmFormatComparison.md) — #10 redundant
  parens, #17 precedence-split binop chains, #19 pipeline `|>` alignment, #20
  let-last-binding comment, #22 position-less separators, … Matching output to
  elm-format is a *gate for shared constructs only*, via the matrix parity
  baseline. **The numbers move**: entries have been removed and later ones
  renumbered down more than once, so cite by reading the entry text, not by
  recalling a number — proposing a citation from memory has produced two wrong
  ones in a single review round.
- **The `let` last-binding trailing comment routes below `in` on purpose** —
  both "fixes" oscillate because `in` has no position. Do not let the
  classifier "improve" this.
- **where-block `--` escape is unfixable** (parser erases the distinction).
- **Gates check consistency, not correctness** (memory
  `feedback_gates_are_consistency_not_correctness`): a wrong-but-stable role
  assignment passes every fuzzer. Phase 1's dual-computation cross-check and
  the elm-format matrix are the correctness oracles — that is why Phase 1
  verifies roles against current behavior rather than against the spec in
  §5.3's prose.
- **Never leave a comment-free fallback path that isn't literally the same
  function as the comment-bearing path** (memory
  `feedback_fallback_layout_consistency`) — Change B's shared-box refactor
  must not fork flat/commented variants of a renderer.
- **`Array.get -1` returns the last element in Gren** — guard every `i-1`
  lookback in the classifier's previous-sibling scan.
- **LPT row-metadata mutation trap** (memory
  `feedback_lpt_row_metadata_mutation`): passes that reorder nodes after
  `Comments.gren` (SortSymbols, VerticalSpace) read shifted rows via the box
  field, not `lpnFirstPos`/`lpnLastPos`. Roles are immune to reordering —
  another argument for them — but don't break those passes while touching the
  constructors.
- **`commentLineDoc`/`CommentBox` already appends the hard break for a `--`**
  (memory `feedback_pretty_printer_doubled_breaks`): re-wrapping can hang the
  formatter. The `TrailsPrevious` glue helper must append the comment to the
  host line, not wrap the comment box again.
- **Rebuild `gren-format` before every fuzzer/matrix run**
  (`cd ../gren-format && ./build.sh`) — they shell out to the built app.
- Run the heavy sweeps with `-j 12` (16-core machine; defaults are `-j 2`).

## 10. Why not smaller?

A fair question the implementer should be able to answer: could we keep
patching arms? Yes — each patch is small. But the 2026-07-17..19 record is
eight patches in three days for one bug family, each requiring its own
fixed-point argument, in a renderer where ~8 sites independently re-derive the
same fact and an audit tool exists solely to catch a predicate layer lying.
The marginal cost of the next arm never goes down, because the architecture
makes every new construct × comment-position pair a fresh proof obligation.
Changes A and B replace per-arm proofs with two global ones (§5.4, §6.1) —
that is the trade.
