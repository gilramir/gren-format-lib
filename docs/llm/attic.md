# The LLM attic

> **This document is written to be read by an LLM.** It is the attic: things
> worth keeping that have no other home. A rule reference says what the code
> does; a doc comment says why the rule that won is right. Neither records the
> approach that was tried and lost, the work that was deliberately deferred and
> what measurement settled it, or the design that was specified and never built.
> Those are the things a model working on this repo will otherwise re-derive —
> usually by proposing something that has already been rejected, because the
> rejected thing is the locally obvious one and nothing in the source signals it
> was attempted.
>
> **Read this before proposing a change to comment placement, glue, or the test
> gates.** Treat a match in [Rejected approaches](#rejected-approaches) as
> evidence against the proposal, not as a draft to refine. Where an entry says
> what something cost, that cost is measured — the fixture and cell counts came
> from running the gates, not from estimating.

Assembled for 1.0.0 from two documents that were retired: the archived long-form
`CLAUDE.md`, and `commentRunTesting.md`. Everything else in those was either
duplicated by [`testing.md`](../testing.md) /
[`commentAlgorithm.md`](../commentAlgorithm.md) or superseded — in particular
`commentAlgorithm.md` §7 (the run rules R1–R5), §8 (why one comment working
implies *n* working, argued from the code rather than from a suite) and §10 (what
each gate varies) now say what `commentRunTesting.md` set out to plan.

Fixture names are under `tests/testfiles/`; counts are what the gates read on
the day.

---

## Contents

- [Rejected approaches](#rejected-approaches) — tried, measured, backed out
- [Deferred, with the measurement](#deferred-with-the-measurement) — decided not to, and why
- [Designed, never built](#designed-never-built) — specified, still open
- [Known coverage gaps](#known-coverage-gaps) — what no gate varies
- [Rules of thumb that cost something to learn](#rules-of-thumb-that-cost-something-to-learn)

---

## Rejected approaches

Each of these was tried against the whole corpus and reverted. The cost line is
the point of the entry.

### `freezeTabs` inside `addSuffixBox`, to fix a `Box.prefix` mis-measurement

**The bug.** `prefix` padded continuation lines using `lineLength 0 pref` — the
prefix's width *if it began at column 0* — while the line renders wherever it
lands. A prefix of `Row[Tab, Tab, "q + one", Space]` sitting at column 6
measures 4+4+7+1 = 16 at column 0 but renders 2+4+7+1 = 14 there, because a
`Tab` snaps to the next multiple of 4 **from where it stands**, and a record
literal's `{ ` is what puts the line at a non-multiple-of-4 column.

**What was tried.** `freezeTabs` on the prefix. It does fix the width — but it
converts `Tab`s to the spaces they render to *standing alone*, which changes the
emitted line at the same time. **2 fixtures regressed** (a `when`-in-parens
header, a `KitchenComments` binop chain), both of which rely on those `Tab`s
re-snapping once the box is embedded.

**What won instead.** `Box.blankLike` — pad with a *blanked copy of the prefix
line* rather than a count of spaces. A copy keeps every element, `Tab` included,
at the same offset within the padding as within the prefix, so both snap
identically at any column and no absolute column has to be known. A `Tab`-free
prefix renders exactly as the old space run did, which is why the corpus did not
move.

**The distinction to keep:** copying pads without touching the emitted line;
freezing changes both.

---

### Adding `Binop` to `boxKeepsTrailingCommentOutside`

It converges the ownership half of the probe that motivated it, and **breaks 7
fixtures** — `BinopChainCommentChain`, `TrailingLineCommentBinopOperand`,
`BinopParenEmptyBracketTrailingComment`, the `"""…"""` backward-pipe pair, and
others. In every one of them a trailing comment belongs *inside* the binop.

Tried, reverted, measured. The list in `Comments.gren` is deliberately short;
`Binop` is not a missing entry.

---

### Relaxing `gluedExposingBox`'s single-line test

**The diagnosis is correct and the one-line fix works.** `gluedExposingBox`
refuses a multi-line header, but only its *inline* branch needs one line — the
vertical branch merely stacks. So a comment in the module header forced the
fallback to the generic flow, which glues the list's first item onto the
header's last row; for a **one-item** list that erases the only evidence a
reparse has that the list was vertical (`MakeLogical.exposedStartsBelowHeader`),
the derived `)` collapses onto that row, and the comment pinned above it escapes
to column 1.

**It cost 4 existing fixtures, and the repair chain is the point.** The hoist
branch applies the same single-line test *on purpose* — its own code comment
says so — and the two agreed only by both falling back to the same generic flow.
Relaxing one alone made a header comment alternate between the two layouts
forever (`SortingCommentZoo`, `ModuleExposingInlineAndHoistedComment`,
`ModuleExposingSortCommentToFront`). Relaxing both fixed those. Then
`headerHasOwnLineComment` turned out to mean "a trailing own-line run", not "an
own-line comment anywhere", because a `--` inside the header puts a later token
on its own row. Then a third finding appeared in
`EffectModuleHeaderInlineComment`: an effect header's `} exposing` tail is
position-less, so a `{- c -}` rendered there reads as own-line on reparse and
moves — `headerTailGlue`'s territory.

Three expanding changes to comment classification in one sitting, none of them
gated over the whole corpus, is how a session ships a regression. **The attempt
was reverted whole.**

**If you pick it up:** start at `headerTailGlue`'s row range for the
effect-module tail, and only then relax `gluedExposingBox`. The renderer change
is the easy half and it is not the half that is wrong.

---

### Adding `SoftIndentedBlock` beside `IndentedBlock` on `blockTailKeepsCommentOutside`

The idea was that a lambda body written on the `->` row and the same body
written below it should treat a trailing comment alike. It converges the probe's
*first* difference, and **it cannot be motivated**: in a correctly-parsed lambda
the body block is the node's last child, so `hasNoFollowingSibling` vetoes the
arm, and the only tree that reaches it is a misparsed one
(`compiler-common#14`). Its whole effect was to turn that probe's
non-idempotency into an AST-mismatch refusal.

---

### Detaching a trailing comment run to column 1

Reading a probe as "the run's tail renders below the declaration, so detach it
to column 1" produced a patch that failed `MultilineCommentTrailedByComment` — a
fixture written for this exact shape, whose own description says detaching there
would "oscillate col 4 ↔ col 0".

The gates cost ten minutes and the fixture named the answer. The general form is
worth more than the case: **when a shape is unstable in one container, look for
the container that already agrees before designing a rule.**

Note also that neither matrix could have found this family — `--comments`
injects exactly one comment per cell, and this needs two.

---

### A "silent flip" check in the decision-stability gate

Dropped as vacuous. Over the corpus the input already *is* the output, so the
two traces come from identical text and nothing can differ — exactly the "if the
two formats agree the comparison collapses into the idempotency check we already
have" trap. The value of that gate is the *reason* a probe moved, which only the
formatter holds; a check that compares two identical texts holds nothing.

---

## Deferred, with the measurement

Not rejected — decided against *for now*, on evidence. The evidence is the part
worth keeping: without it the next reader re-opens the question from scratch.

### The run-classification refactor, sub-steps 2 and 3

The plan was to make two of the comment rules hold by construction rather than by
testing: identify a gap's comment run once, decide **one** role for the whole run
(C1), and let each member contribute only its own `commentTextCanRide` to a fold
(C3). Sub-step 1 — identify the run once — **was done**: `spanTrailingOwnLine`
now lives in `LogicalPrintingTree` beside `CommentRole` and `roleGlues`, and
`Comments.spanTrailingOwnLineNodes`, its mirror, is deleted.

Sub-steps 2 and 3 were **deferred on 2026-08-06, by measuring rather than by
reading**. Three findings, in the order that settles it:

- **The renderer does trust the role**, nearly everywhere except the one slot
  where drift had been found. `FlowPolicy.containerCommentSlot` reads it twice,
  `commentPlacement` turns it into the glue/own-line call for every flow,
  `NodeClassify.literalCommentsRideFlatLine` reads it per child, and
  `MakeRenderBox` has four predicates over it. The field is load-bearing, so the
  refactor is not merely tidying a vestigial one.
- **The split is real, is one shape, and is inert.** A run of two spliced into
  every gap of the corpus (19,081 gaps, single-line block kind) came back with
  two roles at **6,531** of them — every one of them `LeadsOwnLine` +
  `RidesInline`, the generic flow's own chaining answer. By that point the second
  member's separator state is `AlreadyTerminated` / `HardNl`, where
  `commentPlacement` ignores the role, and the bytes matched elm-format's for the
  same input.
- **None of the n=2 bugs came from the split.** Of 20 non-idempotent n=2 probes,
  the four that were this formatter's to fix had **one** role for the run
  (`RidesInline` twice) and a flow-state bug underneath. The other 16 looked like
  an owner split in an effect module's header and were fixed as a *position* bug
  (`b953853`): the `where` block's elided `command` / `=` were anchored by
  counting backwards from the constructor, so a comment in that gap put the
  derived columns inside the comment's own text and carved a phantom slot for the
  second member. Nothing about per-member roles was involved.

So the refactor buys **structure** — C1 and C3 holding by construction — and, on
this evidence, no bugs. If you pick it up: **2 and 3 are one change, not two.**
The per-member role *is* the chaining mechanism (`prevLineGlueRow` /
`prevBlockGlueRow` / `bracketItemRow` all key a comment by its LAST row on
purpose, `a5d948c`), so the second member of a run comes back `TrailsPrevious` /
`RidesInline` *against the first*, which is what tells the renderer to glue it
onto the first's line. Decide one role for the run without the fold in place and
that information is gone; `MultilineCommentTrailedByComment` is the fixture that
says so.

**One unification that looks available and is not.** `spanTrailingComments`
(every trailing comment) and `peelTrailingCommentNodes` (only inline-gluable, at
most one `--`) are **not** the same rule despite the names. `chainedRefRow` and
`SortSymbols.takeSameRowTrailingFrom` genuinely are two formulations of one idea,
but they differ at the margin (backward fold, `firstRow <= acc`, returns a row;
versus forward walk, `firstRow == prevLast`, returns the nodes), so unifying them
picks one comparison over the other and changes behaviour — it needs its own
measurement rather than riding along with another change.

---

## Designed, never built

### The deletion-invariance oracle

The problem it answers: a comment run's test space grows as `3^n`, and the thing
a run gets wrong is usually *layout*, which has no local truth — a run can be
stable, AST-preserving, idempotent and comment-preserving while sitting in
visibly the wrong place. Enumerating is impractical and judging the output is not
mechanical.

The oracle is rule C4 ("a comment changes where the lines fall, and nothing
else") applied to the k-th comment of a run:

> **In a run of two or more comments where at least two members share a
> ride-class, deleting one of those members must change the output by exactly
> that comment's own rendering — nothing else moves.**

You never judge the n-comment output. You check that it differs from the
(n−1)-comment output by exactly the comment removed, and (n−1) against (n−2),
down to n=1 — which the existing gates and the elm-format oracle already cover.
n-comment correctness follows from 1-comment correctness by induction, and no
human reads a three-comment layout.

**The "two share a class" guard is the load-bearing part, and is not a fudge.**
Plain "deleting a comment moves nothing else" is *false*, deliberately: two
shipped rules make a comment change the surrounding layout —
`literalCommentsRideFlatLine` (deleting the only non-ridable member lets the
container collapse back to one line, which is C3 working) and
`NodeClassify.commentBreaksFlowRow` (a comment that ends a row is folded into the
force-vertical decision, so deleting it can re-flow a call or a binop chain).
Both are `any`/`all` folds over the run's *classes*. So if the run still contains
another member of the same class after the deletion, every such fold returns what
it returned before **by construction** — no verdict can flip, and the invariance
holds unconditionally. No baseline, no exception list to keep in step with the
code. It costs one extra comment in the probe.

**Build it in `gen-random.py`, not in the gap fuzzer.** The generator emits from
a tree, so the n and n−1 variants come from the *same* tree deterministically —
the mechanism the sort-order oracle already uses to emit two author orders.
Splicing text to delete a comment from a corpus file cannot guarantee nothing
else moved. The comparison is a **code skeleton**: blank every comment span in
both outputs, drop lines that were wholly comment, require byte-equality of the
rest. That is C4 stated directly, with no span-boundary judgement in it.

---

## Known coverage gaps

Stated so they are not mistaken for coverage. `commentAlgorithm.md` §10 is the
full what-each-gate-varies map; these are the holes in it.

- **`gen-random.py` emits no own-line comment runs.** `comment_chain` emits 1–3
  links, but only *trailing* and only glued — verified still true 2026-08-13.
  Own-line runs are exactly where `detachOwnLineTrailer`,
  `rehomePipelineStepTrailers` and `computeDetachedBelow` each carry
  run-cohesion logic, so the generator cannot reach the code that most needs it.
  This is the wrong *shape*, not merely too few.
- **No elm-format oracle reaches an `import`'s own syntax, or the module
  header.** The corpus fuzzers reach both; nothing asks elm-format about either,
  and the header is where the position-less-tail logic (`headerTailGlue`) lives.
---

## Rules of thumb that cost something to learn

- **Author-intent flag versus renderer prediction.** The distinction decides
  whether a new predicate is legitimate, and getting it wrong is what made
  `commentSplitsType` mis-scoped three times in one day before it was deleted:

  | kind | may read source rows? | example |
  |---|---|---|
  | **author-intent flag** — records what you wrote, information that exists nowhere else | **yes** | `signatureForceVertical` ("did you break at a `->`") |
  | **renderer prediction** — claims what the output will look like | **no**, ask the box | `commentSplitsType` (deleted) |

  A prediction wearing an intent flag's clothes is wrong at the edges forever.
  The replacement is always a question asked of the assembled box
  (`typeContentSpansRows`), which cannot disagree with the renderer because it is
  measuring it.

- **A row-derived layout decision cannot be repaired by ignoring the comment's
  rows.** The comment moves the *code's* rows too, so there is no row-derived
  answer that survives the reflow. Fold the comment's **kind** into the decision
  instead — kinds are stable across a reflow, rows are not.

- **A gate that runs green over the wrong axis reads exactly like a gate that
  runs green.** Before trusting one, check what it *varies*, not whether it
  passed.

- **Attribute before you blame.** With a non-zero baseline, "the fuzzer fails"
  says nothing. Build the previous commit to a second binary and re-probe only
  the failures. Check the instrument first, though: an early attribution script
  keyed each probe's working directory on `hash((path, gap)) % 64`, so concurrent
  threads collided and two identical runs reported 22 and 75 regressions. A flaky
  measurement of a flakiness bug is very easy to mistake for a finding.

- **Adding a comment-bearing fixture is itself a probe.** A rise in a sweep's
  count after adding one is not automatically a regression — attribute it. And
  never delete the fixture to get the number back.
