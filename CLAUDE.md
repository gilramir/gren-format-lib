# CLAUDE.md — gren-format-lib

`gilramir/gren-format-lib` is a Gren **package** (`platform: common`) that holds
the Gren formatter library. The formatter is consumed by:

- `gren-format/` — the standalone `gren-format` CLI (primary consumer)
- `compiler/` — the legacy `gren format` subcommand of the Haskell front-end

All formatter source lives in `src/Formatter/`. The package also hosts three
AST utility modules in `src/Compiler/`, moved here out of `compiler-common`
because only the gren-format tooling uses them:

- `Compiler.Ast.Compare` (`compareModules`) — semantic AST equality, used to
  verify a format preserves meaning
- `Compiler.Ast.Source.Json` (`encodeModule`) — JSON encoder for the source AST
- `Compiler.Parse.Context.Json` (`encodeContext`) — JSON encoder for parse context

All three are re-exposed by the package so the `gren-format` CLI and this
package's `tests/` can import them (their module names are unchanged).

## Sibling repos (expected at `../`)

| Path | Role |
|---|---|
| `../gren-format/` | Standalone CLI that imports this package |

## Build & check

Compile a module to surface type errors (use the **module name**, not a file path):

```bash
cd gren-format-lib
devbox run -- gren make Formatter
```

The package itself has no runnable app — it is a library. The `tests/` directory
is a separate Gren application that depends on this package locally.

## Tests

### Effectful suite (main gate)

```bash
cd gren-format-lib/tests
./run-tests.sh     # builds tests/app via devbox, then runs it
```

`run-tests.sh` recompiles the test harness against the formatter source directly
(the `tests/` app depends on `..` locally), so editing formatter source and
re-running `run-tests.sh` is enough — no separate library build step.

Test cases are in `tests/src/Test/Formatter/Format.gren`. Each calls:

```gren
assertPrettyIn fsPerm "<SuiteDir>" "description" "FileBaseName"
```

which performs three checks:
1. **Formatting** — `format(testfiles/<SuiteDir>/<FileBaseName>.dirty.gren)` is
   byte-equal to `testfiles/<SuiteDir>/<FileBaseName>.formatted.gren`
2. **AST equivalence** — re-parsing the formatted output yields a semantically
   equal AST (catches formatting that changes meaning)
3. **Idempotency** — re-formatting the `.formatted` file changes neither the
   `Module` nor the comment/blank-line `Context`

Fixtures are grouped **one directory per suite** under `tests/testfiles/` —
e.g. `BracketComments/`, `KitchenSink/`, `ImportStatements/` — each named for
the `Format.gren` suite function that reads it. `Divergence/` is the one
suite with no source-tree twin: it holds one fixture per entry of the
divergence catalogue in `docs/elmFormatComparison.md`, named for its entry and
built from that entry's own worked example — that suite tests the
*documentation*, and writing it found six entries whose example no longer
matched the shipped formatter (#8, #9, #18, #22, #25, #26).
`check-divergence-index.py`, run by `run-tests.sh`, fails if the
entry↔fixture mapping stops being 1:1. `tests/corpus.py` is where the python
gates ask which fixture directories exist, so a new suite directory is swept
automatically.

**To add a test:** write both `<Name>.dirty.gren` and `<Name>.formatted.gren` in
the suite's directory, then add an `assertPrettyIn fsPerm "<SuiteDir>"` line in
`Format.gren`. Generate the `.formatted` with:
```bash
node ../../gren-format/app --show <Name>.dirty.gren > testfiles/<SuiteDir>/<Name>.formatted.gren
```
Read it before trusting it — confirm the output is actually canonical.

### Idempotency fuzzer

Inserts a `{- ¤ -}` block comment into every inter-token gap, formats twice,
and requires byte-identical output. The safety net for comment-shift bugs.

```bash
cd gren-format-lib/tests
python3 fuzz-idempotency.py -j 12                                      # whole corpus
python3 fuzz-idempotency.py -v testfiles/<SuiteDir>/Foo.formatted.gren  # one file
```

**Rebuild the `gren-format` app first** (`cd ../../gren-format && ./build.sh`) —
fuzzers invoke `../../gren-format/gren-format.sh` as a subprocess, so they
require an up-to-date binary. Run after any change to comment handling, and
after adding any comment-bearing fixture.

### Construct × context syntax matrix

The corpus reaches only the syntax somebody thought to write, and both fuzzers
perturb *comments* and *whitespace* over that fixed corpus — **neither varies
syntax**. A bug needing a conjunction of features therefore has no fixture. This
is the syntax axis: it embeds every expression form in every context, in up to
four **layout variants**, and checks each one (**2079 cells**).

It has **two vocabularies**, paired by `kind` and never crossed — an expression
cannot stand in a signature and a type cannot stand in a call argument:

- **expr** — 41 expression constructs × 25 expression contexts. The template is
  a `v = <body>`.
- **type** — 11 type constructs (`Int`, `(Array Int)`, `(Int -> Int)`,
  `{ a : Int }`, `{ r | a : Int }`, …) × 15 **declaration** contexts: a
  signature's sole/first/mid/last argument, a signature already broken at a
  `->`, a type alias body, an alias field, a union payload, a `let` binding's
  annotation, and a `port`. The template is a whole declaration, so each carries
  its own header (`port module …` for the port contexts) and its own trailing
  definition.

The type axis was added 2026-08-03. Until then the whole of Gren's declaration
syntax had no cell here, which — together with `fuzz-idempotency.py` sweeping
only one comment kind — is what hid the signature-`->` comment rule long enough
for a change to it to ship with 401 regressions. See
[`docs/commentRunTesting.md`](docs/commentRunTesting.md).

The variants are the author-broken axis (added 2026-07-18, after a record-literal
binop-field crash slipped through a flat-only matrix):
- `flat` — the paren-carrying atom on one line (the original 850 cells).
- `broken` — the same atom pre-broken across rows (valid in every context).
- `bareFlat` / `bareBroken` — the atom with its outer parens stripped, in
  **value-position contexts only** (record field, `let` binding, branch body,
  array item, …). This is the variant that catches value-position bugs: the
  paren-carrying atoms route a multi-line operand through the *handled*
  `ParenBlock` arm, so only the bare form reaches the crash's code path.

```bash
cd gren-format-lib/tests
./matrix-syntax.py -j 12                                  # whole matrix (all variants)
./matrix-syntax.py -v                                     # source + output per failure
./matrix-syntax.py --variant broken --variant bareBroken # author-broken variants only
./matrix-syntax.py --construct recordUpdate1 --context parenBinopArg
./matrix-syntax.py -k /tmp/failing                        # write failing cells out as .gren
./matrix-syntax.py --no-parity                            # skip oracle 4
./matrix-syntax.py --update-baseline                      # rewrite the parity baseline
```

**Rebuild the `gren-format` app first** — it shells out to it. Oracle 4 also
needs `elm-format` on PATH; without it the matrix says so loudly and runs the
other three rather than quietly reporting a thinner green.

Oracles 1–3 need no human review:

1. **Layout, both directions** — *flat-input variants only* (`flat`, `bareFlat`).
   Layout is author-driven — no page width, no fitter — so a construct written
   flat renders flat unless its content forces a break: a flat construct in a
   flat context **must** stay one line; anything involving `if`/`when`/`let`
   **must** break. Over-approximation (pre-breaking something that renders
   inline) fails the first; a construct that stops breaking fails the second.
   This is a flat-*input* truth, so it does not run on `broken`/`bareBroken` — a
   broken input has no local layout truth (gren collapses a broken-but-fitting
   binop), so those variants lean on oracles 2–4 instead.
2. `--show` internally does parse → render → reparse → AST-compare → render
   again → idempotency-compare, so a clean exit also buys AST equivalence,
   idempotency, and "the output parses". Each failure title is its own class.
3. `--audit-predicates` on every cell (see below), over generated syntax rather
   than only the corpus.

**Oracle 4 — elm-format parity.** Gren is a fork of Elm, so on shared constructs
the two formatters should agree byte-for-byte. Every cell is translated to Elm
and diffed against `elm-format --stdin`. Translating *real* Gren source to Elm is
lossy hand work — which is why the audit in the root `CLAUDE.md` is a manual
exercise — but the cells are built from a vocabulary this script authors, and
across all of it the only Gren-vs-Elm difference is `when X is` → `case X of`.
The translator is therefore one regex, and it is *exact* for that vocabulary
rather than approximate. A construct or context that is not valid Elm must
extend `to_elm`, or be given no Elm twin; a bad translation reports a fake
divergence.

Unlike 1–3, **oracle 4 is not a truth**: gren-format diverges from elm-format on
purpose (README "Divergence catalogue"), so it is gated against a reviewed
baseline in `matrix-parity-baseline.json`. Each diverging cell is registered with
a reason, and the matrix fails on a cell that diverges *unregistered*, or a
registered cell that *no longer* diverges (fixed, or the entry was always wrong).

The hazard is the fixtures' hazard — a baseline entry that is really a bug
freezes it as expected output. Three things push back: a reason of `UNREVIEWED`
is counted and printed on every run, so the debt is never silent; a reviewed
entry is expected to name a catalogue number, making registration a documentation
decision rather than a keystroke; and a divergence reviewed and found to be a
genuine bug gets a `BUG:` reason, which is **also** printed every run — being
understood is not the same as being acceptable, and a baseline entry is the
easiest place in this repo for a known bug to go quiet.

Current state: **2079/2079 pass oracles 1–3**; 1358 are byte-identical to
elm-format, with 721 registered divergences — 444 redundant parens (#10), 125
single-item-container collapse (#21), 65 unrecorded type breaks (#28), 38
precedence-split binop chains (#17), 30 parenthesized function types (#27), 10
`let`-annotation head glue (#29+#10), 6 backward-`<|` flat layout (#14), 3
pipeline-`|>` alignment (#19) — **0 UNREVIEWED and 0 known BUGs**. Every
divergence names a catalogue entry.

The type axis arrived on 2026-08-03 with 123 UNREVIEWED. Eighteen were fixed the
same day (a parenthesized *application* now keeps its break, and a signature goes
multi-line whenever a break **survives rendering**); the remaining 105 were
reviewed and became three entries:

- **[#27](docs/elmFormatComparison.md#divergence-27)** (30) — a parenthesized
  *function* type still flattens. An arrow-joined type must break *before* each
  `->`, and that per-segment shape is not rendered inside a `ParenBlock`.
- **[#28](docs/elmFormatComparison.md#divergence-28)** (65) — a type break with
  nothing to record it: a bare application (`InsertTypes.typeWithArgs` splices
  argument nodes flat into the parent flow), a break inside one record field or
  before the first one (`itemsSpanRows` compares each field's start to the
  previous field's *end*), and the outer application of a nested one. **The
  record half is not a type question** — `itemsSpanRows` is shared with
  expression records and arrays, so `v = { a =` ⏎ `1 }` collapses identically,
  and changing it moves every bracketed literal in the corpus.
- **[#29](docs/elmFormatComparison.md#divergence-29)** (10) — a `let` binding's
  annotation is not rendered by `makeSignatureBox` at all, so a broken type
  stays glued to the `bnd :` line. An inconsistency rather than a preference:
  the same type under a top-level `foo :` does lift, and a multi-line *record*
  type already lifts here too (a flow-level `DropBlock` rule).

**The rule that decides all of this is "did the break survive rendering", asked
of the rendered box** (`makeSignatureBox`'s inline arm falls through to the
per-segment layout when the flow comes back multi-line). A row-derived version
— "some segment spans rows" — was tried first and is wrong: it fires for breaks
that do *not* survive, so the first format emits a broken signature wrapped
around a break that got flattened inside it, and the reparse reads a one-row
type and goes back to inline. Twelve cells oscillated that way before the test
moved to the box.

Until 2026-08-03 the flattening was deliberate, pinned by
`SignatureSegmentBreaks`, and justified by a code comment claiming *"elm-format
flattens a segment the author broke inside a record type or parens"*. That claim
is **false** — elm-format keeps every one of them — which is what reopened the
decision.

(A former
divergence, a record update as a direct multi-line `|>` operand keeping its
fields 4 past the `{`, was eliminated 2026-07-31 by rendering the pipeline
operator as a Box *prefix* instead of a flow item — the fields now hang off the
`{` byte-identically to elm-format. Old catalogue #22 was removed and the three
comment-placement entries added the same day took #22–#24.) The
author-broken axis found four real bugs, all **fixed**: a lambda body
over-indenting to +8 in array-item / nested-lambda-body positions
(`LambdaBodyIndentInBrackets`); a `let` as a `<|` body over-indenting its
`in`/result by 4 (`LetAsBackwardPipeBody`); a multi-line container operand
dropping below a dangling `|>` instead of gluing to it (`PipelineContainerOperand`);
and a bare `if`/`let` as an array item over-indenting its body by +4
(`BareIfListItem`, `BareLetListItem`). All four were the same class — an extra
`AcrossOrVertical` item-wrapper (or pipeline-step spread) stacking its +4 on a
block's own +4 — surfaced only because the author-broken axis feeds pre-broken
input. (A former divergence, a lambda record-field value keeping its head on the
`= ` line, was reviewed and eliminated the same way — it now drops whole below
`field =` like every other value, matching elm-format; the old catalogue #23 was
removed and later entries renumbered.) Use `-v` to see each divergence beside
elm-format's output.
`docs/redundantParens.md` is the reader-facing write-up of the #10 family,
every example verified against both formatters. gren-format never strips a
redundant paren, in any position, including call arguments — the former
one-layer-only call-argument stripping (and its `doubleParen/callArg*`
inconsistency) was removed entirely 2026-07-15.
`whenExpr/pipelineOperand` (a `(when …)` direct pipeline operand stranding the
`|>`) and `*/parenBinopArg` (a doubled `((if/when/let ...)` call argument
anchoring `else`/`in`/its inner `)` to the OUTER paren instead of the inner
one) were both fixed the same day, in `Render/MakeRenderBox.gren`, by making the
paren wrap anchor on the paren that actually encloses the block. Both fixes were
originally two dedicated predicates; neither survives — the padding they selected
for is what `wrapParenVerticalPadded` does in every vertical case, so
`parenGenericFallbackBox` now applies it unconditionally there rather than asking
a predicate which paren to pad.

**Reclassifying is not a formality.** When the 46 UNREVIEWED were reviewed, two
weaker tests both got it wrong: "same tokens once parens are deleted" cleared 45
of 46, and "does it still diverge with the parens stripped from the source"
cleared 39 — but the source-stripped form takes a different code path, so it
answers a different question. The decisive test is whether **elm's output has
fewer parens than gren's**: if elm keeps the same parens, the divergence cannot
be about parens. That found 4 cells where both formatters agree on the parens and
only the layout differs — real bugs that a blanket reclassification would have
frozen as expected output, including one already known.

Deliberately not covered, and stated in the script rather than hidden: multi-line
string literals (`"""x"""` does not parse on one line, so it cannot be a one-line
atom) and more than one comment per cell (a comment *run* has its own
all-or-nothing rules; `fuzz-idempotency.py`'s all-gaps pass generates one, but
without the elm-format oracle). Also still uncovered on the declaration side: an
`import`'s own syntax and the module header — both reached by the corpus fuzzers,
neither by an elm-format oracle. The plan for the comment-run half is
[`docs/commentRunTesting.md`](docs/commentRunTesting.md).

#### The comment axis (`--comments`)

Until 2026-07-31 comments were excluded here and left to the fuzzers. That left a
hole at the **intersection**: this matrix varies syntax and asks elm-format, the
fuzzers vary comments and ask only "is it stable?" — so a comment *placement*
divergence from elm-format was invisible to every gate in the repo. It is stable,
AST-equivalent and idempotent; nothing ever asked elm-format what it thought.
That hole hid both leading-`{- -}` pairing divergences (`7c20e15` in broken
calls, `cd774f5` in broken binop chains), and it was not slow-acting — `7c20e15`
was hand-checked against elm-format and gated the same day, and still shipped a
second divergence in a shape its author did not think to type. Manual parity
checking scales with imagination; an oracle over generated input does not.

`--comments` crosses the two axes: each syntax cell gets **one** comment injected
into an inter-token gap, then runs oracles 2–4. Four placements per gap (`{- -}`
/ `--`, each trailing the previous token or leading the next), because
trailing-vs-leading is exactly what the `CommentRole` classifier decides.
Atom-local gaps run for every cell; context-template gaps run once per context,
since they don't depend on which atom fills the hole.

```bash
./matrix-syntax.py --comments -j 12                     # whole axis (~39k cells, ~11 min)
./matrix-syntax.py --comments --construct binop --context top -v
./matrix-syntax.py --comments --comment-kind block --comment-pos lead
./matrix-syntax.py --comments --update-baseline         # rewrite the COMMENT baseline
```

It is a **deliberate gate, not part of a default run** — run it whole after
touching anything in the comment pipeline. A default run prints a line saying it
did not run, so the green never looks broader than it is.

Oracle 1 does not apply (a comment may legally force a break). Oracles 2 and 3
are unchanged truths, and one more is added that the syntax axis has no use for:
the output must contain the marker **exactly once** — a formatter can drop or
duplicate a comment and still be a stable fixed point, which no
diff-against-itself check can see. Oracle 4 gates against its own
`matrix-comment-baseline.json`.

Auto-classification composes with the syntax baseline: a comment cell whose
*uncommented* form already diverges is registered `INHERITED: <that reason>`
rather than booking fresh debt for the same #10. Two comment-position families
are auto-classified:

- **#13** — gren keeps a comment trailing the token it was written after.
- **#22** — the two formatters put it on opposite sides of a token the parser
  records **no position for** (`crossed_only_unrecorded_tokens`). Only a binary
  operator and a bracket carry a position in the Gren AST; `=` `:` `|` `,` `->`
  and the keywords are discarded, so both authorings around one of them arrive
  identically and one of them must differ from elm-format whichever side is
  picked. The rule fires only when *every* token the comment crossed is
  position-less — a move across a bracket or an operator is a boundary gren can
  see, so it still books debt.
- **#23** — gren emitted *exactly* its comment-free rendering of the cell and
  elm-format did not (`only_elm_reflowed`), so the extra structure is elm's
  alone. This one needs the uncommented cell's own two outputs, which
  `--update-baseline` now computes for all 1,738 syntax cells up front. It is
  asymmetric on purpose: "both re-flowed, elm has more lines" is NOT this rule —
  a `{- c -}` in a broken call defeats gren's own fn/arg0 glue, and that second,
  unreviewed difference would ride in behind an elm-re-flowed label.

A divergence where gren stranded the comment **alone on its own line** is
never auto-classified (unless it is #22) — that is the exact shape of both
pairing bugs, so a classifier that swept "the comment moved" into one family
would have frozen the very bug the axis was built to find.

**First run (2026-07-31) found 424 hard failures — all now fixed**, in three
commits:

- 26 emitted **invalid Gren** (a `--` inside a container's item let the container
  collapse to the flat form, putting the synthesized `]` inside the comment) —
  `8ce035b`.
- 184 were a `--` before a lambda body the author started on the `->` row —
  "Put a `--` before a lambda body inside the body".
- The last 214 were one mechanism: **a comment adds a source row, and the reparse
  reads a different layout**, because `forceVertical` (calls, binop chains) and
  the record-field lambda glue are decided from *source rows* — and the AST those
  rows come from has no comments in it. Fixed by folding the missing signal into
  the same decision: `NodeClassify.commentBreaksFlowRow` for the flow flags, and
  `renderGluedLambdaField` observing the body's rendered box for the field glue.
  That last one also fixed a comment-free instance of the same bug
  (`{ fld = \q -> { a = 1` / `, b = 2 } }` oscillated with no comment anywhere).

The axis reported **0 failing cells** until the type axis was added on
2026-08-03. It now runs **45,948 cells with 58 failing**, and those 58 are one
pre-existing family — every one verified against a build of `735adc4^`, so none
is new:

```gren
foo : Int -> {- ¤ -} { a : Int
             , b : String }
```

`typeSegmentsForceVertical` gates its dropping-record trigger on
`not hasComment`, so a comment-bearing signature whose type carries a multi-line
record never commits to the broken layout. The first format emits `foo : Int ->`
with the record below it; the reparse sees the record starting on a later row,
concludes the author broke at the `->`, and renders the fully-broken form. The
same "a comment adds a row and the reparse reads a different layout" class as
`commentBreaksFlowRow` and `commentSplitsType`, in the one place the
force-vertical trigger is still switched off when a comment is present.

**So `--comments` is currently RED at 58.** Left unfixed deliberately: the fix
means ungating that trigger, which reroutes the `hasComment &&
typeHasCommentBracket` branch too, and that is a layout change wanting its own
review rather than a rider on the axis that found it. Treat a count above 58, or
any failure outside `tyRecord2` in a mid/last signature argument, as new.

**Reviewed 2026-07-31** (`comment-parity-triage.md` has the per-family evidence
and the verdicts): the 16,141 UNREVIEWED divergences were sorted into 13
families and read. **Six were real bugs, now fixed** — a comment past a
container's `{`/`[` hoisted out of it (including a record update's, whose base
name's recorded position separates the opener slot from the ambiguous `|` one),
one past a `}`/`]` pulled inside it, a pipeline operator rendered as a flow item
(flat +4) instead of a `B.prefix` at the operator's own width, a `{- c -}`
between a seed and its `<|` forcing the chain vertical, and a comment between a
function and its first argument defeating the broken call's fn/arg0 glue. 3,877 baseline entries
became byte-identical to elm-format; 2,882 registered as #22 and 2,911 as #23,
leaving **5,485 UNREVIEWED** — still debt, not failures; see `tbd.md` for what
they are and the next step for each.

**Interview round 1, 2026-08-01.** 30 of those groups (2,948 cells) were given a
verdict with `--interview`, and read together they are **six English rules** —
now the normative statement of comment behaviour in
[`docs/commentHandling.md`](docs/commentHandling.md#the-six-rules-at-a-glance). The
verdicts were consistent under one of them (**C2**: at a separator the parser does
not record, the comment goes to the *later* side), which `=` `:` `in` `is` `then`
`->` already did and `,` `|` did not. Both were changed to match, via a new
`CommentRole`, `LeadsNext`. A single-line `{- -}` in a list's comma gap now leads
the item below it; a `--` there is a documented exception and still trails the
item above, because that is the only spelling real code uses (round 2 below
generalises that exception from lists to every line-leading separator). Exposing lists
(whose items sort, and whose comment ownership `SortSymbols` models the other way
round) and union variants are deliberately unchanged. **1,522 more cells are now
byte-identical to elm-format; UNREVIEWED fell 5,485 → 3,561, with 0 hard failures
across all 38,560 comment cells.** `gen-random.py` also turned up a crash class
neither matrix can see — a comment glued to the front of an item holding a
`"""…"""` breaks the string's equal indentation — 17 instances widened by this
change and 19 pre-existing, all fixed. See the "Interview round 1" section of
`comment-parity-triage.md`.

**Interview round 2, 2026-08-02.** Ten more groups. Two fixes, and one of them
revises round 1's reading of C2. The exception ("a `--` between two list items
stays with the item above") turned out to be a fact about **line-leading**
separators, not about lists: `,`, a union's `|` and a record update's `|` all lead
their line, so a comment above one strands nothing — and only the record update
was not obeying it, sending *both* same-row spellings past the `|`. It now keeps
the row the author wrote on, via a new `CommentRole`, `TrailsHead` (the base is
not one of the update's children, so `TrailsPrevious` has nothing to reach). A
single-line `{- -}` there is unchanged and still leads the first field. The other
fix: a comment forcing a binop chain to break at an operator the precedence split
would have kept inline now indents the continuation `grenIndent` rather than
landing flush under the seed.

**The first of those cost elm-format parity and was taken anyway.** 600 comment
cells that were byte-identical now diverge and none gained — elm-format renders
each of the record update's three spellings differently, so gren's one collapsed
answer used to match it on `{ rec | -- c` and now matches on neither. The trade
was made for one rule holding at all three separators, and because that spelling
occurs nowhere in `core/`, `compiler-common/`, `compiler-node/` or this repo.
Of the 600, **150 auto-classified** (#22, INHERITED:#21+#22) and **450 became
fresh UNREVIEWED** — real new debt, to be given a `keep` verdict as it comes up in
`--interview`. UNREVIEWED nets 3,561 → 3,534 only because a separate 475 cells
left it the same day (the *old* record-update family, now auto-classifiable as
#13); the two flows crossing is not the 600 being absorbed. Still 0 hard failures
across all 38,560 cells. The reasoning and the revert path are in
[`docs/commentHandling.md`](docs/commentHandling.md) and
[divergence #22](docs/elmFormatComparison.md#divergence-22).

**Interview round 3, 2026-08-01.** Eighteen more groups; one fix, and one thing
worth knowing before the next sitting. Four `bug` verdicts (388 cells) asked for
the comment to stay **right** of a record update's `|` — round 2's question with
the opposite answer. `{ rec -- c` ⏎ `| f = 1`, `{ rec | -- c` ⏎ `f = 1` and the
same with the gap stretched all format to a byte-identical string, so only one of
the two can be had, and round 2 chose. **A superseded decision comes back looking
like a new one**: the fix reshapes the disagreement, the other spelling of the
same gap resurfaces as its own group, and nothing in the review cut says it is the
far side of a question already settled. These four plus one more were the "450
fresh UNREVIEWED" round 2 predicted, and were superseded to `keep`.

The fix came out of a group given `keep`, and out of that verdict's own stated
reason: a `--` mid-chain was breaking the chain at whatever operator it sat before
(`one + two -- c` ⏎ `* three`), gluing across a looser operator so the row reads
as `(one + two) * three` — a grouping gren-format never produces without a comment.
`makeBinopBox` asked `commentBreaksFlowRow` of each operand alone, and a comment at
the end of a *non-last* operand has nothing following it within that operand, so
the chain missed the precedence-aware renderer. `BinopLayout.commentBreaksBinopChain`
asks it of the whole chain with the operators interleaved back in. Round 2 had
fixed the *indent* of this same forced break; this fixes *where it breaks*, and
together they make good the claim
[#17](docs/elmFormatComparison.md#divergence-17) was already making — a comment
changes where the rows fall, never how the operators group. Fixture
`BinopCommentPrecedenceBreak`; the eleven remaining groups were all answered by
decisions already on record (C2 at a record field's `=`; the single-field
container being unconditionally flat, comment or not). Write-up in the
"Interview round 3" section of `comment-parity-triage.md`.

To read them, use `tests/triage-comment-parity.py --review`, which buckets on
the *disagreement* rather than on the cell: names and literals flattened, the
surrounding context dropped, so the same question asked of `1` / `'c'` inside a
call argument / a record field / a pipeline step is one entry with a count.
`--interview` walks the same entries asking for a verdict and appends each to
`comment-review.jsonl`; `--decisions` reads them back. Verdicts are keyed on a
hash of the disagreement, so one recorded before a fix reshaped the group is
re-asked rather than silently carried.

**A verdict is not a registration** — that gap sat open for three rounds. The
baseline read `UNREVIEWED` for every reviewed cell, so 40 decided groups covering
**2,631 cells, 74% of the debt**, were not showing up anywhere. Registering means
giving the group a **`reason`**: a divergence-catalogue number, which is the
documentation decision the tool docstring asks for rather than a keystroke.
`--register` then writes it in, overwriting only `UNREVIEWED` and reporting any
reviewed group still missing a reason. **UNREVIEWED 3,534 → 903.** Round 3's 40
groups needed one new catalogue entry, [#25](docs/elmFormatComparison.md#divergence-25)
(a comment keeps the rows you gave it — elm-format both adds a blank line above
an own-row comment in a container and closes the row break below one leading an
operator); the rest are #22 / #23 / #17 / #14 / #12 / #21 / #24 combinations.

**`PENDING-UPSTREAM:<issue>: <what>`** is a fourth reason class, added 2026-08-01
for a divergence that has been diagnosed and whose cause is **not in this
formatter** — so far, the parser it is built on. It is printed on every run like
`BUG:`, because it is parked rather than accepted, but listed separately since its
work-list belongs to somebody else. It needs no follow-up bookkeeping: when the
upstream fix ships and the compiler-common dependency is bumped, the cells stop
diverging and the existing `parity-baseline-stale` check fails until the entry is
removed. First use: 12 cells on
[compiler-common#14](https://github.com/gren-lang/compiler-common/issues/14).

**Do not invent a reason string to close out a group.** Registering a fixed
group's cells as `FIXED` looked reasonable for about a minute and was wrong twice
over: a fully fixed group's cells leave the baseline on `--update-baseline` and
need no reason at all, and a group that is only *partly* fixed still diverges and
needs the real catalogue number. That mistake wrote a meaningless reason over six
cells that turned out to be plain [#25](docs/elmFormatComparison.md#divergence-25)
— exactly the "a baseline entry is the easiest place for a known bug to go quiet"
failure this file warns about, arriving under a reassuring label.

`--register` keys on the group **as it is now**, never on the cell keys the
verdict recorded. Those drift: a fix reshapes groups, and at the time this was
built the recorded key lists covered **103 cells whose current group had no
verdict at all** — a stale approval arriving with a reviewed label, which is the
one thing this baseline exists to stop. `group_sig` is the same function
`--interview` skips on, so a group registers exactly when it would not be
re-asked.

**Interview round 4, 2026-08-01.** Ten groups; six registered, one real bug found.
Five of the six `unsure` notes were one question — *"do we track the `=` / the `|`
in the AST?"* — which is [#22](docs/elmFormatComparison.md#divergence-22) met for
the third round running without being recognised. **Neither is tracked**: only a
binary operator and a bracket carry a position, so both authorings arrive
identically and one must differ from elm-format whichever side is picked. Groups
42–44 (a `--` in a field's `=` gap) are straight C2; groups 45–47 (a `--` at a
record update's `|`) are C2's line-leading exception, i.e. the debt `fab9370` said
to answer with `keep`. All six registered `#22` (48 cells).

The sixth `unsure` was not that question, and its note is the one that found the
bug: *"what does this look like without a comment?"* — it didn't. A record field
holding a lambda dropped its body **2** past the `{` instead of 4, where every
other commented field value, elm-format, and gren's own comment-free rendering of
the same field all agree on 4. `renderGluedLambdaField` assembled the field flow
with `assembleFlow False 0`, copied from `makePBox`'s `IndentedBlock` arm, where
the 0 is right because there the *parent* applies the indent; a field is a
bracket-list **item** and must carry its own +4, as `renderFieldFlowWithValueBox`
already did. The two only disagree when the field's flow breaks, and a comment in
the head is the only thing that breaks it — invisible to every gate that does not
cross syntax with comments. Fixture `RecordLambdaFieldCommentIndent`; **UNREVIEWED
855 → 831** as its 24 cells became byte-identical to elm-format, with 0 hard
failures across all 38,560 comment cells. Write-up in the "Interview round 4"
section of `comment-parity-triage.md`.

**Revising a verdict is an append with the same `sig`** — `--register` builds
`{sig: decision}` over the log in order, so the last row wins. `--redo` re-asks
interactively; there is no in-place edit, and the superseded rows are the record
of what was thought before.

**Current state (2026-08-03), after the type axis landed.** 45,948 cells,
**25,720 baseline entries**, 58 failing (the one pre-existing family above).
Reasons: 12,477 INHERITED, 4,610 #22, 2,525 #13, 2,278 #23, **1,436
UNREVIEWED**, then combinations. The comment axis had been driven to 0
UNREVIEWED on 2026-08-02; every one of the 1,436 is a **type-context** cell from
this axis's first run and none has been read yet. That is fresh debt of exactly
the kind the interview rounds exist to work down — `triage-comment-parity.py
--review` is the tool, and the type cells are all of it.

### Predicate/renderer agreement audit

Every other check in this repo is a **self-consistency** check — fixture diff,
AST equivalence, idempotency, both fuzzers. Output that is wrongly laid out but
deterministic, AST-equivalent and idempotent passes all of them. This audit is
the missing oracle: it checks the layout predicates against the renderer itself.

Several predicates in `Render/NodeClassify.gren` answer "does this subtree force
a hard break?" *before* rendering, so callers can lay out the code around it.
Each is a hand-written mirror of the renderer, and nothing forces them to agree.
The audit checks, per LPT node:

    predicate node == True   ==>   the node's own box renders multi-line

```bash
cd gren-format-lib/tests
./audit-predicates.py -j 12                              # whole corpus
./audit-predicates.py -v testfiles/<SuiteDir>/Foo.formatted.gren
```

**Rebuild the `gren-format` app first** — it shells out to `--audit-predicates`.

Findings are split into **root** and **propagated** (a recursive predicate's
`Array.any … children` fallback makes one wrong leaf answer wrong at every
ancestor too); only root findings are a work-list.

Under-approximation is deliberately not reported — these predicates claim only
the *unconditional* breaks, and a node can still break for reasons they do not
model (most often the author's own `forceVertical` layout).

The audit itself is `src/Formatter/Audit/PredicateAgreement.gren`. Most of the
former shape predicates (`subtreeHasVerticalBox`, `nodeSpansRows`, …) were
retired — verticality is now decided from the rendered box (`isSingleLine` /
`B.allSingles`), so the audit now covers only the one structural query that
remains (`isMultilineLambdaParenBlockBox`).

### Render-invariant check (`check-render-invariant.py`)

The architecture invariant — **no `Render/*` code reads a source row/position to
make a layout or comment-placement decision** (placement is the stored
`CommentRole`; verticality is the rendered box shape) — is enforced by
`tests/check-render-invariant.py`, which `run-tests.sh` runs first. It greps
`Render/*` (comment/string-aware) for row/position accessors and fails on any
outside a small allowlist of genuinely-structural functions. A new render-side
row-read is almost always a regression toward the oscillation/crash class this
architecture removed; if a use is truly structural, allowlist its function there
with a reason. The model it protects is `CommentRole`'s docstring in
`Formatter.Logical.LogicalPrintingTree` plus `classifyCommentKind` in
`Comments.gren`.

### Whitespace-canonicalization fuzzer

Perturbs inter-token whitespace and requires `format(perturbed) == format(original)`.

```bash
cd gren-format-lib/tests
python3 fuzz-whitespace.py                 # default: stretch mode
python3 fuzz-whitespace.py --mode indent   # modes: stretch | indent
python3 fuzz-whitespace.py -j 12           # parallelise
```

This machine has 16 cores; both fuzzers default to `-j 2`. Use `-j 12` for a
fast whole-corpus sweep.

### Property-based random generator

Every gate above walks a fixed space: the matrix enumerates known shapes, both
fuzzers perturb *comments* / *whitespace* over the fixed corpus, the audit checks
the corpus. None vary **structure**, so a bug needing a conjunction of features
that nobody wrote by hand — the axis the 2026-07-18 corpus scan proved productive
— has no case anywhere. `gen-random.py` is that missing axis: it builds
random-but-legal Gren modules (structure **and** comments) with bounded depth and
checks four oracles per module. Full design in `GENERATOR.md`.

```bash
cd gren-format-lib/tests
./gen-random.py -n 2000 -j 12               # sweep
./gen-random.py --seed 12345                # replay one seed, verbose (+ shrunk)
./gen-random.py -n 500 --max-depth 6        # deeper nesting
./gen-random.py --no-comments               # structure only
./gen-random.py --promote 12345 --name Foo  # a fixed find → a fixture
```

The oracles: **`--pre-ast`** (parses at all — a failure is a *generator* bug, not
a formatter find; it lands in `gen-out/<run>/quarantine/` and is reported
separately, and this bucket must stay ~0); **`--show`** (buys no-crash +
AST-equiv + idempotent + reparses in one call); **comment preservation** (the
multiset of `(type, normalizedText)` from `--pre-context` on the input vs. the
formatted output — positions discarded, so a *moved* comment passes and only a
drop / duplication / invention / kind-change trips it; AST-compare is blind to a
dropped comment and idempotency only catches a *shift*); and **author-order
invariance** (`sort-order`) — the same module re-emitted with its import runs and
`exposing` lists in reversed order, each comment still on the same owner, must
format to the same bytes.

That last one is the only gate that sees a comment attached to the **wrong**
name: the multiset oracle discards positions on purpose, and a wrong-but-stable
attachment is still an idempotent fixed point, so both pass it. Emitting the same
module in two author orders is something only a generator can do. Two positions
are deliberately pinned, since a comment there anchors to the position rather
than to a name — the first slot of each import run (which owns the run's blank
line and its section-header comment) and index 0 of an exposing list (a comment
leading the first item is parsed as a header comment after `exposing`, so it does
not travel, while the same comment at index ≥ 1 does). Ties bail out, because a
stable sort makes author order observable there by design. See `GENERATOR.md`.

Layout decisions are baked into the node tree, so emission is a pure function of
the tree: `--seed` replays exactly, and the shrinker (tree-surgery + deterministic
re-emit) minimizes every failure to `input.min.gren`. Artifacts land in gitignored
`gen-out/run-NNNNNN/` — failures-only, bucketed (`crash` / `ast-mismatch` /
`non-idempotent` / `comment-loss` / `sort-order`), each with a self-contained
`report.txt` carrying the repro command and the pre-computed diff (for
`sort-order`, both author orders and both outputs). `--promote` copies the
minimized repro into `testfiles/<SuiteDir>/` (passed via `--dir`) and prints
the `assertPrettyIn` line.

**Rebuild the `gren-format` app first** — it shells out to `../../gren-format/app`.
When adding a construct to the grammar, verify the quarantine rate stays ~0 after
the addition (0 quarantine + 0 emitter exceptions = the generator is honest, and
only then are its crash/non-idempotent finds trustworthy). Note current-Gren
**constructor patterns take at most one argument** (`Ctor a b` does not parse;
multi-field variants carry a record) — a fact the generator encodes.

Two flags exist for unattended use: `--max-shrinks N` caps how many failures a
run minimizes (one bug can hit hundreds of seeds, and shrinking each one can eat
the whole run — the skipped count is printed and stored, never silent), and
`--seeds 1,2,3 --json` re-checks an explicit seed list with no shrinking and no
artifacts, one JSON verdict per line, exiting non-zero if any still fails.

### Long sweeps across sessions (`fuzzrun.py`)

`gen-random.py` sweeps a seed range and exits. `fuzzrun.py` drives it over days
without Claude Code in the loop: you give it a time budget, it splits that into
~10-minute chunks under `nice`, advances a persistent seed cursor per settings
profile, and records every failure with its repro.

```bash
cd gren-format-lib/tests
./fuzzrun.py run --for 2h      # sweep for two hours, then stop
./fuzzrun.py status            # cursors, coverage, failure counts
./fuzzrun.py failures -v       # what was found, with the report head
./fuzzrun.py resweep           # re-test open failures against this build
```

Config is `fuzzrun.toml` (tracked); state is `fuzzrun.db` (sqlite) and
`fuzzrun-out/` (both gitignored). Ctrl-C stops cleanly.

**Lanes.** Each `[lanes.NAME]` profile — comment density, nesting depth — has
its own cursor and a weight, and a session round-robins chunks across them by
weight so no profile starves. A lane's coverage is the contiguous prefix
`[base_seed, cursor)`: the cursor advances only when a chunk *completes*, so an
interrupted or timed-out chunk is re-swept rather than leaving a hole. Chunk size
is adaptive (measured seeds/sec, capped at 3× the lane's previous chunk), and the
final chunk of a session is sized to the time left — a chunk is never killed to
meet the deadline.

**Generations.** The grammar decides what a seed *means*, so `fuzzrun` hashes
`gen-random.py` and, when it changes, starts a new generation: cursors reset to
base, old results stay queryable under the old hash, and open failures become
`stale-grammar` — their seeds no longer generate the modules that failed, so
re-testing them proves nothing. **Promote any find you still care about to a
fixture before changing the grammar.** It asks before doing this (`--yes` to
skip the prompt, which a cron/`at` invocation needs). The same applies per-lane
when a lane's coverage-affecting parameters change.

**Failures dedupe** by `(bucket, minimized source)`, so one bug hit 400 times is
one entry with 400 hits. Past the per-chunk shrink cap, failures are recorded
unshrunk and dedupe by full source — that under-merges rather than hides, and
the unshrunk count is reported. `resweep` re-runs every recorded seed of each
open failure and closes the ones that now pass.

`run` refuses to start if the built app is older than the formatter sources —
a two-hour sweep of a stale binary tests the wrong code. Override with
`--allow-stale-app`.

## Inspecting formatter internals

Both the standalone CLI and the legacy `gren format` subcommand accept debug flags:

```bash
node ../gren-format/app --show       MyFile.gren   # formatted output to stdout
node ../gren-format/app --show-first MyFile.gren   # shows first formatting, to help debug non-idempotent cases
node ../gren-format/app --pre-ast    MyFile.gren   # parsed AST + context as JSON
node ../gren-format/app --pre-context MyFile.gren   # just the parse Context (comments) as JSON
node ../gren-format/app --lpt        MyFile.gren   # Logical Printing Tree as JSON
node ../gren-format/app --box        MyFile.gren   # the Box tree each decl renders to, as a JSON array
```

`--lpt` is the most useful debug flag for comment-placement and layout bugs.

## Formatter architecture

Pipeline: `Src.Module + Ctx.Context → LPT → Box → String`

```
Formatter                              entry point: prettyPrint
    Formatter.Logical                  logical-stage entry (module Formatter.Logical, file Logical.gren): runs lptFromAst then the comment/sort/blank-line passes
        Formatter.Logical.MakeLogical    AST → LogicalPrintingTree (lptFromAst — one OriginalRows per declaration)
            Formatter.Logical.InsertExpressions   expressions (one insert* per form)
            Formatter.Logical.InsertPatterns      patterns
            Formatter.Logical.InsertTypes         types
            Formatter.Logical.LPTHelpers          construction helpers (mkText*, plainAcross, …)
            Formatter.Logical.BinopPrecedence     operator fixity table
        Formatter.Logical.Comments            re-attaches comments from parse context
        Formatter.Logical.SortSymbols         sorts exposing lists + import groups
        Formatter.Logical.VerticalSpace       inserts blank lines between top-level items
    Formatter.Render                   render-stage entry (module Formatter.Render, file Render.gren): maps each RootBox child through the Box renderer, joins with newlines
        Formatter.Render.MakeRenderBox LPT → Box — recursive core: dispatch (one builder per LPBox constructor) + per-construct renderers
            Formatter.Render.BinopLayout   pure binop-chain layout assembly
            Formatter.Render.CommentBox    comment-node rendering (line / block / doc)
            Formatter.Render.FlowAssembly  FlowItem / SoftGlueAlignment types + pure flow-layout helpers
            Formatter.Render.NodeClassify  boolean predicates / structural queries over LPT nodes
            Formatter.Render.BoxOps        low-level Box / Line manipulation helpers
        Formatter.Render.Box           elm-format's Box IR (Line/Box, Tab tab-stops, prefix)
        Formatter.Render.FlowPolicy    shared inline/break decision layer
```

The Box renderer is the **sole backend** — the earlier `Formatter.Render.Doc`
renderer and the self-verifying Box/Doc guard were deleted at the full cutover.

`Render/MakeRenderBox.gren` was the whole Box renderer; its knot-free helpers
have been split into five sibling modules — `BinopLayout`, `CommentBox`,
`FlowAssembly`, `NodeClassify`, `BoxOps` — leaving `MakeRenderBox` as the
mutually-recursive dispatch (`makePBox`) plus the per-construct renderers. Gren
forbids circular imports, so only functions that never transitively reach the
`makePBox`/`buildFlowBox` recursion could move out. Import DAG:
`MakeRenderBox` → all five; `BinopLayout`/`CommentBox`/`FlowAssembly` →
`BoxOps`, `NodeClassify` (and `FlowAssembly` → `FlowPolicy`); `BoxOps`,
`NodeClassify` and `FlowPolicy` import no other Render module.

Layout is **author-driven, not fit-driven**: there is no page width and no
layout search. Each box already knows whether it renders inline or vertical —
decided from the author's original source rows (`forceVertical`). Indent step:
**4** spaces (`grenIndent`, in `Render/MakeRenderBox.gren`).

**Key invariant:** every top-level declaration becomes exactly one `OriginalRows`
node directly under `RootBox`. Comments and blank lines are inserted as sibling
`OriginalRows` nodes by `Comments` and `VerticalSpace` after the tree is built.

## Authoritative documentation

- `docs/formatterRules.md` — what every formatting rule does, with worked
  examples for every construct. Read first when reasoning about formatter
  behavior. (`README.md` has a shorter version plus one worked example.)
- `docs/howItWorks.md` — a conceptual, step-by-step tour of the pipeline
  (parse → Logical Printing Tree → render plan → text).
- `docs/elmFormatComparison.md` — every place `gren format` deliberately
  diverges from `elm-format`, and why.
- `DEVELOPER.md` — orientation guide for extending the formatter
  with new syntax: the full checklist, position rules, comment-attachment
  hazards, and the "things to worry about" section.
- `docs/commentHandling.md` — reader-facing: the six rules (C1–C6) that decide
  where every comment lands, with a verified before/after example for each.
  **This is the normative statement of comment *behaviour*.** The
  *implementation* model — placement decided once in `Comments.gren` and stored
  as a `CommentRole`, never re-derived from rows in `Render/*` — lives in the
  source: `CommentRole`'s docstring in
  `Formatter.Logical.LogicalPrintingTree` and `classifyCommentKind` in
  `Comments.gren`, with `tests/check-render-invariant.py` as the enforcement
  gate.
