# Remaining idempotency gaps — hand-off

Self-contained hand-off for the 6 non-idempotent gaps left after the
83 → 6 burn-down. Read with `BracketPath.md` (problem/root-cause analysis) and
`BracketPathFix.md` (the bracket-path refactor design + what landed). This file
is the per-gap reproduction + diagnosis so each can be picked up independently.

## State (2026-05-25, branch `formatter`)

- Suite: **225/0** (`cd compiler-node/effectful-tests && ./run-tests.sh`).
- Fuzzer: **3 non-idempotent gaps**
  (`cd compiler-node/effectful-tests && python3 fuzz-idempotency.py`).
- **Gaps 1, 2, 5 FIXED** (see their sections). Gaps 3, 4, 6 remain.
- Also added 10 effectful regression fixtures for the 2026-05-24 fuzzer fixes
  (which had no suite coverage); that's why the suite count jumped 189 → 225.

## How to reproduce any gap

```
cd compiler-node/effectful-tests
python3 fuzz-idempotency.py -v testfiles/Formatter/<Fixture>.formatted.gren
```

`-v` prints, for each non-idempotent gap: the source line, a `…ctx⟨here⟩ctx…`
marker showing where a `{- ¤ -}` block comment was inserted, and the unified
diff between the first format (`format¹`) and the second (`format²`). The bug is
always that those two differ. The fuzzer inserts the comment into one
inter-token whitespace gap at a time; the gaps below are identified by their
line + context marker.

## The common theme

Five of the six are the same shape: **a comment that trails the last token of
some construct is claimed by that construct and rendered at its indent on
`format¹`, but when it lands on its own line there it sits past the construct's
row range and reparses as a comment at the *enclosing* level on `format²`** — so
the indent/placement oscillates. This is an *attachment* ambiguity
(`Formatter.Comments.findOrCreateOrigRow` / the descent), not a rendering bug;
render-time tweaks don't fix it (see the dead-ends in `BracketPathFix.md`).

The constructs that were fixable (when-branch, type-union variant, record /
array / record-update literal, paren) each had a builder that owns *both*
attachment and rendering, or a representable closing bracket to anchor on. The
remaining ones sit at **top-level OriginalRows boundaries** (between two
declarations, or a signature and its definition) or at **let-binding / `in`
boundaries**, where the only shared lever is `findOrCreateOrigRow`, and changing
that reroutes *every* trailing-comment-inside-a-construct — too broad (see
"Cross-cutting lever" at the end).

---

## Gap 1 — KitchenSink ~line 410 — signature→definition boundary — ✅ FIXED (2026-05-25)

**Fix landed:** `Formatter.Comments.findOrCreateOrigRow` now declines to claim a
comment for a `StFunctionSignature` `OriginalRows` when the comment sits past the
signature's last token (new helper `trailsFunctionSignature`, gated by the
existing `commentInsideTrailingBracket` so a comment still inside a trailing
record/paren type is left to descend normally). The comment then falls through to
a fresh top-level `OriginalRows` spliced between the signature and its definition
— rendered at **column 1 on every format** (the canonical "leading comment of the
definition" placement). `findOrCreateOrigRow` gained a `col` parameter for this.

This also re-homed `KitchenComments`' `extremelyCommented` `{-c21-}` (which trails
the signature's record `}`) from inline `} {-c21-}` to column 1 — the fixture was
updated to match. Suite still 189/0; fuzzer 6 → 5 gaps; no new gaps anywhere.

The original analysis follows.

**Summary:** a comment after a function signature's last token, before the
definition, renders at the signature's continuation indent (4) on `format¹` but
column 1 on `format²`.

Fixture context:
```
fold : (a -> accumulatorValue -> accumulatorValue) -> accumulatorValue -> Tree a -> accumulatorValue
fold reducerFunction initialAccumulatorValue currentTreeNode =
```
Reproduce: gap at `… -> accumulatorValue⟨here⟩⏎fold reducerFunctio…` (the final
`accumulatorValue` of the signature, before the definition).

```
format¹:    {- ¤ -}      (indent 4, own line — claimed by the StFunctionSignature OriginalRows)
format²:{- ¤ -}          (column 1 — a top-level comment between the sig and def)
```

**Diagnosis:** the `StFunctionSignature` OriginalRows range is
`[sigStart.row, max(childrenLast, locType.end.row)]`. The comment is on the
signature's last row, so `findOrCreateOrigRow` claims it for the signature; it
renders own-line at the signature flow's `grenIndent`. On reparse it's one row
below the signature's last token → outside the range → a new top-level
OriginalRows at column 1. The signature renderer is `makeFlowIndentableDoc`
(via the `_ ->` arm of `makePrettyLineDoc`).

**Suggested approach:** canonicalize a comment that sits *between* a signature
and its definition to a leading comment of the definition (column 1), the same
on both formats. Likely needs special-casing the sig→def adjacency in
`Formatter.Comments` (the def is the immediately-following OriginalRows), since a
generic "don't claim after-last-token comments" change is too broad.

## Gap 2 — KitchenSink ~line 430 — last let binding → `in` — ✅ FIXED (2026-05-25)

**Fix landed (glue-to-`in`, NOT own-line):** the descent in
`Formatter.Comments.insertCommentIntoSubtree` now refuses to descend into a child
whose next sibling is the `in` keyword (`isInKeyword`, a `SynthesizedText "in"`)
when the comment trails that child's last token (gated by the existing
`commentInsideTrailingBracket`). The comment then routes to the `let` level and
past the position-less `in` — rendering after `in` (`in {- c -}` for a block
comment, own-line below `in` for `--`/multi-line), matching the already-stable
`format²` on both formats.

**Why not own-line (the original suggestion):** `in` has no source position, so a
comment on its own line between the last binding and the body is *indistinguishable*
from a body-leading comment (one the author wrote after `in`, which correctly sits
at the `in` indent). An own-line attempt pulled 3 real body-leading comments
(KitchenSink, KitchenComments, MultilineBlockComments) into the preceding binding.
Glue-to-`in` is the only stable canonical form that doesn't conflict with
body-leading comments — they share the same after-`in` path. (User chose this
trade-off; own-line would need either a fragile column heuristic or parsing the
`in` token, which the project deliberately avoids — see Gap 6 / `BracketPath.md`.)

Like Gap 1, a *short* trailing comment that fit inline (`"!" {-c45-}` in
KitchenComments) also routes after `in` now — can't distinguish "fits" from
"overflows" at attachment time; fixture updated. New regression fixture
`LetInTrailingComment`. Suite 192→195/0; fuzzer 5→4; no new gaps.

The original analysis follows.

**Summary:** a comment after the last let binding, before `in`, renders own-line
at the binding indent on `format¹` but glues to `in` (`in {- ¤ -}`) on `format²`.

Fixture context:
```
                                accumulatedSoFar) initialAccumulatorValue branchAttributesArray
            in
```
Reproduce: gap at `…ranchAttributesArray⟨here⟩⏎            in⏎…`.

```
format¹:                    {- ¤ -}      (own line, binding indent 20)
            in
format²:            in {- ¤ -}            (glued after `in`)
```

**Diagnosis:** the comment is between the let-bindings block and the `in`
keyword. `format¹` keeps it inside the bindings block (own line); `format²`
attaches it to the `in`/body. Lives in the `let … in` rendering (the bindings
IndentedBlock vs the `in` token / body BodyBlock) + its comment attachment.

**Suggested approach:** decide one home for a comment in the bindings→`in` gap
(most natural: own line at the binding indent, i.e. `format¹`) and make
attachment + rendering agree on it regardless of the comment's input row.

## Gap 3 — KitchenSink ~line 291 — between let bindings (record-update field value)

**Summary:** a comment after a single-field record-update binding value, before
the next binding's leading comment, renders at indent 12 on `format¹` but 8 on
`format²`.

Fixture context:
```
        -- Single-field update bound to a name in let.
        containerWithBumpedTimestamp =
            { startingContainer | generatedAtMillis = startingContainer.generatedAtMillis + 1 }
        -- Single-field update inside a let that uses an if/then/else for the new field value.
```
Reproduce: gap at `…eratedAtMillis + 1 }⟨here⟩⏎        -- Single-f…` (after the
update's `}`, before the next binding's `--` comment).

```
format¹:            {- ¤ -}    (indent 12 — claimed by the binding's value subtree)
format²:        {- ¤ -}        (indent 8 — the let-bindings block level)
```

**Diagnosis:** the comment trails the binding's value (the record update, which
DOES now carry its `}` close position). On `format¹` it's pulled to the value's
deeper indent; on `format²`, own-line, it sits at the bindings-block indent.
Same boundary ambiguity, between two let bindings.

**Suggested approach:** a comment between two let bindings should sit at the
bindings-block indent (8), the binding-leading-comment column, on both formats.
Related to Gap 6 (also a between-let-bindings comment).

## Gap 4 — KitchenSink ~line 214 — comment after `)` in a record field value + blank churn

**Summary:** a comment after a parenthesised call result inside a multi-line
`if/else` expression changes indent (28→20) AND a blank line appears/disappears.

Fixture context:
```
                        else
                            Err ("unknown scheme in tracing sink endpoint: " ++ sinkEndpoint)

                    Nothing -> Err "tracing enabled but no usable sinks configured"
```
Reproduce: gap at `…: " ++ sinkEndpoint)⟨here⟩⏎⏎                  …` (after the
`)` closing the `Err ( … )`, at the end of an `else` branch).

```
format¹:                            {- ¤ -}      (indent 28) + a blank line
format²:                    {- ¤ -}              (indent 20, no blank)
```

**Diagnosis:** two coupled effects — an indent shift (the comment trailing the
`)` of an `if/else`-branch expression) and a blank-line churn. Likely needs both
the boundary-attachment fix AND a blank-line (VerticalSpace / branch-separator)
adjustment. The messiest of the six; consider last.

## Gap 5 — MultilineBlockComments ~line 105 — leading comment of a non-first let binding (blank churn) — ✅ FIXED (2026-05-25)

**Fix landed:** the root cause was `selfBoxBounds` in
`Formatter.LogicalPrintingTree` computing a `BlockComment`'s `maxRow` as
`loc.end.row + countNewlines loc.value`. `loc.end.row` is *already* the comment's
real end row (the parser records the true span), so this over-counted by the
number of content lines — matching neither `DocComment` nor `MultilineString`
(both use `loc.end.row`). The inflation pushed a let-binding's row range past its
last token, so a *leading* multi-line comment of the next binding fell inside the
previous binding's range and was absorbed as a *trailing* comment of it (rendered
own-line + a spurious blank before the next binding); reparsed own-line it
re-attached as a leading comment (no blank) → oscillation. Changed to
`maxRow = loc.end.row`. (Same inflation e8ec8e3 worked around in the union-variant
renderer; this fixes it at the source.)

Updated the `MultilineBlockComments` fixture (the spurious blank after the
`{- 47 … -}` comment is gone). That fixture's idempotency+formatting sub-tests now
guard the fix (reverting it re-adds the blank). Suite 225/0; fuzzer 4 → 3.

The original analysis follows.

**Summary:** inserting a comment in a let body makes a spurious blank line appear
between a non-first binding's leading block comment and the binding itself on
`format¹`, absent on `format²`.

Fixture context (the `{- 47 … -}` is a leading comment of the `z = y` binding):
```
         x
        {- 47 a — leading comment of a NON-FIRST let binding
           47 b
           47 c -}
        <blank on format¹ only>
        z = y
    in
```
Reproduce: gap at `…b⏎           17 c -}⟨here⟩⏎         x⏎…`.

```
format¹: …47 c -}⏎⏎z = y      (blank between the leading comment and its binding)
format²: …47 c -}⏎z = y        (no blank)
```

**Diagnosis:** pure blank-line bug in the let-binding renderer / VerticalSpace —
a leading block comment of a non-first binding should sit directly above its
binding (no blank). Independent of the trailing-comment theme; likely the most
self-contained of the six.

## Gap 6 — Records line 1 — comment after the module `exposing ( … )` close

**Summary:** a comment after the module exposing list's `)` renders at indent 4
on `format¹` but column 1 on `format²`.

Fixture context:
```
module Records exposing ( makePoint, movePoint, distSq, singleton, makeOrigin, scalePoint, firstX )

{- Record creation, access, and update -}
```
Reproduce: gap at `…scalePoint, firstX )⟨here⟩⏎⏎{- Record creation…` (after the
exposing list's `)`).

```
format¹:    {- ¤ -}      (indent 4 — claimed by the module line, continuation indent)
format²:{- ¤ -}          (column 1 — top-level)
```

**Diagnosis:** the module `exposing` list's closing `)` has **no AST position**
(the parser discards it; this is the elided-token limitation we deliberately did
NOT solve by changing `Compiler.Parse.Context`). When the long module line wraps,
a trailing comment renders at the continuation indent (4); reparsed own-line it's
past the module line and becomes top-level. Unlike record/array/paren, there is
no representable close to anchor on.

**Suggested approach:** either (a) synthesize a stable `)` position for the
module exposing (note: a naive synthesis at the last-item end regressed earlier —
see `BracketPath.md`), or (b) special-case: a comment trailing the module line
canonicalizes to column 1 (top-level) on both formats. (b) is narrower.

---

## Cross-cutting lever (and why it's not a quick win)

Gaps 3, 6 (and partly 2, 4) would all be solved by one rule: *a comment after
a construct's last token, before the next sibling, is canonicalized to the
enclosing level (column 1 / block indent) on both formats.* The single place
that decides this is `Formatter.Comments.findOrCreateOrigRow` (top-level
attachment) / `insertCommentIntoSubtree` (the descent). But making
`findOrCreateOrigRow` refuse a comment positioned after an OriginalRows' last
token *generically* would reroute **every** trailing-comment-inside-a-declaration
— including the when-branch, union, record/array/update, and paren cases that
were fixed to attach *inside*. That blast radius is why a global change wasn't
attempted; a real fix needs per-context canonicalization rather than a global
attachment change. Gate any attempt on the full `fuzz-idempotency.py` sweep + the
189-test suite.

**Gap 1 (sig→def) used exactly this per-context lever and is the template:** it
added a `StFunctionSignature`-only guard (`trailsFunctionSignature`) to the
`findOrCreateOrigRow` search so *only* trailing-signature comments are declined,
re-using `commentInsideTrailingBracket` to keep inside-bracket comments
descending. Gaps 3/6 want the analogous narrow guard for their own stype /
context (let-binding gaps; module exposing).

## Recommended order for a hand-off

- ~~**Gap 1** (sig→def)~~ — ✅ done (2026-05-25); see its section for the
  per-context-guard template the remaining gaps can follow.
- ~~**Gap 2** (last binding → `in`)~~ — ✅ done (2026-05-25); glue-to-`in` via an
  `isInKeyword`-next-sibling descent guard (own-line was unreachable; see section).
- ~~**Gap 5** (let-binding leading-comment blank)~~ — ✅ done (2026-05-25); root
  cause was `BlockComment` `maxRow` over-count in `selfBoxBounds` (see section).
1. **Gap 3** (between two let bindings) — the remaining let-binding-boundary case;
   note Gap 2's `in`-position ambiguity does NOT apply here (the next sibling is a
   real binding, not the position-less `in`). Check whether the Gap-5 `maxRow`
   fix already moved it (it's a between-bindings comment too).
2. **Gap 6** (module-exposing `)`) — elided position; narrowest fix is
   canonicalize-to-column-1.
3. **Gap 4** (paren `)` + blank churn) — two coupled effects; do last.
