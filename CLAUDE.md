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
the rest an effect-header owner split. Write-up in
[`docs/commentRunTesting.md`](docs/commentRunTesting.md#the-run-axis-what---run-2-found).

A finding whose cause is a **known upstream parser bug** is reported with its
issue number (`[known: compiler-common#35]`) and counted in the summary line.
`known_upstream_issue` is where those live; it labels and never subtracts, so
the count and the exit status are exactly what they were. Adding one means
naming two agreeing signals — see that function's doc.

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
```

**Rebuild the app first** — it shells out to it. The `--gaps` mode imports
`fuzz-idempotency.py`'s probe definitions by path rather than copying them, so
the two gates cannot drift onto different gaps; it reuses that gate's all-gaps
fast path too, which matters more here because `--decisions` formats twice.

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

**The axis runs 68,922 cells with 0 failing** (2026-08-05). It ran 45,948 with 0
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

**The remaining 3,407 are real review debt and were left alone.** Sampled, they
are compounds — a comment crossing a record update's `|` (#22) *while*
elm-format also re-flows the code around it, which `only_elm_reflowed`'s
deliberate asymmetry refuses to sweep. They want an `--interview` verdict, not a
wider classifier. Widening one until the counter reads zero is how a baseline
starts freezing bugs as expected output.

**The axis also exits non-zero on 73 `[untranslatable]` cells, and they are a
coverage hole rather than a divergence**: their Gren-with-comment source
translates to Elm that Elm's own parser rejects, so elm-format never sees them
and their parity is *not being checked at all*. Enumerated 2026-08-05: **73, every
one `block`-kind**, concentrated in type contexts (`tyApp/sigSole`,
`tyVar/letSig`, …) — the multi-line kind added none. Pre-existing, unattributed,
and worth a `to_elm` session of its own; the count is the thing to watch, since a
rise means fewer cells are being checked, not more.

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
hole in the day-old type axis, not a clean green.

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
