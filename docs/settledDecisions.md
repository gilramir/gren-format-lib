# Settled formatting decisions

These are closed questions. Each one is a recorded design decision by the Gren
team, and the formatter's output must keep honouring it: do not change the
logic, the fixtures, or the unit tests in a way that breaks one of these shapes.
Every entry names the test fixture that pins it, so a change trips a test rather
than needing a reviewer to notice.

The first three are about *layout* — the shape the formatter must produce. The
fourth is about *content* — a rewrite the formatter must not perform.

- [`module … exposing` stays on the `module` line](#module--exposing-stays-on-the-module-line)
- [`import … exposing` stays on the `import` line](#import--exposing-stays-on-the-import-line)
- [Open and close brackets align vertically](#open-and-close-brackets-align-vertically)
- [Redundant parens are never stripped](#redundant-parens-are-never-stripped)

The last section is a change the Gren project lead has asked for. **R1 and R2
are implemented and pinned** — by `tests/testfiles/Divergence/D33BackPipeLambdaAligned`
and `tests/testfiles/BinopsAndPipelines/BackPipeContinuationAlignment` — and are
as binding as the four entries above. R3 is decided but not built, and is marked
where it appears. The section stays down here until R3 lands and it can move up
whole.

- [A lambda after `<|` keeps its head on the operator's row](#a-lambda-after--keeps-its-head-on-the-operators-row)
  - [1. The shape, and the one it replaced](#1-the-shape-and-the-one-it-replaced)
  - [2. The behavior](#2-the-behavior)
  - [3. Similar patterns](#3-similar-patterns)
  - [4. One-row lambdas are unaffected](#4-one-row-lambdas-are-unaffected)
  - [5. Which bodies are continuations?](#5-which-bodies-are-continuations)
  - [6. Comments, in the two places R1 and R2 meet one](#6-comments-in-the-two-places-r1-and-r2-meet-one)

---

## `module … exposing` stays on the `module` line

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

## `import … exposing` stays on the `import` line

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

## Open and close brackets align vertically

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

## Redundant parens are never stripped

**The decision.** Parens that carry no meaning are kept exactly where you wrote
them — in every position, at every nesting depth, with no exceptions. Around a
`when`, `if`, or `let`; around a binary operator's operand; around a call
argument; stacked two or three deep. gren-format never removes one.

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
This is [divergence #10](elmFormatComparison.md#divergence-10), the most common
difference between the two formatters on real code; that entry has the full
side-by-side table of what each formatter strips. See also
[Parentheses](formatterRules.md#parentheses).

---

# A lambda after `<|` keeps its head on the operator's row

## 1. The shape, and the one it replaced

`compiler/src/Main.gren` opens with a chain of continuations — a call, `<|`, and
a lambda that takes the result and does it again. This is what the formatter
produces — the **aligned form**, so called because every continuation sits at
the same column:

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

Before R1 and R2, gren-format rewrote it into a staircase — each continuation
four columns further right than the one above it, and the `let` at column 52:

```gren
init env =
    Init.await FileSystem.initialize <|
        \fsPermission ->
            Init.await ChildProcess.initialize <|
                \cpPermission ->
                    Init.await HttpClient.initialize <|
                        \httpPermission ->
                            Init.await Terminal.initialize <|
                                \terminalConfig ->
                                    Init.awaitTask Node.getEnvironmentVariables <|
                                        \envVars ->
                                            Init.awaitTask (FileSystem.homeDirectory fsPermission) <|
                                                \homeDir ->
                                                    let
                                                        userArgs =
                                                            Array.dropFirst 2 env.args
```

The staircase is what `elm-format` still produces —
[divergence #33](elmFormatComparison.md#divergence-33).

## 2. The behavior

Three rules. R1 and R2 are independent of each other and are what the formatter
does today; R3 is a rider on R2 and is not built yet.

They fire on every `<|`, whatever the author wrote. Row placement in the source
decides nothing here: the staircase spelling and the aligned spelling both come
back as the aligned form. That is a normalization, not a preference the author
gets to express per operator — everywhere else a row choice decides whether one
construct is inline or broken, but here it would decide the indentation of
everything below it.

### R1 — head glue

> When the right-hand operand of `<|` is a lambda, the lambda's **head** — the
> `\`, its parameters, and the `->` — stays on the `<|`'s row. Only the lambda's
> **body** moves to the next row.

Today the whole lambda moves down and the body moves down again, which is where
the two indents per step come from. R1 removes one of them.

Two boundaries on R1, both settled:

- **A multi-line left-hand side turns R1 off.** When what sits left of the `<|`
  renders across rows — a call whose arguments you broke, a multi-row record,
  array or paren — that step keeps today's staircase. One simple rule, chosen
  over a finer one that would have let a bracketed seed align on the grounds
  that its closing bracket sits at the base column.
- **A multi-line lambda head does not.** Only a comment can break a lambda's
  parameter list ([divergence #32](elmFormatComparison.md#divergence-32)); when
  one does, the head still glues — its first row follows the `<|` and its
  continuation rows line up under the `\`.

```gren
-- multi-line left-hand side: R1 off, today's staircase kept
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

### R2 — continuation alignment

> A lambda body that is itself another `… <| \… ->` starts at the **same column
> as the row above it** — the chain's base column.
> Any other body starts at **base + 4**.

Both offsets are measured from the same place: the column where the whole `<|`
expression starts.

R2 removes the second of the two indents, and only for bodies
that are themselves steps. Together, R1 and R2 turn a chain of *n* continuations
into *n* rows at one column, closed by a body at +4.

### R3 — the body that closes the chain takes a row of its own

**Not built yet** — R1 and R2 are, so everything else on this page is what the
formatter does today, and this is the one rule that is still only a decision.

> When R2 aligns a continuation to the base column, that continuation's own
> lambda body starts on the **row below** its `->`, at base + 4 — even where the
> author wrote it on the `->` row.

Only the *last* step of a chain can be affected by this, because every other
step's body is itself a continuation and so occupies rows already. So R3 says
one thing: **a row at the chain's column holds a whole step and nothing else**,
and the chain always closes with a body at +4.

```gren
-- as the author wrote it, with the last step's body on its `->` row:
init env =
    Init.await FileSystem.initialize <| \fsPermission ->
    Init.await Terminal.initialize <| \terminalConfig -> done terminalConfig


-- the output: the step still aligns (+0), its body moves down (+4)
init env =
    Init.await FileSystem.initialize <| \fsPermission ->
    Init.await Terminal.initialize <| \terminalConfig ->
        done terminalConfig
```

R3 reaches **only** a lambda that R2 aligned — that is, a step *inside* a chain.
A `<|` lambda that is not part of one keeps whatever the author chose, so
`await one <| \a -> done a` still comes back on a single row
([§4](#4-one-row-lambdas-are-unaffected)).

*That last paragraph is the scope of R3, and it is **awaiting confirmation** —
the example R3 was given by is a two-step chain, so it does not by itself
distinguish "a chain step's body breaks" from "every `<|` lambda body breaks".
The narrow reading is written here because it is the one §4 already stated and
nobody objected to; the wide one would also move the ~1785 standalone
`<| \p ->` sites in the corpus rather than the ~350 chain members.*

### Two different things get called a chain

Just to be clear, there are two things which visually look like a chain,
but are quite different.

What a reader sees as a chain of binops with lambdas,
a column of `step <| \p ->` rows, is **not** one syntactic `<|`
sequence. `<|` is right-associative and a lambda swallows everything to its
right, so

```gren
Init.await one <| \a ->
Init.await two <| \b ->
done a b
```

is really `Init.await one <| (\a -> (Init.await two <| (\b -> done a b)))`: two separate
`<|`s, each with exactly one operand, the second living *inside* the first's
lambda body. `--lpt` shows it as two **nested** `Pipeline` nodes — abbreviated here.

```
Pipeline                              <- the FIRST `<|`
  AcrossOrVertical                       `Init.await one`  -- the seed
  PipelineStep                        <- its one and only step
    UnbreakableText "<|"
    BodyBlock                            the lambda operand
      AcrossOrVertical                   `\a ->`  (PrefixGlue "\", `a`, `->`)
      IndentedBlock                      the lambda's BODY
        Pipeline                      <- the SECOND `<|`, inside that body
          AcrossOrVertical               `Init.await two`
          PipelineStep                <- again exactly one step
            UnbreakableText "<|"
            BodyBlock                    the lambda operand
              AcrossOrVertical           `\b ->`
              IndentedBlock              `done a b`
```

The "chain" is a shape R2 **creates** by aligning that nest, and how readers
see it; it is not a shape the parser hands over.

The other thing is a run of consecutive `<|`s with no lambda between them —
`f <| g <| h` — which the formatter flattens into a **single** `Pipeline` node
with two steps:

```
Pipeline                              <- ONE node, not two
  UnbreakableText "f"                    the seed
  PipelineStep                        <- step 1
    UnbreakableText "<|"
    UnbreakableText "g"
  PipelineStep                        <- step 2
    UnbreakableText "<|"
    UnbreakableText "h"
```

A lambda operand is what ends a run, because everything after it is **inside** the
lambda.

So: **the continuation chain is a nest of one-step runs.** [§5](#5-which-bodies-are-continuations)'s Case 1 is exactly
the place where a run stops having one step.

## 3. Similar patterns

**The new behavior makes `<|` agree with everything else.** A bare, unparenthesized lambda is a
legal right operand of *any* infix operator, and gren-format already keeps its
head on the operator's row for nearly all of them. Every block below is today's
output.

`>>`, `++`, and a user-defined operator all glue the lambda head to the row:

```gren
render =
    toRow >> \rows ->
                done rows


banner =
    header ++ \rows ->
                done rows


parseName =
    parser |= \value ->
                done value
```

So does `|>` as soon as the lambda is *parenthesized* and is the operator's
direct operand — and elm-format produces this one identically (see
[Pipelines](formatterRules.md#pipelines)):

```gren
summary =
    counts
        |> Dict.foldl addRow []
        |> (\rows ->
                done rows
           )
```

Of every operator that could take a lambda operand, only two ever did something
else. `<|` dropped the whole lambda to a row of its own, which is the shape R1
changed — it now gives the same answer as the three above:

```gren
-- before R1:                     -- and now:
fetch =                           fetch =
    counts <|                         counts <| \rows ->
        \rows ->                          done rows
            done rows
```

`|>` with a *bare* lambda still strands the operator on a row by itself, which
gren-format produces nowhere else (**this is surely a bug**):

```gren
tally =
    counts
        |>
            \rows ->
                done rows
```

elm-format never emits that. Fed the bare form it *adds* parens and lands back
in the parenthesized shape above — and it returns the same thing if you feed it
gren-format's stranded spelling, so the two inputs converge:

```elm
-- elm-format, from `counts |> \rows ->` and from the stranded spelling alike:
tally =
    counts
        |> (\rows ->
                done rows
           )
```

That route is closed to gren-format, which keeps redundant parens but
[never introduces any](#redundant-parens-are-never-stripped) — so the stranded
operator is ours alone.

## 4. One-row lambdas are unaffected

A lambda whose body is on the `->` row stays on one row under every answer
below — R1 has nothing to move and R2 has no body row to place. Worth stating up
front so it's known.

```gren
-- input, today's output, and the output under R1 + R2 — all three identical:
oneRow =
    await one <| \a -> done a
```

**And it is one row because the author wrote it that way.** Layout is
author-driven, so nothing else decides it: a body written on the next row comes
back on the next row, and length never pushes one off the `->` row, there being
no page-width fitter to overrule the choice. (R1 still puts the head back on the
operator's row in the first of these — that part is not the author's to choose.)

```gren
-- the body written off the `->` row, and kept off it:
notOneRow =
    await one <| \x ->
        done x


-- 93 columns and still one row:
stillOneRow =
    await one <| \x -> done x veryLongArgument anotherLongArgument yetAnotherOne andMoreStill
```

The one thing that overrides the author is a **body that cannot render on one
row** — a `let`, a `when`. Writing one of those on the `->` row does not produce
a one-row lambda; the body breaks, and takes its +4:

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

So the class this section exempts is "the body fits on the `->` row *and* the
author put it there".

The same holds for the `|>` side (`|> Array.map (\n -> n * 2)`).

**The one exception is R3.** A one-row lambda that is a *step inside a chain*
does have its body moved down — that is what R3 is, and it is the only place the
author's row choice is overruled for a lambda that could have stayed on one row.
A `<|` lambda standing on its own is never touched.

## 5. Which bodies are continuations?

One question was left over. A body that is itself a continuation starts at the
chain's column (+0), every other body starts at +4 — so which bodies are
continuations? Three shapes were not obvious. All three are now decided:

| Case | The choices | Decided |
|---|---|---|
| Case 1 — a multi-step run ending in a lambda | it counts as a continuation · only a single-step run counts | **+4** — only a single-step run counts |
| Case 2 — a continuation whose own body fits on one row | align on structure · require the inner continuation to break first | **+0** — align on structure, and [R3](#r3--the-body-that-closes-the-chain-takes-a-row-of-its-own) then moves that body down |
| Case 3 — a *parenthesized* continuation | look through the parens · don't | **+4** — the parens mark a value, not a step |

Here is each as its own small chain. These are the formatter's actual output,
pinned by `tests/testfiles/BinopsAndPipelines/BackPipeContinuationAlignment`:

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
--          a continuation and aligns: +0. Its own body stays on the `->` row
--          until R3 is built, which will put it on the row below at +4.
case2 =
    Init.await one <| \a ->
    Init.await two <| \b -> done a b


-- Case 3 — a parenthesized continuation is not a continuation: +4
case3 =
    Init.await one <| \a ->
        (Init.await two <| \b ->
            done a b
        )
```

Most bodies are obvious: in `Main.gren`'s chain every body but the last is
plainly another `Init.await … <| \p ->` (+0), and the last is a `let` (+4).

**The vocabulary first**, since the first case turns on it. A **run** is a
maximal chain of `<|`s, which the formatter flattens into one node. Its leftmost
expression is the **seed**; each `<|` together with the thing on its right is a
**step**.

```
    Init.await one   <|   \a -> …
    ──────────────   ──   ───────
          │          │    └─ the step's OPERAND — a lambda
          │          └─ the step's OPERATOR
          └─ the SEED           ← one step ⇒ a single-step run
```

That is the shape R2 was written for — `seed <| lambda` reads as "hand this
callback to the seed", which is what makes it a continuation.

---

**Case 1 — the body is a run with something between the seed and the lambda.**

In plain terms: a `<|` sequence in which the item just before the lambda is
*itself not a lambda*. (This is the one place a `<|` run has more than one step —
see [Two different things get called a chain](#two-different-things-get-called-a-chain)
above. It is not about the continuation chain, whose non-final items are of
course all calls rather than lambdas.)

```
    withRetries 3   <|   Task.mapError toReport   <|   \a -> …
    ─────────────   ──   ──────────────────────   ──   ───────
          │         │              │              │    └─ step 2's OPERAND — the lambda
          │         │              │              └─ step 2's OPERATOR
          │         │              └─ step 1's OPERAND — THIS is the whole question
          │         └─ step 1's OPERATOR
          └─ the SEED        ← two steps ⇒ a multi-step run
```

`Task.mapError toReport` is the whole question. Its presence means the lambda is
not handed straight to the seed, so the run does not read as one continuation
step — but it *does* still end in a lambda, so a structural test would call it
one.

Put that body into the chain. **The row under debate is `withRetries 3 <|` — is
it at column 4 or column 8?** Everything below it moves with it.

```gren
-- Reading A — a run ending in a lambda counts, so the body aligns to
--             the chain's column:
init env =
    Init.await FileSystem.initialize <| \fsPermission ->
    withRetries 3 <|
        Task.mapError toReport <| \attempt ->
            done attempt
```

```gren
-- Reading B — only a SINGLE-step run counts, so this is an ordinary body
--             and takes +4:
init env =
    Init.await FileSystem.initialize <| \fsPermission ->
        withRetries 3 <|
            Task.mapError toReport <| \attempt ->
                done attempt
```

**Decided: B.** Under A, `withRetries 3 <|` sits at the chain's column, which
promises the reader "here is the next step of the chain" — and then the very
next row is different because the multi-step run has its own staircase inside
it. The column stops meaning one-step-per-row. B keeps that promise: a row at
the chain's column is always a whole step.

Multi-step `<|` runs are rare in any case, and the real one in
`compiler/src/Terminal/Run.gren` —
`PackageInstallError <| PackageInstall.PackageInstallGitError <| { … }` — has no
lambda in it at all, so neither example reading touches it.

**A sub-case of this, decided separately.** Case 1 settles whether a multi-step
run *is* a continuation — it is not, so it takes the +4. It does not settle what
happens *inside* one: the run's own lambda still has a body, and that body can
itself be a continuation. **R2 applies there unchanged**, so that body gets the
same +0 and the chain simply restarts at the multi-step run's own column:

```gren
-- the row that was in question is `await two <| \b ->`: it sits at the column
-- of the row above it (8), not +4 from it (12).
subCase =
    withRetries 3 <|
        Task.mapError toReport <| \a ->
        await two <| \b ->
            done a b
```

The alternative was to make a multi-step run suppress R2 inside itself, putting
that row at 12. **Decided against by the Gren lead** on the shape: it starts a
second staircase inside a construct whose whole purpose is to not have one, and
a chain reads worse for it. The uniform rule is also the one the code wants —
the two sites that lay out a `<|` step have to agree, or a chain would render
differently depending on how many steps its innermost run has.

---

**Case 2 — the body is a continuation that fits on one row.**

Structurally it is a single-step run ending in a lambda, so R2 says +0. But its
own body sits on the same row as its `->`, so it takes no row below itself —
there is no chain beneath it to line up with.
**The row under debate is the second one** — column 4 or column 8:

```gren
-- Align on structure (+0):
init env =
    Init.await FileSystem.initialize <| \fsPermission ->
    Init.await Terminal.initialize <| \terminalConfig -> done terminalConfig
```

```gren
-- Require the inner continuation to break first (+4):
init env =
    Init.await FileSystem.initialize <| \fsPermission ->
        Init.await Terminal.initialize <| \terminalConfig -> done terminalConfig
```

**Decided: align on structure** — the first. The step is the same `seed <|
lambda` shape as every other row of the chain, so it belongs at the chain's
column; singling out the last step of a six-step chain as the only one that
steps right would be the odd shape.

**But the body will not stay on that row.** The first spelling above is today's
output, because R3 is not built;
[R3](#r3--the-body-that-closes-the-chain-takes-a-row-of-its-own) moves the body
down, so once it lands both spellings come back as

```gren
init env =
    Init.await FileSystem.initialize <| \fsPermission ->
    Init.await Terminal.initialize <| \terminalConfig ->
        done terminalConfig
```

which keeps the chain's rows uniform — every one of them a whole step and
nothing else — and gives the chain a visible close at +4.

That has a pleasant consequence for the implementation: since a continuation's
body always ends up on its own row, R2's membership test never has to ask
whether the inner body was written on the `->` row. Case 2 stops being a
distinction the predicate can see.

---

**Case 3 — the body is a *parenthesized* continuation.**

Because [redundant parens are never stripped](#redundant-parens-are-never-stripped),
`(await two <| \b -> …)` keeps its parens, and the body's top node is then a
paren block, not a `<|` run:

```gren
-- today:
chain =
    await one <|
        \a ->
            (await two <|
                \b ->
                    done a b
            )
```

**Decided: not a continuation — +4.** This needs no new rule; it falls out of R2
asking about the body's top node. It is also the right answer on its own terms:
the author wrote parens around it, which is a deliberate mark that the inner
expression is a *value* being produced, not the next step of the chain.

Note that the paren block is not a chain either, so R3 does not reach inside it:
a one-row lambda in there stays on its row.

## 6. Comments, in the two places R1 and R2 meet one

Neither of these was in the original list of questions. Both had to be answered
to build R1 at all, because R1 *creates* them: it changes what a row holds, so a
comment that used to have somewhere to sit may no longer, and a rule that
declines to glue over a comment produces two different layouts for the same
code. The house rule they are both decided by is **C4 — a comment changes where
the rows fall, not how the code sits against its neighbours.**

### 6a. A comment between the `<|` and the `\`

R1 wants one row for `seed <| \p ->`. A comment written in that gap either fits
on that row or needs one of its own, and that — not its kind, and not where the
author put the lambda — is what decides.

**A single-line `{- … -}` the author wrote on the operator's row rides**, in
front of the head. Staying on one row is the whole point of R1, and it is
already what the same comment does when the lambda's body is on the `->` row.

```gren
-- both of these spellings, and the comment-free twin, agree:
ridingComment =
    fn <| {- fs -} \a ->
        done a
```

**Everything else keeps the pre-R1 layout for that step**, because there is no
row to give it: a `--` ends its row, a multi-row `{- … -}` occupies rows, and one
the author put on a row of its own keeps that row
([divergence #30](elmFormatComparison.md#divergence-30)).

```gren
ownRowComment =
    fn <|
        {- fs -}
        \a ->
            done a


lineComment =
    fn <|
        -- fs
        \a ->
            done a
```

The alternative — decline over every comment, on the grounds that the comment
slots were an open question — is what makes this a decision rather than an
omission. It would have rendered `fn <|` ⏎ `{- fs -}` ⏎ `\a -> …` differently
from its comment-free twin, which is a C4 violation R1 would have introduced.

### 6b. A comment inside a step does not move R2's column

R2 asks whether a body is the next step of the chain. That question is answered
from the shape of the tree with the comments taken out, so a comment can never
shift the column that every row below it sits at. All it can cost is the head
glue for the step it is written in — 6a's fallback, which is one row, once.

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
`lambdaOperandSplit` has to *place* a head, so it declines anything it cannot
put on the row, while `isBackwardContinuation` only asks what the step *is*, and
ignores comments entirely.

