# Change-1 Cutover Scope

Scoping document for retiring the legacy `Render.Doc` renderer in favour of the
`Render.Box` renderer (`MakeRenderBox.makePrettyLineBox`). Written after tranche
26 (commit `3afd9ea`). This is the plan to get from "Box covers 90%, guarded by
Doc" to "Box is the only renderer".

---

## CUTOVER DECISION (2026-07-09, HEAD `3157780`)

**Decision: keep the self-verifying hybrid as the architecture. Do NOT pursue
full `Render.Doc` deletion this cycle.** The strangler is treated as *complete
as a shipping architecture*, not as a temporary scaffold to be torn down.

### Why (fresh census, 2026-07-09)

Full deletion requires every top-level root child to render via Box. After 60
tranches the corpus residual is **23 `Err` root children + 7 `mismatch` root
children** (Box renders but differs from Doc). The 23 `Err`s span four *hard*
architecture problem-classes, two of which already have reverted attempts:

| class | fixtures | portable? |
|---|---|---|
| per-row exact-space indent (Tab can't do non-mult-of-4) | WhenInCommentedArray, KitchenComments if-condition, post-mlbc decl/lambda | needs new Box primitive; **reverted twice** (t42/t43, decl/lambda) |
| `R.align`-inside-`R.nest` mix (soft value glued to `=`, nested OpAndRhs) | LambdaBracketBodyNestedInCall, KitchenSink recupd×2, KitchenComments recupd, MultilineBlockComments OpAndRhs | flat Box's uniform prefix can't express it |
| comment-in-multi-node-signature idempotency | TrickyComments, KitchenComments | **reverted (t61)** — renders right but non-idempotent |
| rare / not-easily-portable | BlockCommentBodyIndent (nested verbatim → needs col-0 `reset`), backward-pipeline-mixed-ops, "unexpected node among when-branches" (KitchenSink, KitchenComments) | corner cases; low value |

Plus ~6 **tracked-intentional** `Err`s (direct-operand pipeline `|> (\x -> …)`
glue: CallArgBlockRelocation, CommentsPipeline, PrefixAnchorDivergence,
PipelineLambdaArg, + one each in KitchenComments/MultilineBlockComments) that
Box deliberately Errs on so it falls back to Doc's shipped layout.

The payoff of deletion is **internal only** (remove the Doc renderer code); it
delivers **zero** user-facing benefit, because the guard already ships correct
output everywhere. The remaining work is multi-session and revert-prone. The
guard is not debt — it is a *correctness feature*: Box never ships output that
hasn't been proven byte-equal to the reference renderer, and the census IS a
self-documenting coverage map.

### The one real quality gap (worth acting on)

For the **7 `mismatch` nodes** the guard ships *Doc* output, and for the
elm-format-verified subset that output is **wrong**:

- **UniformRecordArray (×2) / UniformUpdateArray (×1)** — Doc emits a mangled
  mixed layout (`{ … }, { name = "triangle"\n  , sides = 3\n  }`) and over-indents
  `| field` to `{`+4; Box (= elm-format v0.8.8, verified Phase B) emits clean
  one-record-per-line at `{`+2.
- **KitchenSink nested-record-type (×2)** — Box matches elm-format; Doc diverges.
- **MultilineBlockComments `-> {record}` sig (×2)** — adopt-Box.

These 7 are the only place a cutover action improves shipped output. See the
open product question at the top of the session log; options are (a) flip the
guard to *trust-Box-when-Ok* + regenerate the affected fixtures (cheap, but drops
the equality safety net globally), or (b) fix the specific Doc-renderer bugs so
Doc == Box + regenerate (keeps the safety net, more work), or (c) freeze as-is.

### Chosen: option (b) — fix Doc bugs, keep the safety net. Progress:

- **fix 1 (`6ef4adc`) — array sibling coupling.** `makeAllAcrossOrAllVertical`
  now goes vertical when `anyChildForcesVertical`, so a one-line-authored list
  with a multi-line element breaks one-per-line instead of leaving it dangling.
  Fixes UniformRecordArray (mixedAuthor, commentInRecord) + improves
  BracketTrailingComments (nested, was ok-but-wrong in both renderers).
- **fix 2 (`c8e356d`) — record-update field indent in an array element.**
  `makeRecordUpdateDoc` takes a `listPrefixWidth`; array elements indent fields
  `grenIndent - 2` so `| `/`, ` land at line-start+4 (= `{`+2), not `{`+4.
  Fixes UniformUpdateArray (updates).

Both verified against the elm-format binary; gates green (139/139, fuzzers 0).

**4 mismatch nodes remain, all deep-gap-hard (drop-vs-glue / comment-reindent in
`buildFlowDoc`, which is shared across signatures / type bodies / record fields /
let bindings — corpus-wide blast radius, needs per-context handling):**

- **KitchenSink type-app `HasIdentifier { record }` + field-value `tracing :
  { record }`** — a multi-line record type after a union-variant head or a
  field `:` must DROP to its own line +4 (elm-format + Box), but the Doc
  soft-glues the `AlwaysVertical` record onto the head line via `FlowSep`. Fixing
  needs `buildFlowDoc` to treat a multi-line record as a dropping block in the
  *type* context without disturbing the expression contexts that share it.
- **MultilineBlockComments sig `-> { record }` + `{ x } {- mlbc -}` glue** — the
  same drop plus a multi-line-block-comment reindent inside a signature type
  (the mlbc-in-flow deep gap, also unported in the Box renderer).

These are the same difficulty as the 23 `Err` deep-gaps; each is a separate
focused effort, not a quick fix. Until done, the guard ships Doc's (wrong)
output for these 4 nodes — a documented, bounded residual.

#### LANDED (scoped): KitchenSink type-record-drop (fix 3, `2694b95`)

The scoped version cleared the idempotency wall and shipped. A multi-line **type**
record following a real head in a flow now drops to +grenIndent (elm-format's
rule), gated four ways: `isTypeRecordLiteral` (`:`-vs-`=` first field),
`separator == FlowSep` (real head precedes), NOT `flowIsFunctionType` (scopes out
the `-> { record }` arrow-oscillation case), and **no comment anywhere in the
flow** (a comment trailing the dropped `}` oscillates +4↔col-0 — backing off to
the glued form keeps every comment position a fixed point). Drop rule is
indent-aware (`indentedBlockRule` at indent 0, else `bodyBlockRule`). Fixes the
**2 KitchenSink mismatch nodes** + 2 bonus whole-sig-type fixtures
(SignatureTrailingComment, CommentPlacement). Effectful 139/139, all 3 fuzzers 0.

**Now 5 of the original 7 mismatch nodes are fixed.** The 2 remaining are both in
MultilineBlockComments and both deferred by design: the `-> { record }` return
record (function-type, scoped out to avoid the arrow-oscillation) and the
`{ x } {- mlbc -}` comment-reindent (the mlbc-in-flow deep gap). Fully closing
them needs the arrow-break made a reparse fixed point + the mlbc reindent — the
same fixed-point work the drop currently sidesteps.

#### LANDED: drop with a comment in the flow (fix 4, `8786dcf`)

Fix 3's fourth gate (no comment anywhere in the flow) is deleted — a type record
now drops even when a comment shares the flow. The trailing-comment oscillation
that motivated the gate is fixed at its root: the drop carries the record's
closing-bracket row as `prevRow` (was `-1`), so a single-line block comment on
that source row glues onto the `}` line (`} {- c -}`) — a reparse fixed point.
`dropRule.nextSep` is kept (an interim `FlowSep` experiment glued a following
type argument, `Dict { … } String` → `} String` — a correctness regression).

#### LANDED: leading comment drops with the record (fix 5)

The residual fix 4 left behind — a comment *between* the head and the record
stayed glued to the head's line, where elm-format drops it onto its own line
directly above the record — is closed. `pairLeadingRecordComments` pairs a
single-line block comment with an immediately following type-record literal
before `buildFlowDocImpl`'s fold; at the record's step, if the exact
`typeRecordDropFires` gates pass, comment + record drop together as one block
(same wrap/nest → same indent). The move is a reparse fixed point because the
decision is keyed off the *next node's structure*, not source rows — the naive
row-based move oscillated (glued↔dropped). Every non-drop case replays the
unpaired fold steps byte-for-byte (`blockCommentStep`, factored out of the
fold). elm-format-verified (alias-body form byte-identical); README point 12
rewritten as resolved; fixtures `TODO.*` renamed
`TypeRecordLeadingComment.*` and regenerated. Effectful 140/140, all fuzzers 0.
**Box census note:** the Doc was briefly *ahead* of Box on the 3 fixture nodes;
the Box port landed the next day (fix 5b, below), restoring guard agreement.

#### LANDED: fix 5b — Box port of the type-record drop + a duplication fix

Two Box-side changes bring the Box renderer back into byte-agreement:
`pairTypeRecordComments` (the `assembleFlowImpl` mirror of the Doc pairing —
paired drop when a real head is on the current row, exact unpaired replay
otherwise) and a `makeSignatureBox` t47 change (a multi-line single-node type
RECORD stacks below the header at +4 — the fix-3 drop — instead of the
`B.prefix` align-glue; other bracket types keep the glue). Clears the
CommentPlacement + SignatureTrailingComment mismatches (Doc-ahead since fix 3)
and the 3 TypeRecordLeadingComment nodes. Remaining Doc-ahead census:
BracketTrailingComments 1 (fix-1 class) + MultilineBlockComments 2 (deferred
deep-gaps).

**Bug found by the trust-Box idempotency fuzzer, fixed in both renderers:
Gren's `Array.get -1` wraps to the LAST element**, so a record at flow index 0
whose flow *ended* in a pairable comment (`type alias R = { … } {- c -}`)
paired with that TRAILING comment via `at (0 - 1)` and rendered it TWICE. The
duplicated output was string-idempotent (the reparse re-derives the same two
positions), so the normal idempotency fuzzer and the pipeline's own checks
(AST equality ignores comments; the idempotency check compares format¹ vs
format², never Context(original) vs Context(format¹)) all passed silently —
only the trust-Box fuzzer tripped, because Box's `trySoftGlueFlow` renders the
duplicated two-comment form differently than Doc. Both `at` helpers now return
`Nothing` for negative indices, and a TrailingOnly regression decl pins the
single glued `} {- trail -}` in the TypeRecordLeadingComment fixture. Gates:
normal 140/140 + all fuzzers 0; trust-Box fuzzers 0, trust-Box effectful fail
set = the 2 known pre-existing divergences only.

### Items 1–2 LANDED (2026-07-10, after fix 5b `b40d8a3`)

**Item 1 `a391cec`: closed the comment-count verification gap.** Fix 5b's
duplication bug was invisible to every gate (AST equality ignores comments;
idempotency compares format¹ vs format² — never Context(original) vs
Context(format¹)). Two hardenings: `fuzz-idempotency.py` now asserts the
formatted output contains the exact expected number of `¤` markers (fast
all-gaps path checks `== len(gaps)`, slow per-gap path checks `== 1`); the
effectful harness's `assertPretty` gained `checkCommentCountPreserved`, which
reparses the formatted output and compares its comment count against the
original parse's. Gates: effectful 140/140, idempotency + whitespace fuzzers 0.

**Item 2 (this commit): BracketTrailingComments — ported fix 1 to Box, the
last easy Doc-ahead node.** Fix 1 (`6ef4adc`) made the Doc's
`makeAllAcrossOrAllVertical` go vertical when any child forces vertical; the
Box renderer's `ElmStructure.groupBox` had the mirror-image bug — a special
case for `{ fm = False, cs = [ only ] }` (a flat-authored list with exactly one
multi-line child) that hugged the closing bracket onto the child's last line
(`} ]`) instead of dropping it to its own line. Deleted that special case so a
multi-line child of ANY count (including one) falls through to `verticalGroup`,
matching elm-format and the Doc renderer. Verified with the trust-Box-always
guard (sed `if True then`, rebuilt `gren-format`, ran both fuzzers, reverted):
trust-Box effectful dropped from "BracketTrailingComments 1 + MultilineBlockComments 2"
to just the 2 known pre-existing MultilineBlockComments deep-gap failures (item
3/4 below, untouched); trust-Box fuzzers 0/0. Normal-guard gates after
reverting: effectful 140/140, idempotency + whitespace fuzzers 0 — the guard
now picks Box's output for this node since it's byte-identical to Doc's.

### Item 3 LANDED (2026-07-10): MultilineBlockComments deep-gap A, `-> { record }` return-record drop

elm-format drops a multi-line record that is a function signature's return
type; the Doc renderer used to glue it instead (scoped out via
`flowIsFunctionType` since fix 3, precisely to dodge the arrow-oscillation
bug documented in the "Attempted + REVERTED" section below). Landed by making
the signature's own `forceVertical` decision a reparse fixed point instead of
just deleting the gate:

- New `segmentHasDroppingRecord : Array LPNode -> Bool` mirrors
  `typeRecordDropFires`'s trigger condition (a type record literal at index >
  0 within a segment — i.e. following the segment's own leading `->`, or an
  earlier type node — that itself renders with a hard break) but evaluates it
  per-segment, independent of the fold's accumulator, so it can run *before*
  the segments are rendered.
- `makeSignaturePrettyDoc`'s `forceVertical` is now
  `segmentsBrokenAtBoundary segments || (Array.length segments > 1 &&
  Array.any segmentHasDroppingRecord segments)` — i.e. commit to the fully-
  exploded one-segment-per-line layout up front whenever the return-type drop
  is *going* to happen, rather than discovering after the fact (on reparse)
  that a boundary now exists. The `Array.length segments > 1` guard matters:
  a single-segment type (no arrow at all, e.g. a type application with an
  embedded record like `HasIdentifier {- note -} { payload : String, … }`,
  the TypeRecordLeadingComment fixture) has no boundary to protect and must
  keep going through the existing single-flow fallback
  (`makeFlowIndentableDoc`) — an earlier attempt without this guard broke that
  fixture by exploding a plain non-function type into a spurious per-segment
  layout.
- Deleted the now-dead `flowIsFunctionType` gate/function entirely.
- Box's `pairTypeRecordComments` (fix 5b) still refuses to pair a comment with
  a type record when the flow contains an arrow — Box hasn't been ported for
  this specific drop yet, so this is a **new, tracked Doc-ahead node**
  (KitchenComments `extremelyCommented`); the self-verify guard safely falls
  back to Doc's (elm-verified) output for it in the meantime.

Verified: reproduced the historical oscillation standalone (`foo : A -> B ->
{ record }` with a comment forcing the record vertical), confirmed the fix
makes it idempotent, and confirmed the fully-exploded shape matches real
`elm-format` output structurally (translated the repro to `.elm` and ran the
`elm-format` binary — it also explodes every `->` segment in this case; the
remaining difference is an unrelated, pre-existing intentional divergence in
how comments are spaced inside a dropped record). Regenerated the stale
KitchenComments fixture (only that one decl changed) and added a dedicated
`returnRecordComment` case to SignatureSegmentBreaks pinning the exact shape.
Ran the full trust-Box drill (sed guard → rebuild → both fuzzers → revert):
trust-Box effectful mismatches are now the pre-existing 2 MultilineBlockComments
deep-gap-B hunks (item 4, untouched, all three of its hunks — the earlier "2"
count undercounted because a prior trust-Box run's output had been truncated
by `tail`) plus the one new Box-lag node noted above; trust-Box fuzzers 0/0.
Normal-guard gates: effectful 140/140, idempotency + whitespace fuzzers 0.

Also discovered along the way (unrelated, not investigated further): `gren
format --show` hangs indefinitely self-formatting `MakeRender.gren` (4512
lines) — reproduces on the pre-item-3 source too, so pre-existing and not a
regression from this work. Repro steps and a bisection plan are recorded
separately (session memory, not committed to this repo) for whoever picks it
up next.

### Remaining work (plan as of 2026-07-10, after items 1–3)

Item 4 is the hard tail; 5 is parked by the cutover decision.

4. **MultilineBlockComments deep-gap B: `{ x } {- mlbc -}` reindent.** A
   multi-line block comment trailing a record in a flow — the mlbc-in-flow
   fixed-point class (t42/t43/t61 territory; comment position shifts on
   reparse). Needs the comment's claimed position to be a fixed point of the
   rendered layout before any layout change; do not retry naively (three
   reverts on record).

5. **Parked (by the cutover decision): the 23 Box `Err` root children.** Full
   Doc deletion needs them at 0; they span 4 hard architecture classes
   (post-mlbc continuation indent, Tab-vs-prefix bracket items, soft-value
   glue, verbatim opener / nested OpAndRhs / multi-step backward pipes). The
   guard keeps them correct; revisit only if the hybrid's dual-render cost
   ever matters.

Process notes for whoever picks this up: run the trust-Box fuzzers after ANY
comment-pairing or attachment change (sed the guard to `if True then`, rebuild
`gren-format`, fuzz, sed back — don't `cp`-restore over other edits); verify
divergences against the elm-format binary
(`/home/gram/prj/gren/node_modules/.bin/elm-format --stdin`) before matching
it; census recipe is in "Residual census recipe" above.

#### Attempted + REVERTED (superseded by the scoped landing above): first try

Pressed on with the 2 KitchenSink nodes. The *output* side is fully solved and
was elm-format-verified — the two pitfalls both have clean answers:

- **Shared-code blast radius** (`buildFlowDoc`/`listItemBoxDoc` serve types AND
  expressions; a naive drop broke 13 expression fixtures): gate the drop on a
  new `isTypeRecordLiteral node` — a curly literal whose first field uses a
  `SynthesizedText ":"` separator (type field) vs `"="` (expression field). This
  fires only for type records, leaving expression records/arrays/patterns inline.
- **Leading-comment false-trigger** (a record-type-alias body with a leading
  comment wrongly dropped): gate on `acc.separator == FlowSep` (real head
  precedes) rather than "not first" — a leading comment leaves the separator
  `AlreadyTerminated`, so it no longer counts as a head.
- **Double-indent** (field-value flow is already `buildFlowDoc grenIndent`): pick
  the drop rule by the flow's `indent` — `indentedBlockRule` (+grenIndent) when
  `indent == 0`, `bodyBlockRule` (hardNl only) otherwise.

With all three, effectful was **139/139** and the whitespace fuzzers **0**; the
drop even corrected 3 more previously-glued fixtures (SignatureTrailingComment,
CommentPlacement, KitchenComments `extremelyCommented`) — elm-format drops *every*
multi-line record type below its head/`:`, so the rule generalizes.

**BUT the idempotency fuzzer found 3 non-idempotent gaps in KitchenSink — the
hard wall:** (1) a trailing block comment after a *dropped* record's `}`
oscillates +4 ↔ col-0 (comment-after-dropped-block instability — hits the primary
`HasIdentifier` case); (2,3) `interpretMicroCommand : … -> { record }` — a
function-type signature whose *return* record now drops makes the whole sig
multi-line, so the `->` arrows re-break on the second pass. The drop entangles
with arrow-layout and trailing-comment placement idempotency, which need more
machinery. **Reverted** (source + 6 fixtures) to `497c1ae`. The output solution
above is reusable; the remaining work is making the drop a reparse fixed point.

### Residual census recipe

Instrument `makePrettyResult` (see session notes): classify each `lpnChildren
root` node as `ok` / `MISMATCH` / `ERR:<msg>`, prepend a `-- CENSUS n=… :: …`
header, rebuild the CLI, sweep `*.formatted.gren` via `node ../../gren-format/app
--show` (the header trips the reparse, so read it from the wrapped `1| … 2|`
error dump). Revert the instrumentation after.

---

## Where we are (historical — pre-decision plan, counts from tranche 26)

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
