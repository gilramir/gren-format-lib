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
python3 fuzz-idempotency.py --gaps --run 2 -j 12                       # a RUN of two per gap
python3 fuzz-idempotency.py --gaps --mix-pairs -j 12                   # runs of MIXED kinds
python3 fuzz-idempotency.py --gaps --mix-triples -j 12                 # runs of THREE
python3 fuzz-idempotency.py --gaps --mix multi,block -j 12             # one such sequence
```

**Rebuild the `gren-format` app first** (`cd ../../gren-format && ./build.sh`) —
fuzzers invoke `../../gren-format/gren-format.sh` as a subprocess, so they
require an up-to-date binary. Run after any change to comment handling, and
after adding any comment-bearing fixture.

**`--run N` is the second axis, added 2026-08-06.** Until then every gate here
varied *where* a comment goes and none varied *how many*, so a rule that only
misbehaves once a comment's neighbour is another comment had no probe anywhere —
and inside a run the neighbour a role is classified against IS another comment.
The members are marked `¤1 … ¤N`, which also buys a **reordering** check (a run
torn across a separator is a stable fixed point, so nothing else here can see
it), and the kind's label grows to `blockx2` so a finding is still
`<fixture>[<kind>]@<gap>` and `repro.py` still reproduces it. It is opt-in, so a
default run is exactly what it was. First whole-corpus run: **20 findings in
19,081 gaps**, one family fixed the same day (a run gluing the following TOKEN
onto its row — unparseable for a `let` binding, and invisible with one comment),
the rest in an effect module's header. Write-up in
[`docs/commentRunTesting.md`](docs/commentRunTesting.md#the-run-axis-what---run-2-found).

**`--run 3` swept CLEAN (2026-08-06) and the length axis is done.** 57,885 gaps,
17 findings, all 17 `[known: compiler-common#35]` — byte-identical to the n=1 and
n=2 residual. Do not sweep n=4; sweep **composition** instead.

**`--mix A,B` / `--mix-pairs` is the THIRD axis, and it is the one that paid.**
`--run N` splices N copies of ONE kind, so every member's neighbours have the
same shape it does — going 2→3 merely adds a second *identical* neighbour, which
is why n=3 found nothing. But the rules a run can break are written about a
neighbour's **shape**, not its count: `commentRendersOwnLine` separates a
multi-line `{- … -}` from a single-line one and from a `--`, `FlowPolicy`'s
inline arm asks whether the previous comment *glued*, and `spanTrailingOwnLine`
peels a suffix that mixes both. Only a mixed run puts those on either side of
each other. Two consequences are forced rather than chosen: a `--` swallows its
row, so the joiner is per-boundary keyed on the member to its LEFT (`run_text`'s
single joiner is the homogeneous special case), and one `--` anywhere puts the
whole run on `splice_line` and off the all-at-once fast path. A mixed kind is the
same 4-tuple `KINDS` holds, labelled `a+b`, so `repro.py` and
`check-decision-stability.py` take one by path without knowing there is such a
thing.

**`--mix-triples` is the same axis at length three, added 2026-08-08.** A pair
gives every member exactly ONE neighbour, so `--mix-pairs` already sweeps every
*boundary* two kinds can form and a triple adds no new boundary at all. What it
adds is a member with a comment on **both** sides — which is the shape the rules
are written about: `FlowPolicy`'s inline arm asks whether the PREVIOUS comment
glued while the member's own kind decides what follows it, and
`spanTrailingOwnLine` peels a **suffix**, so a middle member is the first one
that can be inside a peel with a member left outside it. `mix_sequences(3)`
enumerates the **24** ordered triples that are not all one kind — `a,b,a` kept
for the same reason `--run 3` is not enough, since its middle member's two
neighbours are a different kind from itself. Four times a `--mix-pairs` sweep, so
run it in the background.

**First whole-corpus sweep (2026-08-08): 475,824 gaps, 21,133 skipped by the
parser, 154 findings — all 154 known upstream (136 #35, 16 #25, 2 #14), so 0
formatter-side.** The composition axis stops paying at three, the way the length
axis stopped at three: `--mix-pairs` already puts every ordered pair of kinds on
either side of a boundary, and every rule a run can break is written about **one**
neighbour, so a member with two of them reaches no arm that a member with one
does not. Do not read that as "runs are finished" — it is length and composition
that are, over the corpus. The findings are concentrated exactly where the
upstream bugs are (every `line`-leading sequence reports the same 13 fixtures, a
`--` in front of a `-` operand being #35's signature), which is also the evidence
that the sweep reaches the formatter rather than skipping.

First whole-corpus sweep: **1,752 findings in 115,770 gaps — 1,718
formatter-side**, in three bugs, all fixed the same day, taking it to **48 / 14**
with every other gate unmoved (n=1, run 2, run 3 all still 17-all-known;
decision-stability PASS 0; both parity matrices byte-identical, which is the
evidence the fixes cost no elm-format parity):

- **1,696 were ONE C1 violation** — a comment run torn across a separator. Rule
  C2 sends a single-line `{- -}` in a list's `,` gap to the item below; a `--`
  and a multi-line `{- … -}` both stay above. Each is right alone, and
  `leadsAcrossItemSeparator` asked the question per COMMENT, so a gap holding one
  of each split in half — and when the mover was written first the output
  **reversed the author's order**: `[ 1 {- a -} -- b` came out `[ 1 -- b` ⏎
  `, {- a -} 2`. The test is now unanimous over the run
  (`Comments.gapRunCrossesTogether`), which is the same all-or-nothing C3 already
  applies to *riding*. Because comments attach one at a time in source order, a
  comment can only see the members written EARLIER, so `repairTornGapRun` re-takes
  the earlier decision — by calling `classifyCommentKind` again over the children
  array now holding the whole run — rather than naming a replacement role, since
  the fallback is `RidesInline` or `TrailsPrevious` depending on a branch above.
  Fixture `BracketComments/CommentRunCrossesSeparator`.

  **The reordering oracle sees only half of this class.** A run torn with the
  mover written SECOND comes out in source order (`{- a⏎two -} {- b -}`), and
  nothing in this repo can see that at all; it is pinned as that fixture's
  `tornWithoutCrossing` and was found by enumerating the grid, not by a gate.

- **2 changed the AST**, which no idempotency check can see because the output is
  a stable fixed point. `FlowPolicy` left `FlowSep` after a comment that opened
  its own line, and `FlowSep` IS the space-join, so the next token came up onto
  the comment's row — shifting a broken call's function name right while its
  arguments kept their column. A call's arguments must be indented past the
  function token, so `\item ->` ⏎ `{- c -} fn` ⏎ `arg` reparsed as
  `(\item -> fn) arg`. It needed a separator of its own,
  `TerminatedByOwnLineComment`: a following TOKEN starts a fresh row while a
  following COMMENT may still merge up — `AlreadyTerminated` and `HardNl` both
  forbid the merge, which is what `PipelineLambdaArgTrailingComment` pins.
  Fixture `PipelineComments/OwnLineCommentBreaksBeforeToken`.

- **The bracket opener's two paths disagreed.** Two LPTs identical apart from the
  container constructor rendered a leading run differently — `glueLeadBoxes`
  (flat path) stacked each comment on its own row, `glueLeadingCommentRun`
  (comment path) merged them onto one. Format¹ emits the shape that reparses as
  the other constructor, so the file never settled. What hid it: the multi-line
  comment is a child of the ITEM, not the list, so the list's own comment
  children are two `RidesInline` ones and `literalCommentsRideFlatLine` answers
  True. Every comment there can share a line and only the BODY cannot join them —
  the body's shape decides whether they may share ITS line, not each other's — so
  they stay together. Fixture `BracketComments/OpenerRunStaysOneRow`.

**THE MIXED-KIND RESIDUAL WENT TO ZERO TOO (2026-08-06).** `--mix-pairs` reads
**43 total, 43 known upstream — 0 formatter-side** (34 #35, 8 #25, 1 #14), with
n=1 and `--run 2` unmoved at 17-all-#35, decision-stability PASS 0, the predicate
audit 0, `fuzz-whitespace` PASS 0, and both parity matrices byte-identical
(2079 / 0 failing / 1358 identical; 68,922 / 0 failing / 20,111 identical /
3,407 UNREVIEWED at the time — **0 as of 2026-08-08**). Every mode of every gate
here is now formatter-clean; what remains is upstream. It took one fix and one
label.

**The five effect-header findings were `detachOwnLineTrailer` asking an
all-or-nothing question.** `peelOwnLineTrailingRun` peeled a declaration's whole
trailing comment run and the caller then asked "does the *leader* render below
the declaration?" — so a run whose leader glues kept every later member glued
too. A `--` written past the two-column slack of an effect module's `where { … }`
block is moved to the header's own tail, after `exposing (..)`, and **a `--`
takes the rest of its line**: the `{- four -}` the author wrote after
`exposing (..)` — on a row the header still covers, so it is attached to the
header — is pushed onto a fresh row underneath it, which the reparse then re-homes
to a column-1 `Standalone`. The same first-family disagreement, reached through a
member that the run's leader hides.

The peel now returns the **suffix** that renders below and leaves the members in
front of it where they are, splitting at the earliest member that either brings
its own rows (`runRendersBelowDeclaration`, the old test) or has a `--` in front
of it (`firstRowOfItsOwn` / `endsItsRow`). The split has to live in the peel
rather than in the caller: the run is collected by descending through each node's
last child, so a kept prefix could not be put back without re-nesting comments the
descent had already lifted out. A new `tailComment` field carries "this level's
tail is a `--`" up to the level above, which is what the first member of an outer
run has to know.

**Its fixture had to be reshaped, and the reason is a standing constraint.** The
shape the fuzzer found reaches the header's tail with a multi-line `{- … -}` in
the `MyCmd ⟨here⟩ }` gap — which is the whitespace knife-edge
`docs/knownLimitations.md` describes, two columns from collapsing the block, so
that `.dirty` file failed `fuzz-whitespace.py --mode stretch` on its first sweep.
A second authoring reaches the same rule with **no multi-line comment at all** —
two handlers written across rows and the `--` past the slack — and is
whitespace-clean. Fixture `HeaderComments/EffectHeaderLineCommentPushesTrailer`,
0 findings of its own in every mode, and it fails against a pre-fix binary.

**The `KitchenSink` one is not a formatter bug: it is
[compiler-common#14](https://github.com/gren-lang/compiler-common/issues/14).**
`Expression.parser` bumps an expression's argument-indent scope to the *line
start* of its first term's row, but only when that term is a `Var`/`VarQual`; a
lambda, `if`, `when` or `let` skips the bump and keeps the enclosing scope's
looser indent. A token whose column falls **between the two** is refused by the
body and then absorbed by the outer scope as an argument of the block term
itself. The spliced run puts `(String.isEmpty endpoint)` at column 27 — past the
`let` binding's scope (21), short of the `|>` row's `lineStart` (29) — so the
tree is `(\{ kind, endpoint } -> not) (isEmpty endpoint) && …`. `gren make`
accepts the file, which settles that the real compiler reads it the other way.
This is the "spurious arguments" half already added as a comment on #14 — **do
not file a new ticket**.

`known_upstream_issue` labels it on two signals: a `call` whose `fn` is a **bare**
`lambda` / `if` / `when` / `let` (a parenthesized one arrives wrapped in a
`parens` node, so an unwrapped one cannot have been written), and that call's
first argument starting on a **different row** than its `fn` ends on.

**What was tried and backed out**: adding `SoftIndentedBlock` beside
`IndentedBlock` on `blockTailKeepsCommentOutside`, so that a lambda body written
on the `->` row and the same body written below it treat a trailing comment
alike. It converges the probe's *first* difference and it cannot be motivated:
in a correctly-parsed lambda the body block is the node's last child, so
`hasNoFollowingSibling` vetoes the arm, and the only tree that reaches it is the
misparsed one. Its whole effect was to turn that probe's non-idempotency into an
AST-mismatch refusal.

On the 8: a top-level declaration's `Located.start` is `{name row, col 1}`, so splicing a
run into a `type ⟨here⟩ alias` / `port ⟨here⟩ name` gap (which pushes the name to
the next row) makes the recorded start a point that is neither keyword nor name;
comments are partitioned by it, one hoisted out as `Standalone` and one kept
inside, and the blank-line count above the torn run then differs between passes.
Already filed and **not fixable here** — the keyword's row is simply not in the
AST. See `../COMPILER_COMMON_BUG_decl_start_row.md`.

`known_upstream_issue` names them, on two agreeing signals: some top-level
`import`/`type alias`/`type`/`port` has a recorded start row whose source line
**does not contain that keyword at all**, and the two formats differ **only in
whitespace-only lines**. Two things about writing that detector are worth
keeping, because each failed silently rather than loudly:

  - **The report's own banner starts with `-`.** `-- FORMATTER NOT IDEMPOTENT
    ---------- <path>` reads as a removed diff line, so the whitespace test said
    no every time and nothing was labelled. It happened to fail *safe*; had the
    banner been blank it would have labelled indiscriminately. The scan starts at
    the first `@@` hunk header for that reason.
  - **A port is not `module["ports"]`** — it hangs off `module["effects"]`. The
    top-level lookup returned an empty list, which looks exactly like "no ports
    here", so seven findings labelled and the one `port` finding did not.

Verified by sweep, not by argument: when the label was written `--mix-pairs` read
48 / 42 known / **6 formatter-side**, the #25 label appeared on exactly those six
fixtures and nowhere else, and n=1 / `--run 2` / `--run 3` were unmoved at
17-all-#35.

**Those 16 are fixed too** (`b953853`), and what they were is worth recording
because the first reading of them was wrong. They looked like an *owner* split —
one member of the run outside the `where { … }` block, one inside — and were
described that way here for a day. They were a **position** bug, and the owner
split was its consequence: `MakeLogical.buildWhereBlock` derived the block's
elided `command` / `=` columns by counting **backwards from the constructor**
(`ctorCol - 3 - String.count label`), which is only correct for canonically
spaced input. Put a comment in that gap and the derived columns land *inside the
comment's own text*, carving a phantom slot between the label and the `=` for
the second member to fall into. `LPTHelpers.mkZeroWidthText`'s anchoring policy
forbids a following-token anchor in so many words **and already named this
site** as one with no honest anchor at all; both labels are now position-less
`SynthesizedText`, so every comment left of the constructor collapses to the one
slot between `where` and `{` that `AmbiguousEffectModule` has always documented
as canonical. `--run 2` went **36 → 20**, i.e. 19 → **3** formatter-side, with
the n=1 sweep and both parity matrices unmoved. Fixture
`HeaderComments/EffectWhereCommentRun`.

**The next one down was `nextSiblingIsBoundary` asked of the child** (`27b73ad`,
`--run 2` **20 → 19**, i.e. 3 → **2** formatter-side). That guard already refuses
descent into a non-last `let` binding / `when` branch / `if` branch, because the
first format renders the comment own-line at the child's deep indent and the
reparse — past the child's rows — re-attaches it at the enclosing level. It asks
the question of the NEXT sibling; asked of `child` itself it has one more answer.
An `IndentedBlock` closes with a **dedent** rather than a delimiter, so a comment
that brings its own rows has nothing after it to stay inside of, and the
enclosing flow is where the reparse puts it. Both passes agreed on the run's
roles and differed only in its OWNER, so `--decisions` could name nothing but the
consequence — and **one comment alone is stable here**, rendering identically
whichever flow owns it, so only `--run 2` could see it at all. Scoped to a
multi-line comment (as `containerTailKeepsCommentOutside` is) *and* to a block
something follows — the `ParenBlock` arm's `hasNoFollowingSibling` with the sign
reversed, since a paren's `)` renders after the comment while a block has no
closer at all. Both scopes are load-bearing: without the second, the two
`when`-last-branch fixtures fail. Fixture
`BracketComments/BlockTailCommentRunEscapes`.

**And the last two went with a restored arm** (`c48bad4`), taking `--run 2` to
**17, all 17 `[known: compiler-common#35]` — the formatter-side residual of BOTH
fuzz-idempotency modes is 0.** `makeRecordUpdateVerticalBox` renders a field with
a hand-written **copy** of `renderRecordFieldBox` whose comment claimed it
"mirrors" the original; it was missing that function's FIRST arm,
`isGluedLambdaField` → `renderGluedLambdaField`. In a record update a comment has
forced open, a lambda field therefore fell through to the generic
`isSingleLine valueBox` arm, which drops the *body* and leaves the `\q ->` head
glued — the exact "neither shape" that `renderGluedLambdaField` was written to
prevent, and not a fixed point. It could not settle it either, because the
reparse builds the *other* container and `isGluedLambdaField` only recognises the
one it did not build.

Two things about how it was found are worth keeping. **The recorded diagnosis had
been wrong for two sessions** — filed as "`renderGluedLambdaField`'s glue-vs-drop
decision", which names the function whose *absence* was the bug and sends the
reader inside it. What settled it was `--lpt` of **both** passes plus the
observation that deleting the record-level comment, leaving the field's subtree
**byte-identical**, changed how the field rendered: *when two inputs with
identical subtrees render differently, the bug is in the parent's dispatch.* And
**it needs two multi-line comments doing two different jobs** — one in the lambda
body (makes it render multi-line, never opens the update) and one trailing the
field (opens the update, leaves the body single-line). All six combinations were
formatted; only that one moves. The site now enumerates which arms are shared
with `renderRecordFieldBox` and which are deliberately extra, since "mirrors" is
what went stale. Fixture `BracketComments/RecordUpdateVerticalLambdaField`.

A finding whose cause is a **known upstream parser bug** is reported with its
issue number (`[known: compiler-common#35]`) and counted in the summary line.
`known_upstream_issue` is where those live — today
[#14](https://github.com/gren-lang/compiler-common/issues/14),
[#25](https://github.com/gren-lang/compiler-common/issues/25) and
[#35](https://github.com/gren-lang/compiler-common/issues/35) — and it labels and
never subtracts, so the count and the exit status are exactly what they were.
Adding one means naming two agreeing signals — see that function's doc.

### Decision-stability gate (`check-decision-stability.py`)

The idempotency fuzzer says *whether* a format is a fixed point. It cannot say
**which decision** was not, and with a residual of hundreds of known
non-idempotent probes that is the whole cost: a byte diff has to be traced back
to a culprit by hand, and two findings with the same cause look no more alike
than two with different ones. This is the instrument that makes them group.

`--decisions` formats a file twice and reports which layout *decisions* differed
between the passes — `forceVertical` on a call, a comment's `CommentRole`,
whether a rendered child came back on one line — as named branches with **no
positions in them** (a row is exactly what moves; a decision keyed on one would
report every finding and explain none).

```bash
cd gren-format-lib/tests
./check-decision-stability.py -j 12            # the corpus as written — the gate proper
./check-decision-stability.py -j 12 --gaps     # a comment in every gap — the instrument
./check-decision-stability.py --gaps --kind line -v testfiles/<Dir>/Foo.formatted.gren
./check-decision-stability.py --gaps --run 2 -j 12       # a RUN of two per gap
./check-decision-stability.py --gaps --mix-pairs -j 12   # runs of MIXED kinds
```

**Rebuild the app first** — it shells out to it. The `--gaps` mode imports
`fuzz-idempotency.py`'s probe definitions by path rather than copying them, so
the two gates cannot drift onto different gaps; it reuses that gate's all-gaps
fast path too, which matters more here because `--decisions` formats twice.

**`--run` / `--mix` / `--mix-pairs` / `--mix-triples` reached this gate on
2026-08-08, and until then `--gaps` had only ever run at n=1.** That was the one
axis this instrument could least afford to be missing: the comment RUN is where
every finding since the sixteenth session has come from, and `fuzz-idempotency.py
--run 2` / `--mix-pairs` can only say *that* the bytes moved. Naming the decision
is this script's entire job. The kinds are built by calling that module's own
`run_kind` / `mixed_kind` / `mix_sequences`, so only the flag names are spelled
twice and the probe text cannot drift; a label is that gate's label, so a finding
hands straight to `repro.py`. The all-gaps fast path is told the run length
(`per_gap`), without which every file would fall to the slow path and cost four
times as much to report the same zero.

**Measured the day it landed, and the numbers are a cross-check rather than news:
`--gaps --run 2` reports 17 probes moved and `--gaps --mix-pairs` 43 — the same
counts `fuzz-idempotency.py` reports in those modes, with the same issue split
(34 #35 + 8 #25 + 1 #14).** Two gates arriving at one number over 100k+ probes is
what "imported by path, not reimplemented" is supposed to buy, and it had never
actually been checked at n>1. Every moved probe is known upstream in both modes,
so the run axis books this gate **no formatter-side debt**.

**Its UNEXPLAINED counter rises with runs (1 and 10) and that is honest, not new
trace debt.** Since moved == known in both modes, every unexplained probe is a
known-upstream one; sampled (`TypeAlias`, `TypeComments`, `PortModuleKeywordAdded`,
`DocCommentWithEmoji`, `RecordFieldAsPattern`, all `block+multi` in a
`type ⟨here⟩ alias` / `port ⟨here⟩ name` gap) they are the #25 family, whose two
formats differ **only in a blank-line count**. A blank-line count is not a layout
decision and must not become one to shrink the counter — the rule
`Formatter.Audit.DecisionTrace` states.

**It paid for itself the same day.** Its first whole-corpus run landed on exactly
the same 347 probes `fuzz-idempotency.py --gaps` reports and named 320 of them,
in about five families. The largest — 238 probes, `Comment.role` +
`Comment.endsItsLine` + `Comment.textCanRide` moving together — turned out to be
one shape: a multi-line `{- … -}` past a declaration's last token, which renders
*below* the declaration and is therefore re-homed to column 1 on reparse.
`Comments.gren` already stated that rule and already implemented it from source
rows; asking it of the finished tree instead (`detachOwnLineTrailer`) took
**fuzz-idempotency 347 → 172**.

**The next family down the histogram (67 probes) was one rule too** — the same
comment *placement* reached with a different role. A trailing comment run in a
pipeline step was going through the step's argument stacking ("once one argument
breaks, every argument after it gets its own line"), which put it on a fresh row
below the declaration; the reparse re-homes that to column 1 exactly as above. A
comment is not an argument, and the identical shape written as a plain call has
always glued it onto the previous row. **172 → 140** (`f7c0c54`), 32 fixed and 0
new, with the `--` count unchanged at 51 — that unchanged number is the evidence
the scoping held.

Pinning it with a fixture surfaced a **comment-LOSS** bug that no gate in this
repo could have caught (`312f0a1`): `makeMultilineLambdaArgBox` reads a paren's
head and body and discards any further child, so a comment past the lambda
body's last token was deleted. A dropped comment is AST-equivalent and its
output is its own fixed point, so `--show` passes and so does every stability
check; `gen-random.py`'s comment-multiset oracle is the gate for that class and
this shape was outside its grammar. **Adding a comment-bearing fixture is itself
a probe** — the two fixtures for these commits added eight findings of a
*pre-existing* class (a multi-line comment glued to a lambda body's last token
changes column between formats), which is why the corpus sweep read 148 rather
than 140.

**The third family (43 probes) was the FIRST rule again, on the one top-level
node its gate excluded** — the module header. An effect module's `where { … }`
block makes the header multi-row, so a multi-line `{- … -}` past its last token
renders below it and the reparse re-homes it to column 1, exactly as above.
`detachOwnLineTrailer` was gated on `isDeclStype`, which excludes `StModule` and
is **right to**: that same predicate decides which nodes a *leading* comment may
glue onto and which count as covering a row, and the header answers both
differently. The trailer question is a third question and now has its own
predicate, `hostsOwnLineTrailer`. **148 → 118**, 30 fixed and 0 new, `--` again
unchanged at 51. A **one-row** header still glues its trailing comment (nothing
renders below it) — elm-format detaches that one too, a stable divergence left
alone and pinned by `HeaderTrailingMultilineCommentGlue` beside the fix's own
`EffectHeaderTrailingMultilineComment`.

**Group the histogram's probes by fixture as well as by decision set.** This
family straddled the top two decision groups — 34 probes in `Comment.role` +
`endsItsLine` + `textCanRide`, 9 in `Comment.role` alone — so neither group
looked like one shape, and it was the *fixture* names that gave it away: 43 of
the 148 sat in files with `Effect` in the name. One shape reaching two roles is
not the exception here; the second family was the same story. `-v` now prints
every probe of each group, grouped by fixture, so the clustering is in the
histogram rather than in a `grep` over the sweep log.

**The fourth family (27 probes) was a blank-line count, not a comment
placement** — the first one not to move a comment at all. Both formats put
`{-| doc -}` and a `--` written on its row on two rows with a gap below them;
they disagreed on the gap *above*, two then one. `computeDetachedBelow` asks
whether a node has a gap under it, and it asked per node: on the first format
the `--` is a *child* of the doc comment, so that one node's rows cover both and
it sees the gap; reparsed, the `--` is its own column-1 node directly below, so
the doc comment's own next sibling is adjacent and the gap is somebody else's.
**Detachment is a property of the comment run**, and now propagates up through
an adjacent following comment that is not itself a group start. **118 → 91**, 27
fixed and 0 new — 26 of them `--` probes, the first move in that count (51 → 25)
since the residual work began. Fixture `FloatingCommentRunBlankLines`, which
adds 0 findings of its own.

It is the first of these fixes to change existing output: **16 fixtures gained
a blank line**, each a floating run of two or more comments between
declarations, which now gets the two blank lines above it that a *single*
floating comment has always had. That inconsistency was the bug; elm-format
gives a run and a single comment the same treatment too (three blanks in its
case — the count is [#1](docs/elmFormatComparison.md#divergence-1), unchanged
here).

**The fifth family (11 probes) was the one container that had not learned that
comments chain.** A comment written on a multi-line `{- … -}`'s closing row was
dropped to a row of its own below a binop chain, where the reparse — seeing a
comment past the declaration's last token — re-homes it to column 1.
`classifyCommentKind`'s binop branch keys on the last real **operand** row, so
the injected comment's own closing row counted as "a later row"; every other
container already keys on the previous comment's LAST row (`a5d948c`, the
bracket branch; `prevLineGlueRow` / `prevBlockGlueRow`, the generic flow), which
is why `MultilineCommentTrailedByComment` pins `0 {- a` ⏎ `b -} {- c -}` gluing.
Adding `chainedRefRow` — the operand row grown through the comment run written
on from it — took **91 → 80**, 11 fixed and 0 new, no corpus fixture changed.
Fixture `BinopChainCommentChain`, which pins the boundary too: a comment on a
genuinely later row still keeps its own row at the operator indent.

**The eighth family (15 probes, `Pipeline*` + `KitchenSink`/`KitchenComments`)
needed a renderer fix BEFORE its attachment fix — in that order, and the order is
the lesson.** A multi-line `{- … -}` past a lambda body
inside a parenthesised pipeline argument renders at the body's indent, and the
reparse attaches it one level out, to the `ParenBlock`, beside any comment
already sitting before the `)`. The reparse's placement is the fixed point
(verified: format² of these probes reformats to itself), so the first format is
what has to move.

The attachment change is small and it works — in `insertCommentIntoSubtree`, a
comment past a paren's LAST child, scoped to one that brings its own rows (a
single-line `{- c -}` and a `--` both ride the content's row and must stay
inside — two fixtures pin that), escapes to the paren level. It fixes **15**
probes. It also **adds 11**, and one of those is a **comment LOSS**:

    (when x is
        Nothing ->
            0 {- ¤
   second row -}
    )                       -- the comment does not appear in the output at all

`makeParenBlockBoxWithParts`'s `WhenFlow` arm rendered `children[0]` alone —
"the `when` renders through its ordinary builder" — so once the comment was
attached as a paren child it vanished. Same class as
`makeMultilineLambdaArgBox`'s drop (`312f0a1`), and caught the same way, by
`fuzz-idempotency.py`'s **marker count**: a dropped comment is AST-equivalent and
its output is its own fixed point, so nothing else in the repo can see it.

That arm now stacks any trailing comment children below the `when` before the
paren wrap places them at `(`+1, and a *non*-comment sibling there is an `Err`
rather than a second silent drop. The renderer fix alone is a **no-op on the
corpus** — nothing produced a paren-child comment until the attachment change
did — which is what made it safe to land first and verify separately. Together:
**66 → 50**, 16 fixed and **0 new**, where the attachment change alone had been
15 fixed and 11 new. Fixture `ParenTailMultilineComment`.

**The seventh family (5 probes) was the FIRST family's rule again, on the one
container the peel would not look inside.** A multi-line `{- … -}` past the
**last** `when` branch's bracketed body rendered at the branch's indent, and the
reparse — seeing a comment past the declaration's last token — re-homed it to
column 1. `detachOwnLineTrailer` peels the trailing run by descending through
each node's last child, and `descendsForTrailingRun` listed every flow wrapper
except `WhenFlow` / `WhenBranch`. They qualify on that predicate's own test: a
`when` has no closing delimiter, so its last branch's body is the last thing it
renders. **71 → 66**, 0 new, no corpus fixture changed. Only the last branch is
reachable — the peel descends through the last child alone — so a comment
trailing an earlier branch keeps its place, which the fixture pins along with the
bare-literal body that still rides its own row
(`WhenLastBranchTrailingMultiline`).

**THE FORMATTER-SIDE RESIDUAL WENT TO ZERO (2026-08-05)** — `fuzz-idempotency.py`
reported **17** findings, all 17 `[known: compiler-common#35]`, and
`check-decision-stability.py` PASSed 0 over 343 fixtures. The gate is red on
purpose and stays red until that parser fix ships and the dependency is bumped;
nothing in *that* 17 is this formatter's to fix.

**It reads 19 later the same day, and the 2 are pre-existing.** Both are the same
gap of the new `ContainerTailMultilineComment` fixture, one per comment kind — a
SECOND comment injected into a record-update field that already carries a
trailing multi-line one:

```gren
v =
    { rec
        | fld = \q -> fn {- ¤
   second row -} q {- multi
                              second row -}
    }
```

Format¹ keeps `\q ->` glued to `| fld = `; format² drops the lambda whole below
`fld =`. That is `renderGluedLambdaField`'s glue-vs-drop decision, reached
through a comment RUN — the class `docs/commentRunTesting.md` exists for, and one
no gate here samples (`--comments` injects one comment per cell, the fuzzers one
per gap).

**Attributed by rebuild, not by argument**: it MOVES identically on this build,
on `16a33db` (the container fix alone) and on `16a33db~1` (neither fix), so
neither change caused it. **Adding a comment-bearing fixture is itself a probe** —
the rise from 17 is two new probes of a pre-existing class, exactly as the two
fixtures behind `f7c0c54` once took the sweep from 140 to 148. Do not delete the
`updateField` case to get the number back: narrowing coverage to recover a green
is the mistake this whole section is about.

Two of the last three probes needed **attachment** changes, not renderer ones,
and both had already had a renderer half written and reverted. The lesson is in
that pairing: a renderer fix that makes two owners *render* the same is not the
same as making the owner not matter — the owner also decides what else it is
grouped with. `ea1c2ab` equalised the bytes of a pipeline step's trailing comment
run and left `glueLeading` reading a different run in each format; `ebfb33e`
moved the run to the owner the reparse derives and closed it.

**The long tail: 3 probes, 3 causes, and only ONE of them landed at first.** Past this
point the histogram stops naming families and starts naming individuals, and the
useful lesson is about knowing which of them to stop working on. **20 → 19.**

*Fixed.* `renderGluedLambdaField` splits a record field's head with
`Array.popLast` on the documented assumption that it is exactly
`[ name, =, lambdaHead ]`. A comment written between the lambda's `->` and its
body is **also a child of the field**, so the pop took the COMMENT for the lambda
head: the comment dropped alone below `fld = \q ->` and the lambda stayed glued —
neither shape, and not a fixed point, since the reparse sees the body on a later
row and flips to the drop form. The split is now at the last NON-comment node.
Fixture `RecordFieldLambdaCommentDrop`, 0 findings of its own. Note the class:
*a fixed-arity assumption about a node's children, in a formatter where comments
are children.* `renderPipelineStepChildrenWith` and `spanOperandLeadingComments`
are the same class, two commits earlier.

*Diagnosed, attempted, BACKED OUT — `ModuleLineFloatingComment[line]@6`.*
`gluedExposingBox` refuses a multi-line header, but only its INLINE branch needs
one line; the vertical branch merely stacks. So a comment in the header forced
the fallback to the generic flow, which glues the list's first item onto the
header's last row — for a ONE-item list that erases the only evidence a reparse
has that the list was vertical (`MakeLogical.exposedStartsBelowHeader`), the
derived `)` collapses onto that row, and the comment pinned above it escapes to
column 1. The one-line diagnosis is right and the one-line fix works.

**It also cost four existing fixtures, and the repair chain is the point.** The
hoist branch (an own-line comment between the header and the `(`) applies the
same single-line test *on purpose* — its own code comment says so — and the two
agreed only by both falling back to the same generic flow. Relaxing one alone
made a header comment alternate between the two layouts for ever
(`SortingCommentZoo`, `ModuleExposingInlineAndHoistedComment`,
`ModuleExposingSortCommentToFront`). Relaxing both fixed those. Then
`headerHasOwnLineComment` turned out to mean "a trailing own-line run", not "an
own-line comment anywhere", because a `--` inside the header puts a later token
on its own row; fixing that fixed the next one. Then a THIRD finding appeared, in
`EffectModuleHeaderInlineComment`: an effect header's `} exposing` tail is
position-less, so a `{- c -}` rendered there reads as own-line on reparse and
moves — the ninth family's territory (`headerTailGlue`, `396be16`), which
`classifyBlock` scopes to single-line comments over the one row `refRow` names.

Three expanding changes to comment classification in one sitting, none of them
gated over the whole corpus, is how a session ships a regression. **The attempt
was reverted whole.** Anyone picking it up should start at `headerTailGlue`'s row
range for the effect-module tail and only then relax `gluedExposingBox` — the
renderer change is the easy half and it is not the half that is wrong.

*Diagnosed, not attempted — `KitchenComments[multi]@2121`.* Both passes agree on
every comment's role; they differ only in `glueLeading`, which asks
`commentTextCanRide` of the WHOLE leading run. On reparse the multi-line comment
above joins that run and drags a ridable `{- c -}` off the `|>`'s row. Splitting
the run per comment converges the probe and the ride-form is the correct fixed
point (verified) — but from the *authored* spelling both comments start out owned
by the previous step, where the renderer cannot reach the next `|>`. Completing
it needs the ridable tail ATTACHED to the next step, which is the `5acae7f`
pattern (renderer first, then attachment) with the attachment half still to do.

**The UNEXPLAINED bucket (14 probes) was 13 probes of ONE shape plus one
mislabelled #35** — and it is the answer to "what do I do when the instrument
names nothing". Not, as expected, a decision missing from
`Formatter.Audit.DecisionTrace`: reading the probes' *bytes* was enough, because
14 diffs that all move the same construct by the same 4 columns are a family
whatever the trace says. **34 → 20**, 14 fixed and 0 new, no corpus fixture's
output changed. The formatter-side residual is now **3**.

A multi-line `{- … -}` trailing a `|>` step came out at the operand flow's +4 on
one format and at the `|>` column on the next. The reason the trace was blind to
it is worth keeping: the comment **changes owner** between the passes — last
child of step N, then first child of step N+1 — with the **same role string**
(`LeadsOwnLine`) both times. There is a traced decision for a comment's role and
none for which node owns it, so nothing flipped. A decision set names a symptom;
absence of one names nothing at all.

`LeadsOwnLine` is correct there and deliberately so, which is what ruled out
fixing the classifier: `prevBlockGlueRow`'s `ParenBlock` arm gives no glue row
after a *single-line* paren, so `|> Array.map (\c -> c.kind) {- … -}` is own-line
while `|> Array.map fn {- … -}` glues — a distinction its own docstring defends
and a `KitchenSink` record-pattern fixture pins.

So the fix is in the renderer, and its shape is "make ownership stop mattering":
a trailing comment the glue peel **stopped at** is peeled out of the operand flow
and stacked at the step's own indent — the `|>` column, which is where the
reparse re-derives it either way. The +4 was the flow's continuation indent,
which exists so a broken call's ARGUMENTS land under the operand. A comment is
not an argument of the call it follows — the same sentence as the second family
(`f7c0c54`), which fixed the *stacking* of this construct; this fixes its
*indent*. Fixture `PipelineStepTrailingMultilineComment`, 0 findings of its own.

**Two fixtures corrected the fix, in the order they were written to.** Peeling
before the glue peel stole comments that used to glue (`KitchenSink`'s
`{-c99-}`); the glue peel has to run first. Then keying the peel on the *role*
broke `PipelineTrailingComment`, whose test name **is** the rule — single-line
`{- -}`s trailing a step keep that line whatever their role says. Role was the
wrong question twice; the right discriminator is "the glue peel could not take
it", i.e. the comment breaks the line wherever it lands. It also surfaced an
adjacent two-comment shape (`{- multi⏎line -} {- c -}`) that was already
non-idempotent and that no gate here reaches — `--comments` injects one comment
per cell and the fuzzers one per gap — so the whole trailing run comes down
together, in source order.

**The `Comment.role` group (4 probes) was TWO bugs sharing one symptom**, and
telling them apart cost nothing only because the repro made each one's own
smallest shape obvious. In both, a comment the author put on one side of a
pipeline operator came back on the other side, and the reparse then moved it
again. **38 → 34**, 4 fixed and 0 new, no corpus fixture's output changed.

The first is a helper used at a base it was not written for. When a `<|` drops to
a row of its own — because the body above it ends in a `--` — `backwardMultiStep`
glues the run written *in front of* the operator with `stepLeadBoxes`, which
appends to the **end** of the line it is handed. That is right when the line is
the previous body (the operator is appended after it, giving `body {- c -} <|`)
and wrong when the line **is** the operator: it emitted `<| {- c -}`, which the
reparse reads as a comment leading the OPERAND and renders somewhere else again.
The code's own comment there already said what it meant to do — *"a comment
written in front of it goes on that row"* — so this is a helper whose contract is
"glue onto the end of this line" being called where the contract needed was "glue
onto the front of it". `stepLeadPrefixBoxes` is that mirror, and `{- c -} <|` is
its own fixed point.

The second is the `BodyBlock` wrapper hiding a comment from a peel, which is the
same shape as `16a9b2e` and worth recognising on sight: **a lambda is the operand
that arrives wrapped**, because `insertBinops` wraps every multi-node step body
in a `BodyBlock`. `operatorPrefixedOperandBox` peels the leading comment run off
its operand with `spanLeadingComments`, which looks only at the nodes it is
handed — so a comment leading `one` / `{ a = 1 }` / `gn arg` was peeled and a
comment leading a lambda was not, and the lambda alone got the comment stranded
on a row of its own. `spanOperandLeadingComments` looks inside a lone `BodyBlock`
and rebuilds it around what is left (`lpnReplaceChildren`), so the operand's own
rendering is untouched and only the comment moves.

**Its other half was stable, and therefore invisible to every gate here.** The
peel is shared by the forward and backward pipeline renderers on purpose ("so the
two cannot drift"), and under `|>` the same wrapper produced the same stranded
comment — but as a fixed point, so no fuzzer, no stability gate and no
`--comments` cell ever objected. It was only findable by asking what the
comment-free twin and the *sibling operand kinds* do, which is the C4 test. The
oscillation under `<|` is what dragged a plain inconsistency into the light;
fixing the shared helper fixed both. Fixtures `BackwardPipeOperatorRowComment`
(which also pins that the two sides of the operator are *not* interchangeable —
a comment written after the `<|` still leads the operand) and
`PipelineOperandLeadingComment` (four operand kinds × both operators). Both add
**0** findings of their own, and both fail against a pre-fix binary.

**The next group down — 11 probes, `commentBreaksFlowRow` + `forceVertical`,
every one a `--` — is not a formatter bug at all.** It is a parser one: a binary
`-` whose right operand starts on a later row **at the operator's own column** is
read as a negation, so `10 -` ⏎ `        3` parses as the call `10 (-3)`, and a
`--` written after that `-` renders inside the negation and comes out as `---`,
swallowing the operator. `argOrOperatorLoop` tests `operator.end.col == pos.col`
*after* running the whitespace parser, so it ignores the row; shifting the
operand one column either way flips the parse and no operand kind is safe (only
`-` is affected — it is the sole operator with a unary form). The real compiler
and elm-format both read subtraction. Nothing is fixable here — a comment between
`-` and its operand does not parse in a *genuine* negation, so that tree can only
arrive through the misparse — and **failing is the decision**: no workaround is
wanted, because any rendering faithful to the misparsed tree would rewrite a
subtraction the real compiler accepts. gren-format's AST check refuses the file
and we wait for
[compiler-common#35](https://github.com/gren-lang/compiler-common/issues/35).
Written up in
[`docs/knownLimitations.md`](docs/knownLimitations.md#a-binary---whose-right-operand-starts-at-the-operators-own-column).

**Both gates now name it rather than leaving it to be re-investigated.**
`fuzz-idempotency.known_upstream_issue` marks a finding `[known:
compiler-common#35]` and counts it in the summary; `check-decision-stability.py`
imports the same function and marks the probe in its group listing. It labels,
never subtracts — the findings still count and the run still fails, because
hiding one is how a gate starts lying about what it covers. The label needs
**two** signals to agree: the parser's own AST holds a `negate` whose operand
starts on a different row than the `-` (impossible in a genuine negation), *and*
the format fails the AST comparison. A probe carrying the misparse that fails for
some other reason stays unlabelled and gets investigated.

**The evidence-based label found 17, where grouping had said 11.** The decision
histogram put the family in `commentBreaksFlowRow + forceVertical`, but the same
bug also reaches `commentBreaksFlowRow` alone and
`… + IfCondition.forceVertical` — a decision set is a symptom, not a cause, so a
family can straddle several. The residual was **80 with 17 attributed = 63
formatter-side** when the label was written, and is **17 with the same 17
attributed = 0 formatter-side** as of 2026-08-05 — the upstream count does not
move, so it is now almost all of what is left. When the fix ships and the
dependency is bumped they stop being reported on their own, with no baseline
entry to retire.

**The first characterisation of it was wrong, and reading the parser is what
corrected it.** Three repros, then a grid over operand *kinds*, said "only a
decimal integer literal on the left" — `1.5 -`, `0x10 -` and `a -` all looked
safe. They were only landing at different columns. Ten minutes in
`Compiler/Parse/Expression.gren` produced the real rule and a one-line fix to
propose. **For a suspected parser bug, go read the parser** — a black-box grid
over the wrong variable reads like a characterisation and is not one.

**The sixth family (8 probes) is fixed, in two commits with two different
causes.** One shape, in `RecordLambdaFieldCommentIndent` (×4),
`RecordFieldLambdaDrop` (×2), `BlockRecordFieldValue` and `RecordFieldBlockValues`:
a multi-line `{- … -}` trailing a lambda body inside a record field. Only the
**comment's continuation row** moves; everything else is byte-identical.

The same construct gets three different offsets between the `{-` column and its
continuation row, depending on the path that glued it:

| shape | before | after `1b3f9cc` |
|---|---|---|
| a detached top-level comment | +3 | +3 |
| `{ fld = q + one {- c` (no lambda) | +3 | +3 |
| `q + one {- c` (plain binop) | **+1** | **+3** |
| `{ fld = \q -> q + one {- c` written **flat** | **+1** | **+3** |
| the same, written **already broken** | **+5** | **+3** (`7cd7784`) |

`blockCommentBodyOffset` is 3 and `addSuffixBox`'s contract is "the suffix's
continuation is indented by the glued line's rendered width", so **+3 is the
principled answer**.

**The +1 half is fixed** (`1b3f9cc`): `softGlueAlignment`'s per-box-TYPE table
called `OpAndRhs` `NestCarrying`, meaning "continuation lines already carry their
own indent relative to the flow base, so glue first-line-only". That is true of a
broken operand and false of one whose tail is a multi-line comment — that comment
is glued with literal-space padding, so the box is **align-carrying** whatever
its type says. `subtreeEndsWithMultilineBlockComment` (LPT shape + the comment's
own text; no rows, no rendered output) now overrides the table.

**The +5 half was a `Box.prefix` measurement bug**, fixed by padding with a
*blanked copy of the prefix line* rather than a count of spaces
(`Box.blankLike`). `prefix` used `lineLength 0 pref` — the prefix's width **if it
began at column 0** — while the line is rendered wherever it lands. In the
failing case the prefix was `Row[Tab, Tab, "q + one", Space]` sitting at column
6: measured at 0 it is 4+4+7+1 = **16**, rendered at 6 it is 2+4+7+1 = **14**,
because a `Tab` snaps to the next multiple of 4 *from where it stands* — and the
record literal's `{ ` is what puts the line at a non-multiple-of-4 column. A copy
keeps every element, `Tab` included, at the same offset within the padding as
within the prefix, so both snap identically at **any** column and no absolute
column has to be known. A Tab-free prefix renders exactly as the old space run
did, which is why the corpus did not move.

**What was NOT the fix, tried and reverted: freezing the prefix inside
`addSuffixBox`.** `freezeTabs` converts Tabs to the spaces they render to
*standing alone*, fixing the width and changing the emitted line at the same
time — 2 fixtures regressed (a `when`-in-parens header, a `KitchenComments` binop
chain) because they rely on those Tabs re-snapping once the box is embedded.
Copying pads without touching the emitted line, which is the difference.

**What is NOT the fix: adding `Binop` to `boxKeepsTrailingCommentOutside`.** It
converges the ownership half, and breaks **7 fixtures** — a trailing comment
belongs *inside* the binop in every one of them (`BinopChainCommentChain`,
`TrailingLineCommentBinopOperand`, `BinopParenEmptyBracketTrailingComment`, the
`"""…""" -- c` backward-pipe pair, …). Tried, reverted, measured.

**The first attempt was the opposite fix and a fixture said so.** Reading the
probe as "the run's tail renders below the declaration, so detach it to column
1" produced a patch that failed `MultilineCommentTrailedByComment` — a fixture
written for this exact shape whose own description says detaching there would
"oscillate col 4 ↔ col 0". The gates cost ten minutes and the fixture named the
answer: **when a shape is unstable in one container, look for the container that
already agrees before designing a rule.** Note also that neither matrix could
have found this family — `--comments` injects exactly one comment per cell, and
this needs two.

Three things shape the output, and each was a wrong first design corrected by
running it:

- **Flips are confined to declarations whose rendered output moved.** Formatting
  a non-canonical file legitimately changes many decisions — the second pass
  reads the first pass's tidied rows, not the author's — so the raw trace diff
  is mostly the formatter *converging*. `Formatter.Render.renderRootChildren`
  exists to make this restriction possible: it gives the per-declaration text
  that `makePrettyResult` joins, so a declaration's two renderings can be
  compared. `convergedFlips` counts what the restriction discards.
- **Flips are split into author-intent decisions and rendered-shape
  measurements** (`*.rendersOneLine`). Once a declaration reflows, every shape
  inside it moves too; nine of the ten names in a finding are the reflow being
  observed. Probes are grouped by the *intent* set, which is what shares a cause.
- **A "silent flip" check was tried and dropped as vacuous.** Over the corpus
  the input already is the output, so the two traces come from identical text
  and nothing can differ — exactly the "if the two formats agree the comparison
  collapses into the idempotency check we already have" trap
  [`docs/commentRunTesting.md`](docs/commentRunTesting.md) warns about.

**Two counters are this gate's own debt**, printed every run: a probe whose
bytes moved with *no* flip at all (`UNEXPLAINED`), and one explained only by a
rendered shape. Both come down by adding a decision to
`Formatter.Audit.DecisionTrace` — whose module doc states the rule that keeps it
honest: **trace an input, never a composite.** A traced value is either a flag
read straight off the LPT or the result of calling the renderer's own exported
predicate with the node's own children. `commentForcesBracketOpen` is
deliberately absent, because reproducing its formula here would be a mirror
predicate, and this repo has paid for those. Guessing at a decision to shrink
the counter is how the gate starts lying.

### Reproducing one probe (`repro.py`)

Both gates above report a finding as `<fixture>[<kind>]@<gap>`, and every
investigation starts by turning that triple back into an input you can look at.
`repro.py` does exactly that — splice, format twice, print both passes and the
diff — and then hands you the tree, which is where a comment's `CommentRole` and
its owning declaration actually live:

```bash
cd gren-format-lib/tests
./repro.py TrickyComments.formatted.gren multi 100        # both passes + diff
./repro.py <fixture> <kind> <gap> --input                 # the spliced source
./repro.py <fixture> <kind> <gap> --lpt1                  # the tree pass 1 rendered from
./repro.py <fixture> <kind> <gap> --lpt2                  # the tree pass 2 rendered from
./repro.py <fixture> <kind> <gap> --decisions             # which decisions differed
```

`<fixture>` may be a path or a bare basename (searched under `testfiles/`), so a
name pasted off a findings list works. It imports `fuzz-idempotency.py`'s
`KINDS` **by path** for the same reason `check-decision-stability.py` does: a
repro that splices differently from the gate that found the finding is not a
repro. Exit 0 = STABLE, 1 = MOVED, 2 = could not run — including a probe whose
source the parser rejects, which is the gates' own `skipped (parser)` bucket and
is named rather than reported as a failure.

It uses `--show-first`, not `--show`: `--show` runs the idempotency comparison
internally and fails on precisely the input under investigation, so it would
refuse to print the output you need.

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

**The axis runs 68,922 cells with 0 failing, 0 UNREVIEWED, and — since
2026-08-08 — exit 0** — 68,456 formatted (466 skipped, their commented source
does not parse), of which **68,383 reached oracle 4** and 20,038 are
byte-identical to elm-format, 48,345 registered divergences every one of which
names a catalogue entry, 24 PENDING-UPSTREAM, and **73 with no Elm twin**
(skipped and counted, not failed — see below; they are what the non-zero exit
used to be, and holding them out of the comparison is why the byte-identical
figure reads 20,038 rather than the 20,111 recorded before, with nothing having
moved). It ran 45,948 with 0
failing until the **multi-line block kind** was added that day — `COMMENT_KINDS`
was `{block, line}` while `fuzz-idempotency.py`'s `KINDS` was
`{block, multi, line}`, the same one-kind-per-gap hole this file records costing
401 regressions, left open on the one axis with an elm-format oracle. It found
**70 non-idempotencies immediately**, in two causes, both since fixed (70 → 54 →
0):

- **16 — fixed** (`containerTailKeepsCommentOutside`): a multi-line `{- … -}`
  past a bracketed container's ITEM descended into that item's lambda body,
  rendered at the body's indent, and the reparse handed it to the container. The
  8th family's paren rule asked of a container it had not been asked of. Only a
  lambda item exposed it — `AcrossOrVertical` was already on
  `boxKeepsTrailingCommentOutside`, so the no-lambda form was always stable, and
  that stable form is what the fix lands on. Fixture
  `BracketComments/ContainerTailMultilineComment`; `RecordFieldLambdaCommentDrop`
  gained the same rule for its four flat-authored fields, which stop being broken
  open by a trailing comment (its own `singleLineBodyStaysGlued` said so for the
  other comment kind).
- **54 — fixed, by DELETING a mirror**: `bracketRendersMultiline` derived a glue
  row from `range.maxRow > range.minRow`, the AUTHOR's rows, and a single-item
  container collapses (#21) — so a comment after `(Int` ⏎ `-> Int)` glued on
  format¹ and took its own row on format². The answer was already computed,
  stored and correct one layer up: `authoredBracketList` picks `AlwaysVertical`
  vs `AllAcrossOrAllVertical` from `itemsSpanRows`, and the row re-derivation
  beside it asked a *different* question — it counted a break **inside** one
  item, which `itemsSpanRows` documents itself as ignoring. `prevBlockGlueRow`'s
  `ParenBlock` arm had the same bug with the same stored answer available
  (`forceVertical`), and that is where ~48 of the 54 were: a
  formatter-synthesized type paren carries no author position, so it renders flat
  however the author broke it. Fixture
  `BracketComments/CollapsedContainerTrailingComment`. **Read a mirror predicate
  as a question about where the answer already lives** — both halves here were
  subtractive, and 354 fixtures did not move.

**Do not narrow the sweep to make it green** — that is exactly the mistake that
hid these. Per-kind counts print on every run; a number moving the wrong way is a
regression signal while the absolute figure is non-zero.

##### The RUN axis (`--comment-runs`), added 2026-08-07

`--comments` injects ONE comment per cell. A comment **run** — two in the same
gap — is a third axis, and until now it had never met an oracle:
`fuzz-idempotency.py --run N` / `--mix` vary runs over the *corpus* and ask only
"is this a fixed point", while this matrix asks elm-format and injected one
comment. The same shape of hole as the one-kind-per-gap sweep recorded above.

```bash
./matrix-syntax.py --comments --comment-runs -j 12        # all nine compositions
./matrix-syntax.py --comments --comment-mix multi,line --construct binop -v
```

**Composition is the axis, not length.** `--run 3` swept dry over the corpus, so
this stops at two members and sweeps all nine compositions (three homogeneous +
six ordered mixed pairs). Member texts and the per-boundary joiner are asserted
equal to `fuzz-idempotency.py`'s `run_kind` / `mixed_kind` at startup
(`_assert_run_text_matches_fuzz`), and the labels are that gate's (`blockx2`,
`block+line`) so a finding can be handed straight to `repro.py`. The marker
oracle goes through `marker_check`, which for a run also requires `¤1 … ¤n` in
source order — **a run torn across a separator and reassembled backwards is a
stable fixed point**, invisible to everything else here.

Run cells gate against **`matrix-comment-run-baseline.json`**, a file of their
own: the single-comment baseline is a reviewed asset of ~25k entries and a run
sweep would report every one of them stale.

`trail` and `lead` **collapse** wherever the gap's own whitespace held no
newline — 92,970 of 103,383 slots, so the sweep is 113,796 cells rather than
206,766. Deduplicated for run kinds only and the dropped count is printed; the
single-comment axis has the same redundancy and keeps it, since dropping half its
keys would report them stale.

**First sweep (2026-08-07): 8,842 failures in 113,796 cells**, three classes.

- **284 `box: multi-line comment cannot space-join` — FIXED the same day, 284 →
  0.** `CommentBox.makeCommentLineBox` glued a same-row comment with the
  Line-valued `B.addSuffix` and `Err`ed when the box was multi-line, on a stated
  assumption: *"A multi-line comment never trails another comment inline, so the
  box is single-line."* True of every input any gate could reach until a run
  could put two comments in one gap — `{- a … -} {- b … -}` is valid Gren the
  formatter **refused outright**. The layout was never in doubt: the single-line
  twin (`MultilineCommentTrailedByComment`) has always kept the author's row, and
  `BoxOps.glueCommentSuffix` — already imported by that very file, and documented
  as "the one operation every same-row trailing-comment site shares; a multi-line
  comment no longer needs to fall back" — was the sibling wanted. The fix is a
  deletion. **Two renderers implementing one rule, only one taught it**: the
  expression path handled `multi+multi` all along, and only the standalone
  top-level path (`makeCommentLineBox`) refused. Fixture
  `BracketComments/TopLevelMultilineCommentRun`; every other gate byte-unmoved,
  including both parity matrices.
- **31 non-idempotent — FIXED the same day, 31 → 0** (`8fe5ee6`), two families:
  27 × `lambdaBody@broken|bareBroken#g7.blockx2.trail` (one bug reached from 15
  constructs — `pairLeadingComments` asked about `items[i + 1]`, which can only
  ever see a run of ONE) and 4 × `ifExpr/ifElse@bareFlat#g10|g11.
  {block+line,block+multi}.trail` (`makeIfConditionBox` picked its arm from
  `forceVertical`, whose source rows hold no comments; `AcrossOrVertical` had
  closed that gap already and said so in a comment). Fixtures
  `BracketComments/LeadingCommentRunBeforeBlock` and
  `BracketComments/IfConditionCommentRun`, each pinning the boundary that must
  not move.
- **8,527 `predicate-lie`, all `commentEndsItsLine` — FIXED 2026-08-08, 8,527 →
  0, and not one of them was a layout bug.** The AUDIT was wrong, at its grain.
  `flowCommentFindings` asked, per comment, "does removing this one close the gap
  between the surrounding ITEMS" — a scoping written when a comment's neighbours
  were items — while the predicate claims what the *gap* does. Those coincide
  only when the member is the sole reason for the break, and **in a run there is
  always another reason**: a `--` in a run does end its line, but deleting it
  leaves the other member breaking the row (over-claim), and a single-line
  `{- b -}` that merely OCCUPIES a row the `--` before it created does close a
  row when deleted (under-claim). The census
  (`tests/_run_predicate_census.py`) showed the claim direction is a **pure
  function of the run composition** — no construct or context dependence — which
  is what a grain mismatch looks like and a layout bug does not; and the
  elm-format oracle, measured against its control, did not single these cells out
  either (63% UNREVIEWED vs a 46% background rate, 0 byte-identical either way).

  The audit now asks per comment RUN, and takes the claim by calling
  `commentBreaksFlowRow` itself over `[run … the item after it]` rather than
  folding `commentEndsItsLine` per member — so both sides ask one question. A run
  of one is the single-comment case unchanged. A **third exclusion** came with it,
  and it is a statement about the measurement rather than about the predicate: a
  gap the two items do not share **even with the run deleted** (`gapWithout > 0`)
  cannot show what the run contributed — the difference of the gaps measures extra
  rows, not "the next item starts a fresh row", which is already true without it.
  Nothing is misled there either, because the caller ORs this predicate into a
  `forceVertical` the already-broken gap sets anyway. That was the last 2 cells:
  a `{- ¤1 -} -- ¤2` in front of a record type that takes the `DropBlock` rule on
  its own account — the documented `pairLeadingComments` exclusion reached through
  a run whose `--` is not *pairable*, so the walk past pairable comments stops
  short of the record.

  **Proved non-vacuous by breaking what it watches**, in both directions: making
  `commentEndsItsLine` answer `False` for a `--` takes the corpus audit 0 → **18**,
  and making it answer `True` for a single-line `{- … -}` takes it 0 → **51**,
  run sites among them. Zero-because-nothing-is-checked reads exactly like
  zero-because-all-agree.

**DECIDED 2026-08-08: the run axis gets NO per-cell parity baseline.** It stays
`--no-parity`, and its elm-format agreement is a *sampled* number rather than a
gate. Do not re-open this by writing `matrix-comment-run-baseline.json`; the
reasoning is below and the decision is the user's.

Measured first, at `83eb22d`, with `tests/_run_parity_sample.py 100 -j 12` (1,137
cells, seeded random — **not** a stride, which aliases against the kind axis):
**12% byte-identical to elm-format, 46% auto-classified, 41% would book
UNREVIEWED** — extrapolating to ~13,800 / ~52,700 / **~47,200 UNREVIEWED** over
the axis. Every composition containing a multi-line `{- … -}` is 0% identical
(elm re-lays out the comment's own body); `line+multi` classifies at 90% while
`multi+line` classifies at 40%, i.e. order matters.

Three reasons not to book it:

  - **elm parity is not a goal for runs.** [Divergence #30](docs/elmFormatComparison.md#divergence-30)
    / rule C7 says gren keeps the rows the author wrote; elm-format re-decides
    them per context. A baseline's job is detecting drift from a target, and this
    axis deliberately has a different target.
  - **A 98k-entry asset with ~47k `UNREVIEWED` reads as reviewed.** The
    single-comment baseline's own 3,407 took several `--interview` sittings to
    clear (it reached 0 on 2026-08-08, and found two formatter bugs on the way);
    this would be an order of magnitude more, and this file's standing warning is
    that a baseline entry is the easiest place for a known bug to go quiet.
  - **Nothing is lost that oracles 1–3 already cover.** Every one of the 113,796
    cells still has to format, preserve its comment exactly once, be idempotent,
    be AST-equivalent and tell no predicate lies — which is what found all
    8,842 findings of the first sweep. Only *parity regression detection on runs*
    is given up, and the single-comment axis (68,922 reviewed cells) still gates
    every comment rule against elm.

Re-sample when a comment-layout rule changes, and record the number rather than
booking it.

**The parity debt the kind arrived with: 22,770 new cells, every one diverging**
— elm-format re-lays-out a multi-line comment's own body (`-}` onto a row of its
own) where gren keeps the delimiters the author wrote. 11,109 auto-classified on
arrival and 11,661 booked UNREVIEWED; **a `#25` rule then took it to 3,407**.

That rule is `marker_did_not_move` — the marker occupies the same index in the
same paren-free code-token stream in both outputs. It is deliberately stronger
than "the roles agree": two formatters can agree on what shares the comment's
line while attaching it between different tokens, and only slot equality leaves
*nothing about placement* to differ, which is what makes it safe to attribute the
remaining difference to the comment's own rows. It also requires
`stripped_matches` — without that it appended `#25` to `INHERITED:` cells whose
real difference was the base divergence, attributing to the comment's rows a
difference not in them. Registering it was a **documentation** decision, not a
keystroke: [#25](docs/elmFormatComparison.md#divergence-25) already said "what
elm-format does to the comment's own rows" and carried only single-line examples
because the axis could not reach a multi-line comment; the entry and the `D25`
fixture gained that case rather than a new number being invented.

**Those 3,407 were read down to ZERO on 2026-08-08**, in one sitting of
`--interview`, and reading them found **two formatter bugs** — which is the
argument for reviewing debt rather than classifying it. Widening a classifier
until the counter reads zero is how a baseline starts freezing bugs as expected
output; both of these would have been swept up by any rule broad enough to close
the number.

- **A multi-line `{- … -}` written after a `<|` dragged the operator off its
  seed's row** (104 cells). `spanOperatorRowComments` peeled a `--` written on
  the operator's row and not a multi-line comment, **and said so in its
  docstring** — "it brings its own newlines, so it keeps the operator-leading
  layout". Its own rows are no reason to move the operator: the comment-free
  twin, the `--` and the ridable single-line `{- -}` all keep `fn <|` on the
  seed's row. It is the C4 violation `f330757` / `67e1b0a` removed for the `--`
  on 2026-08-02, six days before the multi-line kind reached this axis at all.
  Registered as [#26](docs/elmFormatComparison.md#divergence-26), whose entry and
  `D26` fixture gained the kind.
- **A multi-line `{- … -}` ENDING a `<|` body dropped the body below the
  operator** (2 cells, but a corpus-wide shape). `backwardSingleStep` drops a
  body whose box is multi-line, and the box was multi-line only because of the
  comment's rows. The fix measures the body **without** its trailing comment run
  by re-running the assembly over already-rendered `FlowItem`s — not by
  predicting from the nodes, and not by re-rendering subtrees. **This one costs
  elm-format parity and was taken anyway**: elm drops the body in all four
  spellings, so gren's odd-one-out was the single case that agreed with it.
  Fixture `PipelineComments/BackwardPipeBodyKeepsOperatorRow`.

The rest were compounds of entries already on record — #5, #22, #23, #26, #27,
#28, #29 — with **#25 on every single one**, which is the shape of the whole
debt: elm re-lays out a multi-line comment's own body and gren keeps the rows
you wrote (rule C7). No new catalogue number was needed to reach zero.

##### The 73 `no-elm-twin` cells — a LANGUAGE difference, not a `to_elm` bug

For months the axis exited non-zero on **73 `[untranslatable]` cells**, and this
file called them "worth a `to_elm` session of its own". **That framing was
wrong**, and the failure message said so out loud on every run: *"to_elm produced
source elm-format rejects"*. `to_elm` is a two-word regex that touches neither
comments nor columns; it had translated them perfectly. What Elm refuses is the
**program**.

    foo : a
    {- ¤ -} foo =      gren: parses, formats, is a fixed point
        one            elm : "Unable to parse file <STDIN>:5:10"

Elm requires a declaration to start in **column 1**. Gren has no such rule at all
— an indented `foo =` with **no comment anywhere** parses too, and formats back
to column 1. The comment's only role is in the *output*: gren never moves a
comment off the row it was written on, so the name stays right of it. So this is
a syntax-**acceptance** difference, the same class as the effect-module
`where { }` one, and there is nothing for `to_elm` to fix: the cell has no valid
Elm twin, and rewriting it to put the comment on its own row would ask elm about
a **different program** — manufacturing a guaranteed divergence, the same
dishonesty as regenerating a fixture to whatever the tool emits. That option is
recorded as rejected in `has_no_elm_twin`'s docstring rather than left to be
re-invented.

**Implemented 2026-08-08 as option (a): skip oracle 4 on them, count and print
them apart, and let the axis exit 0.** Oracles 1–3 still run on all 73 — they are
ordinary cells for everything gren-side; only the elm comparison is skipped,
because there is no elm answer for it to compare against. The cost of the status
quo was the exit status: red-forever for a cause nobody would ever fix means a
real regression on this axis looks exactly like a clean run.

The predicate is **differential, not a shape match** — the same cell *without*
the injected comment must be accepted by Elm — plus a guard that no Gren keyword
survived translation. Both are what keep a genuine `to_elm` bug loud, and the
guard is not hypothetical: this file's one historical translator bug was a
comment defeating the `when … is` pattern so `when` survived into the "Elm"
source. Verified in all four directions (as shipped → `no-elm-twin`; no base
source → `untranslatable`; a deliberately mangled `to_elm` → `untranslatable`; a
surviving `when` → `untranslatable`), because a classifier that excuses
everything reads exactly like one that excuses the right thing.

**Gren does NOT accept all of them, and the exclusion rule must not say it
does.** The elm-rejected set is **131**; gren rejects **58** of those itself, and
only the remaining **73** reach oracle 4. The discriminator is the annotation's
last token: if it can take a type-application argument (`foo : Int`,
`foo : Int -> Int`) gren swallows the next line's declaration name as one and
dies at the `=`; if it cannot (`foo : a`, `foo : { x : Int }`, `foo : (Int)`) the
declaration parses. So the predicate is "elm rejected it **and** gren accepted
it", which is exactly what asking it from inside oracle 4 buys — parity only runs
on cells that formatted. The other 58 sit in the 466 `skipped`.

Their count and their shape breakdown print on **every** run: a number labelled
only "excluded" is how a coverage hole goes quiet, and the breakdown is what would
show a *new* class arriving under the heading. Today the whole of it is one shape,
`block`-kind `lead` position, in `sigSole` / `sigLastArg` / `sigBrokenLast` /
`letSig`. Note they are held out of the byte-identical figure too — before this
they were silently counted as agreeing with elm-format, which they had never been
compared to.

**Registered as [#31](docs/elmFormatComparison.md#divergence-31)**, the first
catalogue entry with no elm-format output in it — every other entry compares two
renderings and this one cannot, because elm-format never gets as far as
formatting. Fixture `Divergence/D31DeclarationOffColumnOne`, whose own `.dirty`
is rejected by the `elm-format` binary at 5:13 (checked, not assumed) and which
adds **0** findings of its own at n=1, `--run 2`, `--mix-pairs`,
`check-decision-stability` and both `fuzz-whitespace` modes. It pins the
"the comment is not what gren permits" half too: a `deeplyIndentedBody` /
`noCommentAnywhere` pair with no comment anywhere, which gren normalizes back to
column 1.

**Whether an indented declaration parses at all depends on what precedes it, not
on a column rule** — which is the fact to know before writing a test for this.
The declaration above absorbs the name as an application argument whenever it
can, so `a =` ⏎ `    one` followed by an indented `b =` is refused, while the
same `b =` after a body indented *past* it is accepted, and a comment-led
declaration is accepted only where nothing above it can swallow the name. That is
also why the 73 exist at all: the matrix's cells put the declaration directly
under the module header.

Before the multi kind, the axis had been 0-failing since the type axis was
added on 2026-08-03, which itself cost 58 for a day — one family, a
comment-bearing signature whose type carries a multi-line record:

```gren
foo : Int -> {- ¤ -} { a : Int
             , b : String }
```

`typeSegmentsForceVertical` gates its dropping-record trigger on
`not hasComment`, because a comment-bearing type has an arm of its own. That arm
was *predicting* which comment would break which row (`commentSplitsType`)
instead of rendering and looking, so the first format emitted `foo : Int ->` with
the record below it, and the reparse — seeing the record start on a later row —
concluded the author broke at the `->`. **Fixed 2026-08-03** by measuring the
assembled box instead: `FlowAssembly.typeContentSpansRows` asks whether the
type's own content came back on more than one row, which is both the layout
question (elm-format drops such a type below `name :`) and the stability one (a
type still on one row cannot have moved a segment). `commentSplitsType` and
`typeHasCommentBracket` are gone; parity went **20,017 → 20,111** byte-identical.
Write-up in [`docs/commentRunTesting.md`](docs/commentRunTesting.md#the-real-fix-do-not-predict).

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

**State as of 2026-08-03** (superseded by the multi-kind expansion above, which
took the axis to 68,922 cells / 54 failing and re-opened the parity debt for the
new kind — see there), after the type axis landed, its debt was read down and
`commentSplitsType` was deleted: 45,948 cells, **0 failing**, 20,111
byte-identical to elm-format, 25,575 registered divergences, **0 UNREVIEWED and
0 BUG** — every divergence names a catalogue entry. The type axis
arrived with 1,436 UNREVIEWED, all type-context cells; the fix below cleared 139
of them outright and the remaining 1,140 were reviewed in one sitting and
registered:

| cells | reason | what it is |
|---|---|---|
| 380 | #23 | elm-format breaks the surrounding code further; gren emitted exactly its comment-free rendering |
| 332 | #22 | every token the comment crossed is position-less, so both authorings arrive identically |
| 172 | #5 | a comment past a type's `->` snaps back to the row above the arrow |
| 110 | #23+#25 | elm both opens the container and floats the comment onto its own row with a blank line |
| 61 | #1 | elm's blank-line separator splits a comment away from the declaration it documents |
| 32 | #28 | a type application has no container to indent its argument from |
| 26 | #25 | elm re-spaces the comment's own rows; gren keeps the rows the author gave it |
| 19 | #24 | an own-row comment leading a `\|` line: gren at the line's column, elm two past the opener |
| 8 | #10+#23 | elm strips the redundant parens and lifts the type below the `:` |

**A further 1,963 cells turned out to be carrying a stale label**, found while
confirming the zero: `INHERITED:UNREVIEWED` — inherited from a base that was
unreviewed *at the time it was written*. It is not literally `UNREVIEWED`, so
`--update-baseline`'s "keep any prior reason" rule preserved it verbatim for ever
and the UNREVIEWED counter never saw it, while it read to a human as reviewed
debt. Not one of those 123 base cells was still unreviewed (119 registered,
mostly #28; 4 gone from the syntax baseline entirely). `reason_is_stale` now
recomputes them against the base's *current* reason; 1,931 auto-classified and
**32 were genuine debt that had been hiding behind the label**, registered
`INHERITED:#28+#23` (20), `INHERITED:#27+#23` / `INHERITED:#29+#23` (4 each) and
`#24` (4). A reviewed reason never contains the token, so `#22`, `BUG: …`,
`PENDING-UPSTREAM: …` and `INHERITED:#28` are all still preserved. **Treat any
compound reason built from another baseline's entry as needing this
treatment** — the bug is not `UNREVIEWED` specifically, it is that a *derived*
label was cached and never re-derived.

One entry was **extended** rather than added: [#24](docs/elmFormatComparison.md#divergence-24)
covered a record update's `|`; the type contexts reached the extensible record
TYPE's `|` and a union's `|`, and both answer the same way. The `D24` fixture now
carries all three. Nothing else needed a new number — which is the useful
result, since a type context asking a *new* comment question would have meant
the C1–C6 rules did not cover types.

Reading it found a bug before any of it could be registered: **a comment written
anywhere inside a `let` binding's type annotation escaped the annotation
entirely**, hoisted onto its own row above the whole binding (or dropped below
onto the value). The same comment in a top-level signature was placed correctly.
`Src.DefineRecord` carries **one** `name` position and it is the *definition's* —
a row below the annotation — and `folderInsertLetDef` reused that node for the
signature flow too, so the flow read `bnd`(row 7), `:`, `Int`(row 6): out of
source order, with no gap inside it for `Comments` to find. `locDef.start` *is*
the annotation's name when the binding has one, so that is what the signature
now uses. **+139 cells of elm-format parity in the `letSig` contexts (76/587
byte-identical → 215/587), 0 new divergences, the 58 failures unchanged** (they
went to 0 later the same day; see the `typeContentSpansRows` fix above) — every
number attributed against a rebuild of the pre-fix source. Fixture
`Declarations/LetAnnotationComment`.

**That one bug was 260 of the 1,381 cells (19%) the triage had left to read** —
186 of family X, 38 of A6, 16 each of A2/A3, 4 of B7. Registering the
pre-decided families first, as the plan said to, would have written catalogue
reasons over groups the fix reshapes. **Read the long tail before registering
the big families**, not after: the tail is where the unclassified cells are, and
"unclassified" is what a bug looks like before anyone has named it.

One thing the reading settled that is *not* a bug: `bnd : Int -> Int -- c`
dropping the comment below the annotation. elm-format keeps it inside — but only
because it breaks the signature open to make room, which it does at top level
too. That is [#23](docs/elmFormatComparison.md#divergence-23) plus
[#29](docs/elmFormatComparison.md#divergence-29), both already catalogued.
Verified against elm-format on four annotation comment positions where the two
formatters now agree byte-for-byte.

Also pre-existing and worth knowing before the next run: the type axis ships
**19 `[untranslatable]` parity failures in `letSig` alone** — cells whose
Gren-with-comment source translates to Elm that Elm's own parser rejects, so
their parity is not actually being checked. Identical on a pre-fix build. `to_elm`
reports them rather than faking a divergence, which is right, but they are a
hole in the day-old type axis, not a clean green. (**Diagnosed and closed
2026-08-08**: those 19 are part of the 73, and they are not a translation hole at
all — Elm refuses the program, not the translation. See "The 73 `no-elm-twin`
cells" above; they are now skipped and counted rather than reported as failures.)

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
dropped comment and idempotency only catches a *shift*); **author-order
invariance** (`sort-order`) — the same module re-emitted with its import runs and
`exposing` lists in reversed order, each comment still on the same owner, must
format to the same bytes; and **predicate/renderer agreement**
(`predicate-lie`, added 2026-08-09) — `--audit-predicates` on every generated
module, root findings only.

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

**`predicate-lie` is the only oracle here that is not a self-consistency check.**
The other four compare the formatter against itself or its own input, so output
that is wrongly laid out but deterministic, AST-equivalent, idempotent and
comment-preserving passes all of them. `audit-predicates.py` runs the same check
over the corpus and `matrix-syntax.py` over its cells; nothing ran it over RANDOM
structure until now. Proved non-vacuous by adding a `\_ -> True` predicate to
`auditedPredicates`, which takes a 20-seed run from 20/20 clean to 20/20
reporting. It costs ~18% of throughput.

**Two grammar axes arrived with it (v1.34 / v1.35).** An array/record pattern in
any of the five PARAMETER slots may now carry an author-break (`\{ alpha` ⏎
`, beta` ⏎ `} -> …`) — until then `emit_pat` had no multi-row path at all, the
matrix wrote every lambda as `\q ->`, and the whole fixture corpus held one
broken pattern. It found a formatter bug in its first 400 seeds:
`trySoftGlueFlow` glued a `LeadsOwnLine` comment onto a broken pattern's first
row, producing a `let` binding that neither compiler-common nor `gren make` will
parse (fixed; fixture `PatternComments/LetBrokenPatternLeadingComment`). And the
two inner own-line comment slots (`when` branch lead, `let` binding lead) take a
RUN of one or two — the run axis reaches the corpus and the matrix vocabulary,
but had never reached random structure. Both verified live by counting the shapes
they emit, not by assuming: 157 broken patterns and 133 two-member runs over
seeds 1..500. Full write-ups in `GENERATOR.md`.

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

**The hoisted-comment family it found, 5 seeds → 0 (2026-08-07).** `-n 3000`
reported five non-idempotent seeds (407, 1920, 2285, 2331, 2992) and they were
**one bug**, pre-existing — confirmed by rebuilding `47e5e39` and re-running
`--seeds … --json` before assuming a fresh finding was this session's. The gate
had not been run for three sessions, which is why it went unnoticed.

A comment inside an **effect module's** `exposing` list whose owning name sorts
to first place is hoisted to the `exposing ⟨here⟩ (` slot (`docs/sorting.md`).
Format¹ put it on its own row and format² glued it onto the `exposing` row, with
format² the fixed point. **Both spellings are stable on their own** — a plain
module keeps a hoisted comment own-row, an effect module glues it — so what was
wrong was that the hoist emitted the *plain*-module shape for an effect header.

The two trees are byte-identical apart from **one field**: the comment's role,
`LeadsOwnLine` in pass 1 and `RidesInline` in pass 2. That is the whole bug and
it names its own fix. `SortSymbols.hoistBracketLeadingComments` moves a comment
out of the bracket it was classified inside and into the header, and it kept the
old slot's role. On a plain module that role is already what a reparse assigns
(the keyword is anchored at the module name's end, so `Comments.headerTailGlue`
is False); on an effect module `exposing` is a position-less `SynthesizedText` —
emitted for `Src.Manager` and nothing else — so everything past it is the
header's position-less tail, which glues a **single-line** comment and leaves a
multi-line `{- … -}` alone. `hoistedTailRole` re-takes the role for the new slot
under exactly that discriminator; a run keeps working because the renderer
already knows a `--` ends its row. Fixtures
`HeaderComments/EffectExposingSortCommentToFront` and
`…ToFrontLine`; `-n 3000` goes 5 → **0** with all 365 fixtures unmoved.

The earlier attempt in this area — relaxing `gluedExposingBox`'s single-line test
— cost four fixtures and was reverted whole (see the "Diagnosed, attempted,
BACKED OUT" note above). It was aimed at the **renderer**, and the renderer is
not what was wrong: both shapes render correctly, and only one of them is the
role the reparse computes. **When two passes differ in exactly one stored field,
fix the pass that computes it, not the code that consumes it.**

**Its fixture then exposed a second, unrelated bug — which is the standing
warning working exactly as written.** The `--mix-pairs` sweep, run once the fix
was in, read **45 / 43 known → 2 formatter-side**, both of them in the *new*
fixture and both **byte-identical against a pre-fix binary**: pre-existing, and
another instance of "adding a comment-bearing fixture is itself a probe".

`headerTailGlue` is a fallback for the effect header's position-less **tokens**,
and it was applied unconditionally. A comment is not one of those tokens — it
carries an exact position, so `prevBlockGlueRow` / `prevLineGlueRow` already
answer for it (`lastRowInSubtree`, the chaining rule of the fifth family). Firing
anyway **overrode that answer** and glued a comment onto the closing row of a
multi-line `{- … -}` it was written *below*. The same shape under a plain
`module` and under an `import` splits and stays split — checked, and that is what
identified the fallback rather than the chaining rule as the wrong half. Scoped
with `not (isCommentNode p)`; a run written on ONE row is unaffected, because
there the previous comment's last row *is* this comment's row and the ordinary
`row == glueRow` test glues it without the fallback. `--mix-pairs` back to
**43 / 43 known → 0**, no existing fixture moved. Fixture
`HeaderComments/EffectHeaderTailCommentChain`, 0 findings of its own in every
mode.

Note what the discriminating input had to be. The fixture's `.formatted` is a
fixed point on both binaries, and so is the *split* spelling of the shape — the
oscillation only appears with a comment already glued on the `exposing` row, the
multi-line, the single-line **and** a trailing multi-line after it. Reconstruct
the fuzzer's spliced source rather than guessing a smaller shape; two smaller
authorings looked like the bug and discriminated on neither binary.

**The lambda-head family, one `fuzzrun` seed → two bugs (2026-08-09).** Session
32 of `fuzzrun.py` swept 81,471 seeds and reported ONE failure, seed 10035748.
It carried two independent non-idempotencies, and only one of them survived
shrinking — **re-check the unminimized `input.gren` after fixing the minimized
one**, which is what turned a one-bug session into a two-bug one.

- **A lambda whose PATTERN ends in a bracket held a trailing comment on the `->`
  row.** `prevBlockGlueRow`'s `AcrossOrVertical` arm means to ask "does this flow
  end in a closing bracket?" and asked `lastBracketEnd`, which caches the
  **rightmost bracket anywhere in the subtree**. For `\[ 1 ] ->` that is the
  *pattern's* `]`, with the `->` still to render after it — so a `{- c -}` past
  the arrow found a glue row that the output does not have, classified
  `RidesInline`, and glued while a multi-line body dropped below it. The twin
  `\y -> {- c -}` had no bracket to find and had always been a fixed point:
  **two spellings of one construct disagreeing, with no fixture and no intent
  behind the difference.** `flowEndsAtBracketClose` asks the flow's last child
  instead. It has to be structural — the token after the bracket is exactly the
  kind that carries no position (`SynthesizedText "->"`, `"="`), so no positional
  comparison can see it. Fixture
  `PatternComments/LambdaBracketPatternArrowComment`.

  **The pass-2 half is the `IndentedBlock` redirect, not "the comment looks
  own-line now"** — the first write-up said the latter and it is wrong. A body
  starting on a later row reparses as an `IndentedBlock`, and
  `insertAmongChildren`'s arm for that box redirects a comment before one inside
  it as `LeadsOwnLine`. Its `SoftIndentedBlock` neighbour already documents the
  identical trade for a `--`; this is the same shape one comment kind over. That
  is also the answer to "why not just keep the author's row (C7)": **the
  keep-the-row shape is not reachable as a fixed point** without changing that
  redirect, and elm-format does not keep it either. C7 governs a run's rows
  against each other and is untouched — a run written on the arrow's row still
  comes out on one row.

- **A comment in a record's `,` gap before a LAMBDA field.** The same premise
  error as `16a9b2e`, one constructor over: `folderInsertRecordField` builds a
  lambda-valued field as an `IndentedBlock`, and the redirect arm's premise —
  "this body always starts a line of its own" — is a fact about the CONTAINER,
  not the box. Inside a bracket that box is an **ITEM**, and an item renders
  after the `, `. Redirected inside, the comment stacked above `fld =`; the
  reparse read it as own-line and moved it in front of the separator. Skipping
  the redirect for `isBracketContainerBox` makes both authorings match their
  non-lambda twins. **Scope it to the whole predicate, not to a role**: the first
  patch kept `LeadsOwnLine` redirecting, and that spelling oscillated too — an
  own-line comment there renders at the BRACKET's column, which is what the
  non-lambda field has always done. Fixture
  `BracketComments/RecordLambdaFieldSeparatorComment`.

  **This one costs elm-format parity and was taken anyway.** elm-format drops the
  field below the comment (`, {- c -}` ⏎ `b =`) for a multi-line-valued field,
  lambda or not; gren keeps `, {- c -} b =`. So the lambda field was the single
  shape that happened to agree with elm — and it was the one that oscillated,
  while the non-lambda field has diverged there all along. The fix joins an
  existing divergence family rather than opening a new one.

**A third bug, found by widening `matrix-syntax.py`'s lambda vocabulary — and the
widening is the lesson.** Every lambda in that file was `\q ->`, so no gate here
had a lambda whose **pattern** breaks. Adding destructuring-pattern constructs
(`\[ 1 ]`, `\{ a, b }`, `\(Just q)`, `\({ a } as whole)`) turned out to be **not
enough on its own**: all four pass on the pre-fix binary too, because the arrow-gap
bug also needs a body written ON the `->` row that RENDERS multi-line. A one-row
body lets the comment ride; a body on the next row parses as an `IndentedBlock`,
whose arm forces the comment own-line. Only `(\[ 1 ] -> [ 0, 1 ])` **broken inside
the body** reaches the shape. **Verify a new probe against the binary that had the
bug** — four of five constructs were vacuous, and "the gate is green" would have
read identically either way.

That construct immediately found a *different*, pre-existing bug (16 cells).
`makeMultilineLambdaArgBox` glues its `(` onto the head's first line only, so a
comment written BETWEEN the parameters keeps its row at the align level (the `(`
column) — right, and pinned by `KitchenComments`. Its docstring then claimed the
other case handles itself: *"align-carrying content INSIDE the head (a multi-line
pattern literal) already carries its own prefix-padding from the head's inner
fold, which survives the first-line glue untouched."* **False.** That padding is
relative to where the head box STARTS; gluing `(` moves the head's first character
one column right and leaves every continuation row behind, so a multi-line
pattern's items and its `]` land a column short of the `\` they hang off. The glue
now picks by where the break comes from — a direct comment child of the head flow
keeps first-line-only, anything else gets `B.prefix` — the same shape as
`softGlueAlignment`'s align-vs-nest override.

**It was an inconsistency before it was an oscillation.** The two authorings of
that lambda (body on the `->` row, body below) differ only in the body's
container, and each was a stable fixed point on its own — they simply disagreed by
one column. Only a shape that flips the container between passes (a pipeline step)
turned it into a non-idempotency, which is why no stability gate had ever objected.
elm-format renders both authorings **identically**, at items `[`+2 and `]` under
`[`, and that is what gren now produces. Fixture
`PipelineComments/LambdaBrokenPatternHead`, which pins the align-level boundary
too.

**The vocabulary change landed separately, after its debt was read.** It booked
**2,410 `UNREVIEWED`** comment-parity cells, which is why it was held out of the
fix commit — unread debt does not belong beside a fix. Reviewed the same day and
registered to **0 UNREVIEWED**.

The 2,410 fell into 251 review groups but only **four** questions, and every
group's reason was **derived from its own outputs** rather than eyeballed — three
independent mechanical facts per cell: does elm give the `->` its own row, does
elm float the comment's `-}`, and does the marker's index in the paren-free code
token stream differ. Most cells carry a compound, which is what makes 251 groups
out of four questions:

| cells | reason |
|---|---|
| 2,304 | **#32**, new — a broken lambda head keeps its `->` on the row before |
| 1,200 | #25 — elm re-spaces the comment's own rows |
| 480 | #22 — the comment sits beside punctuation the parser discards (`,`, `as`) |
| 10 | #23 — elm opens the construct around the comment further |

**#32 is a preference, decided by the user 2026-08-09: gren keeps the arrow.** It
costs a uniform body indent (gren's body starts after `} -> `, elm's sits at a
fixed offset under the `\`) and buys the comment staying on the row of the field
it annotates, plus two fewer rows on the worked example. Only a comment can reach
the shape at all — layout is author-driven, so nothing else breaks a parameter
list. Fixture `Divergence/D32LambdaHeadKeepsArrow`.

**Two things the reading turned up that a classifier would have buried.**
[#16](docs/elmFormatComparison.md#divergence-16) *already stated* the rule the
first bug above violated — "the comment cannot stay on the `->` row here:
reparsed, it is no longer on the body's row, so it would move down on the next
format and the file would never settle" — so that was a documented rule being
broken, not an open question; #32 now cross-references it, since the two are
about different rows of the same construct. And the triage tool's largest bucket,
1,200 cells labelled **"marker missing from one side"**, is a *classifier
artifact*, not comment loss: elm moving `-}` onto its own row desynchronises the
token streams. The marker oracle reported 0 failing throughout.

**Attributed by rebuild, not by argument**: the record-field bug moves identically
on a stash-rebuild of `2eb2205`, so it is pre-existing and not the first fix's
doing. Both new fixtures add **0** findings of their own in every mode. Every
gate unmoved: 375 fixtures (373 + 2), `fuzz-idempotency` 17 / all known `#35` at
n=1 and `--run 2`, `check-decision-stability` PASS 0, `audit-predicates` 0, both
`fuzz-whitespace` modes PASS 0, the syntax axis 2079/2079 with 1358
byte-identical, and the comment axis 68,456 ok / 0 failing / **0 UNREVIEWED** /
20,038 byte-identical — every parity figure identical to the line above.

**Read that parity zero for what it is.** The comment axis injects one comment
per gap into *generated* cells, and the second bug's shape — a two-field record
whose second field holds a lambda with a multi-line body — is not in that
vocabulary, so the axis never reaches it. The divergence is real and was
measured by hand against the `elm-format` binary (table above); it is simply not
one this gate can see. A baseline that does not cover a shape reports zero for
it exactly as it reports zero for a shape that agrees.

**The always-breaking container family, one `fuzzrun` seed → four container
kinds (2026-08-10).** Lane `dense-comments`, seed 10257116, minimized to

```gren
fn2 node Bravo12 { kind, count } = 0 << 0 + 0 && 0 <| ( ( 0 |> 0 |> 0 |> 0 <| 0 ) == 0 >= 0 ) {- k48 -}
```

Format¹ put `{- k48 -}` on its own row *below* the declaration's last token; the
reparse cannot attribute that row to the declaration, so format² re-homed it to
column 1. The first family's oscillation, reached through a container that had no
way to know its own `)` would get a row.

**One question, asked per container kind, and four of them answered from
something narrower than the truth.** `Comments.prevBlockGlueRow` decides whether
a trailing `{- -}` has a closing-bracket row to glue onto:

- `ParenBlock` → `parenContentAlwaysBreaks` dispatched on the paren's **first
  child box** (`WhenFlow` / `IfCondition` / a `let` head), which is only the
  shape where the breaking construct IS the whole content. It misses a mixed
  `|>`/`<|` chain — which breaks with no block construct anywhere, and is the
  seed's own shape — and an `if` buried under a call, a binop, a nested paren or
  a lambda.
- `AllAcrossOrAllVertical` → `bracketRendersMultiline` reads the stored box,
  which `authoredBracketList` picked from `itemsSpanRows` alone; an
  always-breaking ITEM renders the list vertical without spanning any rows.
- `RecordUpdate` → **there was no arm at all**, so it fell to the `_ -> -1`
  default and `{ r | a = 1` ⏎ `, b = 2 } {- c -}` oscillated with no
  always-breaking content involved. `prevLineGlueRow` has always had its arm.

**The paren fix is the closed AST recursion that already existed.**
`exprAlwaysBreaks` answers exactly this question and was already answering it for
the lambda-body case (`insertLambda`'s `bodyAlwaysBreaksVertical`). `insertParens`
stores `contentAlwaysBreaks = exprAlwaysBreaks expression` on the `ParenBlock` and
the predicate reads the stored fact — one recursion subsuming every buried
position, instead of one enumerated shape per bug.

**For the bracket containers the fix was to pick the vertical BOX up front, not
to teach the classifier a second predicate.** `itemsAlwaysBreak` /
`fieldValuesAlwaysBreak` feed `authoredBracketList`'s `spans` and the record
update's `forceVertical`, so `bracketRendersMultiline` needed no change at all.
Render-neutral, and **verified before it was written rather than after**:
`ElmStructure.groupBox` sends any list with a multi-line child to `verticalGroup`
regardless of `forceMultiline` (single-item lists included, so #21 cannot move),
and a flat-authored record update with a breaking field already rendered
byte-identically to the row-authored one. Only the *stored* box changes, which is
what the comment classifier reads. The syntax matrix confirms it: #21's count is
still exactly 125.

**Probe the sibling containers before calling a family fixed.** The paren fix was
complete, gated and verified; probing `[ if … ] {- c -}`, `{ x = if … } {- c -}`
and `{ r | … } {- c -}` found three more live oscillations of the same shape in
about a minute. A bug of the form "container X's closing-row test is too narrow"
has one instance per container kind and nothing makes them share code.

**It cost 6 elm-format parity cells, and the sibling test is what said to take
them.** The comment axis reported 6 new divergences, every one `recordUpdate2` at
the gap past the `}` — the bracket-literal half booked none. Laying all four
containers side by side settled it in one command:

```gren
-- gren-format (all four now agree)     -- elm-format (diverges on all four)
    ] {- c -}                               ]
        |> fn                                   {- c -} |> fn
```

So **before the fix the record update was the single container that agreed with
elm-format — and it was the one that oscillated**, while the array literal, the
record literal and the paren had diverged there all along and register those
cells as [#13](docs/elmFormatComparison.md#divergence-13). Registering there was
a documentation decision, not a keystroke: no new number, and
[#12](docs/elmFormatComparison.md#divergence-12) (which owns the
closing-bracket case) now names the record update, with the `D12` fixture
carrying it. Elm's placement is not available to us either — at a declaration
tail there is no following operator to lead, and deciding per context is exactly
what [#30](docs/elmFormatComparison.md#divergence-30) says gren does not do.
Same trade, and the same reasoning, as the 2026-08-09 lambda-field separator
comment.

Fixtures `PipelineComments/ParenAlwaysBreaksTrailingComment` and
`BracketComments/ContainerAlwaysBreaksTrailingComment`, each adding **0**
findings of its own in every mode. Every gate unmoved: 380 fixtures,
`fuzz-idempotency` 17 / `--run 2` 17 / `--mix-pairs` 43 with every finding known
upstream, `check-decision-stability` PASS 0, `audit-predicates` 0, both
`fuzz-whitespace` modes PASS 0, `corpus-check` 0 in-scope, the syntax axis
2459/2459 with 1598 byte-identical, the comment axis 83,144 ok / 0 failing / 0
UNREVIEWED, and `gen-random -n 15000` clean.

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
./fuzzrun.py export -o bugs.txt  # bundle failures + their .gren for another host
```

**Several hosts at once** (2026-08-09) — `coordinate` is the same runner with a
second transport, not a second runner: `pick_lane`, `size_chunk`, `gen_cmd`,
`run_child`, `ingest_chunk` and `finish_session` are shared, and only "how does
a chunk get executed" differs. `run` is untouched.

```bash
./fuzzrun.py coordinate --for 12h --yes      # on whichever host is free
./fuzzrun.py worker --master hostA:9999 -j 12  # on each of the others
./fuzzrun.py status --master hostA:9999      # from anywhere
```

Every host runs out of the **same shared directory** (config, db, artifacts,
generator, app), so a worker is stateless — no config, no database, no cursor —
and artifacts never travel: the worker reports a path under the shared store and
the master ingests it in place. The master does no sweeping of its own.

The one invariant that is not free with several workers is the contiguous-prefix
cursor: it advances only to the **low-water mark**, the first seed of the oldest
in-flight chunk, so a chunk finishing ahead of a laggard is banked
(`+N done ahead of cursor` in `status`) but not counted. Three guards land with
the shared database, and all three are proved by making them fire in
`tests/test-fuzzrun-distributed.py`: the lock is **hostname-aware** (the old
bare-pid liveness check asked *this* kernel about *another* host's pid and so
failed **open**, reaping a live master's lock), WAL is dropped for
`journal_mode=TRUNCATE` **and the pragma is read back and asserted**, and
`status`/`failures`/`export` on a non-coordinator host refuse to open the shared
db and point at `--master`. Design and rationale in
[`docs/distributedFuzzing.md`](docs/distributedFuzzing.md); the operating manual
is [`docs/fuzzTesting.md`](docs/fuzzTesting.md).

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

**`export` is how a find leaves the machine that swept.** It bundles each
failure's bucket, message, seeds, `input.min.gren` and `report.txt` into one
pasteable text blob (`--full` adds the unminimized `input.gren` and the raw
outputs). **Send the `.gren`, not the seed** — a seed reproduces only against the
same `gen-random.py` and the same lane parameters, so it is worthless to a reader
on another checkout, while the minimized file needs nothing but the app. The
`check` line is bucket-aware because `--show` exits 0 on a comment-loss find, and
each section header states how many lines follow (a `.gren` payload can legally
contain a line starting with `-----`, so a delimiter scan would truncate it).

## Inspecting formatter internals

Both the standalone CLI and the legacy `gren format` subcommand accept debug flags:

```bash
node ../gren-format/app --show       MyFile.gren   # formatted output to stdout
node ../gren-format/app --show-first MyFile.gren   # shows first formatting, to help debug non-idempotent cases
node ../gren-format/app --pre-ast    MyFile.gren   # parsed AST + context as JSON
node ../gren-format/app --pre-context MyFile.gren   # just the parse Context (comments) as JSON
node ../gren-format/app --lpt        MyFile.gren   # Logical Printing Tree as JSON
node ../gren-format/app --box        MyFile.gren   # the Box tree each decl renders to, as a JSON array
node ../gren-format/app --decisions  MyFile.gren   # format twice; which layout decisions differed, as JSON
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
