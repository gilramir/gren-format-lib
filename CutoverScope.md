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

### Item 4 progress (2026-07-10, after items 1–3): one of three hunks landed

Re-ran the trust-Box-always drill (unrelated to this item — done first to
verify an unrelated exponential-blowup perf fix in `Box.gren`'s
`renderRowState`, see the `gren-format` hang fix commits). It re-confirmed
the item-4 residual is exactly **3 hunks in MultilineBlockComments**, all in
the `#10`–`#12`/`#43`/`#44` numbered-comment region: `sig`'s arg-position
extension-record type (`#12`), `parensAccessor`'s binop operand comment
(`#43`), and `asPattern`'s record-pattern trailing comment (`#44`) — plus
one pre-existing, unrelated KitchenComments hunk (item 3's tracked
`extremelyCommented` Box-lag node, untouched).

**LANDED (`2fd515e`): `#43` — paren `(` width not padded onto comment
continuation lines.** Root cause was narrower than the other two:
`wrapParenVertical` (used to close a generic vertical `( … )`) only
prepended `(` to the box's first line, leaving continuation lines alone —
correct for Tab-based "absolute-nested" content (`when`/`if`/lambda
bodies, which re-derive their own indent from the ambient block
independent of the paren's width) but wrong whenever a comment forced the
content multi-line via the comment-aware flow builders' `B.prefix` glue,
which aligns by *exact character width* and so needs the `(` width folded
in on every line. Fixed by routing comment-bearing, non-lambda-body paren
content through the existing `wrapParenVerticalPadded` (already used for
the one other case needing this — the bracket-pattern-lambda). This was a
pure rendering-width bug, no comment-position/attachment logic touched, so
it carried none of the fixed-point risk the other two hunks have. Gates:
effectful 140/140, idempotency + whitespace fuzzers (both modes) 0.

**NOT ATTEMPTED — `#12` and `#44` both trace to the same deep architecture
gap.** Investigated both before touching code: `sig`'s extension-record arg
type (`#12`) goes through `makeSignatureBox`'s general per-segment
`buildFlowBox`, not the narrow `isTypeRecordLiteralBox`-gated special case
(which already correctly excludes extension records — that gate isn't the
bug). `asPattern`'s `{ x } {- mlbc -} as whole` (`#44`, the literal
mlbc-reindent case) is a plain LPT flow (`[recordPattern, "as", name]`, no
dedicated pattern box type), so its glue-vs-drop decision also runs through
the same general flow builder. Both bottom out in `assembleFlow`'s generic
"is this child a droppable block" check, which treats *every* multi-line
node as droppable/glueable uniformly — unlike Doc, which drops only
specific hard-block types and soft-glues the rest via node-specific
predicates (`isTypeRecordLiteral`, etc.). This is the same "assembleFlow's
isBlockNode treats EVERY multiline node as a droppable block" gap flagged
elsewhere as the single biggest, riskiest lever in the whole cutover, and
matches the "mlbc-in-flow fixed-point class (t42/t43/t61 territory)"
description below — the exact class with **three prior reverts**. Left
alone this session; below is the original (still-accurate) description of
the remaining 2-hunk residual.

4. **MultilineBlockComments deep-gap B: `{ x } {- mlbc -}` reindent** (now
   2 of the original 3 hunks — `#12`, `#44`). A multi-line block comment
   trailing a record in a flow — the mlbc-in-flow fixed-point class
   (t42/t43/t61 territory; comment position shifts on reparse). Needs the
   comment's claimed position to be a fixed point of the rendered layout
   before any layout change; do not retry naively (three reverts on
   record).

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

### Item 4 design (2026-07-10, user-approved): shared FlowPolicy decision core

Approach chosen (over a fourth scoped patch): extract the flow fold's
*decision layer* into one shared module both renderers consume, with a
**complete-to-the-Doc** placement vocabulary from day one. Rationale, spec,
and phasing below.

**Phase 0 LANDED (2026-07-11).** `Formatter.Render.FlowPolicy` now holds
`NextSeparator`, `FlowState`, `FlowConfig`, `BlockKind`, `ItemFacts`,
`Placement`, and `decide`, plus the renderer-neutral structural classifiers
(`nodeIsComment`, `isMultiLineCommentValue`, `isTypeRecordLiteral`,
`pairLeadingRecordComments`) moved out of MakeRender. `buildFlowDocImpl` and
every per-box renderer (`renderSingleLineCommentNode`,
`renderBlockCommentNode` — ex-`blockCommentStep`, `renderBracketLiteralNode`
— ex-`joinPairedBracketItem`/`joinBracketLiteralFlowItem`, the block / when-
branch / pipeline-step renderers) now take their join decisions from
`FP.decide` and materialize via one `applyPlacement : Placement -> Doc ->
Doc -> Doc` map plus `blockWrap : BlockKind -> Doc -> Doc` (the ex-
`BlockRule` wraps). Deviations from the spec sketch, both deliberate:
`ItemFacts` is a sum type (one constructor per item kind, facts in the
payload) rather than a flat record, and `decide` is total (the one Err — a
paired "comment" that isn't a `BlockComment` — is a caller-side
classification error and stays in MakeRender). The `mergedFirstStep`
pre-fold lookahead and the peeling helpers stay in MakeRender for now
(node-list surgery, not join decisions). An own-line comment is recognized
by `decide`'s `next.separator == AlreadyTerminated` and gets its terminating
hardNl in the materializer. Gates: effectful 140/140, idempotency +
whitespace fuzzers (both modes) 0; trust-Box drill byte-identical to the
pre-refactor baseline (same 2 effectful failures, same fuzzer gaps — see
next paragraph).

**Phase 1 LANDED (2026-07-11).** `assembleFlowImpl` keeps no layout policy of
its own: each `FlowItem` is classified into `ItemFacts` (verticality facts
from `isSingleLine` on the rendered box — the Box analogue of
`R.hasHardBreak`), every join decision comes from `FP.decide`, and the fold
only materializes placements with Box primitives. Deleted: `isBlockNode`'s
role in the fold (any-multi-line-is-a-block), the Box-local `prevRow`
tracking (`nonCommentStep` set it for every inline item; the policy's
leaf-only discipline replaces it), the `glueable` flag (generalized: a
`SoftSep`/soft-`BlockJoin` placement with an empty current row glues onto the
last row's last line via `B.addSuffix` — "separator is FlowSep after *any*
item", which is what the Doc always did), `isSoftIndentedBlockNode` +
`blockOwnIndent` (the placement carries the rule), and the
`isTypeRecordLiteralBox` verbatim mirror (both renderers now import
`FP.isTypeRecordLiteral`). Placements Box can't materialize Err → self-verify
fallback: soft-glue of a multi-line item (`SoftSep`/soft `BlockJoin`), a
multi-line comment gluing onto a block's last line, and a leading multi-line
comment in an inline-start flow (a first-row box's continuation lines are out
of the enclosing `flowIndent`'s reach — the Doc gets that re-indent from its
outer nest; discovered as a `#37` regression during the drill and re-fenced).

**Phase 1 census: strictly better.** Trust-Box effectful failures 2 → **1**
— MultilineBlockComments now passes entirely (`#12` and `#44` BOTH clear:
with the shared decisions the record/comment either renders Doc-equal or
Errs into the fallback, instead of producing a Box-only layout). Item 4's
original 3-hunk residual is now fully resolved. Trust-Box idempotency fuzzer
gaps 6 → **2** (the four KitchenComments line-377/378 signature-record gaps
cleared; remaining: KitchenComments line 297 + SignatureSegmentBreaks
`returnRecordComment` line 31). Trust-Box whitespace fuzzers both modes 0.
The single remaining effectful failure is the tracked KitchenComments
`extremelyCommented` Box-lag node (`makeSignatureBox`'s segment path, not the
generic flow) — Phase 2/2b territory, together with the 2 fuzzer gaps.
Normal gates: 140/140, both fuzzers 0.

**Phase 2b LANDED (2026-07-11) — trust-Box-always is now FULLY GREEN:
effectful 140/140, idempotency fuzzer 0 gaps, whitespace fuzzers 0 both
modes. Zero Box-vs-Doc mismatches remain anywhere the corpus or fuzzers
reach; every remaining Box gap is an `Err` fallback (correct by
construction).** Three changes: (1) `makeSignatureBox`'s `forceVertical`
folds in a new `segmentHasDroppingRecordBox` (the Box analogue of
`segmentHasDroppingRecord`, verticality from `isSingleLine` on the segment
node's own box) — the item-3 fixed-point rule, mirrored; (2)
`pairTypeRecordComments`' arrow-refusal deleted — function-type segments now
pair, exactly like the Doc, since (1) guarantees the fixed point; (3)
`trySoftGlueFlow` refuses a multi-line TYPE record after a real head
(`FP.isTypeRecordLiteral soft.node`) — the drill showed `extremelyCommented`'s
glued output actually came from this pre-fold glue: the segment's trailing
`{-c21-}` comment satisfied the non-empty-suffix gate, so the soft glue fired
before the fold's `DropBlock` could. This cleared the last effectful failure
(KitchenComments `extremelyCommented`) and both remaining fuzzer gaps
(KitchenComments line 297, SignatureSegmentBreaks `returnRecordComment`).

**Phase 2 (remaining, now purely Err-frontier work — no known mismatches):**
the soft-glue materializer per the static alignment table (`B.prefix` for
align-carrying content, first-line-only glue for nest-carrying content) and
the multi-line-comment-after-block glue. These convert `Err` fallbacks into
Box coverage (the old parked-23-Errs frontier, which should be re-censused —
Phase 1's generalized `B.addSuffix` glue and placement-driven fold likely
moved several classes).

**Phase 2a LANDED (2026-07-11) — the soft-glue materializer + the
pipeline-step trigger split.** Fresh census at the Phase 2b baseline
(`d9d80c8`) first: **31 Errs, 0 mismatches** (the parked-23 figure was
stale; Phase 1 reshaped the classes). Biggest class: "soft-glue of a
multi-line item" ×11. Two changes:

- `assembleFlowImpl`'s `SoftSep` arm materializes a multi-line item per a
  new static alignment table (`softGlueAlignment` in MakeRenderBox,
  verified against the Doc source): bracket literals and multiline strings
  are align-carrying → `B.prefix` glue (their continuation lines are
  punctuation/verbatim-prefixed, so exact-width space padding is sound);
  `AcrossOrVertical`/`OpAndRhs`/`Glue`/`PrefixGlue`/`Pipeline` are
  nest-carrying → first-line-only glue (`B.mapFirstLine`, newly exported;
  continuation lines keep their own Tab offsets, completed by the flow's
  final `applyIndent`). **`RecordUpdate` and `AlignedFlow` are deliberately
  UNclassified**: align-carrying on the Doc side but their Box continuation
  lines carry `Tab`s, which `B.prefix`'s space padding quantizes to the
  next multiple of 4 — the drill caught the `#12` extension record landing
  3 columns short (the "per-row exact-space indent" hard class). They stay
  `Err`.
- **Drill lesson (a new instance of the #37 pattern): a "renderable now!"
  widening must audit CALLERS that relied on the Err.** `renderPipelineStep`
  returned its raw `buildFlowBox` for triggered multi-line steps — correct
  only because the record/lambda trigger used to Err inside the fold and
  fall back. With the soft glue landing, `|> AST.TType { … }` rendered
  GLUED where the Doc relocates the trigger below the step. Fixed by
  mirroring `buildStepChildrenDoc`'s split 1:1 (`splitStepTrigger`, which
  replaces `stepHasTrigger`): preamble glues inline, the trigger drops at
  +grenIndent, suffix args below it. The two ParenBlock trigger kinds
  (`makeMultilineLambdaArgDoc`/`makeMultilineParenArgDoc` reshaping) Err.

Census after: **30 Errs, 0 mismatches** (soft-glue 11 → 2 — the
`RecordUpdate` `#12` node + PrefixAnchorDivergence; the step split
re-classifies 9 nodes as "paren-arg pipeline-step trigger not ported", now
the biggest class and the next target). Gates: effectful 140/140 +
idempotency/whitespace fuzzers 0 under BOTH the normal guard and
trust-Box-always; trust-Box drill fully green.

**Phase 2b′ LANDED (2026-07-11) — paren-arg trigger materializers.**
`makeMultilineLambdaArgBox` and `makeMultilineParenArgBox` mirror the Doc's
`makeMultilineLambdaArgDoc` / `makeMultilineParenArgDoc`: the relocated
lambda arg stacks `(\args ->` / body at +grenIndent / `)` at the `(` column
(the box's own origin plays the Doc's `R.align` anchor); the non-lambda
paren arg glues `(` via `B.prefix` (its vertical content is a bracket
literal whose continuation lines are punctuation-prefixed — `B.prefix`
sound) with `)` on its own line. `renderPipelineStep`'s trigger dispatch
now mirrors the Doc's `makeArgDoc` 1:1. **Drill catch: comment-bearing
triggered steps must Err** — the Doc peels leading/trailing comments in
`renderPipelineStepNode` BEFORE the split; without the peel a step-level
comment lands in the suffix at the relocation indent, which shifts on
reparse (4 trust-Box idempotency gaps in PipelineLambdaArg, all from
fuzzer-inserted comments). Gated on any direct comment child; the peel
port is future work.

Census after: **24 Errs, 0 mismatches** — the paren-arg class fully
cleared (9 → 0; one KitchenComments node re-classified as the
comment-bearing-step gate). Remaining top classes: record-update soft
field values ×4 (Tab-poisoned `RecordUpdate`, the exact-space-indent hard
class), inline-token-after-block ×4, soft-glue residual ×3 (RecordUpdate/
AlignedFlow-class content), direct-operand/no-trigger steps ×3
(tracked-intentional). Gates: full drill green under both guards.

**Phase 2c LANDED (2026-07-11) — the "inline token gluing to a preceding
block" guard is deleted.** The pre-Phase-1 `inlineGluesToPrecedingBlock`
fence in `assembleFlowFallback` (plus `isInKeyword` /
`isGlueablePrefixBlock`) is gone: the placement fold already glues an
inline follower onto a block's last line itself (`glueOntoLastRow`'s
`B.addSuffix`), so the `{ … } =` record-pattern headers and their kin
materialize directly. **Two-iteration fence lesson:** deleting the guard
exposed MultilineBlockComments `#13` (`decl {- mlbc -} x =`): the decl
header is an `AcrossOrVertical` first item of the indented decl flow, and
inside its box the mlbc body lines are align-anchored (`B.prefix` padding)
while the follower's fresh row is ambient-anchored — the Doc gives the
ambient lines the outer flow's nest on top, and no uniform per-box shift
satisfies both anchors (the t42/t43 exact-space class). A first, broad
fence ("anything after a prefix-glued mlbc") cost 8 Errs including ~6
previously-green nodes (HeaderCommentNameWrap's module header,
UnionVariantTrailingComment's variant — both *uniform* consumers where the
bare push is correct); the landed fence is at the OUTER placement and
three-gated: AsFirst + multi-line + `NestCarrying` + `flowIndent > 0` +
`subtreeHasMultilineBlockComment` (without an mlbc the box is homogeneous
— broken record-pattern headers, PatternLayoutByAuthor — and the bare
push is the proven materialization). It catches exactly the 1 `#13` node.

Census after: **22 Errs, 0 mismatches** (inline-token class 4 → 0, `#13`
fence +1, net −2 from 24; the OpAndRhs-continuation and inline-paren
KitchenComments nodes also shifted classes). Gates: effectful 140/140 +
all three fuzzer runs 0 under BOTH guards.

**Phase 2d LANDED (2026-07-11) — the pipeline-step comment peel.** The full
structural mirror of the Doc's comment-bearing-pipeline path:
`makePipelineBox` routes comment-bearing forward pipelines to the generic
fold (`buildFlowBox 0`, mirroring `makePipelineDoc`'s dispatch); `makePBox`
gained a `PipelineStep` arm and `factsFor` classifies steps as
`FP.PipelineStepItem` (hasLeadingComment = first child is a comment);
`renderPipelineStepChildren` peels leading comments (own-line above `|>`,
`spanLeadingComments` + `commentNodeBox`) and trailing inline comments
(`peelTrailingCommentNodes`, the exact `peelTrailingComments True` mirror:
single-line block comments freely, at most one rightmost `--`, stop at an
mlbc) around the trigger split; the `BlockJoin` materializer now honors
`blankBefore` (blank line above a commented step that follows another
step). Mid-flow comments stay in the body and ride the preamble/suffix
folds exactly as the Doc's do — the Phase 2b′ comment gate is deleted.

Census after: **21 Errs, 0 mismatches** — the comment-bearing-triggered-
step node cleared, and the no-trigger class 3 → 1 (only the
MultilineBlockComments direct-operand glue remains, tracked-intentional;
CommentsPipeline + KitchenComments cleared). Two KitchenComments nodes now
Err DEEPER ("unexpected node among when-branches" 1 → 3) — the pipeline
Err had been shadowing a pre-existing when-branch limitation; same nodes,
more machinery exercised. Gates: effectful 140/140 + all three fuzzer runs
0 under BOTH guards, first pass.

**Phase 2e LANDED (2026-07-11) — `EmptyLine` among when-branches.** The
"unexpected node among when-branches" class (×3 after 2d un-shadowed two
KitchenComments nodes) was the blank-line marker preceding an own-line
comment that leads the next branch (`[WB, EmptyLine, comment, WB]`). The
Doc emits the one blank from the marker's `BlankLineOnly` and then joins
the comment-led branch blank-free (`GlueNoSep`); Box's `stackWithBlanks`
already supplies exactly that blank between branch boxes — so the marker
is a no-op, gated to that shape (leading position or after a pending
comment still falls back), with `lastRow` reset so nothing row-glues
across the blank.

Census after: **19 Errs, 0 mismatches** (when-branches 3 → 0; one
KitchenComments node surfaced deeper as "multi-line OpAndRhs with a
continuation operand" ×2). The frontier is now the hard/intentional tail:
record-update soft fields ×4 + `#13` fence + verbatim-opener +
bracket-list Tab item (exact-space class), soft-glue ×3 + no-trigger step
(direct-operand glue, tracked-intentional), sig-type comment (t61),
OpAndRhs-continuation ×2, own-line-comment-in-broken-flow, if-condition,
inline-paren, backward-mixed-ops (unexamined onesies). Gates: effectful
140/140 + all fuzzers 0 under BOTH guards, first pass.

**Tried + REVERTED (2026-07-11): global Tab → exact-4-spaces in `B.indent`.**
Hypothesis: every "Tab-poisoned" Err (RecordUpdate/AlignedFlow soft-glue,
record-update field values, WhenInCommentedArray's Tab-vs-prefix bracket
item) traces to `Tab` snapping to the next multiple of 4 under a
`B.prefix` pad, and the Doc never quantizes, so 0 mismatches implied the
snap was never load-bearing where green. **Falsified: 9 trust-Box
effectful failures** (record-update coupling/extensionGroup, bare-if list
item, comment-leading type record, record-pattern closing brace, three
KitchenSinks…). The two renderers reach the same bytes by DIFFERENT
arithmetic: the Doc compensates prefix widths explicitly
(`makeRecordUpdateDoc`'s `grenIndent - listPrefixWidth`), the Box gets the
same columns from Tab snapping. Killing the snap globally breaks every
construct whose Doc side compensates. Conclusion: the Tab/exact-space
class can only fall per-construct (a targeted spaces-indent or per-line
anchor where the Doc does NOT compensate), never via the primitive.

**Baseline correction discovered during the Phase 0 drill:** the earlier
"trust-Box fuzzers 0/0" note is stale. At clean `7054ae8` (pre-Phase-0) the
trust-Box idempotency fuzzer already showed **6 non-idempotent gaps**:
KitchenComments 5 (line 297 `}⏎-> {-c11-}` and 4 around the line 377/378
`Array/Maybe { code, basisPoints }` signature records) +
SignatureSegmentBreaks 1 (line 31, item 3's `returnRecordComment` fixture —
`Int⏎->⏎{ x }` with an inserted comment). All are the type-record-drop /
comment-in-flow class this item exists to fix; they predate Phase 0
(verified by stash + re-run at `7054ae8`) and were presumably introduced
with the item-3 drop landing or the `#43` fix, after the last true-0
trust-Box fuzzer run. Trust-Box whitespace fuzzers: both modes still 0.
Track these 6 as part of the Phase 1/2 acceptance measurement.

#### Diagnosis — why this class reverted three times

The `#12`/`#44` hunks are two symptoms of one fact: **Box's flow fold runs a
different decision state machine than the Doc's, and only the Doc's has been
hammered into reparse-fixed-point shape** (fix 4's synthetic `prevRow`, fix
5's structural pairing, item 3's `segmentHasDroppingRecord` pre-commitment,
`prevElided`). The two machines diverge in exactly two places:

1. **Block classification.** Box's `isBlockNode` says "block" for
   `BodyBlock`/`IndentedBlock` *or any node whose rendered box is
   multi-line*. The Doc dispatches by box type: only `IndentedBlock`,
   `BodyBlock`, `SoftIndentedBlock`, `WhenBranch`, `PipelineStep`,
   `EmptyLine`, and the gated type-record drop get block treatment;
   **everything else — however multi-line — glues via `FlowSep`** (first
   line joins the current line; continuation lands wherever its own
   `R.align`/`R.nest` puts it). Hence `#12`: the multi-line extension record
   glues as `-> { a` in the Doc (it fails `isTypeRecordLiteral`, so no drop
   fires) while Box unconditionally drops any multi-line bracket literal.
2. **`prevRow` discipline.** The Doc sets `prevRow` only for
   `UnbreakableText` leaves (plus the synthetic closing-`}` row after a
   record drop); records and everything else reset it to `-1`. Box's
   `nonCommentStep` sets `prevRow = item.startRow` for *every* inline item.
   Hence `#44`: the mlbc trails `{ x }` on the same source row, so Box's
   richer row-tracking glues it where the Doc (prevRow `-1` after a record)
   puts it own-line — and the Doc's placement is the proven fixed point.

t42/t43/t61 all failed the same way: a locally-plausible Box glue rule keyed
off real rows or rendered multi-line-ness fired in slightly different
contexts than the Doc's corresponding rule, producing **Box-only layouts**
whose idempotency nothing had ever proven (t61: "rendered right" on the
target, broke idempotency in two *other* flows).

#### Design principle

**Box has no layout policy of its own.** Byte-equality with the Doc is the
only correctness target; fixed-point-ness is then *inherited* from the Doc's
discipline instead of re-proven per tranche. Any Box decision input richer
than the Doc's (real rows where the Doc tracks `-1`, rendered-box shape
where the Doc dispatches on type) is not extra precision — it is a
divergence generator. So: make the glue decision *the same function* in both
renderers, leaving exactly one decision procedure to keep fixed-point-clean.

#### Module spec — `Formatter.Render.FlowPolicy`

- **`FlowState`** — exactly the Doc's fold accumulator minus the Doc itself:
  `{ separator : NextSeparator, prevRow : Int, prevElided : Bool }`.
  `NextSeparator` (`FirstItem | FlowSep | HardNl | AlreadyTerminated`) moves
  here from MakeRender.
- **`ItemFacts`** — the *complete legal input set* for decisions (anything
  not listed is banned): `kind` (classification of `lpnBox`: hard-block
  rule / soft block / bracket literal / comment kind / leaf / empty line),
  `startRow`, `forcesVertical` (caller-supplied from its own IR — Doc:
  `R.hasHardBreak`, Box: `not isSingleLine`), `isTypeRecordLiteral`,
  `pairedComment` (fix-5 pairing, already structural), `isElidedArrow`,
  `lastBracketEndRow` (for fix 4's synthetic `prevRow`).
- **`decide : { inlineStart, brokenFlow, indent } -> FlowState -> ItemFacts
  -> Result String { placement : Placement, next : FlowState }`**, where
  `Placement` enumerates exactly the join shapes the Doc's arms produce
  today (complete-to-the-Doc, per the approved scope):
  - `GlueSpace` — the `FlowSep` flat join (single-line items AND multi-line
    soft items like `#12`'s record)
  - `GlueNoSep` — `AlreadyTerminated` continuation
  - `OwnLine` — `HardNl` join (covers `#44`'s comment)
  - `CommentGlueSameRow` — fix 4's `} {- c -}` row-glue and the
    `prevElided` glue
  - `Block BlockRule` — indented / body / soft-indented drop
  - `DroppedRecord { leadingComment }` — the fix-5/item-3 type-record drop
  - `BlankLine`
- **Materialization stays per-renderer.** Doc: the existing
  `joinDoc`/`joinBlock`/`commentBoxDoc` snippets, keyed by `Placement`.
  Box: existing `B.stack1`/`applyIndent` plus two glue primitives keyed by
  a **static per-box-type alignment table** (verified against the Doc
  source, not curve-fit): bracket literals, multiline strings, and
  block-comment bodies render under `R.align` → glue with `B.prefix` (pads
  continuation by prefix width); `AcrossOrVertical`/`OpAndRhs`/call flows
  render under base-relative `R.nest` → glue first-line-only
  (`mapFirstLine`). A follower after a glued multi-line item glues onto its
  last line via `B.addSuffix` — the existing `glueable` mechanism
  generalized to "separator is `FlowSep` after *any* item", which is what
  the Doc has always done. Any `Placement` Box can't yet materialize stays
  an `Err` → self-verify fallback, so partial coverage remains safe.

How the hunks fall out: `#12` — `decide` returns `GlueSpace` for the
extension record (drop gate is `isTypeRecordLiteral`, which it fails) → Box
materializes `B.prefix (line "-> " …) recordBox`; byte-equal to the Doc,
whose idempotency the fuzzers already prove. `#44` — with Doc `prevRow`
discipline the same-row test fails after the record → `OwnLine` → comment
stacks, `as whole` continues fresh; again byte-equal to shipped Doc output.

#### Phasing (each phase lands separately; full drill after each)

1. **Phase 0 — characterize the Doc.** Extract `decide` out of
   `buildFlowDocImpl` with *zero behavior change*; Doc consumes it.
   Mechanical extraction — move code, don't rewrite; one arm per commit if
   needed. Gate: output byte-identical (140 fixtures + both fuzzers + the
   trust-Box drill verify exactly that).
2. **Phase 1 — Box consumes `decide`.** Rewrite `assembleFlowImpl`'s fold to
   take placements from the shared core, keeping existing materializers and
   existing `Err`s. Delete `isBlockNode`'s multi-line fallback, the
   Box-local `prevRow` tracking, and the ad-hoc gates (`isSoftGlueLiteral`
   drop condition, `trySoftGlueFlow`'s shape test) *only where* the policy
   now decides. Gate: trust-Box census same-or-better, normal gates green.
3. **Phase 2 — the two missing Box materializers**: soft-glue of a
   multi-line item per the alignment table; comment-after-block via
   `B.addSuffix`/stack. This is where `#12`/`#44` clear. Then **2b**: mirror
   `segmentHasDroppingRecord` in `makeSignatureBox` and delete
   `pairTypeRecordComments`' arrow-refusal, clearing the tracked
   KitchenComments `extremelyCommented` Box-lag node.

Drill per phase: effectful 140, `fuzz-idempotency.py -j 12`,
`fuzz-whitespace.py` both modes, and the trust-Box sed-guard drill
**including trust-Box fuzzers** (the `Array.get -1` incident: comment-pairing
changes are invisible to every other gate).

Likely free wins to track (not promised): the generalized `addSuffix`-glue
materializer is the same machinery the parked Err classes "inline token
gluing to a preceding block" (4 decls, the `{ … } =` headers —
`assembleFlowFallback`'s explicit Err) and "multi-line/mid-flow block
comment after a block" (3 decls, `placeComment`'s two Errs) are waiting on.

#### Risks / invariants

- **Phase 0 refactor risk** on `buildFlowDocImpl` (the most battle-hardened
  function in the codebase) — mitigated by the byte-identity gate; a
  transcription slip in a rarely-exercised arm is the failure mode, hence
  mechanical extraction discipline.
- **`forcesVertical` asymmetry** (Doc tests its Doc, Box tests its Box):
  they agree whenever the two renderers agree on the child; disagreement is
  exactly what the self-verify guard catches. Accepted.
- **The alignment table is load-bearing and static**: any new
  `R.align`-carrying builder on the Doc side must update it. Documented
  invariant, same class as the existing `isTypeRecordLiteralBox`
  verbatim-mirror comment — the shared core shrinks this class but can't
  eliminate it while materialization stays dual.

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
