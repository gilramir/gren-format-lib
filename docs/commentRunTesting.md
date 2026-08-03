# Testing runs of comments

How this repo intends to get **more than one comment in a row** right, without
enumerating the combinations and without anyone eyeballing the output.

> **Status.** The analysis and the coverage map below are current. The
> *deletion-invariance oracle* and the *run-classification refactor* are
> proposed, not built — each is marked where it appears. Nothing here describes
> behaviour the formatter does not have; where a claim is about shipped code it
> names the function.

---

## The problem

A comment run — two or more comments with no code between them — has a test
space that grows as `3^n`, because the formatter distinguishes three comment
kinds and the rules branch on all three:

| kind | ends its row? | brings its own rows? | can ride a flat line? |
|---|---|---|---|
| `-- c` | yes | no | **no** |
| `{- c -}` on one row | no | no | **yes** |
| `{- c` … `-}` across rows | no | yes | **no** |

One comment in one gap is 3 cases. Two is 9, three is 27, and a corpus gap
count in the ten-thousands multiplies every one of them. Worse, the thing a
run gets wrong is usually *layout*, and layout has no local truth: a run can be
stable, AST-preserving, idempotent and comment-preserving while sitting in
visibly the wrong place. Every gate in this repo except the elm-format oracle
is a self-consistency check (see
[the gate list](#what-each-gate-actually-covers)), and elm-format needs a
reviewed baseline entry per divergence — which does not scale to `3^n` either.

So: enumerating is impractical, and judging the output is not mechanical.

---

## The combinatorics are mostly illusory

The escape is that a run's *effect on layout* need not depend on the sequence
at all. It already doesn't in one place, and that place is the model for the
rest.

`Render/NodeClassify.gren` decides whether a bracketed literal can stay on one
line with comments inside it:

```gren
literalCommentsRideFlatLine children =
    not (Array.any (\node -> …not ridable…) children)
```

That is `Array.all canRide` — an **all-or-nothing fold**. A run of `n` comments
contributes exactly one boolean to the layout around it. Nine kind-pairs
collapse to two outcomes; twenty-seven kind-triples collapse to the same two.

Where that pattern holds, `3^n` is not the size of anything. The work is to
make it hold *everywhere* a comment can land, and then to gate the fold rather
than the combinations.

---

## The laws, and where they come from

The laws are not new policy. Each of the six comment rules in
[`commentHandling.md`](commentHandling.md), taken at its word, already says what
a run must do — the single-comment statement generalises with no extra
decisions:

| rule | for one comment | the same rule, for a run |
|---|---|---|
| **C1** a comment belongs to the code you wrote it next to | which code | **run cohesion** — one gap is one attachment, so the run gets **one role**, not one per comment |
| **C2** at an unrecorded separator, the comment leads what follows | which side | the side is a property of the *gap*, so the whole run goes to the same side |
| **C3** a comment never forces a break | ride or break | the run rides iff **every** member can ride (`Array.all`) — shipped, as `literalCommentsRideFlatLine` |
| **C4** a comment changes where the lines fall, and nothing else | — | **deletion invariance** — see below |
| **C5** gren-format adds nothing around a comment | — | nothing is injected *between* members; source order is preserved |
| **C6** an own-line comment is indented to the code it leads | which column | **run alignment** — every own-line member of one run sits at one column |

C1 and C3 are the structural ones: together they say a run behaves like a
single comment whose kind is the *worst* kind in it. C4 is the one that turns
into a machine-checkable oracle.

---

## The oracle: deletion invariance

*(Proposed. Not built.)*

C4 says a comment changes where the rows fall and nothing else. Applied to the
k-th comment of a run, that is:

> **In a run of two or more comments where at least two members share a
> ride-class, deleting one of those members must change the output by exactly
> that comment's own rendering — nothing else moves.**

This is the whole answer to "how do we judge whether it looks right": you don't
judge the n-comment output. You check that it differs from the (n−1)-comment
output by exactly the one comment you removed. The (n−1) case is in turn
checked against (n−2), down to n=1 — which the existing gates and the
elm-format oracle already cover. **n-comment correctness follows from
1-comment correctness by induction**, and no human reads a three-comment
layout.

### Why the "two share a class" guard is not a fudge

Naively, "deleting a comment moves nothing else" is **false**, and deliberately
so. Two shipped rules make a comment change the surrounding layout:

- `literalCommentsRideFlatLine` — deleting the only non-ridable member lets the
  container collapse back to one line. That is C3 working.
- `NodeClassify.commentBreaksFlowRow` — a comment that ends a row is folded into
  the force-vertical decision, so deleting it can re-flow a call or a binop
  chain. That fix is what closed 214 cells of the comment axis in 2026-07-31.

Both are `any`/`all` folds over the run's *classes*. So if the run still
contains another member of the same class after the deletion, **every such fold
returns what it returned before, by construction** — no verdict can flip, and
the invariance holds unconditionally. There is nothing to compute, no
baseline, and no exception list to keep in step with the code.

That is why the law is stated over duplicate-class members rather than over
arbitrary ones. It costs one extra comment in the probe and buys an oracle with
no judgement in it.

### What it collapses the test space to

| level | what runs | judged by |
|---|---|---|
| n = 1 | 3 kinds × every gap × every context | idempotency, AST, marker count, **elm-format parity** |
| n = 2 | 4 **class**-pairs (ride+ride, ride+noride, noride+ride, noride+noride) | the above, plus parity — this is the boundary where `Array.all` can flip |
| n = 3 | deletion invariance only | itself; no baseline, no review |

Roughly eight shapes per gap instead of `3 + 9 + 27`, and only the first two
levels need a baseline at all.

---

## The code change that makes C1 and C3 structural

*(Proposed. Not built.)*

The render half is already run-shaped. `Render/CommentBox.gren` exposes
`spanLeadingComments`, `spanTrailingComments`, `spanTrailingOwnLineComments`,
`spanOperatorRowComments` and `peelTrailingCommentNodes` — all of them take and
return `Array LPNode` — and `makeCommentLineBox` folds over the run. Adding a
second comment does not reach new code there.

The classifier half is not. `Comments.gren`'s `classifyCommentKind` decides a
role for **one** comment from its neighbours, and when a run is inserted the
previous *comment* is the neighbour the next one classifies against. So the
role of comment k can depend on comments 1..k−1 through a path nobody wrote
down — which is exactly the situation the laws above are meant to remove.

The change that makes C1 and C3 hold by construction rather than by testing:

1. identify the maximal comment run in a gap **once** (today the run-formation
   rule is spread over several sites that have to agree);
2. decide **one** role for the run, from the gap — the same decision
   `classifyCommentKind` makes now, taken once;
3. let each member contribute only its own `commentTextCanRide` to a fold.

After that, "the run gets one role" and "the run rides iff all members ride"
are not properties to test — they are the shape of the code.

---

## What each gate actually covers

The reason this document exists is that the coverage map had a hole nobody had
drawn, and it hid a real class of bug for a long time.

| gate | varies | does **not** vary |
|---|---|---|
| fixtures | whatever someone wrote by hand | — |
| `fuzz-idempotency.py` per-gap pass | every inter-token gap × **all three kinds** (since 2026-08-03) | more than one comment |
| `fuzz-idempotency.py` decl-end pass | own-line trailing comment, block and line form | more than one comment |
| `fuzz-whitespace.py` | inter-token whitespace | comments, syntax |
| `matrix-syntax.py` | every expression form × 25 expression contexts, **and every type form × 15 declaration contexts**, × 4 layout variants | comments |
| `matrix-syntax.py --comments` | the above × 1 comment × 4 placements | more than one comment |
| `audit-predicates.py` | corpus, predicate vs renderer | nothing new |
| `gen-random.py` | structure **and** comments, randomly | — (the only gate that generates runs at all, and it has no parity oracle) |

Two holes were visible in that table until 2026-08-03, and they compounded:

- **No declaration contexts.** `matrix-syntax.py`'s context list was
  expression-only: no signature, type alias, union, port, or `let` binding with
  a signature. A comment-placement question about a type therefore reached no
  oracle at all. *Closed the same day* by the type axis — a second vocabulary
  of type constructs paired with declaration contexts, run through the same four
  oracles. (Still uncovered: an `import`'s own syntax and the module header,
  which the corpus fuzzers reach but no elm-format oracle does.)
- **One comment kind per gap.** The per-gap pass injected only
  `{- ¤ -}` — the single-line block. That is precisely the kind most placement
  rules do *not* fire for: `commentTextCanRide` is literally "single-line block
  or not", and C2's line-leading-separator exception applies to a `--` and a
  multi-line `{- … -}` but **not** to a single-line one. *Closed the same day*
  by the three-kind sweep.

Their intersection is what hid the signature-`->` rule: a comment in a type
signature's arrow gap was invisible to the fuzzer (only one kind swept) *and*
invisible to the comment matrix (no declaration contexts). When that rule was
changed on 2026-08-03, the only evidence available was hand-written fixtures.

### The worked example: what the hole cost

Extending the per-gap pass to all three kinds, the same afternoon, over the
same 319-file corpus that had been green for months:

| kind | findings |
|---|---|
| `{- c -}` single-line block — *the kind that had always been swept* | **0** |
| `{- c` … `-}` multi-line block | **785**, in 129 files |
| `-- c` line comment | **222**, in 59 files |

Running each failing probe under the previous build attributed them. Of the
arrow-gap subset:

| kind | regression from that morning's commit | pre-existing |
|---|---|---|
| multi-line block | **401** | 2 |
| single-line block | **3** | 1 |
| `--` | 0 | 0 |

The `--` is what that change was actually *about*, and it was clean. The
breakage was entirely in the other two kinds — which the change also moved,
and which nothing exercised.

The diagnosis was quick and the first two fixes were both wrong in the same
instructive way.

`signatureForceVertical` asks "did the author break the type at a `->`", from
source rows. A multi-line `{- … -}` inflated the segment's row span, so the
answer came out "no", the signature rendered inline — and the reparse, where the
comment's own rows had pushed the `->` down, answered "yes". Classic oscillation.

**Fix attempt 1** — skip comment nodes when computing the segment's row span.
This cleared every arrow-gap finding and looked done; a wider re-sweep found 15
still oscillating elsewhere.

**Fix attempt 2** — skip comments *recursively*, since the survivors carried
their comments nested inside a record type where the cached `lpnMaxRow` still
folded them in. **This changed nothing at all**, which is the interesting part:

> Subtracting the comment's rows cannot work, because the comment also moves
> the **code's** rows. There is no row-derived answer that survives the reflow.

**The fix that worked** is kind-based, not row-based: a comment's kind is the
same before and after it reflows, so both passes agree on it where they cannot
agree on rows. That is `commentSplitsType`, and it took two goes to scope:

- **First cut** — "a comment that cannot ride, with a real item after it,
  anywhere in the type". Fixed the oscillation and **regressed 50 comment-axis
  cells**: `foo : -- c` ⏎ `Int -> Int` split at the `->`, against the author's
  own one-row layout, because a comment ahead of *all* the content also has
  content after it.
- **Scoped** — real content both **before and after** the comment. Before,
  because a comment ahead of everything shifts the segments down as one block
  and their rows do not move relative to each other. After, because a comment
  trailing the whole type moves nothing, and that shape is a long-standing
  fixed point.

Between those two guards it catches a comment in the arrow gap and one nested
several levels inside a record alike, which is why it flattens the type in
document order rather than recursing per flow.

One thing the flatten has to add back: a bracket container contributes its
**closing delimiter** as a stand-in token after its last child. The `}`/`]`/`)`
is rendered but is not a node, so without it a comment at the end of a record
(`-> { a : Int -- c }`) looks like nothing follows it — when in fact it pushes
the `}` onto a new row, the record becomes multi-line, the record *drops* below
the arrow, and the reparse sees a boundary the first format did not. That cost a
third pass: I removed the bracket handling on the grounds that the flatten
subsumed it, and 32 cells started oscillating.

**Three wrong scopings of one predicate in one day** is the honest tally, and
the pattern in all three is the same: each was validated against the shapes that
prompted it and not against the shapes it would newly reach. The comment axis
caught every one, which is the argument for running it before committing rather
than after.

Result: **0 regressions**, and 98 *pre-existing* findings cleared as a side
effect. Two fixtures moved closer to elm-format — a signature the author broke
at the `->` no longer collapses onto one line when it carries a comment, which
is the shape elm-format produces.

### The residual, and why this gate ships red

As of 2026-08-03 the corpus sweep stands at **347 findings — 0 regressions, all
pre-existing**: 296 multi-line block, 51 `--`, 0 single-line block. (It was 424,
in the proportions 298 / 126 / 0, until `45f7269` took 77 of them at once.) Every
one was attributed against the previous build; none is new.

The figure moves when the *corpus* grows, not only when behaviour does — the
`D27`–`D29` divergence fixtures added six of those 424 between them, exercising
known families (a `--` splitting a declaration keyword leaves a detached comment
that gains a blank line on reformat). So a rise is a prompt to attribute, not
proof of a regression; and a fall can equally mean a fixture was deleted.

So `fuzz-idempotency.py --gaps` currently **fails**, and that is deliberate. A
green gate would require either fixing those latent oscillations first or
narrowing the sweep back to the kind that was already clean — and narrowing it
is exactly the mistake that hid them. The count is printed per kind on every
run, in the same spirit as the `UNREVIEWED` / `BUG:` counters in the parity
baselines: debt that is visible is debt that gets paid, and a number that moves
in the wrong direction is a regression signal even while the absolute figure is
non-zero.

Two consequences worth stating:

- **Do not add this pass to `run-tests.sh`** until the residual is worked down.
  It is a deliberate gate, run by hand, like `matrix-syntax.py --comments`.
- **Attribute before you blame.** With a non-zero baseline, "the fuzzer fails"
  says nothing on its own. Build the previous commit to a second binary and
  re-probe the failures; the question is always *which* of these are new.

Four things are worth keeping from that:

1. **A gate that runs green over the wrong axis reads exactly like a gate that
   runs green.** Before trusting one, check what it *varies*, not whether it
   passed.
2. **A rule that branches on comment kind must be tested on every kind it
   branches on.** The change was reasoned about, documented and fixtured for
   the `--`; the multi-line block came along for the ride and broke, because
   "follows the `--`" was asserted rather than swept.
3. **A row-derived layout decision cannot be repaired by ignoring the comment's
   rows.** The comment moves the code too. Fold the comment's *kind* into the
   decision instead — kinds are stable across a reflow, rows are not. This is
   the general form of the lesson `commentBreaksFlowRow` already recorded, and
   the second time it has had to be learned.
4. **Attribution is cheap and worth doing.** Building the previous commit to a
   second binary and re-probing only the failures turned "hundreds of findings
   in old code" into "401 of them are yours, from this morning" in one run.
   Without it the regression would have been filed as pre-existing debt.

   Check the instrument before the result, though: the first attribution script
   keyed each probe's working directory on `hash((path, gap)) % 64`, so
   concurrent threads collided and two identical runs reported 22 and 75
   regressions. One directory per worker *thread* made it deterministic. A
   flaky measurement of a flakiness bug is very easy to mistake for a finding.

---

---

## The systemic problem behind the mis-scopings

*(Diagnosis kept for the record; the predicate it is about was deleted the same
day — see [The real fix](#the-real-fix-do-not-predict).)*

Three wrong scopings of one predicate in one day is not bad luck; it is a
predictable failure of the *shape* `commentSplitsType` had. It was a **mirror
predicate** — it hand-predicted what the renderer would do, before rendering —
and this codebase deliberately got rid of its mirror predicates.
`Audit/PredicateAgreement.gren` says so in its own module doc:

> **There is one predicate left under audit, and that is the point.** This
> module was written when `NodeClassify` carried a whole mirror layer …
> All of them are gone: verticality is now decided by rendering the child and
> asking `isSingleLine` / `B.allSingles`, which cannot disagree with the
> renderer because it *is* the renderer.

The distinction that decides whether a predicate is legitimate:

| kind | may read source rows? | example |
|---|---|---|
| **author-intent flag** — records what you wrote, information that exists nowhere else | **yes** | `signatureForceVertical` ("did you break at a `->`") |
| **renderer prediction** — claims what the output will look like | **no**, ask the box | `commentSplitsType` |

`commentSplitsType` was the second kind wearing the first kind's clothes, which
is why it kept being wrong at the edges. The replacement is a question asked of
the assembled box (`typeContentSpansRows`), which cannot disagree with the
renderer because it is measuring it.

### Why the existing audit cannot catch it

*(Checked, not assumed.)* Adding `commentSplitsType` to
`--audit-predicates` would be **vacuous**. That audit's contract is
`predicate True ⇒ the node's box renders multi-line`, and
`commentSplitsType` is True only when the type holds a comment with
`commentEndsItsLine` True — a `--` or a multi-line `{- … -}` — which forces its
flow multi-line *by definition*. The contract is entailed by the predicate. The
50-cell mis-scoping proves it from the other side: `foo : -- c` ⏎ `Int -> Int`
renders multi-line, so it satisfied the contract while being wrong.

### What would catch it: decision stability

*(Proposed. Not built.)*

The formatter knows something no gate can currently see: **which layout decision
it took, and which predicate produced it.** Have it emit that —

    decl 3 `foo`  signatureForceVertical=False  typeContentSpansRows=True  -> perSegment

— then format twice and require the **decision set** to match, not just the
bytes. Two properties fall out, and between them they cover every failure of
this class:

- **under-approximation** (the oscillations: 401 cells, then 15, then 32). The
  chosen form is not a fixed point, so the decision flips on the reparse. Today
  this surfaces as a byte diff and the culprit has to be inferred; here it
  names itself. **This is what the 347-finding residual of step 2 is made of**,
  which is the argument for building the gate before working that list down: it
  turns 347 byte diffs into 347 named decisions, and the names group.
- **over-approximation** (the 50 cells). Render the *alternative* form, reparse
  it, recompute: if the alternative would also have been a fixed point, the
  predicate forced a layout it did not need to.

The predicate this section was written to catch (`commentSplitsType`) is gone —
step 4 deleted it rather than gating it. What remains is every *other*
row-derived decision, and the residual says there are plenty.

Note that a purely external gate cannot do this. "Did the formatter choose the
per-segment shape" is observable in the output, but if the two formats already
agree the comparison is vacuous — it collapses back into the idempotency check
we already have. The value is precisely the *reason*, which only the formatter
holds.

### The real fix: do not predict

*(Done 2026-08-03. `commentSplitsType` is deleted.)*

`makeSignatureBox`'s no-comment arm already did the right thing: it renders the
flow, asks `B.isLine`, and falls through to the per-segment layout when the
answer is "multi-line". No prediction, no mirror, no possible disagreement.

The comment-bearing arm chooses between two renderings of the *same children*,
so it could work the same way. The obstacle was that the question is not "is the
box multi-line" (a comment-bearing type usually is, legitimately) but "did a
`->` boundary land on a later row", which needs per-item positions inside the
assembled box that `buildFlowBox` did not expose.

**Exposing them turned out to need one number per item, and no new judgement.**
`assembleFlowMeasured` returns, alongside the box, how many rows the assembly
*grew by* when each item was placed. That is enough to place every item, because
the two cases differ by exactly one row:

- an item that GLUED onto the running row adds `height item.box - 1` — its first
  line shares the previous item's last row;
- an item that STARTED a row adds `height item.box`, or more when a paired
  leading comment came down with it.

Running the additions up gives each item's last row; subtracting its own height
gives its first. The count is taken **between** placement steps, off the
accumulator — the ~35 placement arms are untouched — so a measured assembly and
an unmeasured one cannot disagree about the box. It is a flag rather than
always-on only to keep the row-summing scan off the hot path of every other flow
in the file.

And then the question turned out to be smaller than "did a `->` boundary move".
`FlowAssembly.typeContentSpansRows` asks only: **did the type's own content come
back on more than one row?** Everything follows from that one bit:

- if it did not, no segment can have moved relative to any other, so the inline
  layout is a fixed point — which is the *stability* half, the reparse's
  `signatureForceVertical` question answered without asking it;
- if it did, the per-segment shape is right, and for a single-segment type
  "per-segment" simply *is* the whole type dropped below the header — which is
  the *layout* half, and what elm-format does.

Skipping comments when deciding what counts as content is what makes
`foo : -- c` ⏎ `Int -> Int` stay inline and `foo : Int -> String {- x` ⏎ `y -}`
with it. Those were `commentSplitsType`'s two guards — "real content before AND
after the comment" — each learned by getting it wrong; here they are not a rule
at all, just the consequence of measuring content.

Two arms disappeared with the predicate:

- the single-node arm (`typeHasCommentBracket`), which glued a comment-bearing
  bracket type after `:` and dropped only an `isTypeRecordLiteral`. The other
  brackets it claimed to keep glued never reached it — `commentSplitsType` had
  already forced them vertical — and elm-format drops all three. For one segment
  `perSegment` *is* that drop, so the general rule covers it.
- `commentSplitsType` itself, from `typeSegmentsForceVertical`, which both
  `makeSignatureBox` and `makeTypeAliasBody` share.

**Result: the comment axis's 58 hard failures went to 0** — the whole
`tyRecord2` family this file described as its residual — with **+94 cells of
elm-format parity** (20,017 → 20,111 byte-identical) and **0 UNREVIEWED, 0 BUG**.
The 28 cells that could not be parity-checked before (a failing cell has no
verdict) auto-classified as #22 and #13; 64 baseline entries went away because
those cells now match elm-format exactly. The syntax axis (2079/2079, 1358
identical), `fuzz-idempotency.py` (347: 0 block / 296 multi / 51 line, per-kind
unchanged), `fuzz-whitespace.py`, `audit-predicates.py` and all 334 fixtures are
unchanged — bar one fixture, `TypeRecordLeadingComment`, whose type-application
declaration is now byte-identical to elm-format where it used to diverge.

That last one is worth noting on its own: the old code kept
`typeAppWithComment : HasIdentifier` ⏎ `{- note -}` glued for a *single-line*
block comment and dropped the whole type for a `--`, because `commentSplitsType`
branched on comment kind. Asking the box is kind-blind, so the two spellings
now agree — and agree with elm-format.

Doing this *before* the decision-stability gate was the right order: the gate
makes the mistake cheap to find, but not making it is better.

## Order of work

1. ~~per-gap pass sweeps all three comment kinds~~ *(done 2026-08-03)*
2. **work the 347-finding residual down to zero**, then wire the pass into
   `run-tests.sh`. Until then it is a hand-run gate with a known baseline. (It
   was ~420 until `45f7269`, which took 77 of them at once: `VerticalSpace`
   inserted a blank line and then asked a *source-row* question about the gap it
   had just created. Worth knowing before picking off the rest one at a time —
   the residual is not 347 separate bugs.)
3. ~~declaration contexts in `matrix-syntax.py`~~ *(done 2026-08-03: 11 type
   constructs × 15 declaration contexts, +341 syntax cells, +4,930 comment
   cells)*. This was the n=1 base case, and nothing above it meant much until it
   existed. Its first run produced three work-lists, none of them regressions:
   - **123 UNREVIEWED parity divergences** — gren flattened an author's break
     inside a type where elm-format keeps it. Closed the same day: 18 fixed (a
     parenthesized application now keeps its break, and a signature goes
     multi-line for any break that *survives rendering*), and the other 105
     reviewed into catalogue entries
     [#27](elmFormatComparison.md#divergence-27),
     [#28](elmFormatComparison.md#divergence-28) and
     [#29](elmFormatComparison.md#divergence-29). **0 UNREVIEWED**;
   - ~~**58 hard failures, one family** — a comment-bearing signature whose type
     carries a multi-line record is not a fixed point, because
     `typeSegmentsForceVertical` switches its dropping-record trigger off when a
     comment is present~~ *(closed 2026-08-03 by step 4 below, which turned out
     to be the same bug seen from the other end: the trigger is off because a
     comment-bearing type has its own arm, and that arm was predicting instead
     of measuring)*;
   - ~~**1,436 UNREVIEWED comment-parity divergences**, all type-context
     cells~~ *(closed 2026-08-03: `aa377fd` cleared 139 by fixing a real bug —
     every comment inside a `let` binding's annotation escaped it — and
     `2e4fcc4` reviewed and registered the other 1,140. Only one catalogue entry
     needed changing, and as an **extension**: [#24](elmFormatComparison.md#divergence-24)
     now covers the extensible-record-type and union `|` as well as the record
     update's. That no type context asked a genuinely new comment question is
     the useful result — it says C1–C6 already covered types.)*
4. ~~**stop predicting in `makeSignatureBox`'s comment arm** — render, look at
   the box, delete `commentSplitsType`~~ *(done 2026-08-03; it also closed the
   58 above and gained 94 cells of elm-format parity)*. See
   [The real fix](#the-real-fix-do-not-predict).
5. **the decision-stability gate** — the formatter emits which layout decision
   it took and why; format twice, diff the decisions. See
   [What would catch it](#what-would-catch-it-decision-stability).
6. run-classification refactor — makes C1 and C3 structural
7. the deletion-invariance oracle
8. n=2 class-pairs, with elm-format parity

Steps 4 and 5 were the systemic pair and 4 came first: the gate makes the
mistake cheap to find, but not making it is better. Steps 2, 6 and 7 come before
8 — an induction is only as good as its base case, and a law is cheaper to hold
by construction than to check.
