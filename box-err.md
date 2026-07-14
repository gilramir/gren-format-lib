# Box-creation `Err` catalogue (`Formatter/Render/`)

Catalogue of every `Err` in the Box-creation phase (LPT → Box), for follow-up work.
Companion to the earlier Logical-phase (LPT-construction) catalogue, which found
6 unreachable defensive-guard `Err` sites plus one error-prefixing wrapper — all
dead code. This phase is different: it contained a **confirmed, reproducible
bug**, now fixed (see §1).

Scope: `gren-format-lib/src/Formatter/Render/MakeRenderBox.gren`,
`MakeRender.gren`, `FlowPolicy.gren`, `Box.gren`, `ElmStructure.gren`.
No shared error-construction helper exists — every site is a literal
`Err "..."` or a passthrough of a child `Result`.

`MakeRenderBox.gren` alone has **66** `Err "..."` string-literal constructions
(20 self-labelled `"unreachable: ..."`, 46 labelled `"box: ..."`), plus one
dead `if False then Err "unreachable" else ...` branch, plus many `Err e` /
`Err _` passthroughs. `Box.gren`'s and `ElmStructure.gren`'s `Err` hits are a
*different* `Result` entirely (`Result Box Line` / `Result (Array Box) (Array Line)`,
an Either-style "is this box single-line" test) — not user-facing format
failures at all.

## 1. Reachable bug (confirmed by running the formatter) — FIXED

**`MakeRenderBox.gren:2615`** (pre-fix line number) — `makeSignatureBox`, arm `else if not forceVertical then` (2601-2616):

```
Err "box: inline signature segment unexpectedly broke across lines"
```

Fires when `buildFlowBox 0 children` returns a multi-line `Box` even though
`forceVertical` was computed `False`. `forceVertical`
(`signatureForceVertical`, 2465-2489) only detects a break **between**
`->`-delimited segments; it never inspects whether a *single* segment (no
`->` at all) itself spans rows, and `segmentHasDroppingRecordBox`
(2445-2460) is gated `Array.length segments > 1`, so it doesn't run either
for a one-segment signature. A record-type field literal (`AlwaysVertical`,
set by `InsertTypes.gren`'s `fieldsMultiLine` when the record's *fields* —
not just its brackets — span source rows) inside a single-segment (no-arrow)
`StFunctionSignature`/`StPort` produces exactly this: `forceVertical =
False`, `hasComment = False`, and `buildFlowBox 0 children` returns a
multi-line box → `B.isLine` fails → `Err`.

Verified end-to-end with the built CLI (`gren-format/app`, `devbox run
build`):

```gren
config :
    { name : String
    , age : Int
    }
config =
    { name = "x", age = 1 }
```

```
$ node app --show config.gren
-- Could not format this file. Please report this 'gren format' bug. - .../config.gren

box: inline signature segment unexpectedly broke across lines
```

Also reproduces for an **extensible** record type in the same position
(`{ r | name : String, age : Int }` with fields on separate rows) — same
message, same line. Does **not** reproduce for a `ParenBlock` (`Int -> Int`
in parens) in the same position — elm-format-style flattening handles that
case, confirming the bug is specific to the record-literal
(`AllAcrossOrAllVertical`/`AlwaysVertical`/`RecordUpdate`) family reaching
`buildFlowBox 0` unguarded when it's the *sole* segment of a signature.
`type alias` bodies (`makeTypeAliasBody`, line 927-929) have no such check
and are unaffected.

**Fix applied:** `forceVertical` now also fires for a single-segment type
(`Array.length segments == 1`) when that lone segment carries a dropping
record, checked via `segmentHasDroppingRecordBox` extended with an
`includeHead` parameter (the multi-segment call site passes `False`,
preserving prior behavior; the new single-segment call site passes `True`,
since there's no second segment for `signatureForceVertical`'s row-break
check to compare against). Gated on `not hasComment` so it doesn't preempt
the existing comment-bearing single-segment handling above (which has its
own, already-correct logic and its own test coverage). Also extended the
"is this a dropping record type" test (`isDroppingSignatureRecordNode`) to
recognize the `RecordUpdate` box, since an extensible record type
(`{ r | name : String }`) reuses that box the same way a record-update
*expression* does — safe here because every call site restricts `node` to a
signature's TYPE position, where `RecordUpdate` can only mean an extensible
record type. Regression-guarded by
`tests/testfiles/Formatter/SignatureSoleRecordType.{dirty,formatted}.gren`
(plain + extensible variants); full gates green (effectful 170/170,
idempotency 0 gaps, whitespace fuzzer both modes 0 drift).

## 2. The `makePBox` wildcard — enumerated against `LPBox`

`LogicalPrintingTree.gren:151-402` defines 29 `LPBox` constructors
(`RootBox`, `OriginalRows`, `EmptyLine`, `UnbreakableText`, `SynthesizedText`,
`SingleLineComment`, `BlockComment`, `DocComment`, `AcrossOrVertical`,
`IfCondition`, `AllAcrossOrAllVertical`, `AlwaysVertical`, `RecordUpdate`,
`EmptyBracketed`, `IndentedBlock`, `PipelineStep`, `Pipeline`, `BodyBlock`,
`WhenBranch`, `WhenBranchPattern`, `ParenBlock`, `OpAndRhs`, `Binop`,
`AlignedFlow`, `WhenFlow`, `PrefixGlue`, `Glue`, `SoftIndentedBlock`,
`MultilineString`).

`makePBox` (MakeRenderBox.gren:2811-2994) explicitly matches all 27
non-`RootBox`/non-`OriginalRows` constructors — including every one that
structurally can never reach it (`SingleLineComment`/`BlockComment`/
`DocComment` → `"unreachable: ... never reaches makePBox"`, `AlignedFlow` →
`"unreachable: AlignedFlow is never constructed"`) — then falls through to:

```
2989   _ ->
2990       -- unreachable: every LPBox constructor other than RootBox/
2991       -- OriginalRows is matched explicitly above. RootBox is the
2992       -- singular tree root and OriginalRows is always a direct child
2993       -- of RootBox — neither is ever passed to makePBox as a child.
2994       Err "unreachable: construct never reaches makePBox"
```

Because every constructor is already matched, this wildcard is currently
**dead** — but it is exactly the guard the project docs describe: **if a new
`LPBox` constructor is ever added without a corresponding `makePBox` arm, it
silently falls into this branch and Errs the whole format** for any
declaration containing it, with no compiler-enforced exhaustiveness check
(Gren's `when` allows a `_` catch-all). This is the single highest-leverage
spot in the file for a future regression.

Contrast: `makePrettyLineBox`'s inner `when stype is` (line 27-75) dispatches
on `SyntaxType` (11 constructors, `LogicalPrintingTree.gren:100-111`) with
**no wildcard** — all 11 are matched by name, so the compiler itself
enforces exhaustiveness there; adding a `SyntaxType` constructor would be a
compile error, not a silent runtime `Err`. `makePBox`'s `LPBox` dispatch
chose the wildcard style instead.

## 3. A second, structurally identical wildcard — `FlowPolicy.Placement` in `assembleFlowImpl`

`FlowPolicy.gren:188-198` defines `Placement` with 10 constructors
(`AsFirst`, `GlueNoSep`, `GlueSpace`, `SoftSep`, `OwnLine`,
`OwnLineAfterBlank`, `IndentNoBreak`, `BlockJoin`, `DropBlock`,
`BlankLineOnly`). `assembleFlowImpl` (MakeRenderBox.gren:4240-4850)
materializes them in three separate `when decision.placement is` blocks,
each ending in a wildcard `Err`:

- `4415`: `Err "box: unexpected multi-line comment placement"` (handles AsFirst/GlueNoSep/OwnLine/GlueSpace)
- `4450`: `Err "box: unexpected terminating comment placement"` (same 4)
- `4477`: `Err "box: unexpected inline comment placement"` (AsFirst/GlueSpace/SoftSep/OwnLine)
- `4772-4773`: `Err "box: unexpected flow placement"` (AsFirst/GlueNoSep/SoftSep/OwnLine/IndentNoBreak/BlockJoin/DropBlock/BlankLineOnly — 8 of 10)

Traced against `FlowPolicy.decide` (FlowPolicy.gren:211-433):
`commentPlacement`/`LineCommentItem`/`BlockCommentItem` can only ever yield
`{AsFirst, GlueNoSep, GlueSpace, OwnLine}`, so 4415/4450/4477 are provably
unreachable today, not just "probably". The 4772-4773 wildcard's two
uncovered placements are `GlueSpace` (never assigned to a non-comment
`ItemFacts` case in `decide`) and `OwnLineAfterBlank` (assigned only by
`WhenBranchItem`). Grepping `MakeRenderBox.gren` for `WhenBranchItem`
returns **zero hits** — `factsFor` (line 4259-4319) never constructs it;
`WhenBranch` nodes are routed exclusively through the dedicated
`renderWhenBranchesBox`/`makeWhenBranchBox` path, never through
`assembleFlowImpl`'s generic `factsFor`. So `FP.WhenBranchItem` and
`Placement.OwnLineAfterBlank` are dead in the current wiring — this is the
same "future-enum-growth trip-wire" pattern as `makePBox`'s wildcard, one
layer down in the pipeline.

## 4. Verbatim "unreachable"/similar quotes (all 20, `MakeRenderBox.gren`)

| Line | Message |
|---|---|
| 755 | `"unreachable: comment-line node"` |
| 1087 | `"unreachable: pipeline operator"` |
| 2195 | `"unreachable: when-branch shape"` |
| 2255 | `"unreachable: comment before first when-branch"` |
| 2320 | `"unreachable: comment left pending after the last when-branch"` |
| 2580 | `"unreachable: multi-line signature header"` |
| 2774 | `"unreachable: multi-line exposing item"` |
| 2906, 3395, 3443 | `"unreachable: bracket kind"` (×3, one per bracket-punc call site) |
| 2963 | `"unreachable: SingleLineComment never reaches makePBox"` |
| 2967 | `"unreachable: BlockComment never reaches makePBox"` |
| 2973 | `"unreachable: DocComment never reaches makePBox"` |
| 2987 | `"unreachable: AlignedFlow is never constructed"` |
| 2994 | `"unreachable: construct never reaches makePBox"` (the wildcard, §2 above) |
| 3029 | `"unreachable: multi-line glue suffix"` |
| 3520 | `"unreachable"` — inside literal dead code `if False then Err "unreachable" else ...` (see §5) |
| 4550 | `"unreachable: multi-line item soft-glued after a block"` |
| 4639 | `"unreachable: multi-line non-align paren soft-glued in a flow"` |
| 4659 | `"unreachable: multi-line non-paren unclassified soft-glue item"` |

All 20 carry an inline comment arguing unreachability from AST/construction
invariants; spot-checked several (binop flattening always yields ≥1
operand, `PipelineStep` children always start with the op leaf, exposing
items are always `UnbreakableText` leaves, `ListBrackets` has exactly 3
constructors) and found the reasoning sound in each case examined. None of
these 20 quote "TODO" or "not supported" language — the codebase uses
"unreachable" as its dead-code idiom, not "TODO".

## 5. Notable: literal dead code, not just a runtime-unreachable guard

**`MakeRenderBox.gren:3519-3521`**, inside `commentBracketListBox`'s fold step:

```
3519    if False then
3520        Err "unreachable"
3521    else
```

The condition is a hardcoded `False` — this branch cannot execute regardless
of input; it is disabled at compile time, not merely defensively
unreachable. The 14-line comment above it (3505-3518) explains it is a
stand-in for handling a Tab/prefix composition bug ("Fall back until the
Tab-vs-prefix interaction is modelled") — i.e. a known, currently-unaddressed
limitation that was scaffolded but never wired live.

## 6. Defensive-guard `Err` sites verified unreachable by construction (representative sample; ~35 more `"box: ..."` sites follow this same shape and were checked against their construction site)

- **75** `"box: root child not an OriginalRows funcDecl"` (`makePrettyLineBox`) — per architecture doc, every `RootBox` child is `OriginalRows` or `EmptyLine`; confirmed no other constructor is ever pushed under `RootBox` (`VerticalSpace.gren`/`MakeLogical.gren`).
- **351** `"box: empty binop chain"`, **426** `"box: empty binop"` — `Binop` is only constructed from a real `Src.Binop` in `InsertExpressions.gren:584`, which always yields ≥1 operand.
- **638/672** `OpAndRhs` empty/non-leaf-op — `OpAndRhs` children are always `[opLeaf, ...rhs]` by construction.
- **912** `"box: empty declaration"` — `type alias`/`type` bodies always have a header + body block.
- **1041** `"box: union has no variants"` — a `Src.TypeUnion` always has ≥1 variant.
- **1303** `"box: multiline lambda paren: no children"` — only call site (`stepBodyBox:1461`) is gated by `isMultilineLambdaParenBlockBox`, which already confirmed `Array.get 1` exists.
- **1590/1743** backward-pipeline "no children"/"empty body" — `PipelineStep` nodes are always built `[opNode] ++ body` (`InsertExpressions.gren:548`).
- **1872/1877** if-condition empty/missing-then — a `Src.If` always has condition+then.
- **1994** `"box: empty record-update field"` — only called after `recordFieldValueDrops` already proved `Array.popLast` succeeds on the same array.
- **2142/2147/2152** `when` missing children/is/when — `WhenFlow` is always `[when, ...scrutinee, is, IndentedBlock]`.
- **2195/2198** when-branch shape/empty — `WhenBranch` is always built with exactly 2 children (pattern, body) by `folderInsertWhenBranch`.
- **2303** `"box: unexpected blank line among when-branches"` — traced against `VerticalSpace.insertBlanksInWhenFlow` (only inserts `EmptyLine` directly after a `WhenBranch`, before a line comment); the guard `Array.isEmpty acc.pending && not (Array.isEmpty acc.boxes)` always holds at that insertion point.
- **2522** `"box: signature has no colon"` — a `StFunctionSignature`/`StPort` always has a `:` per Gren grammar.
- **2752** `"box: inline exposing list rendered multi-line"` — only caller passes `effectiveFV = forceVertical || hasComment`; when `False`, `makeExposingListBox` provably returns a single-line box (traced the one call chain).
- **3006/3029** empty glue / multi-line glue suffix — `Glue` accessor children are freshly built plain-text leaves (`InsertExpressions.gren`'s `insertAccess`).
- **3190** `"box: when-in-paren has no children"` — same `WhenFlow` invariant as 2142 above, reached via the same node.
- **3426** `"box: not a comment node"` (`commentNodeBox`) — every call site pre-filters with `nodeIsComment`/a comment-only box slot.
- **3815** `"box: empty broken flow"` — all traced call sites (`buildFlowBoxBroken`, `assembleBrokenCall`) pass non-empty `items`.
- **4073/4078** `"box: multi-line item in soft-glue flow"` / `"box: non-inline comment in soft-glue flow"` — these `Err`s never escape: `trySoftGlueFlow` (4087-4147) consumes them via `traverseResult` and folds any `Err` into `Nothing` (→ falls back to `assembleFlowImpl`), so they are an internal `Maybe`-via-`Result` signal, not a user-facing failure path.

## 7. `Box.gren` / `ElmStructure.gren` — not part of the failure catalogue

`Box.gren:161-168` (`isLine : Box -> Result Box Line`) and `:171-188`
(`allSingles`) return `Err` values typed `Box`/`Array Box`, used purely as an
Either-style "is this single-line" test throughout the renderer.
`ElmStructure.gren:56` (`{ cs = Err _ } -> verticalGroup ...`) matches on
`B.allSingles`'s result and routes to a normal, correct rendering path
(`verticalGroup`) — it is not a failure at all. Neither file constructs any
`Err "..."` string message; the initial grep hits here are a different
`Result` entirely from the `Result String Box` failure type this catalogue
is about.

## 8. Signatures with no `Err` arm at all (fully total `Result String Box` builders)

`nodeIsComment`, `subtreeHasComment`, `subtreeHasMultilineBlockComment`,
`typeHasCommentBracket`, `flattenBinopNodes`, `opText`,
`bracketOperandForcesVertical`, `operandIsMultilineBracketLiteral`,
`isChainBreakingOperandBox`, `nodeSpansRows`, `makeTypeAliasBody` (927-929,
unconditionally delegates), `rowsSpanMultiple`, `isPipelineStepNode`,
`nodeForcesVerticalBox`/`flowChildForcesVerticalBox`/`subtreeHasVerticalBox`
(swallow inner `Err` to `False` — see §9),
`isMultilineLambdaParenBlockBox`/`…AnyBodyBox`,
`isMultilineContentParenBlockBox`, `splitStepTrigger`,
`parenContentIsLambdaHead`, `makeMultilineParenArgBox` (1398-1404,
unconditional `Result.map`), `spanLeadingComments`/`spanTrailingComments`/
`peelTrailingCommentNodes`, `glueTrailingComments`,
`makeBackwardCommentedPipeline`/`renderCommentedBackwardStep`,
`makeWhenBranchPatternBox`, `stackWithBlanks`,
`leafValue`/`isArrowLeaf`/`isColonLeaf`, `splitSignatureSegments`,
`segFirstRow`/`segLastRow`, `segmentHasDroppingRecordBox`,
`signatureForceVertical`, `leafLineRow`, `exposingLineFallback` (2734-2736,
unconditional delegate), `verticalExposingBox`, `makeGlueBox`'s base-box
handling, `glueCommentSuffix`, `bracketPunc` (total over its 3-constructor
domain), `renderFlowItems`, `nodeStartRow`/`isCommentItem`,
`buildFlowBox`/`buildFlowBoxInline`/`buildFlowBoxBroken` (each delegates
entirely), `shouldGlueBox`/`nodesShareStartRow`, `joinInline`,
`isBlockNode`/`isSingleLine`, `parenInnerIsAlignCarrying`,
`softGlueAlignment`, `isSoftGlueLiteral`, `trySoftGlueFlow`, `assembleFlow`
(4150-4162, delegates), `pairTypeRecordComments`,
`softBlockChildForcesVerticalBox`, `isEmptyLine`/`isElidedArrow`,
`applyIndent`, `traverseResult`.

## 9. `Err`-swallowing points (an inner Err becomes `False`, not a propagated failure)

Three(-plus) helpers deliberately catch a genuinely-possible inner `Err` and
downgrade it to `False` rather than propagating it: `nodeForcesVerticalBox`
(1152-1159: `Err _ -> False`), `segmentHasDroppingRecordBox` (2457-2458),
`parenContentLeadsWithMultilinePrefixGlue` (3298-3299),
`softIndentedBlockChildForcesVerticalBox` (3364-3365). Each is documented
inline as "an unrenderable node counts as not-forcing". This doesn't hide
failures from the user — the same subtree gets rendered again through the
real `makePBox` path later in the same declaration's assembly, where its
`Err` (if genuinely reachable, as in §1) surfaces normally; these call sites
are only speculative "does it force vertical" probes, not the final render.

## 10. How a Render-phase `Err` surfaces vs. the Logical phase

- **Logical phase** (`Formatter.gren:48-56`, `MakeLogical.gren:96-101`):
  `makeLogicalPrintingTree` chains every top-level declaration via
  `resultFoldl` (`LPTHelpers.gren:150-155`), which is `Array.foldl (\item
  acc -> acc |> Result.andThen (callback item))` — a **true short-circuit**:
  once one declaration `Err`s, `callback` is never invoked for the remaining
  declarations. Processing stops at the first failure.
- **Render phase** (`MakeRender.gren:30-41`): `makePrettyResult` first does
  `Array.map (\node -> Result.map String.trimRight (makePrettyLine node))
  (lpnChildren root)` — this **eagerly renders every top-level child**
  regardless of whether an earlier one failed (plain `Array.map`, not a
  short-circuiting fold) — then joins the resulting `Array (Result String
  String)` via `joinResultStrings` (`Formatter/Strings.gren:29-40`), which
  *does* fold with `Result.andThen` and so **reports only the first `Err` in
  document order**. Net user-visible effect is the same (first failure, in
  source order, is what's shown) but the Render phase does strictly more
  work: every declaration after the failing one is still fully rendered
  (and its own potential `Err`, if any, is silently discarded) before the
  fold picks the first one to report.
- **No re-prefixing anywhere in this phase**: `Formatter.gren:48-56`'s
  `prettyPrint` passes both the Logical-phase `Err` and `makePrettyResult`'s
  `Err` straight through unmodified (`Err errString -> Err errString`; `Ok
  lpTree -> makePrettyResult lpTree` with no `Result.mapError`). Contrast
  with the Logical phase's one identified re-prefixing wrapper (per the
  prior catalogue) — the Render phase has no equivalent; whatever leaf `Err
  "box: ..."`/`Err "unreachable: ..."` string is constructed deepest in
  `MakeRenderBox.gren` reaches the CLI byte-for-byte.
- **CLI presentation** (`gren-format/src/Format.gren:104-109, 550-555`):
  `renderModule` maps *any* `prettyPrint` failure (Logical or Render,
  indistinguishably) to `PrettyPrintFailure { path, error = errString }`,
  rendered under the fixed heading `"Could not format this file. Please
  report this 'gren format' bug."` followed by the raw error string
  verbatim. This is a different heading/`Error` variant from `ParseFailure`
  ("FAILED TO PARSE") — so at the CLI, a Render-phase Err is visually
  indistinguishable from a Logical-phase Err (both show the generic "bug,
  please report" banner), the only signal being the literal text of the
  message itself (e.g. `"box: inline signature segment unexpectedly broke
  across lines"` vs. whatever the Logical phase's wrapper prefix looks
  like).

## Open items / next steps

1. ~~Fix the reachable bug in §1 (`makeSignatureBox` single-segment record-type signature).~~ DONE.
2. Consider converting the `makePBox` wildcard (§2) and the `assembleFlowImpl` `Placement` wildcards (§3) into exhaustive `when` matches (no `_ ->` arm) so a future new `LPBox`/`Placement` constructor is a compile error instead of a silent runtime `Err`.
3. Decide what to do about the scaffolded-but-disabled Tab/prefix branch in §5 (`if False then Err "unreachable" else ...`, MakeRenderBox.gren:3519-3521) — currently masks a known, unaddressed limitation.
