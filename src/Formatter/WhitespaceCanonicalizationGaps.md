# Whitespace-canonicalization gaps — findings

Companion to `RemainingIdempotencyGaps.md`. Where the idempotency work asks
"is an *already-formatted* file a fixed point?", this asks a stronger question:

> **Does the formatter produce canonical output that depends only on the
> program's meaning, not on the incoming whitespace?**

If you perturb the inter-token whitespace of a source file *without changing
what it parses to*, the formatted output should be byte-identical. This file
records where that does **not** hold.

## The tool

`compiler-node/effectful-tests/fuzz-whitespace.py` — for each
`testfiles/Formatter/*.dirty.gren` it perturbs whitespace and asserts **both**:

1. **AST invariance** — the perturbed file parses to the same AST as the
   original, disregarding source positions (`--pre-ast`, with `start`/`end`
   stripped recursively).
2. **Format invariance** — `format(perturbed)` is byte-identical to
   `format(original)`.

A perturbation that fails (1) is reported as `ast-changed/illegal` and
discarded — it was an illegal edit (a newline that actually changed an
indentation-sensitive parse), **not** a formatter bug. Everything reported as
`format-drift` is a genuine canonicalization gap on AST-identical input.

```bash
cd compiler-node/effectful-tests
python3 fuzz-whitespace.py --mode newline            # whole corpus (slow)
python3 fuzz-whitespace.py --mode newline -v File.dirty.gren   # one file + diffs
```

Three perturbation modes:

| Mode | Perturbation | Purpose |
|------|--------------|---------|
| `stretch` | widen every same-line gap to a varying 2–4 spaces (whole-file) | horizontal canonicalization |
| `indent`  | per-gap: push one newline gap's continuation line 4–6 spaces deeper | continuation-indent canonicalization |
| `newline` | per-gap: replace one same-line gap with `\n` + deep indent | the thorough one — turns every inter-token space into a hard break |

To reproduce a single drift, build the variant for `<file>` at a given byte
offset `gap@N` and diff `format(variant)` against `format(original)` (the
helper scripts used during analysis live in `/tmp/repro*.py`; the perturbation
for `newline` is `src[:N] + "\n" + " "*(col(N)+4) + src[end_of_gap:]`).

## Headline (corpus = 78 `.dirty.gren` fixtures)

- **`newline`: as first measured, 44/78 files fully canonical; 34 files → 262
  drift variants.** **A1 fixed → 233. B fixed → 177. C characterised + fuzzer
  refined → 117** (2026-05-26). The 117 are **Family A2 (blocked on
  compiler-common #25)** plus a small genuine-attachment residue; the ~60
  by-design comment-placement perturbations are now classified `comment-placement`
  and discarded, not counted as drift (see Family C).
- `stretch`: was 4; **B fixed → 3.** All 3 are Family C column-only
  re-attachment (`EffectModuleFxWhereComment`, `KitchenComments`,
  `TrickyComments`) — no newline, so the prev-row comment fingerprint can't see
  them; genuine open residue.
- `indent`: was 5 files (36 variants), almost all multiline-comment bodies;
  **B fixed → 0. `indent` is fully canonical.**

(All counts are from `fuzz-whitespace.py` *after* the comment-placement
refinement; the pre-refinement newline number was 177.)

The good news first: most constructs are perfectly whitespace-canonical —
`WhenPatterns` 106/106, `TypeSignature` 54/54, `BinaryOps` 77/77,
`FunctionCalls` 31/31, `LetExpression` 58/58, `Records` 185/191, etc. Every
drift falls into one of **three root-cause families** below.

Coarse split of the 262 `newline` findings (auto-categorised by diff shape):

- **128 — blank-line drift** → Family A
- **134 — "comment text moved in the diff"** → a mix of Family B (multiline
  block-comment bodies, e.g. `MultilineBlockComments`×71) and Family C (true
  re-attachment, e.g. `KitchenComments`×38).

---

## Family A — VerticalSpace blank-line decisions read input row positions

`Formatter.VerticalSpace.insertEmptyLines` inserts a blank between two
top-level siblings iff `currentRows.first > prevRows.last + 1` — i.e. "was
there a blank source row between them?". The `first`/`last` come from the
`OriginalRows` row ranges, which are derived from **AST token positions**.
Some of those ranges are imprecise, and perturbing whitespace shifts the rows,
flipping the decision. ~128 of the 262 findings.

### A1 — module `exposing` list: post-header blank line dropped

> **RESOLVED 2026-05-26.** Fixed *not* with the flat-width predicate sketched
> below but with a simpler, stronger rule: the module declaration now
> **unconditionally** gets a following blank line, regardless of how its
> exposing list renders. `VerticalSpace.insertEmptyLines` takes the
> blank-insertion branch whenever `prevRows.stype == StModule` (the existing
> 1-vs-2 group-start logic still applies inside that branch). This sidesteps the
> miscomputed `prevRows.last` entirely rather than correcting it. Side effect:
> three fixtures with a comment glued to the module header
> (`EffectModuleFxWhereComment`, `ModuleExposingTrailingComment`,
> `ModuleVerticalExposingComment`) gained a blank before that trailing comment
> and were regenerated. The mechanism analysis below is kept for the record.

The most widespread one — fires on essentially every file whose module line is
`exposing (A, B, …)` (an explicit, multi-item list). Files with `exposing (..)`
(no commas → no inject points) are immune, which is exactly why `FunctionCalls`
et al. are clean.

Repro (`TypeUnion.dirty.gren`, `--mode newline`, gap@33 — inside the list).

> **100-col boundary:** this is the headline case where the boundary is the
> crux. The blank-line decision *should* be a width decision — "does the flat
> module line exceed the 100-col page width, forcing the list vertical?" — but
> the code reads input row positions instead. The formatted module line is only
> 49 chars, so it renders horizontally no matter the input; the bug fires
> precisely because an *injected* newline made the *input* span two rows while
> the *output* stayed under 100 cols. See the fix sketch below.

**Before** — input to the formatter (a newline is injected after `Color,`, so
the exposing list now spans two input rows even though it's still short):

```gren
module TypeUnion exposing (Color,
                                     Maybe, Shape)

type Color = Red | Green | Blue
…
```

**After** — what the formatter produces (the module line collapses back to one
horizontal line, but the blank line after the header is now gone):

```gren
module TypeUnion exposing ( Color, Maybe, Shape )
type Color
    = Red
    | Green
    | Blue
…
```

Diff vs `format(original)` (which keeps the post-header blank):

```
 module TypeUnion exposing ( Color, Maybe, Shape )
-                                                    ← blank line vanishes
 type Color
```

Mechanism (confirmed via `--lpt`):

| | module `OriginalRows` | next decl `first` | `first > last+1` |
|--|--|--|--|
| original | `{first:1, last:1}` | 3 | `3 > 2` → **blank** ✓ |
| perturbed | `{first:1, last:3}` | 4 | `4 > 4` → **no blank** ✗ |

`last` jumps from 1→3 because of the `exposingClosesBelow` bump in
`MakeLogical.processModuleLine` (`MakeLogical.gren` ~lines 245–290). The closing
`)` has no AST position, so when the list renders **vertically** the module
line's `last` would be one row short; the code bumps `last += 1` to cover the
`)`. But it decides "renders vertically" from `exposedRow lastExposed >
exposedRow firstExposed` — i.e. **"items span >1 *input* row"**. The injected
newline makes the input span 2 rows while the *output* stays horizontal, so the
bump fires spuriously and over-counts `last`.

**Fix sketch.** Base the bump on whether the line renders vertically in *our
output*, which is a width decision, not an input-row decision: the
`AllAcrossOrAllVertical` list breaks iff the flat module line exceeds the
100-col page width. Replace the predicate with (or AND it with) a flat-width
check. The flat width is computable in `MakeLogical`:

```
exposed item width: ExposedLower → len(name)
                    ExposedUpper Public → len(name)+4   ("(..)" suffix)
                    ExposedUpper Private → len(name)
                    ExposedOperator → len(name)+2       ("(" name ")")
exposing list flat width = Σ(item widths) + 2*n + 2     ("( " ", "… " )")
module line flat width (non-effect) =
    len(keyword) + 1 + len(name) + 1 + 8 ("exposing") + 1 + listFlatWidth
```
Verified against the renderer: `module TypeUnion exposing ( Color, Maybe, Shape )`
= 49 chars. **Safest form**: `exposingClosesBelow = inputItemsSpanMultipleRows
&& (flatWidth > 100)`. On *canonical* inputs `inputItemsSpanMultipleRows` is
already true iff the list rendered vertically iff `flatWidth > 100`, so the AND
is provably identical to today's behavior on formatted files (no idempotency
risk) and only suppresses the spurious bump on non-canonical input. (Effect
modules add a `where { … }` block before `exposing`; their exposing list is
`Open` in practice, so `exposingClosesBelow` is already `False` — but the same
`whereClosesBelow` bump just below has the identical input-row weakness and
should get the same treatment.)

Affected fixtures (A1, ~2 each): `TypeAlias`, `TypeUnion`, `Imports`,
`IfExpression`, `WhenExpression`, `SimpleArithmetic`, `LambdasAndCalls`,
`Records`, `Ports`, `ExposedOperator`, `ExtensibleRecords`, `ModuleLine*`, …

### A2 — declaration row range anchored to an inner token → blank splits a comment from its decl

> **BLOCKED on compiler-common issue #25 (filed 2026-05-26).** Root-caused to
> the parser: every keyword-led top-level declaration (`import`, `type`,
> `type alias`, `port`) stores its `Located.start` as `{ name-row, col 1 }` —
> the *name's* row, not the leading keyword's row (Module.gren `importLoopParser`
> and the three `declarationToModule*` converters). The two rows coincide in
> formatted code, so it's invisible normally; a newline between keyword and name
> drags `start.row` down to the name. The formatter **cannot** work around this
> on its own — the keyword's true row is simply absent from the parsed AST. Fix
> belongs in compiler-common (capture the keyword position with
> `Parser.getPosition`). **Revisit and re-run the fuzzer once #25 lands.** Plain
> value declarations are immune (the name is itself the leading token).

A leading comment that documents a declaration (no blank between them) gets a
blank inserted when a newline is injected *inside* the declaration's head.

Repro (`Imports.dirty.gren`, `--mode newline`, gap@52 — between `import` and the
module name). Not a 100-col issue: the line is far under width; the gap widens
purely because `first` follows the wrapped module name to a later row.

**Before** — input to the formatter (a newline is injected between the `import`
keyword and `Array`, so the module name drops to its own row):

```gren
-- Plain import
import
          Array
```

**After** — what the formatter produces (a blank line appears between the
comment and its import, so the comment now looks detached):

```gren
-- Plain import

import Array
```

Diff vs `format(original)` (no blank — comment documents the import):

```
 -- Plain import
+                  ← blank inserted; comment now looks detached
 import Array
```

Mechanism (confirmed via `--lpt`): the import's `OriginalRows.first` is taken
from the **module-name** row, not the `import` keyword row:

| | comment `last` | import `first` | `first > last+1` |
|--|--|--|--|
| original | 3 | 4 | `4 > 4` → no blank ✓ |
| perturbed (`import⏎    Array`) | 3 | **5** | `5 > 4` → **blank** ✗ |

The `import` keyword is on row 4 both times, but `first` follows `Array` to
row 5. Same shape appears for other declaration kinds when their head wraps
(`type␤alias …`, etc.).

**Fix sketch.** The formatter already sources `OriginalRows.first` from the
enclosing `Located`'s `start.row` (`makeOrigRows locImport.start.row …`, etc.) —
so the intended anchor is right; the problem is upstream, where the parser sets
that `start.row` to the *name* row rather than the keyword row. The fix is in
compiler-common (issue #25): capture the keyword position before consuming the
keyword token. No formatter change is possible or needed once #25 lands.

Affected fixtures: `Imports`, `TypeAlias`, `Ports`, `ModuleHeaderComments`, and
others in the "blank: near comment/decl" bucket.

---

## Family B — multiline block-comment bodies re-indented from the input `{-` column

> **RESOLVED 2026-05-26.** `blockCommentDoc` (`MakePretty.gren`) now derives the
> body indentation from the **body's own structure**, never the input `{-`
> column: the smallest leading indent among the comment's content lines maps to
> a canonical offset of 3 (the width of `{- `), and deeper internal indentation
> is preserved relative to that base. Blank lines and a closing `-}` alone on
> its own line are excluded from the base so they can't drag it to column 0
> (covers the KitchenSink "preserve as-is" comment with its bare `-}`). The old
> `dedent = loc.start.col - 1` is gone. Side effect: five fixtures had
> non-canonical body offsets baked in (0/2/3 depending on how each `.dirty` was
> written) and were regenerated to uniform offset 3:
> `MultilineBlockComments`, `MultilineEffectModule`, `ImportWithComments`,
> `CommentsEverywhere`, `UnionVariantTrailingComment`. Their `.dirty` inputs
> carry non-3 offsets, so `run-tests.sh`'s formatting assertion now guards the
> canonicalization directly. A dedicated self-documenting guard was also added,
> `BlockCommentBodyIndent` (top-level comment with base offset 7→3 + a deeper
> line + a blank line + a bare `-}`; and a let-binding comment written
> flush-left to prove the offset is measured from the rendered open column, not
> the absolute column). Verified: `indent` 36→0, `stretch` 4→3, `newline`
> 233→177, suite 237/0, idempotency 0 gaps; the new fixture is itself 0-drift
> under both `indent` and `newline`. The mechanism analysis below is kept for
> the record.

A `{- … -}` whose body spans lines has its continuation lines re-indented to
align under the `{-`. That alignment is computed from the **input column** of
`{-`, so shifting that column (any mode) changes the body indentation — the
body is preserved-relative-to-input rather than canonicalised. This dominates
`stretch`/`indent` drift and accounts for most of `MultilineBlockComments`'
`newline` drift (×76).

Not a 100-col issue in either repro below — the drift is driven entirely by the
input column of `{-`, not by page width.

Repro (`AdjacentTopLevelComments.dirty.gren`, `--mode indent`).

**Before** — input to the formatter (`indent` mode pushes the `{- multi` start 4
spaces deeper; its body line `line -}` stays at its original column 3):

```gren
{- a -} {- b -}
first = 1


    {- multi
   line -} {- trailing -}
second = 2
```

**After** — what the formatter produces (the body indentation collapses to
column 0 because it is recomputed relative to the shifted `{-`):

```gren
{- a -} {- b -}
first = 1

{- multi
line -}
{- trailing -}
second = 2
```

Diff vs `format(original)` (body stays aligned under the `{-`):

```
 {- multi
-   line -}     ← original: body aligned under `{-`
+line -}        ← perturbed: body indentation collapses
```

Repro (`LetBindingComments.dirty.gren`, `--mode indent`): a standalone
multi-line comment inside a `let` shifts its body indent from 11→8 when the
binding above it is deepened.

**Before** — input to the formatter (`indent` mode deepens the gap before the
comment, pushing `{- standalone` to column 13; the body lines stay at column 11):

```gren
multi x =
    let
        first = x + 1
             {- standalone
           multi-line
           block comment -}
        second = first + 1
    in
    second
```

**After** — what the formatter produces (`{-` re-aligns to the binding column 8
and the body collapses to that same column 8):

```gren
multi x =
    let
        first = x + 1
        {- standalone
        multi-line
        block comment -}
        second = first + 1
    in
    second
```

Diff vs `format(original)` (body sits at column 11, under the `{- ` text):

```
         {- standalone
-           multi-line          ← original: body at col 11 (under `{- `)
-           block comment -}
+        multi-line             ← perturbed: body collapses to the `{-` col (8)
+        block comment -}
```

**Fix sketch.** Re-indent multiline block-comment bodies to a canonical offset
from the comment's *output* column (the `{-`), independent of the body's
original column. See `blockCommentDoc` in `MakePretty.gren` and how
`Formatter.Comments` records the body text. (Caution: the idempotency fuzzer
inserts only single-line `{- ¤ -}` markers, so this class is invisible to
`fuzz-idempotency.py` — it needs its own regression fixtures.)

Affected fixtures: `MultilineBlockComments`, `AdjacentTopLevelComments`,
`LetBindingComments`, `KitchenComments`, `KitchenSink`.

---

## Family C — comment re-attachment (the hard, known class)

> **CHARACTERISED 2026-05-26 — mostly BY DESIGN, not a bug.** Investigation
> showed the bulk of Family C is the formatter *correctly* preserving a
> meaning-bearing distinction: whether a comment **shares a line with the
> preceding token** (rendered inline, `token {- c -}`) or **sits on its own
> line** (rendered standalone, possibly re-homed to the enclosing/top level).
> Both forms are canonical — proven by committed fixtures that keep an own-line
> trailing comment as-is (`BracketTrailingComments`, `RecordUpdateFieldComment`,
> `BracketListOverflowComment`, `ImportExposingMultilineComment`). The
> rendering decision (`loc.start.row == acc.prevRow` in `buildFlowDoc` /
> `makeUnionBodyDoc`) is also load-bearing for idempotency, so forcing one form
> is both wrong and unsafe. The `newline`/`stretch` perturbations move a comment
> across a token-row boundary, which *changes* that placement; the formatter
> reflects the change. The mismatch was in the **fuzzer**: its `--pre-ast`
> AST-equivalence strips comment positions, so it treated those perturbations as
> meaning-preserving. Fixed by `comment_fingerprint` in `fuzz-whitespace.py`
> (each comment's text + whether it shares a line with its preceding token + the
> adjacent-below column-claim relation for own-line comments under a synth-led
> line — `=`, `|`, `|>` — whose visible start the formatter reconstructs from
> the first positioned token, so a same-line stretch on that line legitimately
> flips the claim); a
> perturbation that flips any fingerprint is now classified `comment-placement`
> and discarded, like `ast-changed`. Documented in the formatter README
> ("Comment placement is meaning-bearing and preserved") and guarded by the
> `CommentPlacement` effectful fixture (inline stays inline, own-line stays
> own-line). Concrete minimal example: `foo : { a : Int {- c -} }` keeps the
> comment inline; the same with a newline before `{- c -}` keeps it on its own
> line — different placements, both idempotent.
>
> **Genuine residue still flagged** (correctly — these do NOT flip the
> fingerprint): (1) a trailing comment whose inline-ness is lost when the
> *preceding field* spans extra input rows (`SignatureTrailingComment`
> gap@596/598 — a newline inside `{ a : Int }` pushes the comment own-line
> though it still trails `Int`); (2) column-only re-attachment under `stretch`
> (`EffectModuleFxWhereComment` — no newline, so a prev-row fingerprint can't see
> it). These are the true open attachment-ambiguity cases, same class as the
> idempotency burn-down; left deferred.

When surrounding whitespace shifts, a comment can attach to a *different* token
on reparse, changing its placement and sometimes the layout it forces. This is
the same attachment-ambiguity family as the idempotency burn-down
(`RemainingIdempotencyGaps.md`, `Formatter.Comments.findOrCreateOrigRow` + the
bracket-path descent) — not a rendering bug.

Not a 100-col issue — the layout flips below come from where the comment
re-attaches on reparse, not from width. (In the `TrickyComments` repro the
lambda's vertical→horizontal flip can *look* width-driven, but the horizontal
form is only 89 cols; what forced the vertical form was the comment sitting as
the bracket-list's last child, and moving it past `->` removes that force.)

Repro (`EffectModuleFxWhereComment.dirty.gren`, `--mode stretch`): a trailing
`{- c3 -}` on a `where` field detaches and re-emerges before the next trailing
comment.

**Before** — input to the formatter (`stretch` widens every same-line gap; one
physical line):

```gren
effect  module   EffectModuleFxWhereComment    where  {   command    =  TrickyCmd   {- c2 -},    subscription  =   TrickySub    {- c3 -}  }   exposing    (..)  {- trailing comment after the exposing list -}
```

**After** — what the formatter produces (`{- c3 -}` has detached from the
`subscription` field and re-emerged on the trailing-comment line):

```gren
effect module EffectModuleFxWhereComment where
    { command = TrickyCmd {- c2 -}
    , subscription = TrickySub
    } exposing (..)
{- c3 -} {- trailing comment after the exposing list -}
```

Diff vs `format(original)` (`{- c3 -}` stays on its field):

```
     { command = TrickyCmd {- c2 -}
-    , subscription = TrickySub {- c3 -}
+    , subscription = TrickySub
     } exposing (..)
-{- trailing comment after the exposing list -}
+{- c3 -} {- trailing comment after the exposing list -}
```

Repro (`TrickyComments.dirty.gren`, `--mode newline`): a trailing comment in an
array-pattern lambda moves past `->`, which also flips the lambda from vertical
to horizontal.

**Before** — input to the formatter (a newline is injected after `c`, dropping
the trailing comment + `] -> a` onto a deeper continuation row):

```gren
arrayPatTrailing =
    \[a, b, c
                 {- trailing comment after last array-pattern item -}] -> a
```

**After** — what the formatter produces (comment has jumped past `->` and the
whole lambda collapses to one horizontal line):

```gren
arrayPatTrailing = \[ a, b, c ] -> {- trailing comment after last array-pattern item -} a
```

Diff vs `format(original)` (comment stays the bracket-list's last child, which
holds the lambda vertical):

```
-arrayPatTrailing =
-    \[ a
-        , b
-        , c {- trailing comment after last array-pattern item -}
-        ] -> a
+arrayPatTrailing = \[ a, b, c ] -> {- trailing comment after last array-pattern item -} a
```

**Fix sketch.** Out of scope for a quick fix — this is the open attachment
problem. Track alongside the idempotency gaps.

Affected fixtures: `KitchenComments`, `TrickyComments`,
`EffectModuleFxWhereComment`, `SignatureTrailingComment`,
`WhenBranchTrailingComment`, `UnionVariantTrailingComment`, `PatternComments`.

---

## Suggested order of attack

1. ~~**A1**~~ — **DONE 2026-05-26.** Implemented as an unconditional
   post-module blank in `VerticalSpace.insertEmptyLines` (not the flat-width
   predicate). 262 → 233 newline-drift variants; suite 234/0; idempotency 0 gaps.
2. **A2** — **BLOCKED on compiler-common issue #25** (filed 2026-05-26). The
   parser anchors declaration `start.row` to the name, not the keyword; not
   fixable in the formatter. Revisit + re-run the fuzzer once #25 lands.
3. ~~**B**~~ — **DONE 2026-05-26.** Body indentation now derived from the body's
   own structure (min content indent → offset 3), not the input `{-` column.
   `indent` 36→0, `stretch` 4→3, `newline` 233→177; suite 234/0; idempotency 0.
4. ~~**C**~~ — **CHARACTERISED 2026-05-26 — mostly by design** (see the Family C
   blockquote). The inline-vs-own-line distinction is meaning-bearing and
   preserved; the fuzzer was over-claiming AST-equivalence for comment moves, now
   fixed via `comment_fingerprint`. Documented in the formatter README and
   guarded by the `CommentPlacement` fixture. `newline` 177→117. The genuine
   residue (a comment re-homing to a different owner — `SignatureTrailingComment`
   gap@596/598, the 3 `stretch` column re-attachments) is the open
   attachment-ambiguity problem, deferred with the idempotency gaps.

After A1, B, C: the remaining `newline` drift is **A2 (blocked on #25)** plus the
small genuine-attachment residue. Nothing else actionable in this repo until #25
lands.

After any fix, re-run `./run-tests.sh`, `python3 fuzz-idempotency.py`, **and**
`python3 fuzz-whitespace.py --mode newline` to confirm no regressions.

## 2026-06-07 — bullet-5 tail fully triaged; accept mechanism added

Triaged the whole remaining drift tail (was: `newline` 102, `stretch` 1,
`indent` 5). Two real bugs fixed, the rest classified and an **accepted** bucket
added to the fuzzer so only genuine drift fails the run.

Fixed (real formatter bugs):
- **Infix row extent** — `processInfixDecl` (MakeLogical) synthesised every
  token, so a wrapped `infix` decl's `OriginalRows` range collapsed to the
  keyword row; the blank-line pass then saw a phantom gap and inserted a stray
  blank. Fix: anchor the trailing fn token zero-width at `locInfix.end`.
  Fixture `InfixWrapped`. Killed 20 `newline` findings.
- **Signature multi-line trigger** — a line break *inside* a record type or
  `( … )` in a signature flipped the whole signature multi-line, against the
  documented "only a break between `->` parts counts" rule. Root cause: the
  opening bracket has no leaf, so `segContentFirstRow` read the first field's
  row. Fix: record the opening-bracket position on the node
  (`lpnBracketStart`/`lpnWithBracketStart` in LogicalPrintingTree, set in
  InsertTypes for `TRecord`/`TParens`) and switch the trigger to a
  *boundary* test (`segmentsBrokenAtBoundary`: a segment starts below the
  previous segment's last row). The fuzzer's `_signature_segments_span_rows`
  was updated to the same boundary rule. Fixture `SignatureSegmentBreaks`.
  Killed 5 `newline` findings.

Accepted (known limitations, flagged with reason, no longer fail the run):
- **Blank-line spacing near a keyword-led declaration** — all blank-line-only
  drift; the A2 / compiler-common#25 keyword-row bug.
- **Comment placement near a position-less header token** (`exposing`, `port`,
  effect `where`, comment-beside-comment) — same missing-position cause as
  imperfections #2/#3. Detected post-format by `format_diff_class`: a diff whose
  outputs are identical after dropping blank lines (→ blank-line accept), or
  whose code skeletons *and* comment multisets match (→ comment-placement
  accept). The multiset/skeleton guards still fail real comment loss or code
  reflow. Pre-format, `comment_fingerprint` now also treats a preceding comment
  as a line-sharing neighbour, so `{- a -} {- b -}` splits are discarded as
  placement changes.

Result: `newline` PASS (0 drift, 77 accepted), `stretch` PASS (0 drift, 1
accepted — the long-standing `EffectModuleFxWhereComment`), `indent` 1 genuine
drift left:
- **Open:** a free-standing multi-line `{- … -}` between two imports re-indents
  its body to follow a perturbed opener column (MultilineBlockComments
  gap@624), instead of from its own structure as the B fix does everywhere
  else. The B fix doesn't reach the top-level free-floating-comment path.
  Tracked for future work.

Gates after this work: effectful 315/0, idempotency 0 gaps.
