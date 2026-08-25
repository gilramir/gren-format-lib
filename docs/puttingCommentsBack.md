# Putting the comments back

*How `gren-format` places comments when the parser it uses throws them away:
what the problem is, what we built, what it cost, and what we measured
afterwards.*

## TL;DR

`gren-format` reuses the Gren compiler's parser, and that parser discards
comments. The formatter is handed a comment-free syntax tree plus a flat list of
comments carrying nothing but `(row, column)` positions, and has to work out
where each one belongs. The whole difficulty fits in a sentence: **placement is
decided from source positions, and formatting invalidates the positions the
placement was decided from.** Get that wrong and the formatter is
non-idempotent: formatting an already-formatted file changes it again.

**The position barrier** is a line drawn across the pipeline that source
positions do not cross. Above it, in the logical stage, code may read the
author's rows and columns freely — that is where every comment's placement is
decided, once, and recorded on the comment as one of seven roles. Below it, in
the stage that chooses line breaks and writes bytes, positions are not merely
off-limits; they are *absent*. The renderer consumes a different type, produced
by a lowering function that drops every position field, so a row read down there
is a compile error rather than a review comment.

**We name it because almost nobody does.** Of the fifteen production formatters
surveyed (§10), the eight whose front ends hand them already-attached comments
have the barrier for free: there is no positional question left for their layout
stage to ask. Of the seven that must reconstruct attachment, as we do, ours is
the only one that has it — and that column is where every instability in the
survey lives. The idea is not novel (swift-format wrote it down in 2020); what
is unusual is holding it in the one position where it has to be *built* rather
than inherited, and making it a type is what keeps holding it from costing a
feature.

**And the barrier is necessary but demonstrably not sufficient.** What it buys is
not the fixed point — it is *localization*. With nothing downstream reading a
position, the only thing that can differ between two runs is one function's
answer, which is small enough to argue about. Discharging that remaining
obligation is what the second half of this document is: holding *every*
row-keyed decision to the criterion and not just the ones that place a comment
(§6.3), covering the token rewrites that move a comment's anchor out from under
a decision that was right when it was made (§8), and knowing which bug classes
a property gate cannot see at all (§9).

The rest of this document is those claims with the evidence attached.

---

This is an essay, not a reference, and deliberately not normative. The rules that
decide where a comment actually lands are stated in **[How gren-format places
your comments](commentHandling.md)** (C1–C7, with a worked example for each), and
the implementation in **[The comment algorithm](commentAlgorithm.md)** (attachment
fold, roles, state machines, the gates). This document restates just enough of
both to make an argument and links down whenever the detail matters; where either
disagrees with this one, it is right. Every code example here was produced by
running the formatter.

---

## Table of contents

- [TL;DR](#tldr)
- [1. Five kinds of front end](#1-five-kinds-of-front-end)
- [2. The trade: no parser of our own](#2-the-trade-no-parser-of-our-own)
- [3. The archetype](#3-the-archetype)
- [4. Decide once](#4-decide-once)
- [5. What it replaced](#5-what-it-replaced)
- [6. The position barrier, and how to make it a type](#6-the-position-barrier-and-how-to-make-it-a-type)
- [7. Why forty comments in a row is not forty cases](#7-why-forty-comments-in-a-row-is-not-forty-cases)
- [8. The anchor — what that argument does not cover](#8-the-anchor--what-that-argument-does-not-cover)
- [9. What the gates cannot see](#9-what-the-gates-cannot-see)
- [10. What fourteen other formatters do](#10-what-fourteen-other-formatters-do)
- [11. What generalizes](#11-what-generalizes)
- [Sources](#sources)
- [See also](#see-also)

---

## 1. Five kinds of front end

Formatters are usually told apart by their layout algorithm — whether they fit
lines to a page width, how they choose where to break. For comments, none of
that is the variable that matters. The variable that matters is **what the front
end hands the formatter**, because that is what decides whether the formatter
has to work out where a comment goes at all. We read the sources of fourteen
production formatters besides our own (§10); they sort into five rungs, in
decreasing order of how much work the parser has already done.

| rung | what arrives | who |
|---|---|---|
| **A0** | comments are ordinary nodes of a concrete syntax tree; the formatter needs no comment concept at all | [topiary](https://github.com/tweag/topiary) (over [tree-sitter](https://github.com/tree-sitter/tree-sitter)) |
| **A1** | named, typed comment slots on AST nodes — and no source positions anywhere | [elm-format](https://github.com/avh4/elm-format) |
| **A2** | *trivia* hanging off tokens | [dart_style](https://github.com/dart-lang/dart_style), [google-java-format](https://github.com/google/google-java-format), [swift-format](https://github.com/swiftlang/swift-format), [CSharpier](https://github.com/belav/csharpier), [biome](https://github.com/biomejs/biome), [Black](https://github.com/psf/black) |
| **A3** | a **comment-free AST**, beside a flat source-ordered list of located comments | **gren-format**, [ormolu](https://github.com/tweag/ormolu), [ocamlformat](https://github.com/ocaml-ppx/ocamlformat), [gofmt](https://github.com/golang/go/tree/master/src/go/printer), [prettier](https://github.com/prettier/prettier) |
| **A4** | nothing; comments are recovered by re-reading the raw source between two nodes' byte offsets | [rustfmt](https://github.com/rust-lang/rustfmt), [zig fmt](https://github.com/ziglang/zig/blob/master/lib/std/zig/Ast/Render.zig) |

**One word in that table does a lot of the work, so it is worth spelling out.**
*Trivia* is what compiler front ends call the bytes between two tokens that the
grammar does not care about: whitespace, newlines and comments. A
trivia-carrying front end does not discard them. It hangs them off the token
they sit beside, split into *leading* trivia (before the token) and *trailing*
trivia (after it). For a fragment like

```csharp
// set it up
foo(1);  // why
```

`// set it up` is stored as leading trivia of the `foo` token and `// why` as
trailing trivia of the `;`. Both comments are fields of a token in the tree from
the moment the lexer finishes, and no later stage has to work out where they
belong.

On **A0–A2** the comment is already attached to something before the formatter
starts: the front end answered "which code is this comment beside?" while it was
still looking at the input, and the answer travels with the tree. Placement is
then a question about a tree the comment is already in, and the problem this
document is about does not arise — though it is worth noting that four of those
eight tools have shipped idempotency fixes anyway (§10.5), and that the gate
blindness of §9 is not rung-specific.

On **A3 and A4** that question was never asked, so attachment does not exist yet
and has to be reconstructed — from source positions, which are the one piece of
evidence formatting destroys. Every instability story in §10 lives on those two
rungs.

`gren-format` is on A3, deliberately (§2). That is not an exotic place to be: A3
is simply **what a production compiler's parser hands a formatter**, and five of
the fifteen tools surveyed are there.

---

## 2. The trade: no parser of our own

`gren-format` has no parser. It calls the Gren compiler's parser — the same front
end that compiles the code being formatted.

That buys one guarantee, and it is the reason for everything that follows: **the
formatter can never drift from the language.** It accepts exactly what the
compiler accepts, this release and every future one. A formatter with its own
grammar is a bug factory whose bugs are silent, because they appear only on the
inputs nobody wrote a test for — and a language that is still moving will
outrun a second grammar.

The price is exact and unavoidable. A compiler's parser discards comments,
because a compiler does not need them.

**Why we took it anyway.** Gren's front end is still moving, and a formatter
that can never disagree with it is worth more to us than easy comment placement.
The alternative was committing to maintain a second grammar for the rest of the
project's life — a larger cost than the one below, and a quieter one. Had the
compiler already carried trivia we would have taken that instead, because it is
free; a project that can afford the second grammar can buy its way out of this
document entirely. The rest of it is the size of the bill we chose to pay, and
§8 and §9 are the two largest line items on it.

---

## 3. The archetype

### 3.1 What the formatter is actually handed

Two things. The first is `Src.Module`: an ordinary AST with source ranges on its
nodes and **no comment nodes anywhere** — there is nowhere in the type for a
comment to be. The second is the parse `Context`, which carries a flat,
source-ordered list of located comments:

```gren
type Comment
    = Line String     -- `-- like this`, the text after the `--`
    | Block String    -- `{- like this -}`, the text between the delimiters

-- the context carries: Builder (Located Comment)
-- where `Located` is a start (row, col) and an end (row, col).
```

For this module:

```gren
module S exposing (sizes)


sizes =
    [ 1 -- one
    , 2
    ]
```

the formatter receives an AST for `sizes = [1, 2]` and, separately:

```json
{ "comments": [ { "type": "line", "value": " one",
                  "start": { "row": 5, "col": 9 },
                  "end":   { "row": 5, "col": 15 } } ] }
```

The comment does not know it is inside an array. The array does not know a
comment exists. The only thing connecting them is a `(row, column)` pair.

### 3.2 The one sentence

That is a reattachment problem, and reattachment interacts badly with the one
thing a formatter does:

> **Placement is decided from source positions, and formatting invalidates the
> very positions the placement was decided from.**

Here is the whole difficulty in one picture. The author wrote a multi-line block
comment trailing a call:

```
you wrote:                  format¹ produced:        format² produces:

v =                         v =                      v =
    fn a b {- c                 fn a b                    fn a b
   second -}                    {- c                 {- c
                                   second -}            second -}
```

Format¹ read "the comment is on the declaration's last row" and rendered it at
the call's indent. But a multi-line `{- … -}` cannot sit *on* that row — it
brings its own newlines — so it landed *below* the declaration. Format² is now
reading a comment written below a declaration, which is a different question
with a different answer (detach to column 1). The file now has two spellings and
alternates between them for ever.

**Every comment bug this project has fixed is a variation on that one sentence.**
Oscillation is the single largest bug class in the project's history that any
property gate can see (§9.2).

### 3.3 Three properties, gated separately

1. **Preservation.** Every input comment appears exactly once in the output, text
   and kind unchanged. (Continuation rows of a multi-line block comment are
   re-indented; that is layout, not text.)
2. **Faithful placement.** The comment lands beside the code the author wrote it
   beside. Specified as [C1–C7](commentHandling.md#the-seven-rules-at-a-glance):
   the first two decide *which code a comment attaches to*, the last five decide
   *how it is laid out*. They do not trade against each other — attachment is
   settled first, and layout works with whatever it is given.
3. **Idempotency.** `format(format(x)) = format(x)`, byte for byte.

Properties 1 and 2 come nearly free from a trivia-preserving front end. Property
3 is where the architectures separate. Under trivia, a comment's slot in the tree
is stable across formatting because it was never derived from layout in the first
place. Here it is derived from layout, and layout is the output:

```
placement    = place(code, positions)
output       = layout(place(code, positions))
second run   = place(code, positions')    where positions' = rows-of(output) ≠ positions
```

Nothing makes those two agree by construction. Something has to.

It is worth separating 1 from 2 explicitly, because keeping the comment does not
give attachment. Black keeps every comment — they ride in a leaf's whitespace
prefix — and still says so in the docstring of the function that has to place
them:

> "The sad consequence for us though is that comments don't 'belong' anywhere.
> … We simply don't know what the correct parent should be."

### 3.4 One thing we are not doing

`gren-format` has **no page-width limit and no fitter**. Layout is author-driven:
a construct goes vertical only if the author wrote it across rows, or if
something inside it is multi-line and forces the rest open. There is no search
for a best arrangement and no reflow.

That fact sits close enough to §3.2's problem to look like its cause. The
tempting inference is that author-driven layout is *why* the instability class
exists — an optimizer that recomputes layout from scratch has no stale authorial
rows to be wrong about. The survey in §10 contradicts that inference in both
directions: prettier *is* that optimizer and has the class anyway, at a nine-year
scale, while gofmt has no fitter at all and is not stable either. What actually
sorts the stable formatters from the unstable ones is the **position barrier** —
whether the stage that decides breaks and verticality may read a source position
at all. Five of the width-aware formatters surveyed sit behind one, fitter and
all, and are stable. The variable is whether *placement* reads positions, not
whether *layout* is author-driven. §6 is where ours is built, and why the type
checker enforces it instead of a convention.

What author-driven layout really does is make the authorial rows *more tempting
to read*. They are right there, and they are usually correct. That is precisely
why the ban in §6 has to be mechanical rather than advisory.

### 3.5 The shape of the answer

Eight steps; the rest of this document is about four of them.

![The seven pipeline steps, and where the position barrier falls](diagrams/position-barrier.png)

**Step 2** is where every placement decision is made, and §4 is the argument for
making all of them there, once, rather than at each point of use. **Step 6** is
the barrier, and §6 is what it costs to have the type checker hold it.

**Steps 4 and 5 are shaded with step 2** because they are the same kind of step:
all three read the author's rows. The difference is that they run *after* the
placement decision and still on the legal side of the line, so nothing stops
them moving a row step 2 was already decided from. Both of this document's
hard-won sections are about that — §6.3, where the barrier turns out not to
cover them, and §8, where sorting moves the code a comment was attached to.

---

## 4. Decide once

Every comment's placement is decided **exactly once**, in the logical stage,
while every row in the tree is still the author's. It replaced deciding the same
thing eight times over, in eight places, at render time (§5). The answer is
stored on the comment leaf as a `CommentRole`:

```gren
type CommentRole
    = TrailsPrevious   -- glue onto the previous sibling's last rendered line
    | LeadsLine        -- own line, at the current flow/body indent
    | LeadsNext        -- belongs to the sibling AFTER an unrecorded separator
    | TrailsHead       -- glue onto the container's head (a record update's base)
    | RidesInline      -- rides mid-line without breaking it (`f {- k -} x`)
    | LeadsInline      -- glued to the FRONT of a declaration (`{- c -} import Qux`)
    | Standalone       -- a detached top-level comment, its own column-1 node
```

The renderer reads the stored answer. It never recomputes it.

One module exercising all seven — the role column is the formatter's own `--lpt`
output for this file, not a hand annotation:

```gren
module Ex exposing (a, b)

{- c -} import Qux

import Dict {- inline -} as D

a rec =
    { rec -- about the base
        | alpha = 1
    }

b =
    let
        base =
            -- start here
            1
    in
    [ base -- the seed
    , 2, {- two -} 3
    ]

-- detached below b
```

| comment | role |
|---|---|
| `{- c -}` before `import Qux` | `LeadsInline` |
| `{- inline -}` in `import Dict … as D` | `RidesInline` |
| `-- about the base` | `TrailsHead` |
| `-- start here` | `LeadsLine` |
| `-- the seed` | `TrailsPrevious` |
| `{- two -}` | `LeadsNext` |
| `-- detached below b` | `Standalone` |

**Four questions, asked once.** Each comment is placed by answering, in order:
**which declaration** owns it; **how deep** inside that declaration's subtree it
belongs; **which gap** between siblings it falls in; and **which role** it takes
at that gap. All four are answered while the rows are still authorial, and none
is revisited. The full decision procedure, with the two decision diagrams, is
[commentAlgorithm.md §4](commentAlgorithm.md#4-stage-1--attachment).

**Every role has to survive being re-derived from the formatter's own output.**
That is the rule the classifier is written to satisfy, and it fits in a sentence:

> A role is a reparse fixed point: **the row a comment's placement is decided
> from is the row that comment renders on.**

Concretely: if the classifier says "this comment trails the item before it"
because the author wrote it on that item's last row, then the comment must come
out on that item's last row. The second run asks the same question of the same
row, gets the same answer, and nothing moves.

**When a role cannot satisfy that, the rule is not the thing to change.** §3.2's
oscillating example is the case in point: the comment is decided from the row the
author wrote it on, and cannot render there. No rewording helps. The rule is
already right about where the author put it; the trouble is that the output puts
it somewhere else.

What fixes it is **making format¹ build the tree format² would build**. The
comment is going to end up below the declaration, so the first pass puts it
there itself — at column 1, as its own node, which is precisely what a reparse
would produce — and the second pass finds nothing left to change.

Two passes do that, and they run over the **finished** tree rather than inside
the walk that places comments one at a time. What forces them to the end is the
comments: a comment's neighbors are not known until the last one is in, and
"where is this going to render?" depends on both halves of the tree. They are
worked through in
[commentAlgorithm.md §4.6](commentAlgorithm.md#46-repairs-that-need-the-finished-tree).

**Every comment gets a role; there is no "don't know".** `classifyCommentKind`
returns a `CommentRole` — not a `Maybe CommentRole`, not a `Result`. There is no
fallback arm, no `Unknown` constructor, and no branch that leaves the question
for the renderer to settle later. The renderer's own layout policy is total in
the same way.

That buys more than it appears to, and §7 is where it gets spent. Because the
answer is always exactly one of seven values, a file with *n* comments ends up
carrying *n* independent labels — not a combined state that depends on how those
comments sit relative to one another. The alternative is what people expect a
comment placer to look like inside: a case analysis over *configurations* — a
line comment followed by a block comment, inside a list, after a separator — and
a space of configurations grows with the number of comments in one place and
always has a corner nobody enumerated. There is no such space here to enumerate.
There are *n* leaves, each holding one of seven answers, which is what lets §7
argue about runs of any length at all.

Deciding once is, incidentally, *not* the unusual part: ten of the fifteen tools
surveyed in §10 do it, into role sets of two to nine members. What differs is what
the classifier is allowed to read, and what the stages downstream are allowed to
do with its answer.

---

## 5. What it replaced

The two sections above state a design. They do not say that it was arrived at
the expensive way, or that this formatter spent most of its life doing the
opposite. That is worth saying, because the before-and-after is the closest
thing here to a controlled measurement of what the architecture buys — same
formatter, same corpus, same output, only the location of the decision changed.

Three eras, all dated in this repository's log:

| | dates | placement decided | what held the line |
|---|---|---|---|
| **1** | 2026-03-01 → 07-19 | re-derived at each point of use | nothing |
| **2** | 07-19 → 08-23 | once, into a stored role | a grep over an enumeration of accessor names |
| **3** | 08-23 → | once, into a stored role | the type checker |

### 5.1 What era 1 looked like

Era 1 is what a comment placer looks like before anyone has named the problem.
"Does this comment glue onto the previous item's line, or take its own?" was not
a function. It was **at least eight independent places** asking the question
again, each in its own way, each from the author's rows: the generic flow
assembler, the binop splitter, the bracket-list renderer, the pipeline peeler,
and the rest.

Holding that up took real machinery. A two-row state machine threaded through
the flow policy (`prevRowBlock`, `prevRowLine`, `prevElided`), and a per-box-kind
table of start rows about ninety lines long whose only job was to tell the
renderer which row a box would begin on, so that a comment could be compared
against it. Both are exactly what §6.1's second table forbids, written out at
length.

Every one of those sites was individually defensible, and most were usually
right. What they could not be is *consistent with one another*, and nothing in
the program required it. The same comment in a binop and in a bracket list was
two questions with two answers, and the answers drifted apart.

### 5.2 What ended it was a rate, not an insight

Eight comment fixes landed in three days, 2026-07-17 to 07-19 — crashes and
oscillations, each one small, correct and local. The plan written at the end of
that week opens by naming what they had in common:

> "Each fix was small, correct, and local — and each was the same fix, re-proven
> for one more code path. The root cause is architectural."

It then states the invariant, and the sentence is §6.1's, four weeks earlier and
in this project's own words:

> "After `Formatter.Logical.Comments` runs, no code in `Render/*` reads source
> rows or positions to make a layout or comment-placement decision."

What makes that document worth quoting is not the rule but the *evidence
standard* attached to it. It required the whole rewrite to be **byte-identical**
on every fixture, fuzzer and parity baseline: the point was explicitly not to
change any output, only to change where the decision lived. About nineteen
commits, each verified that way.

The row machinery never had to be argued away. Once every site read the stored
role, it was write-only, and it was deleted — the state machine, the ninety-line
table, and the accessors underneath.

### 5.3 What each era actually taught

Era 2 was not the end, and that is the part worth carrying forward. The grep
script held for five weeks; §6.2 is what became of it. Two of this document's
harder findings — §6.3's row-keyed passes sitting above the line, and §8's
anchor — were still ahead of us at the start of era 3.

Each era's answer exposed the limit of the one before it:

1. the question must be asked **once** (era 1);
2. "once" has to be **enforced**, not intended (era 2);
3. what is enforced is **narrower than what must be true** (era 3). The barrier
   forbids one thing: reading a position below the line. The row reads *above*
   it stay legal, and the renderer still has judgment left *below* it — the two
   gaps of §6.3.

Two things this history is *not* evidence for. It is not evidence that the
architecture is quicker to write: it cost about nineteen commits to produce
identical bytes. And it is not evidence that the bug class is gone — §6.3 and §8
are both findings from after the barrier existed.

What it is evidence for is §6.4's claim about where the obligation ends up. Era
1 spread it over eight sites and a ninety-line table; era 3 has it in one
function that returns one of seven values. That is the same difference §10
measures from the outside — prettier's 19 print modules of 73 that read the
source, ocamlformat's 14 source reads in the printer — observed here from the
inside, on one codebase, with the output held fixed.

---

## 6. The position barrier, and how to make it a type

### 6.1 The rule

> **No code in the rendering stage may read a source row or position to make a
> layout or comment-placement decision.**

What that means in practice is a split between two lists of questions. **Above
the barrier**, in the logical stage, where every row in the tree is still the
author's:

| question | what it reads |
|---|---|
| which declaration owns this comment, how deep in its subtree, in which gap between siblings, and with which role (§4) | the comment's `(row, col)` against each node's cached range |
| did the author write this construct across rows? — the `forceVertical` flag on a call, an `if` condition, a record literal | the node's own first and last row |
| did the author write this node on the same row as the item before it? | both nodes' rows |
| did the author break this signature at a `->`, or spread this union's variants over several lines? | the children's rows |
| does this node cover any source row at all, or was it synthesized? | whether the node has a range |
| how many blank lines go between two top-level items, and where one import group ends and the next begins | the last row of one subtree against the first row of the next |

**Below the barrier**, in the rendering stage, where no position exists to read:

| question | what it reads |
|---|---|
| where does this comment go? | the `CommentRole` stored on the comment leaf |
| is this construct vertical? | `isSingleLine`, applied to a `Box` that has already been built |
| can code follow this comment on the same line? | the comment's own **text** — the three kinds of §7.2 |
| any of the author-intent questions above | the boolean `lower` computed at the barrier — the answer, never the evidence behind it |
| is there a line here to glue onto? | what the renderer has emitted so far (§6.3) |

The second list is the whole of what the renderer is allowed to ask, and nothing
on it is a source position — including the first row, which sounds like one. A
role names a *relationship*, not a place: `TrailsPrevious` says *glue onto the
previous sibling's last rendered line*, `TrailsHead` says *onto the container's
head*, `LeadsNext` says *the sibling after an unrecorded separator*.

The fourth row is what makes this affordable rather than merely strict: the
questions that genuinely do need the author's rows still get answered — the
renderer is handed the answer instead of the evidence, and cannot re-derive it
later against different rows.

### 6.2 Making it a type error

This is enforced by the type checker, not by documentation and not by a linter.

The rendering stage does not consume the logical printing tree at all. It
consumes `RenderNode`, a **mirror of the node type with the seven cached position
fields removed**, reachable only through a `lower` function that drops them.
`RenderShape` strips the `Located` payloads off eight shape constructors too, so
there is no position anywhere in the renderer's view of the tree.

A render-side row read is therefore not a lint failure. It is a type error, and
there is no allowlist to grow.

Five render-side decisions genuinely needed the author's rows — the fourth row
of §6.1's second table. `lower` computes them once, at the barrier, as four
booleans: `rnSharesRowWithPrevItem`, `rnHasSourceContent`, `rnVariantsSpanRows`
and `rnTypeSegmentsBroken`. `RenderShape` is total over
the logical shape type, so adding a new shape does not compile until the lowering
maps it.

**The previous version of this barrier was a script**, and deleting it is the
part worth reporting. It ran first in the test suite and grepped the rendering
stage for an enumeration of position-accessor names. It worked, and it was a
barrier only until someone wrote the accessor a different way — or until the
enumeration of another module's vocabulary went stale, silently, which it did.
A grep over an enumeration is a reviewed convention wearing a script's clothes.

"Be careful not to read positions here" is a comment in a file nobody reads at
2am. "It does not compile" is a property of the system.

The cost is a second tree, and it is measured rather than asserted:
[renderTreeMemory.md](renderTreeMemory.md), which also explains why peak RSS is
the wrong instrument for measuring it.

### 6.3 What the barrier does not cover

Two gaps, both of which have produced real bugs here. Neither was a reason not to
build the barrier; both were reasons to stop believing we had finished.

**On the renderer's own side of the line: a role is not a row.** Turning a role
into output still requires a fact no role can carry — *is there a line here to
glue onto?* A `TrailsPrevious` comment glues onto the previous item's last line,
but whether the previous sibling ended on a line this comment may join is a fact
about what the renderer has just emitted. That question is legitimately below the
barrier, it is answered from render state rather than from positions, and it can
simply be got wrong.

**On the other side of the line: a row that this pipeline is itself going to
move.** Above the barrier, reading source rows is legal and necessary — that is
what the logical stage is *for*. The gap is that the rows stop being the
author's part-way through it. Steps 4 and 5 of §3.5 can each move a row step 2
was decided from, and the renderer at steps 7–8 then moves nearly all of them. A
decision keyed to such a row is stale for exactly the reason a render-side read
is stale — and the barrier does not touch it, because every step involved sits
above the line.

This is the one that catches people, and it caught swift-format in exactly the
same place (§10). Our criterion in §4 is stated *for roles*, and that scoping was
itself the defect. Steps 4 and 5 are row-keyed decisions too; neither produces a
role, so neither was ever held to the sentence. Both were later found violating
it. The vertical-space pass (step 5) decided whether a comment was free-floating
from the gap *below* it — and then closed that very gap itself, when it pulled a
definition up under its signature. First format saw a gap and emitted two blank
lines; second saw none and emitted one.

Read correctly, the sentence was never about roles:

> **Every** row-keyed decision in the logical stage must be decided from a row
> that this pipeline does not itself move.

Roles are simply the case where we noticed first.

### 6.4 What the barrier is actually for

It does not *prove* the fixed point. It **localizes the obligation**.

With no positional read downstream, the only thing that can differ between run 1
and run 2 is the classifier's answer. So "is formatting a fixed point?" stops
being a question about the whole pipeline and becomes a question about a single
function — one that always returns an answer, and always one of seven. That is
what makes the coverage argument in §7 possible at all. Without it, the
obligation is spread over every layout rule that reads a row: 19 of prettier's
73 print modules, 14 source reads in ocamlformat's printer. There is no
corresponding argument to be made about those, and §10 shows what gets shipped
instead.

Localizing is not discharging, and §10.5 is the counterweight: most of the tools
that hold the barrier have shipped idempotency fixes anyway. What is left after
the barrier is small enough to argue about. Something still has to argue.

---

## 7. Why forty comments in a row is not forty cases

The question every reader of a comment placer asks is "what happens if I write
forty comments in a row?". This section argues that it is the wrong shape of
question, and §7.6 reports what happened when we tested the argument as a
prediction.

The claim:

> For any *n* ≥ 0 comments of any kinds in one place, the algorithm assigns
> exactly one placement to each; and a run of *n* reaches no decision that a run
> of two does not.

It rests on three properties of the implementation, plus one honest boundary
(§8). The full version, with the code sites for each, is
[commentAlgorithm.md §8](commentAlgorithm.md#8-why-this-covers-every-run--the-argument-not-the-test-suite).

### 7.1 Placement is prefix-determined, so *n* is never an input

The attachment fold walks comments in source order, and when comment *k* is
placed, comments *k+1 … n* **are not in the tree**. So

```
role(k) = f(code, comments 1 … k−1)
```

and `f` is the same function for every *k*. No branch anywhere in the attachment
module asks how many comments a gap holds, because at the moment the question is
asked the answer is not knowable. *n* enters the algorithm in exactly one way: as
the number of times `f` is applied.

That is why "does it handle a run of 40?" is not a question about runs. It is the
same question as "does it handle the 40th comment in the file", and the fold does
not distinguish the two. With the classifier's totality (§4) alongside it, the
tree after *n* comments carries the same *kind* of state it carried after one.

### 7.2 The formatter tracks three kinds of comment

The language has two comment syntaxes, but every layout question about a
comment's kind routes through **one** predicate, which reads the comment's own
*text* and distinguishes three shapes:

| | can code follow it on the same line? |
|---|---|
| `-- like this` | no |
| `{- like this -}` on one row | **yes** |
| `{- like this` ⏎ `and this -}` | no |

Nothing else about a comment — its length, its content, its original indentation
— is read by any decision. "Any variety of comments" is not an unbounded axis:
it is three kinds.

The design decision underneath that is the one we would defend hardest here:
**every kind-sensitive question routes through a single predicate**, so the
number of kinds is a fact we can state rather than an emergent property of
scattered `case` arms. Ours is three because that predicate says so.

### 7.3 Every local rule reads at most one neighbor

This is the most important of the three properties. Every place a run's members
interact locally:

| rule | what it reads |
|---|---|
| the reference row (four sites) | the **previous** member's last row |
| the render fold | a 6-value state summarizing the row in front |
| the peel scanner | the **previous** member's kind |
| "can this text ride?" | the member's **own** text |

Not one looks two members back, or forward. So a run of comments is a chain of
**boundaries** — the junctions between one thing and the next — and every rule in
the table is a function of exactly one boundary. A run of three comments, A, B
and C, written between two pieces of code has four of them:

```
   code │ comment A │ comment B │ comment C │ code
        ↑           ↑           ↑           ↑
     code→A        A→B         B→C       C→code
```

Three of those four are comment→comment boundaries, and with three kinds of
comment there are exactly **nine** of those possible. A run of any size is built
from the same nine.

A longer run can therefore reach something new in only two ways: by containing a
boundary a shorter one could not — impossible once all nine have appeared — or by
putting a member at **two** boundaries at once, which is observable only if some
rule reads both sides. By the table above, none does.

### 7.4 Where one neighbor is not enough

Three rules genuinely cannot be decided from one neighbor, because they are
about the run as a whole. Each is stated as a **quantifier over the run**, never
as a case analysis over its length:

| rule | the quantifier |
|---|---|
| may the run cross an unrecorded separator? | ∀ members: could this one cross? |
| may the run ride a flat line? | ∀ members: can this one ride? |
| who owns the run — detaching, sorting, blank lines? | the run is the unit |

A `∀` over a set is still length-independent: folding a predicate over an array
does not care how many elements it has. Both boolean ones are additionally
**monotone** — adding a member can only turn the answer *off*, never on — so they
can only make a comment stay where it was written, never move one that was not
already moving.

**Every one of those three was written per-member first, and every one was found
as a bug**: a mixed run torn in half and reassembled in the author's *reverse*
order, a run that half-rode a flat line and never settled, a blank line above a
floating run that alternated between one and two. The per-member version is the
natural thing to write, it is correct for a run of one, and a corpus of
hand-written fixtures will not contain the mixed run that breaks it. §10 shows
two other projects learning the same thing independently.

### 7.5 What it looks like

Six comments, three kinds, one gap:

```gren
v =
    fn a {- 1 -} {- 2
                    over two rows -} {- 3 -} {- 4
                                                again -} {- 5 -} -- 6
```

Every member keeps the author's single logical row; each multi-line member's
continuation is re-indented under its own `{-`; member 3 glues after member 2's
`-}` two rows down, because the reference row grew through the run; and the whole
thing is a fixed point. Nothing in producing it consulted the number six.

### 7.6 The honest form

> Given that no rule reads more than one neighbor except the three that quantify
> over the whole run, length and composition add nothing past two members.

The premise is a property that must be **maintained**, not one that anything
enforces. So the run sweep axes do not prove the argument; they test its premise.
A fourth all-or-nothing rule discovered tomorrow gets added to §7.4's table, and
the reasoning carries on unchanged. This is a small-scope-hypothesis result, but
with a **mechanism**: we name the structural property (boundary locality) that
makes the small scope sufficient, and then test that property directly.

**And the argument is falsifiable, so we ran it as a prediction.** §7.3 says in
advance which sweeps will pay for themselves and which will not. The probe is
mechanical, which is what makes the numbers comparable across axes: for every
fixture in the corpus, insert a marked comment — or a run of *N*, or a run of a
given composition — into **every** inter-token gap, format twice, and require the
two outputs to be byte-identical. Read "**finds bugs**" as "this probe reaches
something no earlier probe did".

| probe | what it newly reaches | predicted | measured |
|---|---|---|---|
| one comment | nothing — every neighbor is *code* | (the baseline) | the baseline every gate started from |
| a run of 2 | the first comment→comment boundary ever tested | **finds bugs** | **20 findings** in 19,081 gaps; one real family, fixed the same day |
| a run of 3 | nothing — `block│block` already appeared at *n*=2 | **nothing new** | 17 findings in 57,885 gaps, **all 17** a known upstream parser bug |
| mixed pairs | the other eight boundaries — a *different* kind on each side | **finds bugs** | **1,752 findings** in 115,770 gaps, **1,718 formatter-side**, in three bugs |
| mixed triples | nothing — every ordered pair already appeared | **nothing new** | 154 findings in 475,824 gaps, **all 154** known upstream |

668,560 probe sites across the four axes; two axes — run *length* and run
*composition* — swept independently, each finding real bugs at exactly the size
where a new boundary first becomes expressible and nothing beyond it, twice. The
two "nothing new" rows carry the weight. If any rule *had* been reading both
sides of a member, mixed triples was 475,824 chances to catch it, and it caught
nothing formatter-side.

---

## 8. The anchor — what that argument does not cover

This is the section whose lesson we learned last and most expensively, and the
one that changed our test portfolio most. The
implementation-facing version, naming the passes involved, is
[commentAlgorithm.md §8.7](commentAlgorithm.md#87-the-anchor--the-obligation-this-argument-does-not-discharge).

### 8.1 Code is an input for deciding the role

Everything in §7 quantifies over *the run*, holding the tree fixed. It says
nothing about the tree. Look again:

```
role(k) = f(code, comments 1 … k−1)
```

§7 shows that `f` is length-independent in its **second** argument. But `code` is
an argument too. The fixed-point obligation is therefore two obligations, and §7
discharges only one:

> **(i)** given the same `code`, `f` returns the same roles when re-asked over the
> output's rows; and
> **(ii)** the second run *is given the same* `code`.

(ii) is not free. It holds exactly insofar as formatting does not rewrite
concrete syntax — and ours does, in three ways that delete, insert or reorder a
token: it **sorts** exposing lists and import groups, it strips redundant
parentheses from patterns, and it adds or drops the `port` keyword on a module
header. (Two further rewrites — uppercasing hex digits, normalizing string
escapes — change bytes only *inside* a token, so they cannot move an anchor.)

**Any formatter that rewrites tokens at all — sorts imports, removes redundant
syntax, normalizes a keyword — carries obligation (ii), and an idempotency gate
does not discharge it.** Ours does all three. Here is how we found that out.

### 8.2 What actually moves

A comment's **anchor** is the code its placement was decided against — the
declaration it trails, the import it leads. Obligation (ii) fails when the
formatter moves that code out from under the decision, and the narrow shape we
hit is what made it hard to test for. Start with an input a person would have to
write deliberately — comments *inside* an import statement:

```gren
import {- k0
    tango -} Qux0 exposing (..) {- k1
            tango -}
import Bar3
```

Each comment's range **overlaps** the import's: written inside a statement, it is
physically within the span of the thing it interrupts. There is nowhere inside an
import node for a comment to live, so attachment (step 2) promotes each one to a
**sibling** of the import — a node beside it in the tree rather than within it —
and the first pass prints them on rows of their own, then sorts (step 4):

```gren
import Bar3
{- k0
   tango -} {- k1
               tango -}
import Qux0 exposing (..)
```

Read that back with fresh eyes, which is what the second run does. The comments
are no longer inside anything: to a parser they are now **leading** comments of
`Qux0`, a different attachment from the one the first pass chose — and leading
comments count as part of an import unit, so the group boundary moves, and the
boundary is what the sorter keys on. Nothing re-read a position at render time.
The classifier answered a different question because the code had changed shape.
Underneath, the sorting and blank-line passes had been written against an
invariant nobody had written down — **siblings are disjoint and in row order** —
which promotion is exactly what breaks.

**And no gate here could have seen it.** Every comment-gap sweep in this project
ran against the corpus of **already-formatted** fixtures — and the rewrite that
moves an anchor cannot happen on a fixed point. Promotion is sharper still: it
happens only to a comment written *inside* a statement, and the formatter's own
output never contains one, because the first pass is what lifts them out. The
input class was not merely absent from that corpus, it was *excluded from it* by
construction. Running the same instrument over the 391 *unformatted* halves of
the same fixture pairs — 66,252 probe sites — produced **24 findings in 10
fixtures** on the first sweep, 22 of them this class. That axis is now a standing
default (`--corpus both`, in
[testing.md](testing.md#idempotency-fuzzer-fuzz-idempotencypy)).

### 8.3 The part that no idempotency gate can see

We first read this defect as a format¹-vs-format² disagreement and fixed it that
way. It is not only that. When the same misreading also causes the
**vertical-space** pass to emit a blank line — which it does, since that pass asks
the same adjacency question of the same overlapping range — the phantom blank
becomes a **real run boundary on the next parse**, and the wrong grouping is
*self-consistent*. Formatting is then a fixed point that has silently declined to
sort two adjacent imports.

No idempotency gate can see that, and ours did not. The shape survived the first
fix, survived a second fix to the sorter's row rule, and was finally caught by
the **author-order invariance** oracle: emit the same module with its import run
in the other order and require byte-identical output. That is the one gate in the
portfolio that does not ask about repetition at all.

### 8.4 The scope, stated exactly

> §7 establishes that run length and composition are not inputs to placement. It
> does **not** establish that placement is a fixed point, because it assumes an
> anchor the formatter is free to move.

Obligation (ii) is discharged by testing rather than by argument — and, more
precisely, it **cannot be discharged by idempotency testing at all**, since §8.3
exhibits a member of the class that *is* a fixed point. Covering it requires an
oracle that varies the input's **authoring** rather than repeating the formatter:
*emit the same program spelled two legal ways and require the same bytes.* We
have exactly one, we built it late, and building a second is the open work.

This is not a parochial worry. Two other projects ship the same shape today, one
of them deliberately: Black, in a tool that runs a stability assertion on every
format (§10.5), and swift-format's `OrderedImports`, where it is a fixed point and
was closed as working-as-intended (§10.6).

---

## 9. What the gates cannot see

A portfolio of property gates has *shaped* holes, and they can be enumerated in
advance. What each gate varies is in [testing.md](testing.md) and
[commentAlgorithm.md §10](commentAlgorithm.md#10-coverage-what-each-gate-actually-varies);
two results are worth carrying out of them.

### 9.1 Three holes that look covered

- **A dropped comment passes almost everything.** Deleting a comment is
  AST-equivalent and the output is its own fixed point, so the end-to-end check
  passes and so does every stability check; only a marker count and a multiset
  oracle can see it. Caught twice here, both times a renderer indexing a node's
  children positionally — in a formatter where **a comment is a child**. Not ours
  alone: rustfmt carries 89 issue titles reporting a lost comment, 29 open, and
  swift-format loses one today in a rule that is *guarded* against comments but
  on two of its three slots (§10.6) — which is how the class survives a portfolio
  that cannot see it.
- **A wrongly *attached* comment passes even those.** A multiset oracle discards
  positions on purpose, so a wrong-but-stable attachment is a perfectly good
  fixed point; only §8.3's author-order oracle sees it, and only a generator can
  run that.
- **A run reassembled backwards is a perfectly good fixed point.** Tear a run
  across a separator with the mover written *first* and the output is stable,
  AST-equivalent and comment-preserving; only the *ordered* marker oracles see
  it. Torn with the mover written *second*, the output comes out in source order
  and nothing in the portfolio can see it at all — that case is pinned by a
  fixture, found by enumerating the grid rather than by a gate.
  [prettier #10108](https://github.com/prettier/prettier/issues/10108), "Comments
  in array: idempotence violation *and change of order*", is the same shape
  reported externally.

One methodological finding generalizes well past formatters:

> **A gate green over the wrong axis is indistinguishable from a correct
> implementation.**

Our comment axis ran green for months over two of the three comment kinds. Adding
the third found 70 non-idempotencies the same afternoon. Check what a gate
*varies* before trusting what it reports.

### 9.2 The class no property gate sees at all

Replayed against the project's own history — 135 fix commits, each parent built
and today's oracles run against the one triggering input, an oracle counting as a
**witness** only when it fired at the parent and was clean at the fix — 61 rows
built at both ends with a usable input. 37 were witnessed. **21 were invisible to
the entire portfolio**: the output changed, and was *wrong but stable* —
AST-equivalent, its own fixed point, every comment preserved, the end-to-end
check exiting 0 at the parent.

| class | witnessed | invisible | not reproduced |
|---|---:|---:|---:|
| oscillation · crash · wrong-attachment · performance | 33 | 0 | 2 |
| **layout** | **0** | **16** | 1 |
| mixed · dropped-content · blank-lines · literal-corruption | 4 | 5 | 0 |

The separation is almost perfect. The property portfolio caught **every** crash,
oscillation, wrong-attachment and performance bug it was given — and **none of
the seventeen layout bugs**, the single largest class in the corpus.

> A property oracle asks "did the output violate an invariant?" A layout bug
> violates none. The output is complete, stable, AST-equivalent and
> comment-preserving, and merely **wrong**. Only an expected answer, or a second
> implementation to compare against, can say so.

That is a *stronger* claim than this project's own documentation made: the blind
spot our docs emphasize is the dropped comment; the largest measured one is
wrong-but-stable layout, at a third of the replayable corpus. It is also why one
gate's inputs are chosen by nobody here — every other one is synthetic, built
from a vocabulary this project authored, and a sweep over ten published packages
found nine bugs, each a *feature conjunction* no single-axis gate could
generate.

---

## 10. What fourteen other formatters do

**All counts and repository states in this section are as of 2026-08-23**, the
date of the pull.

We read the sources of fourteen production formatters. Beyond §1's input rung,
three axes separate them: *when* placement is decided; whether layout is
width-aware or author-driven; and — the axis that is almost never named — the
**position barrier** of §3.4, which §6 builds in detail.

The barrier is not implied by deciding once. A tool can decide attachment
exactly once and still let every layout rule re-derive verticality from the
author's rows. It is also a stronger bar than "the fitter is positionless" —
several tools reach their fitter through a tree walk that is itself full of
layout choices, and it is that walk the barrier has to cover.

| formatter | comments arrive as | placement decided | layout | position barrier? |
|---|---|---|---|---|
| topiary | A0 CST nodes | *never asked* — author-row facts resolved to atoms once | author-driven | yes |
| elm-format | A1 named slots | at parse time | author-driven | yes (no positions exist) |
| dart_style | A2 token trivia | once, front end → 4 roles | **80-col cost solver** | yes (positionless `Piece` IR) |
| swift-format | A2 token trivia | at token-stream build, *lexically* → 4×2 | 100-col Oppen | yes (printer reads only output rows) |
| google-java-format | A2 token trivia | at lex time, *lexically* | 100-col greedy `Doc` | **partial** — `Doc` sees none, op-builder reads 2 |
| CSharpier | A2 [Roslyn](https://github.com/dotnet/roslyn) trivia | while walking trivia → 2×2 | 100-col `Doc` | yes |
| biome | A2 [rowan](https://github.com/rust-analyzer/rowan) trivia | once, pre-pass → 3×3 | 80-col `Doc` | yes |
| Black | A2 prefix *strings* | once, `list_comments` → 2 roles | 88-col | yes (comments become typed leaves) |
| **gren-format** | **A3 located list** | once, logical stage → 7 roles | author-driven | **yes, type-enforced** |
| prettier | A3 located list | once, `attach.js` → 3×3 | 80-col `Doc` | **no** — 19/73 printers read the source |
| ocamlformat | A3 located list | once, `Cmts.init` → 3 roles | 80-col `Format` boxes | **no** — 14 `Source.*` reads in the printer |
| ormolu | A3 located list | **at print time**, span compares | author-driven | no |
| gofmt | A3 located list | **at print time**, row/col compares | author-driven | no |
| rustfmt | A4 missing spans | **at print time**, byte spans | 100-col | no |
| zig fmt | A4 raw source scan | **at print time**, byte offsets | author-driven | no |

Read as a partition, it stops being a list:

| | **position barrier** | **none** |
|---|---|---|
| **A0–A2** — comment arrives attached | all eight (google-java-format: partial) | — |
| **A3–A4** — attachment must be reconstructed | **gren-format, alone** | prettier, ocamlformat, ormolu, gofmt, rustfmt, zig |

**Every tool whose front end hands it attached comments has the barrier, and gets
it for free** — there is no positional question left for the layout stage to ask,
so nothing had to be forbidden. **Every tool that must reconstruct attachment
lacks it, except ours.** That column is also where every instability in this
survey lives.

Stated that way it is a claim about the substrate, not a trophy: the barrier is
neither an idea of ours (swift-format wrote it down in 2020) nor a rarity (eight
of fifteen have it). Only the cell it sits in is unusual.

### 10.1 Deciding once is the norm, not the contribution

Ten of the fifteen classify each comment exactly once, before printing, into a
small finite role set: Black's two (`COMMENT` / `STANDALONE_COMMENT`),
ocamlformat's three, dart_style's four, elm-format's five slots, ours seven,
swift-format's four kinds crossed with a boolean, prettier's and biome's 3×3.
Every one is small, finite and fixed before layout.

So §7.1's premise is not an assumption anyone needs to defend; it is what
production formatters already do. What differs is **what the classifier may
read** and **what happens downstream**. Two of the tools that decide once still
oscillate — prettier and ocamlformat — and they are exactly the two whose
*layout* stage reads positions.

### 10.2 biome and CSharpier are the controlled experiment

This is the strongest external evidence available for anything in this document,
and it was produced by people with no stake in it.

**biome** is prettier's algorithm, prettier's 3×3 role model and prettier's
80-column `Doc` fitter, rebuilt over a trivia-carrying rowan CST. **CSharpier** is
the same for C# over Roslyn trivia. Both were written by people reading prettier
closely.

In both, the positional reads simply *vanish*. biome counts `piece.is_newline()`
over trivia pieces where prettier reads `options.originalText` and node offsets to
answer the identical question — and the barrier appears without anyone setting
out to design one.

Holding algorithm, role model and fitter fixed and varying only the substrate
isolates the variable:

> prettier's instability is not caused by its algorithm, its role model, or its
> fitter. It is caused by its substrate forcing positional reconstruction.

That is a better argument than any count of an issue tracker. It also states
exactly what a formatter like ours is for: we are in prettier's substrate
position and cannot leave it, because reusing the compiler's parser is the whole
point — so the barrier has to be *built*.

### 10.3 ocamlformat ships an iteration instead of an argument

ocamlformat decides once, and its printer still reads `Source.begins_line` and
`Source.empty_line_between`. So `Translation_unit.ml` does not format the file;
it formats it repeatedly. The comment above the loop is `(* iterate until
formatting stabilizes *)`, bounded by a user-facing `--max-iters`, default
**10**, after which it emits `BUG: formatting did not stabilize after %i
iterations`. Comment preservation is re-checked on every iteration.

This is the most direct external evidence that the instability class of §3.2 is
real, general, and unsolved: a mature, widely used formatter's shipped answer is
*run it up to ten times and report a bug if it still moves.*

### 10.4 gofmt has the strongest corpus gate in the survey, and an exemption inside it

gofmt's printer has no fitter at all; layout is maximally author-driven. But
`writeCommentPrefix` decides same-line-vs-own-line from `pos.Line == p.last.Line`,
blank lines from `pos.Line - p.last.Line`, and — for a comment before a `case`
label — *which block the comment belongs to* from `pos.Column == next.Column`, all
inside the emission loop. A maintainer's diagnosis on the resulting issue states
§3.2 in one sentence:

> "When the next token is on the same line as the comment, this appends a space
> character instead of `\t`. **However, the next token is not necessarily on the
> same line after formatting.**"

`cmd/gofmt/long_test.go` re-formats **every `.go` file under `GOROOT`** and
asserts the fixed point. That is a corpus-scale sweep, larger than rustfmt's
400-file gate and larger than any hand-written fixture suite. It works: it caught
the bug on a file in Go's own tree. But the bug is architectural, not local, and
what has shipped since 2018 is an exemption *inside the gate*:

```go
if !bytes.Equal(b1.Bytes(), b2.Bytes()) {
    // A known instance of gofmt not being idempotent (see Issue #24472)
    if strings.HasSuffix(filename, "issue22662.go") {
        t.Log("known gofmt idempotency bug (Issue #24472)")
        return
    }
    t.Errorf("gofmt %s not idempotent", filename)
}
```

[golang/go#24472](https://github.com/golang/go/issues/24472) is open, labeled `NeedsInvestigation`, seven and a half years
old, and the exempted file still fails when replayed on go1.25.1 — as does
[golang/go#73958](https://github.com/golang/go/issues/73958) (2025), whose second pass *invents* a bare `//` line. Separately,
`go/printer`'s own fixture suite opts out per file, because idempotency "is very
difficult to achieve in general", and `comments.input` is one of the three files
that opt out. Reducing that fixture gives a seven-line witness of the same root
cause with a different symptom:

```go
func _() {
	var a = []int{1, 2, // c
	}
	_ = a
}
```

Pass 1 collapses the literal and emits `var a = []int{1, 2}// c` — no separator, a
spelling gofmt will not produce from scratch. Pass 2 sees the comment on a row it
did not occupy in the input, takes the same-line branch, and inserts the space;
pass 3 is stable.

This is §7.6's argument made by the Go project against itself, and it is the
sharpest form of the thesis available anywhere in the survey: **the corpus gate is
not the missing piece.** gofmt has a bigger one than we do. What it does not have
is an architecture in which the answer cannot go stale — so the gate finds the
instance, the instance resists fixing, and the file gets an exemption.

### 10.5 The barrier is necessary and demonstrably not sufficient

Mining the full histories of the five barrier-holding tools we cloned turns up
idempotency fixes in four of them, two within six weeks of the survey date:

| tool | idempotency fixes | comment-related | latest | fixed-point gate |
|---|---|---|---|---|
| topiary | 8 (OCaml ×6, Nickel ×2) | 0 | 2026-07-20 | runtime, default on |
| Black | 9 | 3 | 2026-07-21 | runtime, default on |
| biome | 4 (all embedded-language) | 1 | 2026-04-10 | none |
| CSharpier | 2, user-reported | 0 | 2024 | none |
| swift-format | 1 — *a feature removal* | 1 (the removal itself) | 2020-01-30 | fixtures |

Topiary's two 2026 fixes are §3.2 verbatim — "the type ascription **collapsed onto
one line, which flipped the body's indentation between runs**" — and they show
where the obligation went: topiary's engine cannot have the bug, so it lives in
the per-language query files instead, for four years. Black's is worse and better
documented: omitting optional parentheses "**re-parents the comment onto a
different leaf after the next parse**", changing the split on a second pass, over
three accumulated issues, in a tool that runs `assert_stable` on every format.
Biome's four are all at *composition boundaries*, where one barriered formatter's
output becomes another's input.

Read that last row of the table together with §6.4: four tools have the barrier
and no coverage argument, all four still ship the bug, and three compensate with
a runtime check. Black's case is additionally the shape §7 does not cover — the
classifier answering differently because its *anchor* moved, not its rows — which
is §8.

**And elm-format, from the other direction.** It sits on rung A1 — named comment
slots, no source positions anywhere for a layout rule to misread — and ships
§3.2's mechanism anyway: for a pipeline whose last step forces a multi-line
block, it produces two different outputs for semantically identical code,
differing only in how the author happened to break the source lines. We found it
because every generated cell is diffed against elm-format, gated against a
**reviewed baseline** rather than treated as an oracle — the two tools diverge on
purpose in 34 cataloged places, an unregistered divergence fails, and a
registered one that *disappears* fails too. We reported it as
[elm-format#842](https://github.com/avh4/elm-format/issues/842), where the
discussion established what the divergence itself had not shown: running
elm-format again on the first output yields the second, so the first output is
**not a fixed point**. Either side of a differential comparison can be the wrong
one, and once it was.

### 10.6 swift-format is the control condition

This is the survey's single most useful external datapoint, because each stage of
it is a stage of this document's argument.

**The rule.** `BlankLineBetweenMembers` shipped in swift-format's initial
implementation (2019-07-10): at least one blank line between each member of a
type. It is a phase-1 rule — it rewrites the syntax tree before the Oppen printer
runs — and it decides whether to insert the blank line from an
`isSingleLine(includingLeadingComment:sourceLocationConverter:)` helper whose body
ends by comparing the start and end **line numbers of the input**. That is "did
the author span rows", read off the source, consumed by a layout decision — §3.2's
mechanism exactly, in a tool that otherwise holds the barrier cleanly (every
line-number read in `PrettyPrint.swift` is `outputBuffer.lineNumber`, the line
being *written*). The rule is the one place the barrier was broken, and
swift-format broke it where we did (§6.3): not in the printer, but in a pass that
runs before it and reasons about what the printer will do.

**Two attempts to patch it, both about comments.** The failures did not present
as "non-idempotent" but as comment bugs, which is why they were patched locally
twice — 2019-11-12 and 2019-11-20, eight days apart — before the class was
recognized. The second commit message is this document's §3 in another language:

> "When we were doing the is-single-line check, we weren't considering that the
> first comment in the trivia might precede any newlines in that trivia,
> **meaning it's an end-of-line comment for a previous line, not a line comment
> that we should consider part of the current decl.**"

**Then the class is named, and the rule is deleted.** 2020-01-30 removes 149
lines of rule and 365 lines of tests:

> **Remove BlankLineBetweenMembers rule because it creates non-idempotent
> behavior.**
>
> "The rule is unfortunately **based on the trivia before the pretty printing
> pass, which means it decides single-line-ness based on the input which may be
> incorrect. The single-line-ness must be based on the source *after* pretty
> printing**, so it cannot be accomplished in a phase 1 rule. …
> **In the future, the blank line between members requirement can be better
> implemented using breaks in the pretty printer.**"

The first paragraph is §6.1's sentence, reached independently, six years earlier.
Two days later, "Delete some dead code" removes the `isSingleLine` accessor as
well — a separate and stronger decision, because it makes the question
*unaskable* rather than merely unasked, which is §6.2's remedy arrived at by
review instead of by a type.

**And the correct fix was never performed.** At the survey tip (2026-08-21, six
years and seven months later) there is no `BlankLineBetweenMembers` rule, no
configuration key, and nothing in `PrettyPrint.swift` that inserts a blank line
between members. The break-based reimplementation the commit proposed does not
exist; what swift-format does instead is preserve whatever the author wrote,
clamped by `maximumBlankLines`. The feature was traded for the invariant and the
trade was never revisited. **The principle held, though**, and that is checkable:
of swift-format's 44 rules, exactly three mention `sourceLocationConverter` today
and all three use it to build a diagnostic's location. Not one phase-1 rule reads
an input row to decide layout. Six years, no regression, no allowlist, no
exemption file.

**How to read this.** swift-format is not a counterexample and not a precedent
that dissolves anything here. It is the *control condition*: the principle is
discoverable without the architecture (they found it), finding it does not by
itself yield the feature (they lost it), and holding it by review is possible but
expensive (six years of not re-adding a rule the style guide asks for). The claim
worth making is not that the rule is novel but that **enforcement makes it
affordable**: `Formatter.RenderTree` deletes the *field* rather than the
*feature*, so our analogue of `BlankLineBetweenMembers` — the vertical-space pass
— can exist, because the fact it needs is computed once at the barrier as author
intent and does not go stale. swift-format had to choose between the rule and the
invariant. The point of the barrier being a type is not having to choose.

**§8's anchor, in someone else's tracker.** We ran swift-format 6.3.3 on the
three rules that rewrite tokens. `OrderedImports` reconstructs attachment from row
adjacency and then sorts the row out from under it — a file header above the first
import is carried into the middle of the block when that import sorts down, and
the output is **a fixed point**, so it is §8.3's category exactly: invisible to
every gate in §9.1 and visible only to a reader who knows what the comment was
about. **This one is not a defect, and that is the better result.** It was
reported as swift-format #772 (2024-07-18) and closed three weeks later as
working-as-intended: attaching a leading comment is *required*, because a comment
about the import below it is indistinguishable from a licence header, so "it
seems like just requiring a blank line is the cleanest way forward." The reporter
accepted the blank line. A second project reached the anchor, recognised that the
missing input is *what the comment is about*, and resolved it by making the author
encode the answer in whitespace — the same move we make, since a blank line is the
only run boundary our import handling has either. That is stronger evidence for §8
than a bug would have been.

`FullyIndirectEnum` is clean. `NoCasesWithOnlyFallthrough` **deletes a comment**.
The rule merges a case whose only statement is `fallthrough` into the case below
it, and there are three slots a comment can occupy across that merge. Run
6.3.3 on each (`swift-format format`, no configuration):

```swift
1. own-line, above the absorbed case      the merge is suppressed

   // lead on 1                            // lead on 1
   case 1:                        ──►      case 1:
     fallthrough                             fallthrough
   case 2:                                 case 2:
     print("hi")                             print("hi")

2. own-line, above the surviving case     kept, but re-anchored: it now
                                          reads as a note about `case 1`
   case 1:
     fallthrough                  ──►      // lead on 2
   // lead on 2                            case 1, 2:
   case 2:                                   print("hi")
     print("hi")

3. same-line, trailing the absorbed case  gone

   case 1:  // trail on 1         ──►      case 1, 2:
     fallthrough                             print("hi")
   case 2:
     print("hi")
```

The rule *is* guarded against comments — the first slot suppresses the merge —
but on two slots of three, and each unguarded one fails differently: the second
keeps the comment and moves what it is about, the third does not keep it at all.
Both guards open with `.drop(while: { !$0.isNewline })`, which discards exactly
the same-line fragment the lost comment lives in. That is §4's failure mode in a
single rule: attachment decided per-slot at each point of use rather than once
for every comment, so a slot nobody enumerated has no answer, and "no answer"
renders as nothing. There was no tracker entry; we filed
[swift-format #1274](https://github.com/swiftlang/swift-format/issues/1274)
(2026-08-25), open as of writing.

### 10.7 Runs, independently corroborated

§7 spends a long time on runs of comments. Four of the surveyed tools model runs
explicitly and three have shipped run bugs.

dart_style's `CommentSequence` is documented as *n* comments and *n+1* newline
counts — §7.3's boundary counting, arrived at independently. ocamlformat groups
adjacent comments before deciding and decides the group as a unit. gofmt passes
`prev *ast.Comment`, "the previous comment in a group" — **exactly one
neighbor**, which is §7.3's rule. Black's `list_comments` is itself a run scanner
in which only the *first* line of a prefix can be a trailing comment and every
later one is standalone — §7.3 in its most reduced possible form.

And the bugs. Ormolu's 0.1.0.0 changelog fixes **five** comment-idempotence bugs
in a single release, and two of them are run bugs in the project's own words:
comments "picked up as 'continuation' of a series of comments"
([#449](https://github.com/tweag/ormolu/issues/449)) and "different indentation levels in a
comment series" ([#512](https://github.com/tweag/ormolu/issues/512)). rustfmt
[#7019](https://github.com/rust-lang/rustfmt/issues/7019), "Non-idempotency in
consecutive block comment", was filed 2026-08-10 — in a formatter with a
400-file idempotency gate.

That is §7.4's sentence — *a corpus of hand-written fixtures will not contain the
mixed run that breaks it* — coming true twice, independently, in other projects.

### 10.8 The two trackers, read against each other

*rustfmt is in the identical position to ours.* It reuses the production Rust
compiler's parser, whose AST carries no ordinary comments, and recovers them from
**"missing" source snippets** — the raw text between the last emitted byte
position and the next node's span. Its `A-comments` label carried **447 issues,
147 open**; **89 titles report a comment being removed, deleted, eaten or lost**,
29 still open, the oldest from 2019. Twelve titles report non-idempotency,
including [#7019](https://github.com/rust-lang/rustfmt/issues/7019) above and [#6347](https://github.com/rust-lang/rustfmt/issues/6347), "rustfmt forcefully moves trailing comments to
irrelevant code above (and not idempotent either)" — which is §3.2's picture in
Rust. Three more report a comment migrating between owners across an import
reordering ([#5485](https://github.com/rust-lang/rustfmt/issues/5485), [#6241](https://github.com/rust-lang/rustfmt/issues/6241), [#3127](https://github.com/rust-lang/rustfmt/issues/3127)) — §8's class, and the one §9.1 says only an
author-order oracle can see; [#6241](https://github.com/rust-lang/rustfmt/issues/6241)'s reporter notes it changes the code's
meaning.

Two things keep this from being a cheap comparison. The **arrival rate** rather
than the backlog is the signal: roughly forty comment issues a year for eleven
consecutive years, no downward trend, in a formatter that has been mature and
near-universally deployed for most of that period. And **rustfmt is not missing
an idempotency gate** — `src/test/mod.rs` re-formats every file in `tests/target`
and asserts no change, with a floor of 400 files, and additionally formats
rustfmt's own source. That is exactly the gate our hand-written fixture suite
provides. What it does not have is a probe that inserts a comment into every
inter-token gap, or one that varies runs by length and composition.

*prettier shows that keeping the comment is not sufficient.* prettier never
discards a comment, but it must still **attach** each one, and it does so from
source positions: a binary search over child nodes locates the preceding,
enclosing and following node, and a placement of `ownLine` / `endOfLine` /
`remaining` is decided from the author's line structure. That is structurally
§4's role assignment, decided once, before printing. It oscillates anyway,
because prettier reflows: the line structure of the output is not the line
structure the classifier read. Its `area:idempotency` label carried **109 issues,
54 open**, 50 of the 109 mentioning comments; **47 issues carried both
`area:comments` and `area:idempotency`**. The oldest open comment-instability
issues date from **April 2017**. By contrast only 16 prettier titles named comment
*loss*.

The contrast is the point, and it is sharper than either tracker alone:

> A **discarding** parser puts *preservation* at risk — rustfmt loses comments in
> volume, for years. **Keeping** the comment does not buy the fixed point,
> because attachment is still decided from positions that printing invalidates —
> prettier loses almost nothing and oscillates constantly.

The instability class is therefore caused by **positional attachment**, not by
comment discarding. Discarding merely *forces* positional attachment. What
removes the class is deciding once while the positions are still valid, and being
mechanically forbidden to look again.

### 10.9 The exempt-rather-than-fix reflex is general

Beside gofmt's filename exemption: Black keeps an expected-*failure* fixture set
"with the unstable formattings" and ships `--unstable` as a release channel;
swift-format deletes the rule; ocamlformat iterates ten times; topiary reserves an
exit code. Five projects, five ways of declining a fix, each an implicit judgment
that the class is architectural rather than local.

Runtime fixed-point checking is itself a recognized pattern — four of the fourteen
ship one. ormolu offers `--check-idempotence`; ocamlformat iterates; topiary
checks by default (`--skip-idempotence` is the opt-out, with its own exit code);
Black checks by default in `--safe` mode, alongside an AST equivalence check. Its
source names the failure mode precisely:

> "We shouldn't call `format_str()` here, because that formats the string twice
> and may hide a bug where we **bounce back and forth between two versions**."

Three of those four tools *have* the barrier and check anyway.

---

## 11. What generalizes

**Decide once, behind an enforced barrier** is a pattern for any pass whose *own
output destroys the evidence its input decisions were made from* — nothing in it
is specific to formatters, or to comments. Two components do the work, and they
are separable: the decision must be recorded as a value (not recomputed on
demand), and the ban on re-deriving it must be mechanically checked rather than
documented. The second is what turns "we were careful" into "the mistake is
unrepresentable".

Two limits, both visible in §10. **A barrier protects one pass, not a pipeline of
them.** Where one formatter's output becomes another's input — a snippet of one
language embedded in a file written in another — the outer pass decides from rows
the inner pass just wrote rather than from rows the author typed, and the premise
that made deciding once safe is gone. All four of the idempotency fixes biome has
shipped are at such a seam.

**And a barrier relocates the obligation rather than removing it.** Topiary's
engine cannot have the bug, so it lives in the per-language query files instead,
for four years and counting. So where the obligation lands is the whole question:
ours lands on one function that always returns one of seven answers — small
enough that §7 can be an argument about all of it. A growing set of declarative
queries admits no such argument.

---

## Sources

Every formatter named in this document, with the commit the survey behind §10 was
read at (2026-08-13 → 2026-08-23) and the code this document actually cites. They
are live repositories: expect the file paths to have moved, and re-read at the
tip rather than trusting a line number here.

| formatter | language | repository | read at | what is cited above |
|---|---|---|---|---|
| [topiary](https://github.com/tweag/topiary) | generic, over tree-sitter | `tweag/topiary` | `a307aee` | the engine's atom resolution; the per-language `.scm` query files where the obligation ended up (§10.5) |
| [elm-format](https://github.com/avh4/elm-format) | Elm | `avh4/elm-format` | `e7e5da37` | `AST/V0_16.hs` — named comment slots, no positions anywhere (§1, §10) |
| [dart_style](https://github.com/dart-lang/dart_style) | Dart | `dart-lang/dart_style` | `39edc2d9` | the positionless `Piece` IR; `CommentSequence`, *n* comments and *n+1* newline counts (§10.7) |
| [swift-format](https://github.com/swiftlang/swift-format) | Swift | `swiftlang/swift-format` | `9c9a9fa` | `SyntaxProtocol+Convenience.swift`, `PrettyPrint.swift`, the removed `BlankLineBetweenMembers` rule, `OrderedImports.swift` (§10.6). Also **run**, 6.3.3, 2026-08-24 — the only surveyed tool we executed as well as read |
| [google-java-format](https://github.com/google/google-java-format) | Java | `google/google-java-format` | `b291d95` | the `Doc` fitter and the two positional reads in the op-builder (§10) |
| [CSharpier](https://github.com/belav/csharpier) | C# | `belav/csharpier` | `c8ac0cb` | the [Roslyn](https://github.com/dotnet/roslyn)-trivia walk; half of the controlled experiment (§10.2) |
| [biome](https://github.com/biomejs/biome) | JS/TS/CSS | `biomejs/biome` | `7a111ba7` | `piece.is_newline()` over [rowan](https://github.com/rust-analyzer/rowan) trivia, where prettier reads `originalText` (§10.2) |
| [Black](https://github.com/psf/black) | Python | `psf/black` | `8947c48` | `list_comments`, the comment-placement docstring quoted in §3.3, `assert_stable` and the "bounce back and forth" comment (§10.9) |
| [prettier](https://github.com/prettier/prettier) | JS/TS and more | `prettier/prettier` | `d9969c573` | `src/main/comments/attach.js`; the 19 of 73 print modules that read the source (§10.2, §10.8) |
| [ocamlformat](https://github.com/ocaml-ppx/ocamlformat) | OCaml | `ocaml-ppx/ocamlformat` | `20c45431` | `Cmts.init`; `Source.begins_line` / `empty_line_between` in the printer; `Translation_unit.ml`'s `--max-iters` loop (§10.3) |
| [ormolu](https://github.com/tweag/ormolu) | Haskell | `tweag/ormolu` | `d5727c0` | `spitPrecedingComment`, `--check-idempotence`, the five 0.1.0.0 comment fixes (§10.7, §10.9) |
| [gofmt / `go/printer`](https://github.com/golang/go/tree/master/src/go/printer) | Go | `golang/go` | `c97cfcb37f` | `writeCommentPrefix`; `cmd/gofmt/long_test.go` and its exemption; `go/printer/printer_test.go`'s per-file opt-out (§10.4) |
| [rustfmt](https://github.com/rust-lang/rustfmt) | Rust | `rust-lang/rustfmt` | `1191d91d` | `src/missed_spans.rs` (`format_missing`); `src/test/mod.rs`'s 400-file gate (§10.8) |
| [zig fmt](https://github.com/ziglang/zig/blob/master/lib/std/zig/Ast/Render.zig) | Zig | `ziglang/zig` | `738d2be9d6` | `std.zig.Ast.Render`, which substring-searches `tree.source` for `"//"` at render time (§1) |

**Front ends named in passing.** [tree-sitter](https://github.com/tree-sitter/tree-sitter)
(A0), [Roslyn](https://github.com/dotnet/roslyn) and
[rowan](https://github.com/rust-analyzer/rowan) (A2 trivia). Black's A2 rung is
its vendored `blib2to3` fork, in the Black repository above, which carries a
comment in a leaf's `prefix` *string* rather than as a typed node.

**The formatter this document is about.** `gren-format-lib` is the library (this
repository); `gren-format` is the CLI that wraps it. The parser both are built on
— the A3 front end whose output §3.1 shows — is Gren's
[`compiler-common`](https://github.com/gren-lang/compiler-common), used by the
[Gren compiler](https://github.com/gren-lang/compiler) itself. The upstream
parser bugs that make up our entire residual non-idempotency are filed there and
listed in [knownLimitations.md](knownLimitations.md).

**Issue trackers.** The counts in §9.1 and §10.8 are frozen pulls dated
2026-08-23. The labels they were pulled from are
[rustfmt `A-comments`](https://github.com/rust-lang/rustfmt/labels/A-comments),
[prettier `area:comments`](https://github.com/prettier/prettier/labels/area%3Acomments)
and [prettier `area:idempotency`](https://github.com/prettier/prettier/labels/area%3Aidempotency).

---

## See also

**In this repository**

- [How gren-format places your comments](commentHandling.md) — the normative
  rules C1–C7, with a verified before/after for each. Start here if you want to
  know what the formatter actually does.
- [The comment algorithm](commentAlgorithm.md) — the implementation: the
  attachment fold's four phases, the whole-tree repairs, the three state machines,
  the coverage argument in full, and the gate-by-gate coverage table.
- [How the formatter works](howItWorks.md) — the pipeline, conceptually.
- [Testing gates](testing.md) — every gate, what failure class it aims at, and how
  to run it.
- [What lowering to a RenderNode costs](renderTreeMemory.md) — the measurement
  behind §6.2's second tree.
- [Comparison with elm-format](elmFormatComparison.md) — the 34 cataloged
  divergences, each with a reason and a fixture.
- [Known limitations](knownLimitations.md) — including the upstream parser bugs
  that make up the entire residual non-idempotency.
