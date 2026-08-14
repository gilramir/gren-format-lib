# Rejected approaches

Fixes that were **tried and backed out**, or diagnosed and deliberately not
attempted, with what each one cost.

This is the one thing the rest of the documentation does not record. The rule
reference says what every rule does; a predicate's doc comment says why the rule
that won is right. Neither says which alternative was tried first, or what broke
when it was. That is what a future contributor re-derives expensively, so it is
what this file keeps.

Extracted from the archived long-form `CLAUDE.md` when that file was retired for
1.0.0; everything else in it was either duplicated by
[`testing.md`](testing.md) / [`commentAlgorithm.md`](commentAlgorithm.md) /
[`commentRunTesting.md`](commentRunTesting.md) or superseded. Fixture names are
under `tests/testfiles/`; the counts are what the gates read on the day.

**Read this before redesigning a comment-placement or glue rule.**

---

## `freezeTabs` inside `addSuffixBox`, to fix a `Box.prefix` mis-measurement

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

## Adding `Binop` to `boxKeepsTrailingCommentOutside`

It converges the ownership half of the probe that motivated it, and **breaks 7
fixtures** — `BinopChainCommentChain`, `TrailingLineCommentBinopOperand`,
`BinopParenEmptyBracketTrailingComment`, the `"""…"""` backward-pipe pair, and
others. In every one of them a trailing comment belongs *inside* the binop.

Tried, reverted, measured. The list in `Comments.gren` is deliberately short;
`Binop` is not a missing entry.

---

## Relaxing `gluedExposingBox`'s single-line test

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

## Adding `SoftIndentedBlock` beside `IndentedBlock` on `blockTailKeepsCommentOutside`

The idea was that a lambda body written on the `->` row and the same body
written below it should treat a trailing comment alike. It converges the probe's
*first* difference, and **it cannot be motivated**: in a correctly-parsed lambda
the body block is the node's last child, so `hasNoFollowingSibling` vetoes the
arm, and the only tree that reaches it is a misparsed one
(`compiler-common#14`). Its whole effect was to turn that probe's
non-idempotency into an AST-mismatch refusal.

---

## Detaching a trailing comment run to column 1

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

## A "silent flip" check in the decision-stability gate

Dropped as vacuous. Over the corpus the input already *is* the output, so the
two traces come from identical text and nothing can differ — exactly the "if the
two formats agree the comparison collapses into the idempotency check we already
have" trap that
[`commentRunTesting.md`](commentRunTesting.md) warns about.

---

## Not open: `KitchenComments[multi]@2121`

The archive carried this as diagnosed-but-not-attempted, and it is recorded here
only so nobody re-opens it from git history. **It does not reproduce** (checked
2026-08-13, on a freshly built CLI): `repro.py KitchenComments.formatted.gren
multi 2121` reports the two passes agreeing. It was never registered in
`tests/idempotency-known-baseline.json` either, so nothing was forgiving it — had
it still reproduced, the gate would be failing on it.

The shape it described was `glueLeading` asking `commentTextCanRide` of the
*whole* leading run, all-or-nothing, so that on reparse a multi-line comment
joined the run and dragged a ridable `{- c -}` off the `|>`'s row. That mechanism
and the lesson it belongs to — making two owners render alike is not the same as
making the owner not matter — are documented in
[`commentAlgorithm.md` §4.6](commentAlgorithm.md).
