# Settled formatting decisions

These are closed questions. Each one is a recorded design decision by the Gren
team, and the formatter's output must keep honouring it: do not change the
logic, the fixtures, or the unit tests in a way that breaks one of these shapes.
Every entry names the test fixture that pins it, so a change trips a test rather
than needing a reviewer to notice.

- [SD1. `module … exposing` stays on the `module` line](#sd1-module--exposing-stays-on-the-module-line)
- [SD2. `import … exposing` stays on the `import` line](#sd2-import--exposing-stays-on-the-import-line)
- [SD3. Open and close brackets align vertically](#sd3-open-and-close-brackets-align-vertically)
- [SD4. Redundant parens are never stripped](#sd4-redundant-parens-are-never-stripped)
- [SD4b. Pattern parens are synthesized, not preserved](#sd4b-pattern-parens-are-synthesized-not-preserved)
- [SD5. A lambda after `<|` keeps its head on the operator's row](#sd5-a-lambda-after--keeps-its-head-on-the-operators-row)
  - [R1 — head glue](#r1--head-glue)
  - [R2 — continuation alignment](#r2--continuation-alignment)
  - [R3 — the body that closes the chain takes a row of its own](#r3--the-body-that-closes-the-chain-takes-a-row-of-its-own)
  - [Which bodies are continuations](#which-bodies-are-continuations)
  - [One-row lambdas](#one-row-lambdas)
  - [Comments](#comments)

---

## SD1. `module … exposing` stays on the `module` line

**The decision.** When the module statement renders multi-line, the `exposing`
keyword stays on the same line as `module` and is the last word on that line.
The list of exposed names starts on the next line, indented +4.

**What it looks like.**

```gren
module Foo exposing
    ( Configuration
    , Container
    )
```

**Pinned by.** `tests/testfiles/HeaderComments/ModuleExposingClosePinned.formatted.gren`
(and every other multi-line header fixture in that suite). See also
[Module declaration](formatterRules.md#module-declaration).

## SD2. `import … exposing` stays on the `import` line

**The decision.** Same rule, one line down: when an import renders multi-line,
`exposing` is the last word on the `import` line and the list starts on the next
line at +4. A wrapped `import` therefore looks exactly like a wrapped `module`.

**What it looks like.**

```gren
import Basics exposing
    ( max
    , min
    )
```

**Why.** The two header forms read the same way, so a reader learns one shape
instead of two. This is also where gren-format parts company with elm-format,
which drops `exposing` onto a row of its own at +4 and the list at +8 —
[divergence #4](elmFormatComparison.md#divergence-4).

**Pinned by.** `tests/testfiles/Divergence/D04ImportExposingWrap.formatted.gren`.
See also [Import statements](formatterRules.md#import-statements).

## SD3. Open and close brackets align vertically

**The decision.** A pair of open/close parens, square brackets, or curly braces
that lands on different lines always aligns vertically — same column, different
rows.

**What it looks like.**

```gren
parenExample =
    (x
        + y
    ) <|
        value


recordExample =
    { field = 1
    , other = 2
    } <|
        value


arrayExample =
    [ 1
    , 2
    ] <|
        value
```

**Pinned by.** `tests/testfiles/Divergence/D14BackPipeMultilineSeed.formatted.gren`
and `tests/testfiles/PipelineComments/BackwardPipeMultilineSeed.formatted.gren`.

## SD4. Redundant parens are never stripped

**The decision.** Parens that carry no meaning are kept exactly where you wrote
them — in every **expression** and **type** position, at every nesting depth.
Around a `when`, `if`, or `let`; around a binary operator's operand; around a
call argument; stacked two or three deep. gren-format never removes one.

**Scope: this rule is about expressions and types, not patterns.** It can be,
because the AST records the parens: `Src.Expr_` has a `Parens` constructor and
`Src.Type_` a `TParens`. `Src.Pattern_` **has neither**, so a pattern's parens
never reach the formatter and there is nothing to preserve. Pattern parens are
therefore *synthesized from need*, not echoed — see
[SD4b](#sd4b-pattern-parens-are-synthesized-not-preserved).

**What it looks like.**

```gren
-- you wrote, and gren-format keeps, all of these:
v =
    (if cond then
        one

     else
        two
    )

logBase base number =
    (Gren.Kernel.Math.log number) / (Gren.Kernel.Math.log base)

attrs =
    node "div" ({ foo = 1, bar = 2 }) []
```

**Why.** Stripping a paren means proving it carries no meaning. For an operand
that takes the operator's precedence; in general it takes knowing what each
syntactic position can hold. gren-format doesn't do that analysis, and won't.
Nothing about the output is *wrong* — only more explicit than elm-format's,
which normalizes parens down to the minimum the meaning requires.

A call argument is not an exception, even though a positional slot can never
make parens load-bearing: consistency across every position is the point of the
rule.

Note that the resulting indentation is not a second difference from elm-format
— it follows from the parens. Once the `(` is there the block hangs off it, so
`else` and `in` sit one column right of the `(` and the `)` gets a line of its
own. Take the paren away and the block simply starts the line. You cannot keep
the parens *and* get elm-format's columns; it is one difference, not two.

**Pinned by.** `tests/testfiles/Divergence/D10RedundantParens.formatted.gren`.

---

## SD4b. Pattern parens are synthesized, not preserved

**The decision.** Not really a decision — a consequence. `Src.Pattern_` has no
paren constructor, so the formatter cannot know whether the author wrote
`(Just y)` or `Just y`. It emits parens exactly where the pattern *needs* them
and nowhere else, which means a pattern paren that is not needed **is dropped**:

```gren
-- you wrote:                        gren-format emits:
when x is                            when x is
    ({a} as whole) ->                    { a } as whole ->
    { value = (Just y) } ->              { value = Just y } ->
```

This is invisible to the AST-comparison gate, and correctly so: the two spellings
parse to the same `Pattern_`.

**Where parens are emitted.** `InsertPatterns.argNeedsParens` — an `as`-alias, or
a constructor applied to a payload — for a space-separated argument or a
constructor payload. The `as`-alias *base* uses a strictly stricter test,
`aliasBaseNeedsParens`, because the parser is stricter there than the language
is: bare `as` fails after **any** constructor pattern including a 0-arg one, and
after a bare `Int` literal, so `Nothing as whole` and `0 as n` do not parse.
That stricter rule is a **parser workaround**, and it is why the dangerous case
survives — `(Just y) as whole` keeps its parens (compiler-common#31, see
[knownLimitations](knownLimitations.md#an-unparenthesized-constructor-pattern-cant-be-aliased-with-as)).

**Why this matters beyond cosmetics.** Dropping a paren *deletes tokens*, which
can move a comment's anchor. That is the one rewrite class the fixed-point
argument does not cover; see the paper draft's §5.6.

**Pinned by.** `tests/testfiles/HeaderComments/Ambiguous.formatted.gren` and
`tests/testfiles/PatternsAndLiterals/CtorAppNestedPattern.formatted.gren`.
This is [divergence #10](elmFormatComparison.md#divergence-10), the most common
difference between the two formatters on real code; that entry has the full
side-by-side table of what each formatter strips. See also
[Parentheses](formatterRules.md#parentheses).

## SD5. A lambda after `<|` keeps its head on the operator's row

Three rules — **R1**, **R2** and **R3**. R1 and R2 are independent of each other;
R3 is a rider on R2. They fire on every `<|`, whatever the author wrote: row
placement in the source decides nothing here, and the staircase spelling and the
aligned spelling both come back as the **aligned form**, in which every
continuation of a chain sits at the same column.

```gren
init : Node.Environment -> Init.Task { model : Model, command : Cmd Msg }
init env =
    Init.await FileSystem.initialize <| \fsPermission ->
    Init.await ChildProcess.initialize <| \cpPermission ->
    Init.await HttpClient.initialize <| \httpPermission ->
    Init.await Terminal.initialize <| \terminalConfig ->
    Init.awaitTask Node.getEnvironmentVariables <| \envVars ->
    Init.awaitTask (FileSystem.homeDirectory fsPermission) <| \homeDir ->
        let
            userArgs =
                Array.dropFirst 2 env.args
```

This is a normalization, not a preference the author gets to express per
operator: everywhere else a row choice decides whether one construct is inline or
broken, but here it would decide the indentation of everything below it.

elm-format instead produces a staircase — one further indent per continuation,
putting that `let` at column 52 — which is
[divergence #33](elmFormatComparison.md#divergence-33).

**Pinned by** `tests/testfiles/Divergence/D33BackPipeLambdaAligned` and
`tests/testfiles/BinopsAndPipelines/BackPipeContinuationAlignment`.

### R1 — head glue

> When the right-hand operand of `<|` is a lambda, the lambda's **head** — the
> `\`, its parameters, and the `->` — stays on the `<|`'s row. Only the lambda's
> **body** moves to the next row.

Two boundaries:

- **A multi-line left-hand side turns R1 off.** When what sits left of the `<|`
  renders across rows — a call whose arguments you broke, a multi-row record,
  array or paren — that step keeps the staircase.
- **A multi-line lambda head does not.** Only a comment can break a lambda's
  parameter list ([divergence #32](elmFormatComparison.md#divergence-32)); when
  one does, the head still glues — its first row follows the `<|` and its
  continuation rows line up under the `\`.

```gren
-- multi-line left-hand side: R1 off, the staircase kept
recordSeed =
    { fs = fsPermission
    , cp = cpPermission
    } <|
        \a ->
            done a


-- comment-broken head: R1 on
tally =
    withRow row <| \{ quantity -- always >= 1
                    , unitPriceCents
                    } ->
        done (quantity * unitPriceCents)
```

R1 makes `<|` agree with every other operator that can take a bare lambda
operand: `>>`, `++`, a user-defined operator and `|>` all keep the head on the
operator's row too. The other bare operands that break onto their own line — an
`if`, `when` or `let`, and a multi-line `"""` string — likewise leave the
operator's row occupied rather than stranding it. All are pinned by
`tests/testfiles/BinopsAndPipelines/PipelineBareLambdaOperand`.

### R2 — continuation alignment

> A lambda body that is itself another `… <| \… ->` starts at the **same column
> as the row above it** — the chain's base column.
> Any other body starts at **base + 4**.

Both offsets are measured from the column where the whole `<|` expression starts.
R1 and R2 together turn a chain of *n* continuations into *n* rows at one column,
closed by a body at +4.

### R3 — the body that closes the chain takes a row of its own

> When R2 aligns a continuation to the base column, that continuation's own
> lambda body starts on the **row below** its `->`, at base + 4 — even where the
> author wrote it on the `->` row.

Only the *last* step of a chain can be affected, since every other step's body is
itself a continuation and so occupies rows already. So R3 says one thing: **a row
at the chain's column holds a whole step and nothing else**, and the chain always
closes with a body at +4.

R3 reaches **only** a lambda that R2 aligned — a step *inside* a chain. A `<|`
lambda that is not part of one keeps whatever the author chose, so
`await one <| \a -> done a` still comes back on a single row
([One-row lambdas](#one-row-lambdas)).

The boundary is how many `<|`s there are, not how many rows the author used: a
whole chain written on one row is still a chain, and normalizes to a row per step
plus the closing body.

### Which bodies are continuations

A **run** is a maximal chain of `<|`s, which the formatter flattens into one
node; its leftmost expression is the **seed**, and each `<|` together with the
thing on its right is a **step**. R2 was written for the single-step run
`seed <| lambda`, which reads as "hand this callback to the seed" — that is what
makes a body a continuation. Three shapes needed deciding:

| Case | Decided |
|---|---|
| Case 1 — a multi-step run ending in a lambda | **+4** — only a single-step run counts |
| Case 2 — a continuation whose own body fits on one row | **+0** — align on structure, and [R3](#r3--the-body-that-closes-the-chain-takes-a-row-of-its-own) then moves that body down |
| Case 3 — a *parenthesized* continuation | **+4** — the parens mark a value, not a step |

Each as its own small chain — the formatter's actual output, pinned by
`tests/testfiles/BinopsAndPipelines/BackPipeContinuationAlignment`:

```gren
-- Case 1 — this body is a run of TWO `<|`s, so the lambda is handed to
--          `Task.mapError toReport` rather than to the seed
--          `withRetries 3`. That is not the plain `seed <| lambda` shape a
--          continuation is, so: +4
case1 =
    Init.await one <| \a ->
        withRetries 3 <|
            Task.mapError toReport <| \attempt ->
                done attempt


-- Case 2 — the inner step is the plain `seed <| lambda` shape, so it counts as
--          a continuation and aligns: +0. Its own body then takes the row below
--          at +4, which is R3 — written on the `->` row or not.
case2 =
    Init.await one <| \a ->
    Init.await two <| \b ->
        done a b


-- Case 3 — a parenthesized continuation is not a continuation: +4
case3 =
    Init.await one <| \a ->
        (Init.await two <| \b ->
            done a b
        )
```

Case 1 keeps the promise that a row at the chain's column is always a whole step:
a multi-step run has its own staircase inside it, so it must not sit at that
column. **Inside** such a run R2 applies unchanged, and the chain simply restarts
at the run's own column:

```gren
-- `await two <| \b ->` sits at the column of the row above it (8), not +4 (12)
subCase =
    withRetries 3 <|
        Task.mapError toReport <| \a ->
        await two <| \b ->
            done a b
```

Case 3 needs no rule of its own — it falls out of R2 asking about the body's top
node, which for a
[never-stripped paren](#sd4-redundant-parens-are-never-stripped) is a paren block
rather than a `<|` run. A paren block is not a chain either, so R3 does not reach
inside it: a one-row lambda in there stays on its row.

### One-row lambdas

A lambda whose body the author put on the `->` row stays on one row. Layout is
author-driven, so nothing else decides it: length never pushes a body off the
`->` row, there being no page-width fitter, and a body written on the next row
comes back on the next row. (R1 still puts the head back on the operator's row —
that part is not the author's to choose.)

```gren
-- 93 columns and still one row:
stillOneRow =
    await one <| \x -> done x veryLongArgument anotherLongArgument yetAnotherOne andMoreStill
```

Two things override the author. One is **R3**, for a lambda that is a step inside
a chain — the only place a lambda that could have stayed on one row is broken.
The other is a **body that cannot render on one row**, a `let` or a `when`, which
breaks and takes its +4:

```gren
-- input, all on one row:
letBody =
    await one <| \x -> let y = 1 in y


-- output:
letBody =
    await one <| \x ->
        let
            y =
                1
        in
        y
```

### Comments

R1 changes what a row holds, so it creates two places where a comment has to be
answered for. Both follow house rule **C4 — a comment changes where the rows
fall, not how the code sits against its neighbours.**

**Between the `<|` and the `\`**, what decides is whether the comment fits on the
row R1 wants — not its kind, and not where the author put the lambda. A
single-line `{- … -}` the author wrote on the operator's row **rides**, in front
of the head. Everything else keeps the pre-R1 layout for that step, because there
is no row to give it: a `--` ends its row, a multi-row `{- … -}` occupies rows,
and one the author put on a row of its own keeps that row
([divergence #30](elmFormatComparison.md#divergence-30)).

```gren
ridingComment =
    fn <| {- fs -} \a ->
        done a


lineComment =
    fn <|
        -- fs
        \a ->
            done a
```

**Inside a step**, a comment never moves R2's column. R2's question is answered
from the shape of the tree with the comments taken out, so all a comment can cost
is the head glue for the step it is written in — one row, once.

```gren
-- the comment costs `fn` its head glue and nothing else: the step is still at
-- the chain's column, and so is everything after it
lineCommentStep =
    await one <| \a ->
    fn <|
        -- c
        \b ->
            done b
```

This is why the two predicates behind R1 and R2 differ in exactly one respect:
`lambdaOperandSplit` has to *place* a head, so it declines anything it cannot put
on the row, while `isBackwardContinuation` only asks what the step *is*, and
ignores comments entirely.
