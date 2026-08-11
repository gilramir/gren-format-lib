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
a run must do — for C1–C6 the single-comment statement generalises with no extra
decisions. **C7 is the exception, and it was added because of a run** (2026-08-08):
one half of it fell out of C3 + C5, and the other half — a run you wrote on
separate rows stays on separate rows — is a statement neither of them makes:

| rule | for one comment | the same rule, for a run |
|---|---|---|
| **C1** a comment belongs to the code you wrote it next to | which code | **run cohesion** — one gap is one attachment, so the run gets **one role**, not one per comment |
| **C2** at an unrecorded separator, the comment leads what follows | which side | the side is a property of the *gap*, so the whole run goes to the same side |
| **C3** a comment never forces a break | ride or break | the run rides iff **every** member can ride (`Array.all`) — shipped, as `literalCommentsRideFlatLine` |
| **C4** a comment changes where the lines fall, and nothing else | — | **deletion invariance** — see below |
| **C5** gren-format adds nothing around a comment | — | nothing is injected *between* members; source order is preserved |
| **C6** an own-line comment is indented to the code it leads | which column | **run alignment** — every own-line member of one run sits at one column |
| **C7** a comment keeps the rows you gave it | its own row | **row preservation** — members written on one row stay on one row, members written apart stay apart; the formatter neither joins nor splits a run's rows |

C1 and C3 are the structural ones: together they say a run behaves like a
single comment whose kind is the *worst* kind in it. C7 is the row-level one, and
it is what stops "behaves like a single comment" being read as "collapses to a
single row". C4 is the one that turns into a machine-checkable oracle.

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

**2 and 3 are one change, not two** (measured 2026-08-06; the numbering above is
older than the reading that produced this paragraph). The per-member role *is*
the chaining mechanism: `prevLineGlueRow` / `prevBlockGlueRow` / `bracketItemRow`
all key a comment by its LAST row on purpose (`a5d948c`, "comments chain"), so
the second member of a run comes back `TrailsPrevious` / `RidesInline` **against
the first member**, and that is exactly what tells the renderer to glue it onto
the first's line instead of dropping it to its own. Decide one role for the run
without the fold in place and that information is simply gone — the run stops
chaining, and `MultilineCommentTrailedByComment` is the fixture that says so.
Do them together or not at all.

---

## What each gate actually covers

The reason this document exists is that the coverage map had a hole nobody had
drawn, and it hid a real class of bug for a long time.

| gate | varies | does **not** vary |
|---|---|---|
| fixtures | whatever someone wrote by hand | — |
| `fuzz-idempotency.py` per-gap pass | every inter-token gap × **all three kinds** (since 2026-08-03) × **a run of N** (`--run`, since 2026-08-06) × **its composition** (`--mix` / `--mix-pairs`, 2026-08-06; `--mix-triples`, 2026-08-08) | nothing about a run's length or composition any more — but all of it is opt-in, so a default run still varies one comment |
| `fuzz-idempotency.py` decl-end pass | own-line trailing comment, block and line form | more than one comment |
| `fuzz-whitespace.py` | inter-token whitespace | comments, syntax |
| `matrix-syntax.py` | every expression form × 25 expression contexts, **and every type form × 15 declaration contexts**, × 4 layout variants | comments |
| `matrix-syntax.py --comments` | the above × 1 comment × 4 placements | more than one comment, **and the multi-line `{- … -}` kind** — its kinds are the single-line block and the `--` only |
| `audit-predicates.py` | corpus, predicate vs renderer | nothing new |
| `check-decision-stability.py` | nothing of its own — it re-probes the per-gap pass, **runs included** (`--run` / `--mix*`, since 2026-08-08) | it varies no input; it reports the formatter's *reason* for a diff the others found |
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

> **Superseded 2026-08-05.** The residual reached **zero** that day — 17 findings,
> all `[known: compiler-common#35]`, a parser bug. It reads **19** by the evening,
> and both extras are *pre-existing*, attributed by rebuilding the two previous
> commits: a **comment run** in a record-update field (`{ rec | fld = \q -> fn`
> `{- ¤ … -}` `q {- multi … -} }`) flips `renderGluedLambdaField`'s glue-vs-drop
> decision between formats. It is this document's own subject — two comments in
> one place, which no gate here samples — and it surfaced because a new fixture
> probed it. **Adding a comment-bearing fixture is itself a probe.** What follows
> is the record of how the residual came down, kept because the *method* is the
> reusable part: every family named below turned out to be one rule.

As of 2026-08-04 the corpus sweep stood at **118 findings — 0 regressions, all
pre-existing**: 67 multi-line block, 51 `--`, 0 single-line block. (424 → 347
when `45f7269` took 77 at once; 347 → 172 when the decision-stability histogram
below named the largest family and it turned out to be one rule, missing;
172 → 140 when `f7c0c54` took the *next* family — see below; 140 → 148 because
the two fixtures pinning that fix expose eight probes of a pre-existing class;
and 148 → 118 when the recomputed histogram's largest family turned out to be
the module header — see below.) Every one was attributed against the previous
build; none is new.

**The third family: 148 → 118.** Recomputing the histogram put 43 of the 148 in
one place: an effect module's header. They spanned the top two entries (34 of
the 45 `Comment.role` + `endsItsLine` + `textCanRide`, 9 of the 38 `Comment.role`
alone), which is why the *fixture* clustering found them and the decision
clustering did not — one shape can reach two roles, as the second family also
showed.

An effect module's `where { … }` block makes the header multi-row, so a
multi-line `{- … -}` written past its last token finds no glue row, classifies
`LeadsOwnLine`, and renders on a fresh row underneath — the exact shape
`detachOwnLineTrailer` was built for in the first family, applied to a node it
never saw. Its gate was `isDeclStype`, which excludes `StModule` **and is right
to**: that predicate also decides which nodes a *leading* comment may glue onto
and which count as covering a row, and the header answers both of those
differently. The trailer question is a third question, so it gets its own
predicate (`hostsOwnLineTrailer`). 30 probes fixed, 0 new, `--` unchanged at 51
— the same evidence of scoping the previous two families used.

The boundary is worth stating because it is not "a trailing comment on a
header": a **one-row** header still glues its trailing multi-line comment, since
nothing renders below it. elm-format detaches both, so gren's one-row glue is a
divergence — an existing, stable one this change deliberately leaves alone.
Fixtures `EffectHeaderTrailingMultilineComment` and, for the boundary,
`HeaderTrailingMultilineCommentGlue`.

**What is left of that family: 13 probes**, and they are a different mechanism
rather than a remainder of this one. In each, the *injected* comment changes how
many rows the header occupies, and an **existing** header comment then changes
role between the two passes — a `--` or a single-line `{- c3 -}` that glued to
`} exposing (..)` when the header was one row moves off it when the header is
several. That is a placement still derived from source rows — the class the
render-invariant check exists to keep out of `Render/*`, met on the logical
side. The `--` half is also inside `runRendersBelowDeclaration`'s deliberate
exclusion, so it needs its own fixtures and its own decision rather than a
widening of this one.

**The second family: 172 → 140.** The histogram's next entry (67 probes,
`Comment.role` + `endsItsLine` + `textCanRide`) reproduced as a multi-line
`{- … -}` written past a *pipeline step's* last token. A step relocates its
first breaking argument and stacks everything after it one per line — the
broken-call rule — and a trailing comment was going through that stacking, so it
landed on a fresh row below the declaration and the reparse re-homed it to
column 1. A comment is not an argument: it now glues onto the trigger's last
row, which is what the identical shape written as a plain call has always done,
and what this same path already did whenever a real argument followed the
trigger. 32 probes fixed, 0 new, `--` unchanged at 51.

Pinning it found a **comment-loss** bug that no gate here could have caught
(`312f0a1`): the relocated-lambda renderer reads the paren's head and body and
discards any further child, so a comment past the lambda body's last token was
deleted outright. A dropped comment is AST-equivalent and its output is its own
fixed point — `--show` passes, and so does every stability check. Only a
non-canonical *input*, where the comment survived the first format and vanished
on the second, made it visible. **That is the hole this whole document is about,
met from the comment-preservation side rather than the placement side**: the
oracle for it is `gen-random.py`'s comment multiset, and this shape was outside
that generator's grammar.

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

- **Do not add this pass to `run-tests.sh`** until it can exit zero. That was
  blocked on the residual; since 2026-08-05 it is blocked on
  `compiler-common#35` alone. It is a deliberate gate, run by hand, like
  `matrix-syntax.py --comments`.
- **Attribute before you blame.** With a non-zero baseline, "the fuzzer fails"
  says nothing on its own. Build the previous commit to a second binary and
  re-probe the failures; the question is always *which* of these are new.
- **Ask what flipped before reading the diff.**
  `./check-decision-stability.py --gaps` re-probes these same 347 and prints the
  decision each one moved, grouped. They come to about five families, one of
  which is 238 of them; see
  [What catches it](#what-catches-it-decision-stability). Working the residual
  from that histogram rather than from the byte diffs is the point of having
  built it.

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

### What catches it: decision stability

*(Built 2026-08-03. `--decisions`, `Formatter.Audit.DecisionTrace`,
`tests/check-decision-stability.py`.)*

The formatter knows something no other gate can see: **which layout decision it
took.** It now emits that. `--decisions` formats a file twice and reports which
decisions differed between the passes — `forceVertical` on a call, a comment's
`CommentRole`, `commentBreaksFlowRow`, whether a rendered child came back on one
line — as named branches with **no positions in them**, because a row is exactly
what moves and a decision keyed on one would report every finding and explain
none.

Two properties were wanted, and one shipped:

- **under-approximation** (the oscillations: 401 cells, then 15, then 32) — the
  chosen form is not a fixed point, so the decision flips on the reparse. This
  is what the 347-finding residual of step 2 is made of, and it is what the gate
  now names. **Built.**
- **over-approximation** (the 50 cells) — render the *alternative* form, reparse
  it, recompute; if the alternative would also have been a fixed point, the
  predicate forced a layout it did not need to. **Not built**: it needs a way to
  force a decision the other way, which nothing in the pipeline has.

A purely external gate could not have done this. "Did the formatter choose the
per-segment shape" is observable in the output, but if the two formats already
agree the comparison is vacuous — it collapses back into the idempotency check
we already have. That is not a hypothetical: a "silent flip" check (a decision
moved, the bytes did not) was built, run, and **deleted the same day** for
exactly that reason. Over the corpus the input already *is* the output, so both
traces come from identical text and nothing can differ. The value is precisely
the *reason*, which only the formatter holds.

#### The two design corrections, both found by running it

**The raw trace diff is mostly noise, and the noise is the formatter working.**
Formatting a file that is not already canonical legitimately changes many
decisions: the second pass reads the first pass's tidied rows, not the author's.
On one fixture the first version reported 11 flipped probes where 7 had moved.
The fix is to confine flips to declarations whose **rendered output** actually
differs — a decision that flipped in a declaration whose bytes are unchanged
caused nothing. That needed `Formatter.Render.renderRootChildren`, the
per-declaration render `makePrettyResult` joins; `convergedFlips` reports how
much the confinement discards, rather than hiding it.

**A flat list of flipped names buries the cause.** Once a declaration reflows,
every `rendersOneLine` inside it has moved too, so a one-cause finding still
shows ten names. They are split into *author-intent* decisions (read off the
tree before anything is rendered) and *rendered-shape measurements*, and probes
group by the intent set. The split is a ranking, not a claim: some decisions
legitimately render a child and measure it — `makeSignatureBox` is one — so a
measurement can be a cause.

#### What the first whole-corpus run found

It landed on **exactly the same 347 probes** `fuzz-idempotency.py --gaps`
reports, which is the cross-check that the two gates are probing the same gaps
(they share the probe definitions by import, not by copy). Of those, **320 are
named by an author-intent flip**, 7 by a rendered shape alone, and **20 are
unexplained**:

| probes | the decisions that moved |
|---|---|
| 238 | `Comment.role` + `Comment.endsItsLine` + `Comment.textCanRide` |
| 47 | `Comment.role` alone |
| 24 | `AcrossOrVertical.forceVertical` (+ `commentBreaksFlowRow`, `checkContentVertical`) |
| 12 | `BracketContainer.literalCommentsRideFlatLine` |
| ~6 | `Pipeline` / `Binop` / `IfCondition` `forceVertical` |

The single sharpest line is which way the branches moved:
`Comment.role=LeadsOwnLine` was **lost 246 times** and
`Comment.role=Standalone` **gained 268** — one transition accounting for most of
the residual.

The two comment families are distinguishable, and that is the useful part.
`endsItsLine` and `textCanRide` are functions of the comment's *text*, which
cannot change between two formats of the same file — so they can only move if
the comment changed **which declaration owns it**. The 238 are a comment
relocating; the 47 are one staying put and changing role. Read on a probe, the
big family is a comment going from `LeadsOwnLine` under one declaration to
`Standalone` above the next, which is the detached-comment class `45f7269`
already took 77 findings out of.

**So the residual is not 347 problems; on this evidence it is about five**, and
the largest is already a named, half-fixed one. That is what the instrument was
for.

#### What the 238 turned out to be — 347 → 172

Read on the probes rather than guessed at, the family is one shape: **a
multi-line `{- … -}` written past a declaration's last token.** It finds no glue
row, so it classifies `LeadsOwnLine` and renders on a fresh line *under* the
declaration — and a row below the declaration is not inside it, so the reparse
re-homes it to a column-1 `Standalone`.

The rule that fixes it was **already written down**: `Comments.gren`'s own module
doc says an own-line comment below a top-level declaration is never attached to
it, always detaching to column 1, "which is what elm-format does, and column 1 is
trivially a fixed point". `findOrCreateOrigRow` implements it — from the
comment's **source row**, which for this shape is still the declaration's last
row. `detachOwnLineTrailer` asks the same question of the finished tree instead,
lifting a trailing comment run that renders own-line. Same lesson as `45f7269`
and `aa377fd` before it: a placement decided from a source row that the pass is
about to invalidate.

Three things had to be got right, and the first two attempts got them wrong:

- **`LeadsOwnLine` is not "renders own-line".** The render layer keeps a
  *single-line* `{- c -}` inline whatever its role, so lifting on the role alone
  moved `[ 1 ] {- one -} {- two -}` off its row — **nine fixtures**, caught by
  `run-tests.sh` before anything else ran.
- **A bracket's close is a token that is not a node.** The peel descended into a
  record update and swallowed the `-- inclusive` written *inside* the braces,
  whose `}` still follows it; that comment's `TrailsPrevious` role then vetoed
  the lift and the fix was a no-op on its own reproducer. The descent is now an
  allowlist of flow wrappers, defaulting to "stop" — the safe direction, since a
  missed lift leaves an existing finding rather than moving a comment wrongly.
  This is the stand-in-token trap recorded above for `commentSplitsType`, met
  from the other side.
- **A `--` is deliberately excluded**, though it also ends its row. Fixtures pin
  an own-line `--` staying at the construct's indent below a wrapped import and
  below a pipeline's last step; whether those should detach is its own question.
  The `--` count moved **51 → 51**, which is the evidence that the scoping held.

Result: **fuzz-idempotency 347 → 172** (multi 296 → 121, `--` 51 → 51, block 0 →
0), 336 fixtures pass, and the comment axis, both whitespace modes, the predicate
audit, the syntax matrix and the corpus decision gate are all unmoved. Fixture
`Declarations/DeclTrailingMultilineComment`, which pins the four shapes that move
and the four that must not.

One honest note on the comment axis's silence here: it injects only the
single-line `{- c -}` and the `--`, never a multi-line block, so it **cannot see
this change at all**. Its 0-failing / 20,111-identical result is evidence of no
regression and no evidence of the fix. That is the same one-kind-per-gap hole
this document records the per-gap fuzzer having had until 2026-08-03 — still open
on this axis.

The 20 unexplained and the 7 shape-only are the gate's own debt, printed on
every run. They come down by adding a decision to `DecisionTrace` under its
stated rule — **trace an input, never a composite** — which is narrower than
"whatever makes the number smaller". `commentForcesBracketOpen` is deliberately
absent even though it is a real decision: reproducing its formula in the tracer
would be a mirror predicate, the exact shape this document spends its middle
section explaining the cost of.

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
2. ~~**work the residual down to zero**~~ *(done 2026-08-05)*. It went
   **424 → 0 formatter-side** in eleven families over three days. What remains is
   **17 findings, all `[known: compiler-common#35]`** — a parser bug, labelled and
   counted but not ours — and `check-decision-stability.py` PASSes 0 over the
   corpus.

   **The gate still cannot go into `run-tests.sh`,** and the reason has changed:
   it is not our debt any more, it is that those 17 make it exit non-zero. That
   is blocked on the upstream fix shipping and the dependency being bumped, at
   which point the findings disappear with no baseline entry to retire.

   The durable lesson, since the numbers are now history: **the residual was
   never N separate bugs.** Each family was *one rule*, and four of the eleven
   were the *same* rule asked of a node, a role or a container it had not been
   asked of before. Work a histogram, not a byte diff — and group its probes by
   **fixture** as well as by decision set, which is how the third family was
   spotted: it straddled the top two decision groups and was invisible in either
   alone. Full family-by-family record in
   [the residual section](#the-residual-and-why-this-gate-ships-red) and in
   [`devHistory.md`](devHistory.md).
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
5. ~~**the decision-stability gate** — the formatter emits which layout decision
   it took and why; format twice, diff the decisions~~ *(done 2026-08-03:
   `--decisions`, `Formatter.Audit.DecisionTrace`,
   `tests/check-decision-stability.py`)*. See
   [What catches it](#what-catches-it-decision-stability). Its first run
   reduced step 2's 347 findings to **about five named families**, 320 of them
   attributed, 20 unexplained. **Step 2 should now be worked from that
   histogram, largest family first**, not from the byte diffs.
6. **the multi-line block kind in `matrix-syntax.py --comments`** — *in progress
   2026-08-05*. `COMMENT_KINDS` is `{"block", "line"}`; `fuzz-idempotency.py`'s
   `KINDS` is `{"block", "multi", "line"}`. **This is the same one-kind-per-gap
   hole recorded above, still open on the one axis that has an elm-format
   oracle**, and it has already cost us: `detachOwnLineTrailer` (347 → 172) was
   invisible here, so the axis's "0 failing" was evidence of no regression and no
   evidence of the fix. Finish the n=1 base case before building anything on top
   of it.

   Not a one-line dict change. Four helpers assume a marker is single-line —
   `comment_stripped_matches`, `marker_role`, `_marker_slot`, `_canon_lines` —
   each with a `\{-\s*¤\s*-\}` regex applied *per line*. Against a multi-line
   marker every one silently fails to strip, leaving the comment's own words in
   the text being compared. The failure is mostly *safe* (a cell that will not
   classify books UNREVIEWED rather than being swept into a family), except
   `marker_role`, which is what the "gren stranded it alone — never
   auto-classify" guard reads. Make the strippers span-aware first, then add the
   kind.

   **First run: 68,922 cells (up from 45,948) and 70 hard failures, every one a
   non-idempotency on the new kind.** They are three shapes with two causes, and
   the hole had been hiding both:

   - **16 — a multi-line comment past a bracketed container's ITEM** descended
     into that item's lambda body, rendered at the body's indent, and the reparse
     handed it to the container. The 8th family's paren rule
     (`parenTailKeepsCommentOutside`) asked of a container it had not been asked
     of; `containerTailKeepsCommentOutside` now covers both. **Fixed** —
     70 → 54, 0 new. Fixture `BracketComments/ContainerTailMultilineComment`.
     Only a lambda item exposed it: `AcrossOrVertical` was already on
     `boxKeepsTrailingCommentOutside`, so the same shape with no lambda had
     always been stable, which is also the output the fix lands on.
   - **54 — a glue row derived from source rows that the format then
     collapses.** `bracketRendersMultiline` used `range.maxRow > range.minRow`
     as its proxy for "does this render multi-line", and a single-item container
     collapses ([#21](elmFormatComparison.md#divergence-21)), so a comment after
     an author-broken `(Int` ⏎ `-> Int)` or `[ 1` ⏎ `]` glued on the first format
     and took its own row on the second. Its docstring already named the shape of
     the problem — *"it is a logical-stage predicate, so it cannot observe the
     rendered box"* — which made it the same class as `commentSplitsType`.
     **Fixed** — 54 → 0, and **the fix is subtractive**: the answer was already
     computed, stored and correct in the box constructor
     (`authoredBracketList` picks `AlwaysVertical` vs `AllAcrossOrAllVertical`
     from `itemsSpanRows`), and the row re-derivation beside it was both
     redundant and wrong — it counted a break *inside* one item, the very thing
     `itemsSpanRows` documents itself as ignoring. The `ParenBlock` arm of
     `prevBlockGlueRow` had the same bug and the same stored answer
     (`forceVertical`), which is where ~48 of the 54 were: a formatter-synthesized
     type paren carries no author position, so it renders flat however the author
     broke it. Fixture `BracketComments/CollapsedContainerTrailingComment`.

   **The axis is back to 0 failing, at 68,922 cells.** Both fixes are one rule
   each, and neither needed a new concept: the first was an existing rule asked
   of one more container, the second was deleting a mirror that an author-intent
   flag already answered.
7. run-classification refactor — makes C1 and C3 structural. **Before any n=2
   baseline**, or the baseline is built against ownership the refactor changes.
   **Half done (2026-08-05).**

   *Done — sub-step 1, "identify the run once".* The **mirror pair is gone**:
   `spanTrailingOwnLine` lives in `LogicalPrintingTree`, beside `CommentRole` and
   `roleGlues`, for that module's stated reason — the answer is a property of the
   role and of nothing else. `Comments.spanTrailingOwnLineNodes` is deleted. Its
   docstring had called itself a mirror and blamed the import direction
   (`Render` depends on `Logical`, not the reverse), but both modules already
   import `LogicalPrintingTree`: **the necessity was in the placement, not in the
   rule.**

   *Done — the one place C1's "one gap is one attachment, one role" was false.*
   Measured before changing anything: runs in four of five gaps already got a
   single role. The violator was the item list's **opener slot** —
   `[ {- a -} {- b -} 1 ]` gave `RidesInline | LeadsNext`, and an own-row opener
   run gave `RidesInline | LeadsOwnLine`. The fix is the **record update's own
   formulation**, `Array.all isCommentNode before` rather than
   `Array.isEmpty before` (`inOpenerRun`); that arm has read the opener region
   that way since `b544d53`, with a comment saying why. No output moved — the
   whole comment axis is byte-identical before and after.

   *Why it drifted, and what it implies for the rest.* At the opener slot **the
   renderer does not read the role**: `commentBracketListBox` takes `atOpener`
   from its own fold and the comment's SHAPE from `commentTextCanRide`. A stored
   fact nobody consults is free to be wrong — which is also why
   `commentRidesInline`'s docstring had to warn that a shape-ridable comment may
   not be role-ridable. **Sub-steps 2–3 are only worth doing if the renderer then
   trusts the role**; otherwise they tidy a vestigial field.

   ***Decided 2026-08-06, by measuring rather than by reading: not vestigial, but
   not the bug source either — so they wait.*** Three findings, in the order they
   settle the question:

   - **The renderer does trust the role**, nearly everywhere except the slot
     where the drift happened. `FlowPolicy.containerCommentSlot` reads it twice
     (`LeadsNext` → `LeadsFollowing`; `roleGlues` → `GluesPrevious`),
     `commentPlacement` turns it into the glue/own-line call for every flow,
     `NodeClassify.literalCommentsRideFlatLine` reads it per child, and
     `MakeRenderBox` has four predicates over it. The field is load-bearing.
   - **The split is real and it is one shape.** A run of two spliced into every
     gap of the corpus (19,081 gaps, single-line block kind) comes back with two
     roles at **6,531** of them — and every single one is
     `LeadsOwnLine` + `RidesInline`, the generic flow's own chaining answer. It
     is also **inert**: the second member's separator state is
     `AlreadyTerminated` / `HardNl` by then, where `commentPlacement` ignores the
     role, and the bytes are identical to elm-format's for the same input. A
     further 52 gaps split the run across two OWNERS rather than two roles, all
     of them in an effect module's header.
   - **None of the n=2 bugs came from the split.** Of 20 non-idempotent n=2
     probes, the four that were this formatter's to fix had **one** role for the
     run (`RidesInline` twice) and a flow-state bug underneath; the other 16
     were in an effect module's header. So the refactor buys structure — C1 and C3
     holding by construction — and not, on this evidence, bugs.
   - **The 16 confirmed that, and not by being the owner split they looked
     like.** Fixed in `b953853` as a *position* bug: the `where` block's elided
     `command` / `=` were anchored by counting **backwards from the
     constructor**, so a comment in that gap put the derived columns inside the
     comment's own text and carved a phantom slot for the run's second member to
     fall into. The two owners were the symptom. Both labels are now
     position-less `SynthesizedText`, per `mkZeroWidthText`'s stated policy —
     which forbids a following-token anchor and names this very site as having
     no honest one. Nothing about per-member roles was involved, which is one
     more reason to leave sub-steps 2–3 where they are.

   The cheap axis pays first: `--run 2` costs no review time and found a
   comment run that dragged the following token onto its row. Do that, then the
   refactor, and treat 2 and 3 as the single change they are.

   *Left, and the six-site list corrected.* It was partly a conceptual count.
   `prevLineGlueRow` / `prevBlockGlueRow` / `bracketItemRow` keying a comment by
   `lastRowInSubtree` are three **call sites of one shared function**, not three
   implementations. What is genuinely two formulations is `chainedRefRow`
   (backward fold over `before`, `firstRow <= acc`, returns a ROW) against
   `SortSymbols.takeSameRowTrailingFrom` (forward walk, `firstRow == prevLast`,
   returns the NODES) — unifying them picks one comparison over the other and
   changes behaviour at the margin, so it needs its own measurement rather than
   riding along with another change. And `spanTrailingComments` (every trailing
   comment) and `peelTrailingCommentNodes` (only inline-gluable, at most one
   `--`) are **not** the same rule despite the names; do not unify them.
8. the deletion-invariance oracle. **Build it in `gen-random.py` first**, not in
   the gap fuzzer: the generator emits from a tree, so the n and n−1 variants
   come from the *same* tree deterministically — the mechanism the `sort-order`
   oracle already uses to emit two author orders. Splicing text to delete a
   comment from a corpus file cannot guarantee nothing else moved. The
   comparison is a **code skeleton**: blank every comment span in both outputs,
   drop lines that were wholly comment, require byte-equality of the rest — C4
   stated directly, and no span-boundary judgement.
9. n=2 class-pairs, with elm-format parity. Last, because it is the only step
   that costs review time.

   **Its stability half needs none of that review, and it is done (2026-08-06).**
   `fuzz-idempotency.py --run N` splices a RUN of N comments into each gap
   instead of one; `--show` still buys no-crash + AST-equivalence + idempotency +
   reparses, and the marker check gains a **reordering** arm, since the members
   are marked `¤1 … ¤N` rather than repeated. Only the *parity* half of this step
   is expensive, and separating them turned out to be worth a fix on the first
   run. See "the run axis" below.

Two expansions feed steps 8 and 9 and can proceed in parallel with either:

- **`gen-random.py`'s runs are the wrong shape, not merely too few.**
  `comment_chain` already emits 1–3 links, but only *trailing* and only
  *same-row*. There is no own-line run anywhere in the grammar — and own-line
  runs are exactly where `detachOwnLineTrailer`, `rehomePipelineStepTrailers` and
  `computeDetachedBelow` each carry run-cohesion logic.
- ~~**The probe machinery needs a run concept in one place.**~~ *(done
  2026-08-06.)* `fuzz-idempotency.py` owns `KINDS` and the gap enumeration;
  `check-decision-stability.py` and `repro.py` import them *by path* precisely so
  they cannot drift. The run generator lives there too — `run_text` / `run_kind`
  widen a kind's text and its label together, so a run is still one entry of the
  same 4-tuple table and every consumer takes one without knowing there is such a
  thing. The finding ID grew by growing the LABEL (`blockx2`) rather than the
  format, so `<fixture>[<kind>]@<gap>` is unchanged and `repro.py` reproduces a
  run finding by splitting the suffix back off.

## The run axis: what `--run 2` found

First whole-corpus run, single-line block kind, 19,081 gaps: **20
non-idempotent, 19,052 clean, 9 skipped by the parser.** Two families.

**Four were one bug, now fixed, and it is a bug only a run can reach.** A run of
single-line `{- … -}`s glued onto a multi-line comment's `-}` row pulled the
NEXT TOKEN up onto that row — with one comment the token already went below:

    {- standalone            with ONE comment `second` goes below;
       multi-line            with TWO it came back up onto the row
       comment -} {- a -} {- b -} second =

For a `let` binding that is not merely wrong, it does not **parse** — a binding
must start at the block's column — so the finding arrived as
`COULD NOT PARSE FORMATTED OUTPUT`, which no amount of idempotency checking on a
single comment would have produced. `FlowPolicy`'s `BlockCommentItem` inline arm
answered `FlowSep` for what follows a glued comment, and `FlowSep` *is* the
space-join; the arm above it had already worked out that a comment gluing onto a
hard-broken line must leave `HardNl`. A comment that glues inherits the line's
own rule. Fixture `PatternComments/CommentRunAfterBlockComment`.

**The scope of that fix is the whole lesson, and the first attempt got it
wrong.** Preserving `HardNl` across *any* comment in that arm also stopped an
own-line run from merging onto one row — and elm-format merges it, from either
authoring, which `PipelineLambdaArgTrailingComment` pins. The rule is narrower:
`HardNl` survives a comment that **glued** (placement `GlueSpace`), not one that
opened its own line. One fixture caught it in the ten minutes between writing
the fix and believing it.

**The other 16 are the effect-module header, and they are an OWNER split rather
than a role one** — the run is torn in half across the `where { … }` block's
`{`, one member landing outside it and one inside, after which the reparse puts
them back together:

    effect module M where { command = {- ¤1 -} {- ¤2 -} MyCmd } exposing (..)
    effect module M where {- ¤1 -} { command {- ¤2 -} = MyCmd } exposing (..)

Each member ALONE is stable at the same gap. This is the next family to work,
and it is not the accepted `where {}` limitation recorded in
[`knownLimitations.md`](knownLimitations.md) — that one is column-sensitive and
moves a comment with one comment present.

### The 23 `multix2` findings: 21 fixed, and the peel that took nothing

A run of multi-line `{- … -}` trailing a `|>` step rendered at the operand
flow's +4 instead of the step column — the **11th family's bug again**, whose
n=1 fix (`ea1c2ab`, then `ebfb33e` for the attachment half) both halves share
through `spanTrailingOwnLine`:

    |> Array.map (\c -> c.kind)         one comment: at the step column ✓
        {- one                          two: at +4, and glued together ✗
           second row -} {- two …

`spanTrailingOwnLine` peeled *the maximal suffix of `LeadsOwnLine` comments*, and
the second member of a run is classified `TrailsPrevious` **against the first** —
so a run whose last member glues ended the suffix immediately and the peel took
**nothing at all**. Both the attachment pass (`movePipelineStepTrailers`) and the
renderer (`renderPipelineStepChildrenWith`'s `stopped`) then left the whole run
in the operand flow. Rule C1 says the run travels together, and
`detachOwnLineTrailer` had already written that down — *"the whole run moves
together and only the first member becomes `Standalone`; a `-- c` glued behind it
keeps `TrailsPrevious`"*. **Look for the container that already agrees.**

**The first version of the fix over-reached, and the fixture that caught it is
the same one the rule was borrowed from.** Splitting at the earliest own-line
member peeled a run led by a *single-line* `{-c99-}`, which the render layer
keeps **inline** whatever its role says — so the run stopped gluing onto its
step's line and stacked below it, where the reparse detaches it to column 1
(`KitchenComments`). That is exactly what `runRendersBelowDeclaration`'s
docstring warns about (*"`LeadsOwnLine` alone is not renders-own-line ... lifting
on the role alone broke nine fixtures"*), and the corrected rule uses that
predicate. It now has **one spelling**, `commentRendersOwnLine`, beside
`CommentRole` for the reason `roleGlues` lives there; `runRendersBelowDeclaration`
delegates to it rather than keeping a copy.

The peel is therefore unchanged wherever it used to take something, and only
extends where it took nothing:

| trailing run | before | after |
|---|---|---|
| `[own]`, `[own, own]` | peeled | unchanged |
| `[own(multi), glues]` | **nothing** | the whole run |
| `[own(single-line), glues]` | nothing | **nothing** (it renders inline) |
| `[glues]` | nothing | unchanged |

**23 → 2 on those eight files, 0 new**, no corpus fixture moved. Fixture
`PipelineComments/PipelineStepTrailingCommentRun`, which pins the run *and* the
single-line-lead boundary; only its first case moves against a pre-fix build,
which is what says the second is a control rather than a claim.

Also corrected while there: `movePipelineStepTrailers`' leftover branch appended
the run to the last step's children instead of restoring them, which **lifts** a
run that was nested deeper inside the operand out to the step level. Nothing is
handed on, so nothing should move.

**The two survivors are one known and one open.** The known one is the
`ContainerTailMultilineComment` record-update field pair recorded above — a
second comment flips `renderGluedLambdaField`'s glue-vs-drop decision, attributed
by rebuild across three commits and not caused here. The open one is
`KitchenSink[multix2]@16534`: a run between a function and its argument in a
broken call glues member 2 onto member 1's `-}` row on the first format and
splits them on the second.

Still uncovered by any elm-format oracle, and worth listing so it is not
mistaken for coverage: an `import`'s own syntax, and the module header. The
corpus fuzzers reach both; nothing asks elm-format about either, and the header
is where the position-less-tail logic (`headerTailGlue`) lives.

Steps 4 and 5 were the systemic pair and 4 came first: the gate makes the
mistake cheap to find, but not making it is better. Steps 6, 7 and 8 come before
9 — an induction is only as good as its base case, and a law is cheaper to hold
by construction than to check.

---

## The triple axis, and where the run axis stops paying (2026-08-08)

Two gaps were closed on the same day, and between them they say the corpus side
of the run axis is finished.

### `check-decision-stability.py` had never seen a run

The instrument that answers *which* decision moved only ever ran at n=1, while
the two gates that find run findings — `--run N` and `--mix*` — can say only
*that* the bytes moved. It now takes the same flags, building its kinds by
calling `fuzz-idempotency.py`'s own `run_kind` / `mixed_kind` / `mix_sequences`,
so only the flag names are spelled twice.

| mode | probes moved | known upstream | named by an intent flip | UNEXPLAINED |
|---|---|---|---|---|
| `--gaps --run 2` | 17 | 17 (#35) | 16 | 1 |
| `--gaps --mix-pairs` | 43 | 43 (34 #35, 8 #25, 1 #14) | 33 | 10 |

**Both numbers equal `fuzz-idempotency.py`'s residual in the same mode, split by
issue the same way.** That is what importing the probe definitions by path is
*for*, and until now it had only ever been true at n=1. The run axis therefore
books this gate no formatter-side debt at all.

The UNEXPLAINED counter rises with runs, and it is honest. Because moved ==
known in both modes, every unexplained probe is a known-upstream one; sampled,
they are the **#25** family — a run spliced into a `type ⟨here⟩ alias` /
`port ⟨here⟩ name` gap, where the two formats differ *only in a blank-line
count*. There is no layout decision to trace, and inventing one to shrink the
counter is exactly what `Formatter.Audit.DecisionTrace`'s rule forbids.

### `--mix-triples`: 24 sequences, 0 formatter-side

`--mix-pairs` gives every member exactly ONE neighbour, so it already sweeps
every *boundary* two kinds can form. A triple adds no new boundary; what it adds
is a member with a comment on **both** sides — the first probe that can sit
inside a peel with a member left outside it (`spanTrailingOwnLine` takes a
suffix), or be classified against a previous comment while a following one
decides what may glue to it (`FlowPolicy`'s inline arm). `mix_sequences(3)`
enumerates the 24 ordered triples that are not all one kind; `a,b,a` is kept,
because its middle member's two neighbours are a kind it is not.

**475,824 gaps, 21,133 skipped by the parser, 154 findings — all 154 known
upstream** (136 [#35](https://github.com/gren-lang/compiler-common/issues/35),
16 [#25](https://github.com/gren-lang/compiler-common/issues/25),
2 [#14](https://github.com/gren-lang/compiler-common/issues/14)).

So **composition stops paying at three, the way length stopped at three.** The
reason is the same one that made `--run 3` dry, one level up: every rule a run
can break is written about *one* neighbour, so a second neighbour reaches no arm
the first did not. The claim is about the corpus, not about runs in general —
`matrix-syntax.py --comment-runs` deliberately stops at two members and is the
axis with the elm-format oracle.

**Non-vacuity was checked rather than assumed**, in the two places it could hide:

- The splice reaches the formatter with three comments in one gap, joined per
  boundary — `./repro.py TypeAlias.formatted.gren block+multi+line 83 --input`
  shows `type {- ¤1 -} {- ¤2 ⏎ second row -} -- ¤3 ⏎ alias Point =`.
- The sweep still reports: the 154 findings are the upstream families, and every
  `line`-leading sequence lands on the same 13 fixtures.

`repro.py` could **not** take a triple label when the sweep started — its `kind`
argument enumerated mixed labels pairwise, so the one tool whose job is to turn a
gate's label back into an input rejected a label the gate had just printed. It
now validates the spelling (`+`-joined, any length; `xN` up to `MAX_RUN`) instead
of listing the labels that existed when it was written. **An enumeration of
another module's vocabulary goes stale silently**, and the way it fails is a
tool refusing its own output.

## The axis all of this was NOT: two comments at two gaps (2026-08-11)

Everything above varies a run **within one gap** — its length (`--run N`), its
composition (`--mix*`), and what the neighbours inside it do to each other. The
matrix's comment axis injects one comment per cell. So did the per-gap pass.
Across every gate in the repo, no probe had ever placed two comments at two
*different* gaps.

The `if`/`when` header bug is what that cost. Its recipe is a riding comment in
the header **and** a row-breaking one nested inside the condition — one comment
at each of two gaps, which is a shape no run can express however long it gets.
`fuzz-idempotency.py` reported it for weeks, but only because
`KitchenComments.formatted.gren` happens to have the first half written into it
by hand, so the single-gap pass supplied the second half by accident. Nothing
was sweeping for the shape, and nothing would have found it in a fixture that
did not already contain half of it.

`--pairs` is that axis. Two comments, two gaps, both inside **one declaration**
— scoped there for two reasons:

- the whole corpus all-pairs is not a tractable sweep. 20,874 gaps for one
  comment kind is ~2×10⁸ pairs, ×9 ordered kind pairs;
- the bug class is local. An outer construct whose row is broken by something
  nested inside it needs both comments in the same declaration to interact at
  all.

The default kind pairs are `block,multi` and `block,line` — a riding comment
first, a row-breaking one after it, which is the recipe stated as a probe.
`--pair-cap N` (default 400) subsamples a declaration with more pairs than
that, seeded so a run replays and a finding reproduces.

**Non-vacuity was measured, not assumed.** With the header fix reverted,
`--pairs` reports **32 findings** on `IfExpression.formatted.gren` — a fixture
the single-gap pass has always called clean, in every kind, for as long as the
gate has existed. With the fix in, 0.

The first whole-corpus run (2026-08-11, 224,166 probes, 65 minutes at `-j 12`)
reports **127 findings, all of them `[known: compiler-common#35]`** — the same
upstream family the single-gap pass carries, reached by more paths. No new
formatter bug on this axis, which is the answer worth having only because the
axis was shown able to find one first.

