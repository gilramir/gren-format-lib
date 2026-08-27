# Putting the comments back

*How `gren-format` places comments when the parser it uses throws them away:
what the problem is, what we built, what it does not cover, and what fourteen
other formatters do about the same thing.*

**TL;DR.** Comment placement is decided from source positions, and
formatting invalidates them. We decide each placement once, as a role,
and hand the renderer a tree with no positions on it, so a stale read
is a compile error.  That does not end the bugs; it confines them to the
passes above the line, and every comment-placement decision to one
classifier function, small enough to argue about in full. Of the fourteen
formatters we surveyed, every other one that has to reconstruct comment
attachment from source positions lacks that barrier, and that is where the
survey's instabilities that resist a local fix cluster: ocamlformat iterates,
gofmt exempts. The barrier does not make a tool immune, and three tools that
have it live with an instability too (§9.9); what it buys is keeping the
bugs small enough to argue about.

## Table of contents

- [1. Introduction](#1-introduction)
- [2. Five kinds of front end, and no parser of our own](#2-five-kinds-of-front-end-and-no-parser-of-our-own)
- [3. The problem, concretely](#3-the-problem-concretely)
- [4. Decide once — the role, not the position](#4-decide-once--the-role-not-the-position)
- [5. The position barrier, and how to make it a type](#5-the-position-barrier-and-how-to-make-it-a-type)
- [6. Why forty comments in a row is not forty cases](#6-why-forty-comments-in-a-row-is-not-forty-cases)
- [7. The anchor can move out under you](#7-the-anchor-can-move-out-under-you)
- [8. What the barrier does not reach](#8-what-the-barrier-does-not-reach)
- [9. What fourteen other formatters do](#9-what-fourteen-other-formatters-do)
- [10. Summary](#10-summary)

---

## 1. Introduction

`gren-format` has no parser of its own. It uses the Gren compiler's parser,
which, like most compiler parsers, throws comments away. That is a choice: a
formatter that shares the compiler's parser can never drift from the language,
and we would rather pay for comment placement than for a second parser (§2).
So the formatter is handed a syntax tree with no comments in it, plus a
separate list of the comments, each tagged with nothing but a `(row, column)`
pair, and it has to work out where each one belongs and put it back. The
difficulty is easy to state:

> **Placement is decided from source positions, and formatting invalidates the
> positions the placement was decided from.**

Get that wrong and the output is not idempotent: run the formatter on a file
it just formatted and the file changes again. Keeping every comment is easy
and placing each beside the right code is harder, but neither gives
idempotency; it has to be engineered in on its own (§3.3), and the need is not
peculiar to author-driven layout, since formatters that refit to a page width
have the same problem (§3.4).

Our answer is the **position barrier**: a line across the pipeline that source
positions do not cross. Above the line, in the logical stage, code reads the
author's positions freely, and each comment's placement is decided there,
once, as one of seven *roles*. Below the line, in the stage that picks line
breaks and writes bytes, positions are not off-limits but gone: the renderer
works on a data structure with every position field removed, and the type
checker, not review, enforces that (§5).

**What crosses the barrier is a role, not a position.** A role describes
placement relative to the comment's neighbours (`TrailsPrevious`: "glue onto
the previous sibling's last rendered line"), a direction the renderer can
follow however the lines move. The classifier is written to one rule, **a role
must re-derive to itself**: the comment renders at the position its placement
was decided from, so the second run asks the same question of the same row (§4).

The barrier does not guarantee idempotency; it **localizes** the risk. With
nothing below the line reading a position, two runs can disagree only if
something above it answers differently the second time, chiefly the
role classifier. Before the barrier that risk was spread over every layout
rule; after it, the comment decision sits in one place, and §6 argues that the
one place is right for a run of comments of any length and any mix of kinds.

What the barrier leaves behind is real, and we hit all of it. A role is not a
row: gluing `TrailsPrevious` onto a line still needs to know that a line is
there, which is render state and can be wrong (§5.3). Other passes above the
line read positions too, and some of them *move* positions that were already
decided from (§5.3). The formatter rewrites code as well as laying it out, and
sorting an import list moves the code a comment was attached to (§7). And some
bugs break no property at all: the output is stable, complete, and wrong, and
no property gate can see it (§8).

§9 puts all of this beside fourteen other formatters. The barrier is neither
our idea nor rare; what is new is having it while also reconstructing
attachment from a list of positions, which no other tool that has to do that
reconstruction does.

---

## 2. Five kinds of front end, and no parser of our own

Formatters are usually compared by their layout algorithm: whether they fit
lines to a page width and how they pick where to break. For comments, that is not
the variable that matters. What matters is **what the front end hands the
formatter**, because that decides whether the formatter has to work out where a
comment goes at all. We read the source of fourteen production formatters
besides our own (§9). They sort into five levels, ordered by how much of the
work the parser has already done.

| level | what arrives | who |
|---|---|---|
| **A0** | comments are ordinary nodes of a concrete syntax tree; the formatter needs no comment concept at all | [topiary](https://github.com/tweag/topiary) (over [tree-sitter](https://github.com/tree-sitter/tree-sitter)) |
| **A1** | named, typed comment slots on AST nodes, and no source positions anywhere | [elm-format](https://github.com/avh4/elm-format) |
| **A2** | *trivia* hanging off tokens | [dart_style](https://github.com/dart-lang/dart_style), [google-java-format](https://github.com/google/google-java-format), [swift-format](https://github.com/swiftlang/swift-format), [CSharpier](https://github.com/belav/csharpier), [biome](https://github.com/biomejs/biome), [Black](https://github.com/psf/black) |
| **A3** | a **comment-free AST**, beside a flat source-ordered list of located comments | **gren-format**, [ormolu](https://github.com/tweag/ormolu), [ocamlformat](https://github.com/ocaml-ppx/ocamlformat), [gofmt](https://github.com/golang/go/tree/master/src/go/printer), [prettier](https://github.com/prettier/prettier) |
| **A4** | nothing; comments are recovered by re-reading the raw source between two nodes' byte offsets | [rustfmt](https://github.com/rust-lang/rustfmt), [zig fmt](https://github.com/ziglang/zig/blob/master/lib/std/zig/Ast/Render.zig) |

**Trivia** is the compiler-writer's word for the bytes between two tokens that
the grammar does not care about: whitespace, newlines and comments. A
trivia-carrying front end keeps them, hung off the token they sit beside and
split into *leading* trivia (before the token) and *trailing* trivia (after
it). In `foo(1);  // why`, the comment is trailing trivia of the `;`. It is a
field of a token in the tree from the moment the lexer finishes, so no later
stage has to work out where it belongs.

On **A0 through A2** the comment is already attached to something before the
formatter starts. The front end answered "which code is this comment beside?"
while it was still looking at the input, and the answer travels with the tree.
Placement is then a question about a tree the comment is already in, and the
problem this document talks about does not arise. That does not make those tools
immune to instability; every one of the five whose history we mined has
shipped idempotency fixes anyway (§9.5). And the blind spot in §8 applies to every level equally.

On **A3 and A4** the comments are not attached to the tree, so the attachment has
to be reconstructed. The only evidence available is source positions, which are
exactly the thing formatting destroys. Every instability story in §9 lives on
these two levels.

A3 is not an exotic place to be. It is simply **what a production compiler's
parser hands a formatter**, and five of the fifteen tools surveyed are there.

**`gren-format` is on A3 on purpose.** It calls the Gren compiler's in-development
parser, the same front end that compiles the code being formatted. That buys
one guarantee, and it is the reason for everything that follows: **the formatter
can never drift from the language.** It accepts exactly what the compiler
accepts, this release and every future one. A formatter with its own parse
is a bug waiting to happen, and a language that is still moving would have a
compiler whose development outruns a formatter's independent parser.

The price is that a compiler's parser discards comments, because a compiler
does not need them. We pay it, because a formatter that cannot disagree with
the compiler is worth more to us than easy comment placement. Had the compiler
already carried trivia we would have taken that instead.

---

## 3. The problem, concretely

### 3.1 What the formatter is actually handed

Two things. The first is `Src.Module`: an ordinary AST with source ranges on
its nodes and **no ordinary comment anywhere**. There is nowhere in the type
for a `--` or `{- -}` comment to be. (The one comment the parser does keep is
a declaration's doc comment, `{-| … -}`, because the compiler needs it for
documentation; it arrives on the declaration it documents and never has to be
placed.) The second is the parse `Context`, which carries a flat,
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

the formatter receives an AST for `sizes = [1, 2]`. Drawn as a tree, with
each node's source range as `@row:col-row:col`, it is:

```
module  name='S'  @1:8-1:9, effects='none'
    ├── exports
    │   └── [0]: lower  name='sizes'  @1:20-1:25
    └── values
        └── [0]  @4:1-7:6
            └── value  name='sizes'  @4:1-4:6
                └── body: array  @5:5-7:6
                    └── items
                        ├── [0]: number  @5:7-5:8
                        │   └── value: int  value=1
                        └── [1]: number  @6:7-6:8
                            └── value: int  value=2
```

There we see the `array` lives from row 5 to row 7.
Separately, the Context has this:

```json
{ "comments": [ { "type": "line", "value": " one",
                  "start": { "row": 5, "col": 9 },
                  "end":   { "row": 5, "col": 15 } } ] }
```

The comment lives on row 5.
The comment does not know it is inside an array. The array does not know a
comment exists. The only thing connecting them is a `(row, column)` pair across
two data structures.

### 3.2 Reattachment versus formatting

That is a reattachment problem, and reattachment interacts badly with the one
thing a formatter does -- change source code positions.
Here is an example of an old bug that was fixed.
The author wrote a line comment trailing a two-token body:

```
you wrote:              format¹ produced:        format² produces:

v =                     v =                      v =
    foo bar -- c            foo bar                  foo bar
                            -- c                 -- c
```

The first format read the comment's position, the body's last row, and decided
"trails the body". But the code that turns "trails" into output could not find
the row to glue onto for a body of more than one token (a mistake since fixed),
so it dropped the comment to a line of its own at
the body's indent. The second format read *that* file. The comment is now on
the row after the declaration's last row, and the rule for a comment there is
different: it is seen as detached from the body, so it goes to column 1.
Same rules, same comment, a different row, a different answer.
The file settles there after two formats instead of one.

**A large share of the comment bugs this project has fixed are a variation on
this**, and in the replay of §8 it is the class the property gates see most of.

### 3.3 Three properties, gated separately

Preserving comments and placing them correctly, by themselves, do not provide
idempotency; that needs a separate guarantee --- that placement decided from the output
of the formatter equals placement decided from the input to the formatter,
and it has to be engineered in.

1. **Preservation.** Every input comment appears exactly once in the output,
   text and kind unchanged. (Continuation rows of a multi-line block comment
   are re-indented; that is layout, not text.)
2. **Faithful placement.** The comment lands beside the code the author wrote
   it beside. This is specified as
   [C1–C7](commentHandling.md#the-seven-rules-at-a-glance): the first two
   rules decide *which code a comment attaches to*, the other five decide *how
   it is laid out*. Attachment is settled first; layout works with whatever it
   is given.
3. **Idempotency.** `format(format(x)) = format(x)`, byte for byte.

Preserving the comment in the output is a very low bar for a formatter.
It does not give you faithful placement of the comment, which is why 1 and 2 are
separate. The Black formatter preserves every comment (they ride in a leaf's whitespace prefix)
and still says this in the docstring of the function that has to place them:

> "The sad consequence for us though is that comments don't 'belong' anywhere.
> … We simply don't know what the correct parent should be."

With a trivia-preserving front end, properties 1 and 2 come nearly free.
Property 3 is where the architectures part ways. With trivia, a comment's slot
in the tree is stable across formatting because it was never derived from
layout in the first place. In `gren-format` it *is* derived from layout,
and layout is again the output:

```
run¹   placement¹ = place(code, positions)            -- the author's positions
       output¹    = layout(code, placement¹)

run²   placement² = place(code, positionsOf(output¹)) -- no longer the author's
       output²    = layout(code, placement²)
```

Idempotency is `output² = output¹`, and it follows as soon as
`placement² = placement¹`, that is, as soon as `place` gives the same answer
about the formatter's own positions as it gave about the author's. Nothing in
the picture makes those two agree by construction. Something has to, and for us
it is one rule on the classifier: **a role must re-derive to itself** (§4),
where a **role** is how that comment rides in the code, relative to its
neighbors.

Every comment is placed so that the position its placement was decided from is
the position it renders at. Run 2 then asks the same question of the same
position and gets the same answer.

Note what is held fixed in the equation: `code` is the same in both runs. §7 is about what
happens when it is not.

### 3.4 Is author-driven layout the culprit?

`gren-format` has **no page width**. Whether a construct renders on one line or
several is decided from whether the author wrote it on one line or several
(and for a few constructs the multi-line form is mandatory). There is no search
for the prettiest arrangement and no re-fitting to a column limit.

It is natural to wonder whether that is what causes the problem in §3.2, and
whether formatters that recompute layout from scratch to fit a page width
escape it. The survey in §9 says no. prettier is exactly that kind of
formatter and has had the problem for nine years. gofmt has no page-width
fitter at all and is not stable either. The variable is whether *placement*
reads source positions, not whether *layout* is author-driven.

What author-driven layout does do is make the author's positions more tempting
to read. The (row, column) data are right there, and they are usually correct.
That is why the ban in §5 has to be mechanical rather than advisory.

### 3.5 The pipeline

Here is the pipeline, with the position barrier dividing it.

![The eight pipeline steps, and where the position barrier falls](diagrams/position-barrier.png)

**Step 2** is where every placement decision is made; §4 argues for making all of
them there, once, rather than at each point of use. **Step 6** is the barrier; §5
describes it.

**Steps 4 and 5 are shaded like step 2** because they are the same kind of step:
all three read the author's positions. The difference is that 4 and 5 run
*after* the placement decision, still on the legal side of the line, and
nothing stops them from moving a position that step 2 already decided from.
Two sections of this document are about that: §5.3, where the
barrier turns out not to cover those gaps, and §7, where sorting moves the code a
comment was attached to.

**Step 3 is blue with an orange border** because it is half of each. Its
decisions come from a comment's role and its own text, never from a (row,
column) position. But the bookkeeping that records those decisions reads
and rewrites the row ranges that later steps go on to read, and that
fixes its place in the order.  Step 4 renumbers rows when it moves an
import; step 3 writes a range derived from the numbering step 4 has not
yet changed.

---

## 4. Decide once — the role, not the position

Every comment's placement is decided **exactly once**, in the logical stage, while
every position in the tree is still the author's original position.
For most of this project's life it was the other way round: the attachment of
a comment was re-derived at each point of use, in at least
eight separate places, at render time. The answer is now stored on the comment
leaf as a `CommentRole`:

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

One of those needs a word of explanation. Almost none of the punctuation in a
Gren file survives parsing as a node with a position: `=`, `:`, `,`, `|`, `->`
and keywords like `then` and `in` are all gone by the time the formatter sees
the tree. Only binary operators and brackets keep a recorded position. Those missing
tokens are what we call *unrecorded separators*, and they are a problem for
comments, because `x {- c -} = y` and `x = {- c -} y` arrive as the same three
facts: where `x` ends, where the comment is, where `y` starts. The formatter
cannot tell the two spellings apart, and it must not look at whitespace width
(formatting must not depend on your spacing). So one of them has to move. The
rule is that the comment lands *after* the separator, and `LeadsNext` is that
rule recorded as a role.

**The role is what the renderer gets instead of a position.** A `(row, column)`
pair says where the comment *was*, in a file that is about to be laid out
again. The role says how it *fits*: which neighbor it belongs to, and how it
attaches. Every decision that used to compare the comment's position against
another node's now reads one of these seven values instead. The renderer reads
the stored answer and never recomputes it.

Here is one Gren module that exercises all seven. The role column is the
formatter's own `--lpt` output for this file, not a hand annotation:

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

Each comment's role is decided by answering, in order:
* **which declaration** owns it
* **how deep** inside that declaration's subtree it belongs
* **which gap** between siblings it falls in
* **which role** it takes at that gap.

All four are answered while the positions are still the author's, and none is
revisited. The full procedure is in
[commentAlgorithm.md §4](commentAlgorithm.md#4-stage-1--attachment).

Every role has to survive being re-derived from the formatter's own output.
That is the one rule the classifier is written to satisfy:

> **A role must re-derive to itself**: the position a comment's placement is
> decided from is the position that comment renders at.

If the classifier says "this comment trails the item before it" because the
author wrote it on that item's last row, then the comment must come out on
that item's last row. The second run asks the same question of the same row,
gets the same answer, and nothing moves.

**When the output does not honour the role, the rule is not the thing to
change.** §3.2's example is the plain case: "trails the body" was the right
answer, and the fix went to the code that failed to put the comment on the
body's row. Sometimes the role genuinely cannot render at the row it was decided
from. A multi-line block comment trailing a declaration's last row:

```
you wrote:              format¹ produced:        format² produces:

v =                     v =                      v =
    fn a b {- c             fn a b                   fn a b
   second -}                {- c                 {- c
                               second -}            second -}
```

is decided from that row, but the renderer gives a comment that spans rows a
line of its own, so it comes out *below* the declaration, and a reparse reads a
comment below a declaration as detached. The rule is already right about where
the author put it; the trouble is that the output puts it somewhere else. The
fix is to **make the first format build the tree the second format would
build**. The comment is going to end up below the declaration, so the first
pass puts it there itself, at column 1, which is what a reparse would produce.
Two passes do this once the last comment is in, over the whole tree, because a
comment's neighbours are not known until then. They run at the tail of the
comment pass itself, before sorting or vertical spacing touches anything
([commentAlgorithm.md §4.6](commentAlgorithm.md#46-repairs-that-need-the-finished-tree)).

**Every comment gets a role; there is no "don't know".**
That totality is what makes the argument in §6 possible. Because the answer is
always exactly one of seven values, a file with *n* comments carries *n*
independent labels, not a combined state that depends on how the comments sit
relative to one another. The alternative is what most people expect a comment
placer to look like inside: a case analysis over *configurations*, a space that
grows with the number of comments in one place and always has a corner nobody
enumerated. There is no such space here.

Deciding once is not the unusual part, incidentally. Ten of the fifteen tools
surveyed in §9 do it, with role sets of two to nine members. What differs is
what the classifier is allowed to read, and what the downstream stages may do
with its answer.

---

## 5. The position barrier, and how to make it a type

### 5.1 The rule

> **No code in the rendering stage may read a source row or position to make a
> layout or comment-placement decision.**

This splits the questions the formatter asks into two groups. **Above
the barrier**, in the logical stage, where every position in the tree is still
the author's:

| question | what it reads |
|---|---|
| which declaration owns this comment, how deep in its subtree, in which gap between siblings, and with which role (§4) | the comment's `(row, col)` against each node's cached range |
| did the author write this construct across rows? (the `forceVertical` flag on a call, an `if` condition, a record literal) | the node's own first and last row |
| did the author write this node on the same row as the item before it? | both nodes' rows |
| did the author break this signature at a `->`, or spread this union's variants over several lines? | the children's rows |
| does this node cover any source row at all, or was it synthesized? | whether the node has a range |
| how many blank lines go between two top-level items, and where does one import group end and the next begin? | the last row of one subtree against the first row of the next |

**Below the barrier**, in the rendering stage, where no position exists to read:

| question | what it reads |
|---|---|
| where does this comment go? | the `CommentRole` stored on the comment leaf |
| is this construct vertical? | `isSingleLine`, applied to a `Box` that has already been built |
| can code follow this comment on the same line? | the comment's own **text** (the three kinds in §6.2) |
| any of the author-intent questions above | a boolean that `lower` computed at the barrier: the answer, never the evidence behind it |
| is there a line here to glue onto? | what the renderer has emitted so far (§5.3) |

The second list is everything the renderer is allowed to ask -- its roles
and attributes, not source positions.
That does include "where does this comment go?", which sounds
like a question about source position. It's not. It's "where", relative to
its neighbors. `TrailsPrevious` says *glue onto the previous sibling's last rendered line*,
`TrailsHead` says *onto the container's head*, `LeadsNext` says *the sibling
after an unrecorded separator*. Those are directions a renderer can follow
without source positions.

The author-intent row of questions in the table is important.
The questions that genuinely need the author's rows still get answered. The
renderer is just handed the answer instead of the `(row, column)` evidence,
so it cannot re-derive the answer later against different rows.

### 5.2 Enforcement via a type

The barrier is enforced by the type checker, not by documentation and not by a linter.

The rendering stage does not consume the logical printing tree at all. It
consumes `RenderNode`, a **mirror of the node type with the seven cached position
fields removed**, reachable only through a `lower` function that drops them.
`RenderShape` strips the `Located` payloads off eight shape constructors as
well, so there is no position anywhere in the renderer's view of the tree.

A handful of render-side decisions genuinely needed the author's rows: the
fourth entry in the second table above. `lower` computes them once, at the
barrier, as four booleans:

* `rnSharesRowWithPrevItem`
* `rnHasSourceContent`
* `rnVariantsSpanRows`
* `rnTypeSegmentsBroken`

`RenderShape` is total over the logical shape type, so adding a new
shape does not compile until the lowering maps it.

The previous version of this barrier was a script, and we deleted it. It ran
first in the test suite and grepped the rendering stage for a list of
position-accessor names. It worked, and it was a barrier only until someone
wrote the accessor a different way, or until the list of another module's
names went stale, silently, which it did. It was a weak way of enforcing
the position barrier.

"Be careful not to read positions here" is a comment in a file nobody reads.
"It does not compile" is a property of the system.

The cost is a second tree structure during formatting.
We measured it rather than asserting it, and found it acceptable:
[renderTreeMemory.md](renderTreeMemory.md), which also explains why peak RSS is
the wrong instrument.

### 5.3 What the barrier does not cover

Two gaps, both of which have produced real bugs here. Neither was a reason not
to build the barrier. Both were reasons not to believe that the barrier alone
was enough.

**On the renderer's side of the barrier, a role is not a row.** Turning a role
into output still needs a fact no role can carry: is there a line here to glue
onto? We had a bug where that was not true!
A `TrailsPrevious` comment glues onto the previous item's last line, but
whether that sibling ended on a line this comment may join is a fact about
what the renderer has just emitted. That is legitimately below the barrier,
answered from render state rather than positions, and perfectly possible to
get wrong.

**On the logical side of the barrier, different stages of the pipeline can modify
positions.** Above the barrier, reading source positions is legal and necessary.
But they stop being the author's part-way through the logical stage. Steps 4
and 5 in §3.5 can each move a position that step 2 decided from. A decision
keyed to such a position is stale for the same reason a render-side read is
stale, and the barrier does not touch it, because every step involved sits
above the line. We hit this bug when sorting `import` statements.

This is a bug of the kind that catches many formatters: a pass that runs before
the printer and reasons from rows the pipeline goes on to change. swift-format's
version (§9.6) read the input's rows in a pre-printer pass that the printer then
invalidated; ours read rows that a *later logical pass* moved. It caught us
because of how we had worded the rule. The criterion
in §4 is stated for *roles*. Steps 4 and 5 are position-keyed decisions that
produce no role. The vertical-space pass decided whether a comment was
free-floating by looking at the gap *below* it, and then closed that very gap
itself when it pulled a definition up under its signature. The first format saw
a gap and emitted two blank lines; the second saw none and emitted one. Read
properly, the rule was never about roles:

> **Every** position-keyed decision in the logical stage must be decided from a
> position that this pipeline does not itself change.

### 5.4 What the barrier is actually for

It does not prove idempotency. It **localizes the mechanism**.

With no positions read downstream, the only things that can differ between run
1 and run 2 are the decisions the logical stage makes from positions: the
role-classifier's answer for every comment, and the short list of author-intent
reads in §5.1's first table.
So "is formatting idempotent?" stops
being a question about the whole pipeline and becomes a question about that
list, and for comments about a
single function, one that always returns an answer, and always one of seven.
That is what makes the coverage argument in §6 possible at all. Without it, the
obligation is spread over every layout rule that reads a position: 19 of
prettier's 73 print modules, 14 source reads in ocamlformat's printer.
Section 9 shows the bugs that happen there.

Localizing the mechanism is not enough, and §9.5 is the counterweight: every
barrier-holding tool whose history we mined has shipped idempotency fixes anyway. However, what is left after
the barrier is small enough to argue about and get correct.

---

## 6. Why forty comments in a row is not forty cases

The first question anyone asks about a comment placer is "what happens if I
write forty comments in a row?". Luckily, to keep us from going mad while
testing, that is the wrong question. This section is the **coverage
argument**: the reason every run of comments, of any length and any mix of
kinds, is handled by rules that were written for one. §5.4 and §9.5 refer back
to it by that name.

The claim:

> For any *n* ≥ 0 comments of any kinds in one place, the algorithm assigns
> exactly one placement to each; and a run of *n* reaches no decision that a
> run of two does not.

It rests on three properties of the implementation, plus one boundary it does
not cover (§7). The full version, with the code sites for each, is
[commentAlgorithm.md §8](commentAlgorithm.md#8-why-this-covers-every-run--the-argument-not-the-test-suite).

### 6.1 Placement is prefix-determined, so *n* is never an input

The attachment fold walks the comments in source order, and when comment *k* is
placed, comments *k+1 … n* **are not in the tree** yet. So

```
role(k) = f(code, comments 1 … k−1)
```

and `f` is the same function for every *k*. No branch anywhere in the
attachment module asks how many comments a gap holds, because at the moment
the question is asked the answer is not knowable. *n* enters the algorithm in
exactly one way: as the number of times `f` is applied.

That is why "does it handle a run of 40?" is not a question about runs. It is
the same question as "does it handle the 40th comment in the file", and the
fold does not distinguish the two. Add the classifier's totality from §4 and
the tree after *n* comments carries the same *kind* of state it carried after
one.

### 6.2 The formatter tracks three kinds of comment

The language has two comment syntaxes to place (the doc comment of §3.1 never
needs placing), but every layout question about a comment's kind is answered by
**one test on the comment's own text**, whether it contains a newline, which
distinguishes three shapes (the 3rd one is multi-line):

| | can code follow it on the same line? |
|---|---|
| `-- like this` | no |
| `{- like this -}` on one row | **yes** |
| `{- like this` ⏎ `and this -}` | no |

Nothing else about a comment (its length, its content, its original
indentation) is read by any placement decision. The one other read is layout,
not placement: a multi-row block comment's continuation rows are re-indented
relative to where its opening `{-` was, the re-indentation §3.3 sets aside.
"Any variety of comments" is not an unbounded axis. It is three kinds.

This is the design decision we would defend hardest: **every kind-sensitive
question asks that one thing of the text and nothing else**. The test is
written out at eight sites across five modules, three of them named predicates
in `NodeClassify`, and every one is the same newline check, so the number of
kinds is a fact we can state outright... it's three.

### 6.3 Every local rule reads at most one neighbour

This is the most important of the three properties. Everywhere the members of
a run interact locally, the rule reads one neighbour and no more:

| rule | what it reads |
|---|---|
| "what row is in front of me?" (asked at four sites when placing a comment) | the **previous** member's last row |
| the fold that lays a flow's items out left to right | a six-state summary of the line in front of the next item |
| the scan that finds where a trailing run starts spilling below its declaration | the **previous** member's kind |
| "can this comment ride on a line of code?" | the member's **own** text |

Not one looks two members back, or forward. So a run of comments is a chain of
**boundaries**, the junctions between one thing and the next, and every rule in
the table is a function of exactly one boundary. A run of three comments, A, B
and C, written between two pieces of code has four of them:

```
   code │ comment A │ comment B │ comment C │ code
        ↑           ↑           ↑           ↑
     code→A        A→B         B→C       C→code
```

Two of those four are comment-to-comment boundaries, and with three kinds of
comment there are exactly **nine** of those possible. A run of any size is built
from the same nine types of comment-to-comment pairs.

The formatter never enumerates those nine; each rule sees one side of one
boundary. They are the number a *test* has to reach: a probe that has put every
ordered pair of kinds side by side has exercised every boundary the rules can
tell apart, which is what `--run 2` and `--mix-pairs` together do (§6.5).

### 6.4 Where one neighbor is not enough

Three rules genuinely cannot be decided from one neighbour, because they are
about the run as a whole. Each is written as a **quantifier over the run**, never
as a case analysis over its length:

| rule | the quantifier |
|---|---|
| may the run cross an unrecorded separator? | ∀ members: could this one cross? |
| may a one-line construct stay on one line with this run inside it? | ∀ members: can this one sit mid-line? |
| who owns the run when detaching, sorting, or spacing? | the run is the unit |

A `∀` over a set is still length-independent: folding a predicate over an array
does not care how many elements it has. Nor does it contradict §6.1: each of
these reads the run as it stands in the tree when it is asked, and the
decisions that need the whole run are re-taken over the finished tree once the
last comment is in (§4's tail passes), so the fold itself still never asks
about length. The two boolean rules are also
**monotone**. Adding a member can only turn the answer off, never on, so they can
only make a comment stay where it was written, never move one that was not
already moving.

**Every one of those three was written per-member first, and every one was found
as a bug**. The per-member version is the natural thing to write, it is correct for a
run of one, and a corpus of hand-written fixtures will not contain the mixed
run that breaks it. §9.7 shows
two other projects learning the same thing independently.

### 6.5 What it looks like

Six comments, three kinds:

```gren
v =
    fn a {- 1 -} {- 2
                    over two rows -} {- 3 -} {- 4
                                                again -} {- 5 -} -- 6
```

The roles, again from the formatter's own `--lpt` output:

| comment | kind | role |
|---|---|---|
| `{- 1 -}` | one-row block | `LeadsLine` |
| `{- 2 … -}` | multi-row block | `TrailsPrevious` |
| `{- 3 -}` | one-row block | `RidesInline` |
| `{- 4 … -}` | multi-row block | `TrailsPrevious` |
| `{- 5 -}` | one-row block | `RidesInline` |
| `-- 6` | line | `TrailsPrevious` |

Member 1 is the odd one. It sits in the gap after the call's last argument,
where the classifier found no row to glue onto and answered `LeadsLine`; but a
one-row block comment renders inline whatever its role, so the role there
records the missing glue row, not a fresh line.

**One caveat.** The premise, that no rule reads more than one neighbour except the
three that quantify over the whole run, is a property that has to be
**maintained**. Nothing enforces it. A fourth all-or-nothing rule discovered
tomorrow gets added to the table in §6.4 and the reasoning carries on. So the
run sweeps do not prove the argument; they test its premise, and we ran them as
a prediction. Probes that insert a run of *two* comments, or of two *different*
kinds, into every inter-token gap found real bugs: 20 findings in one family,
and 1,752 findings (1,718 of them formatter-side) in three bugs. Runs
of three and mixed triples, which §6.3 says reach no new boundary, found
nothing formatter-side in 533,709 gaps.

---

## 7. The anchor can move out under you

This lesson came last and cost the most. It is also described in
[commentAlgorithm.md §8.7](commentAlgorithm.md#87-the-anchor--the-obligation-this-argument-does-not-discharge).

### 7.1 Code is an input too

Everything in §6 quantifies over *the run*, holding the tree fixed. Look again
at

```
role(k) = f(code, comments 1 … k−1)
```

§6 shows that `f` is length-independent in its **second** argument. But `code` is
an argument too, so we must worry about both code and comments, requiring:

> **(i)** given the same `code`, `f` returns the same roles when re-asked over
> the output's positions; and
> **(ii)** the second run *is given the same* `code`.

(ii) holds exactly as far as formatting does not rewrite the concrete syntax. Ours
does, in three ways that delete, insert or reorder a token: it **sorts** exposing
lists and import groups, it strips redundant parentheses from patterns, and it
adds or drops the `port` keyword on a module header (and a fourth on request:
`--remove-unused-imports` deletes whole imports).

> **Any formatter that rewrites tokens at all — sorts imports, removes
> redundant syntax, normalizes a keyword — carries obligation (ii), and an
> idempotency gate does not protect against all of it: the class has members
> that are fixed points.**

### 7.2 The bug we hit

A comment's **anchor** is the code its placement was decided against. Requirement
(ii) fails when the formatter moves that code out from under the decision.
Ours did, for a comment written inside an import statement:

```gren
import {- k0
    tango -} Qux0 exposing (..)
import Bar3
```

That comment's range overlaps the import's, and there is nowhere inside an
import node for it to live, so attachment promotes it to a sibling and the
first pass prints it on a row of its own. When we sorted the imports,
we messed up, and the formatter saw the comment as a different role as
a result. Same number of comments, but roles came out different because
we changed the code incorrectly.

That is the oscillating face of the class, and it was fixed as one. Our
comment-gap sweeps could have found it, but there we made a mistake too:
they all ran over the **already-formatted** half of the corpus, not the
**dirty** corpus, and a `.formatted.gren` has nothing left to sort. Once we
realized that mistake, running the test over the 391 *unformatted* halves of
the corpus at the time, 66,252 probe sites, found **24 findings** on the first
sweep, 22 of them this class. `--corpus both` is now the default
([testing.md](testing.md#idempotency-fuzzer-fuzz-idempotencypy)).

The class has a second face, and it is the one the boxed rule above is about.
When the same misread row range also makes the vertical-space pass emit a blank
line, that blank becomes a real run boundary on the next parse and the wrong
grouping is *self-consistent*: a fixed point that has silently declined to
sort two adjacent imports. No idempotency gate can see that member, and none of
ours did. It outlived two fixes and was finally caught by the random-module
fuzzer's **author-order oracle**, which emits the same module with its import
run in the other order and requires byte-identical output
([commentAlgorithm.md §8.7](commentAlgorithm.md#87-the-anchor--the-obligation-this-argument-does-not-discharge)).

---

## 8. What the barrier does not reach

This section is a survey of the bugs this project has fixed, not evidence for
or against the position barrier. What it settles is scope: which of our bugs
are of the kind §§4–6 are about at all, and how much of the bug history lies
outside their reach. So we counted.

The question is whether the test gates we have **today** can see each bug the
project has fixed. Not the gates that existed when the bug was found: most of
the portfolio was built after most of the bugs, so that history says only
which gate came first. Instead we replayed old bugs against the current gates,
one bug at a time:
* Take a commit that fixed something.
* Check out the commit before it, which is the last state of the tree where the
  bug is still present, and build a formatter from it.
* Recover the input that triggered the bug: usually the fixture the fix added,
  otherwise the snippet in the commit message.
* Run every gate in today's `tests/` against that one input, using that old
  formatter. Then do the same at the fix commit.

If a gate fails at the parent and passes at the fix, that gate can see the bug;
call the bug **witnessed**. If no gate fails at the parent, the bug was
invisible to the entire portfolio: it shipped, and nothing we own today would
have said a word about it.

One limit on what that measures. It is per input, not per sweep: it says a
gate would fail *on the triggering input*, not that the gate's own probe would
ever have generated that input, so "witnessed" means the gate can see the bug,
not that it would have found it unprompted.

Not every commit can be used. Of 135 fix commits, 57 predate a shift in
how the formatter and library were built together,
12 turned out on inspection not to be bug fixes,
and 5 no longer build. That leaves 61. Of those, 37 were
witnessed, **21 were invisible**, and 3 would not reproduce. An invisible one
means the output was wrong and also AST-equivalent, its own idempotent
formatting, every comment preserved, the end-to-end check exiting 0.

| class | witnessed | invisible | not reproduced |
|---|---:|---:|---:|
| oscillation · crash · wrong-attachment · performance | 33 | 0 | 2 |
| **layout** | **0** | **16** | 1 |
| mixed · dropped-content · blank-lines · literal-corruption | 4 | 5 | 0 |

**Row one: the gates see every bug of the kind the barrier is about.** The
barrier exists to prevent oscillation and wrong attachment, a comment placed
from a position that formatting then invalidated (§3.2). There were 21 of
those, and a gate caught every one. The row also holds the 8 crashes and 4
performance bugs, which `--show` catches for the ordinary reason that the
formatter died or hung. So on this row the gates are complete, though one of
the 21 needed a gate we built late: the fixed-point member of §7's anchor
class, which only the author-order oracle could see.

**That does not say the barrier worked.** A replay asks whether a gate fires on
a bug that was written; it cannot ask whether an architecture kept a bug from
being written. The dates are the only evidence on that, and they are mixed: 13
of the 21 were fixed after deciding-once landed on 2026-07-19, most within
four days of it as the random-module fuzzer swept out bugs that were already
there, but three of them weeks later, in the passes above the line (§5.3) and
at the anchor (§7). The type-enforced barrier of §5.2 is newer than this study,
so nothing here tests it. §5.4 claims the barrier confines what remains to one
function; it never claimed a lower count.

**Row two: no gate can see a layout bug, and it is the biggest single class in
the replay.** Sixteen
layout bugs ran and every one was invisible (a seventeenth would not
reproduce). A layout bug breaks no property: the output parses, means the same
thing, keeps every comment, is its own fixed point, and is wrong. There is
nothing left for a property gate to check. The barrier is no help either. It
controls *where* a placement decision is made, not whether the decision is
right, and a role decided once and re-derived identically forever can still be
the wrong role.

**The dividing line is not comments versus code. It is *where* a thing goes
versus *how* it looks once it is there.** Everything this essay argues about,
the roles, the barrier, the coverage argument of §6, is on the *where* side,
and so is every gate that fired. The *how* side has no architectural defence
and no invariant to check. What catches a bug there is an expected answer or a
second implementation: for us, the 396 fixture pairs (hand-written, apart from
the ones that pin the divergence catalogue), and the elm-format comparison of
§9.5.

---

## 9. What fourteen other formatters do

We read the source of fourteen production formatters. **All counts and
repository states below are as of 2026-08-23**, the date of the pull, except
where a later date is given; counts of source sites (prettier's print modules,
ocamlformat's `Source.*` reads, swift-format's rules) are our own greps of that
checkout. Beyond the
input level from §2, three axes separate them: *when* placement is decided;
whether layout is width-aware or author-driven; and, the axis that is almost
never named, whether there is a **position barrier** of the kind §5 builds.

The barrier is not implied by deciding comment attachment once.
A tool can decide attachment
exactly once and still let every layout rule re-derive verticality from the
author's rows. It is also a stronger bar than "the fitter is positionless",
since several tools reach their fitter through a tree walk that is itself full
of layout choices, and it is that walk the barrier has to cover.

| formatter | comments arrive as | placement decided | **layout** | position barrier? |
|---|---|---|---|---|
| [topiary](https://github.com/tweag/topiary) | **A0** CST nodes | *never asked*; author-row facts resolved to atoms once | author-driven | yes |
| [elm-format](https://github.com/avh4/elm-format) | **A1** named slots | at parse time | author-driven | yes (no positions exist) |
| [dart_style](https://github.com/dart-lang/dart_style) | A2 token trivia | once, front end → 4 roles | **80-col cost solver** | yes (positionless `Piece` IR) |
| [swift-format](https://github.com/swiftlang/swift-format) | A2 token trivia | at token-stream build, *lexically* → 4×2 | 100-col Oppen | yes (printer reads only output rows) |
| [google-java-format](https://github.com/google/google-java-format) | A2 token trivia | at lex time, *lexically* | 100-col greedy `Doc` | **partial**: `Doc` sees none, op-builder reads 2 |
| [CSharpier](https://github.com/belav/csharpier) | A2 [Roslyn](https://github.com/dotnet/roslyn) trivia | while walking trivia → 2×2 | 100-col `Doc` | yes |
| [biome](https://github.com/biomejs/biome) | A2 [rowan](https://github.com/rust-analyzer/rowan) trivia | once, pre-pass → 3×3 | 80-col `Doc` | yes |
| [Black](https://github.com/psf/black) | A2 prefix *strings* | once, `list_comments` → 2 roles | 88-col | yes (comments become typed leaves) |
| **gren-format** | **A3 located list** | once, logical stage → 7 roles | author-driven | **yes, type-enforced** |
| [prettier](https://github.com/prettier/prettier) | A3 located list | once, `attach.js` → 3×3 | 80-col `Doc` | **no**: 19/73 printers read the source |
| [ocamlformat](https://github.com/ocaml-ppx/ocamlformat) | A3 located list | once, `Cmts.init` → 3 roles | 80-col `Format` boxes | **no**: 14 `Source.*` reads in the printer |
| [ormolu](https://github.com/tweag/ormolu) | A3 located list | **at print time**, span compares | author-driven | no |
| [gofmt](https://github.com/golang/go/tree/master/src/go/printer) | A3 located list | **at print time**, row/col compares | author-driven | no |
| [rustfmt](https://github.com/rust-lang/rustfmt) | A4 missing spans | **at print time**, byte spans | 100-col | no |
| [zig fmt](https://github.com/ziglang/zig/blob/master/lib/std/zig/Ast/Render.zig) | A4 raw source scan | **at print time**, byte offsets | author-driven | no |

Grouped by whether comments arrive attached to the tree, the table collapses
to this:

| | **has position barrier** | **no position barrier** |
|---|---|---|
| **A0–A2**: attachment delivered by the front end | all eight (google-java-format: partial) | — |
| **A3–A4**: attachment must be reconstructed | **gren-format, alone** | prettier, ocamlformat, ormolu, gofmt, rustfmt, zig |

**Every tool that has to reconstruct attachment lacks the barrier, except ours.**
Those six are where the survey's instabilities that no one has found a local
fix for cluster (§9.3, §9.4, §9.8). §9.5 and §9.9 are the counterweight:
having the barrier does not make a tool immune, and three barrier-holders
decline fixes too; what it does is keep the bugs local.

The top row invites an inference that does not hold, that a front end which
delivers attached comments delivers the barrier with them. Attachment is a fact
about the input; the barrier is a prohibition on a stage, and most of the
questions in §5.1 are not about comments at all. A trivia concrete syntax tree
(CST) still carries a
position on every token. So seven of those eight built the barrier themselves,
each in an IR that drops positions. Only elm-format is simply handed it, and
only because level A1 has no position information for comments.

The position barrier is neither our idea (swift-format wrote it down in 2020)
nor rare (nine of fifteen have it, counting ours). What is new is having it while also
reattaching comments to the tree.

### 9.1 Deciding attachment once is the norm

Ten of the fifteen classify each comment exactly once, before printing, into a
small finite role set: Black's two, ocamlformat's three, dart_style's four,
elm-format's five slots, our seven, swift-format's four kinds crossed with a
boolean, prettier's and biome's 3×3. So the premise of §6.1 is not something
anyone needs to defend; it is what production formatters already do. What
differs is **what the classifier may read** and **what happens downstream**. Two of the
tools that decide once still oscillate, prettier and ocamlformat, and they are
exactly the two whose *layout* stage reads positions.

### 9.2 biome and CSharpier are the nearest thing to a controlled experiment

**biome** and **CSharpier** are both ports of prettier. Each keeps prettier's
algorithm, its role model and its width-fitting `Doc` printer, and changes the
input (along with the language and the tree type, which a rewrite cannot help
changing). prettier is handed a comment-free AST plus a list of located
comments, and has to work out where each one goes. biome is handed a rowan
CST, a lossless tree in which every comment is already trivia on a token;
CSharpier is handed the same kind of tree from Roslyn, the .NET compiler.
Their authors wanted prettier in Rust and prettier for C#.

In both ports the positional reads on the comment path vanish, because
attachment arrives with the tree. Where prettier reads `options.originalText`
at node offsets to decide whether a comment is on its own line, biome counts
`piece.is_newline()` over the token's trivia pieces to answer the identical
question, and the barrier appears without anyone having set out to design one.
Neither port is spotless (biome's row in §9.5 carries a comment-related
idempotency fix, at an embedded-language boundary), and a rewrite changes more
than one thing, so this is suggestive rather than controlled. Held that
loosely, it still points at the variable:

> prettier's instability does not look like a property of its algorithm, its
> role model, or its fitter, all of which the ports kept. It looks like a
> property of a front end that hands it comments unattached, so that it has to
> rebuild the attachment from positions.

That also says exactly what a formatter like ours is up against. Our front
end hands us the same thing prettier's does, and we cannot change that, because
reusing the compiler's parser is the whole point. So the barrier has to be
built.

### 9.3 ocamlformat ships an iteration instead of an argument

ocamlformat decides once, and its printer still reads `Source.begins_line` and
`Source.empty_line_between`. So `Translation_unit.ml` does not format the file;
it formats it repeatedly, under a comment reading `(* iterate until formatting
stabilizes *)`, bounded by a user-facing `--max-iters`, default **10**, after which
it emits `BUG: formatting did not stabilize after %i iterations`.

A mature, widely used formatter's shipped answer to §3.2 is: run it up to ten
times and report a bug if it still moves. The bug class is real, and nobody has
solved it.

### 9.4 gofmt has the strongest corpus gate in the survey, and an exemption inside it

gofmt's printer has no fitter at all; layout is as author-driven as it gets.
But `writeCommentPrefix` decides same-line-versus-own-line from
`pos.Line == p.last.Line`, blank lines from `pos.Line - p.last.Line`, and, for
a comment before a `case` label, *which block the comment belongs to* from
`pos.Column == next.Column`, all inside the emission loop. A maintainer's
diagnosis on the resulting issue states §3.2 in one sentence:

> "When the next token is on the same line as the comment, this appends a space
> character instead of `\t`. **However, the next token is not necessarily on the
> same line after formatting.**"

`cmd/gofmt/long_test.go` re-formats **every `.go` file under `GOROOT`** and asserts
idempotency. It works: it caught the bug on a file in Go's own tree. But the
bug is architectural, not local, and what has shipped since 2018 is an
exemption inside the gate, a `strings.HasSuffix(filename, "issue22662.go")`
branch that logs "known gofmt idempotency bug" and returns.
[golang/go#24472](https://github.com/golang/go/issues/24472) is open, labelled
`NeedsInvestigation`, more than eight years old (opened March 2018), and the exempted file still
fails when replayed on go1.25.1. So does
[golang/go#73958](https://github.com/golang/go/issues/73958) (2025), whose
second pass *invents* a bare `//` line. Separately, `go/printer`'s own fixture
suite opts out per file, because idempotency "is very difficult to achieve in
general".

**The corpus test gate is not the missing piece.** gofmt has a bigger one than we do.
What it does not have is an architecture in which the answer cannot go stale.
So the gate finds the instance, the instance resists fixing, and the file gets
an exemption.

### 9.5 The barrier is necessary and demonstrably not sufficient

Mining the full histories of the five barrier-holding tools we cloned turns up
idempotency fixes in all five (swift-format's one is a feature removal rather
than a repair), two of them within six weeks of the survey date:

| tool | idempotency fixes | comment-related | latest | idempotency gate |
|---|---|---|---|---|
| topiary | 8 (OCaml ×6, Nickel ×2) | 0 | 2026-07-20 | runtime, default on |
| Black | 9 | 3 | 2026-07-21 | runtime, default on |
| biome | 4 (all embedded-language) | 1 | 2026-04-10 | none |
| CSharpier | 2, user-reported | 0 | 2024 | none |
| swift-format | 1, a feature removal | 1 (the removal itself) | 2020-01-30 | fixtures |

Topiary's two 2026 fixes are §3.2 in someone else's words ("the type
ascription **collapsed onto one line, which flipped the body's indentation
between runs**") and they show where the obligation went: topiary's engine cannot
have the bug, so it lives in the per-language query files instead, and has for
four years. Black's is better documented: omitting optional parentheses
"**re-parents the comment onto a different leaf after the next parse**", which
changes the split on a second pass, in a tool that runs `assert_stable` on
every format. That is the shape of §7: the classifier answered differently
because its *anchor* moved, not because anything re-read a position. Biome's
four are all at composition boundaries, where one barriered formatter's output
becomes another's input.

Read that table together with §5.4. Four of the five have the barrier but
nothing like §6, no argument that what the barrier leaves behind is correct,
and all four still ship the bug; two, topiary and Black, compensate with a
runtime check.

**elm-format reaches the same conclusion from the opposite end.** The tools
above built a barrier and still have the bug. elm-format never needed to build
one: it sits on level A1, with named comment slots and no source positions
anywhere for a layout rule to misread, and it ships §3.2's mechanism anyway.
For a pipeline whose last step forces a multi-line block, it produces two
different outputs for semantically identical code, differing only in how the
author happened to break the source lines. Its AST records that the author
broke the lines, a layout rule keys on the record, and the output breaks them
differently, so the second run reads a different record. Taking the positions
away did not take away the class, because positions are only one way to read
the input's line structure. We found
it because every generated cell is diffed against elm-format and gated against
a **reviewed baseline** rather than treating elm-format as an oracle. We reported
it as [elm-format#842](https://github.com/avh4/elm-format/issues/842). Running
elm-format again on the first output yields the second, which the divergence
alone had not shown: the first output is **not a fixed point**. Either side of
a differential comparison can be the wrong one, and once it was.

### 9.6 swift-format is the control condition

Each stage of swift-format's story is a stage of this document's argument,
which is why it gets a section of its own.

`BlankLineBetweenMembers` (2019-07-10) put at least one blank line between the
members of a type. It is a phase-1 rule, meaning it rewrites the syntax tree
before the Oppen printer runs, and it decided whether to insert that blank line
by comparing the start and end **line numbers of the input**. That is §3.2's
mechanism, in a tool that otherwise holds the barrier cleanly, and the one
place it was broken is where we broke it too (§5.3): not in the printer, but in
a pass that runs before it and reasons about what the printer will do. It was
patched twice in 2019, eight days apart, both times as a comment bug rather
than as non-idempotency. **Then the class was named, and the rule was deleted.**
The commit of 2020-01-30 removes 149 lines of rule and 365 of tests:

> "The rule is unfortunately **based on the trivia before the pretty printing
> pass, which means it decides single-line-ness based on the input which may be
> incorrect. The single-line-ness must be based on the source *after* pretty
> printing**, so it cannot be accomplished in a phase 1 rule."

That is the rule in §5.1, reached independently, six years earlier. Two days
later "Delete some dead code" removes the `isSingleLine` accessor too, which is
the stronger decision: it makes the question unaskable rather than merely
unasked, the remedy of §5.2 arrived at by review instead of by a type. The
break-based reimplementation the commit proposed still does not exist; the
feature was traded for the invariant and the trade was never revisited. **The
principle held, though**. Of swift-format's 44 rules, exactly three mention
`sourceLocationConverter` today, all three to build a diagnostic's location.
Six years, no regression, no allowlist, no exemption file.

So swift-format is not a counterexample; it is the control condition. The
principle is discoverable without the architecture (they found it), finding it
does not by itself yield the feature (they lost it), and holding it by review
is expensive. What enforcement buys is affordability. `Formatter.RenderTree`
deletes the *field* rather than the *feature*, so our analogue of that rule,
the vertical-space pass, can exist, because the fact it needs is computed once
at the barrier and does not go stale. swift-format had to choose between the
rule and the invariant. The point of making the barrier a type is not having
to choose.

**§7's anchor, in someone else's tracker.** We ran swift-format 6.3.3 on its
three rules that rewrite tokens, and two of them are this essay's later
sections in miniature.

`OrderedImports` is §7. It decides that a comment belongs to the import
directly below it, then sorts that import away. A file header above the first
import travels with it into the middle of the block, and the result is a fixed
point, so no idempotency check can see it. This was reported as
[swift-format #772](https://github.com/swiftlang/swift-format/issues/772)
(2024-07-18) and closed as working as intended: a comment about the
import below is indistinguishable from a licence header, so the author must
say which it is, with a blank line. We came to the same answer, for the same
reason: a blank line is the only run boundary our import handling has either.
A second project reaching §7's anchor and resolving it the same way is better
evidence for §7 than a bug would have been.

`NoCasesWithOnlyFallthrough` is §4. When it merges a `fallthrough`-only case
into the case below, a comment can be in one of three places. Above the
absorbed case, the merge is suppressed. Above the surviving case, the comment
is kept but now reads as a note about the wrong case. Trailing the absorbed
case on the same line, the comment is **deleted**: the rule's guards start by
dropping everything up to the next newline, which is exactly where that
comment lives. That is what deciding attachment per site instead of once per
comment looks like: a place nobody enumerated has no answer, and no answer
renders as nothing. We filed
[swift-format #1274](https://github.com/swiftlang/swift-format/issues/1274)
(2026-08-25), open as of writing.

### 9.7 Runs, independently corroborated

Four of the surveyed tools model runs of comments explicitly, and two others
have shipped run bugs. dart_style's `CommentSequence` is documented as *n* comments
and *n+1* newline counts, which is §6.3's boundary counting arrived at
independently. ocamlformat groups adjacent comments and decides the group as a
unit. gofmt passes `prev *ast.Comment`, "the previous comment in a group":
**exactly one neighbour**, §6.3's rule. Black's `list_comments` is §6.3 in its most
reduced form: only the *first* line of a prefix can be a trailing comment;
every later one is standalone.

And the bugs. Ormolu's 0.1.0.0 changelog fixes **five** comment-idempotence bugs in
a single release, two of them run bugs in the project's own words: comments
"picked up as 'continuation' of a series of comments"
([#449](https://github.com/tweag/ormolu/issues/449)) and "different indentation
levels in a comment series"
([#512](https://github.com/tweag/ormolu/issues/512)). rustfmt
[#7019](https://github.com/rust-lang/rustfmt/issues/7019), "Non-idempotency in
consecutive block comment", was filed 2026-08-10, in a formatter with a
400-file idempotency gate. That is §6.4's warning, that a corpus of
hand-written fixtures will not contain the mixed run that breaks a per-member
rule, coming true twice in other projects.

### 9.8 The two trackers, read against each other

rustfmt is in the same position we are. It reuses the production Rust
compiler's parser, whose AST carries no ordinary comments, and recovers them
from **"missing" source snippets**: the raw text between the last emitted byte
position and the next node's span. Its `A-comments` label carried **447 issues,
147 open**; **89 titles report a comment being removed, deleted, eaten or lost**, 29
still open, the oldest from 2019. Twelve titles report non-idempotency, and
three report a comment migrating between owners across an import reordering
([#5485](https://github.com/rust-lang/rustfmt/issues/5485),
[#6241](https://github.com/rust-lang/rustfmt/issues/6241),
[#3127](https://github.com/rust-lang/rustfmt/issues/3127)), which is §7's
class, whose fixed-point members only an author-order oracle can see. Two things are interesting.
The **arrival rate** rather than the backlog is the
signal: roughly forty comment issues a year for eleven consecutive years, with
no downward trend in the per-year counts, in a mature and near-universally deployed formatter. And
**rustfmt is not missing an idempotency gate**. It re-formats every file in
`tests/target` and asserts no change, with a floor of 400 files, exactly the
gate our fixture suite provides. What it does not have is a probe that inserts
a comment into every inter-token gap, or one that varies runs by length and
composition.

Historically, prettier does a good job of not dropping comments.
Of course, prettier must **attach** each comment, and it does so from
source positions, deciding `ownLine` / `endOfLine` / `remaining` from the
author's line structure. Structurally that is §4's role assignment, decided
once, before printing. It oscillates anyway, because prettier reflows: the
line structure of the output is not the line structure the classifier read.
Its `area:idempotency` label carried **109 issues, 54 open**, and **47 carried both
`area:comments` and `area:idempotency`**; the oldest open comment-instability
issues date from **April 2017**. By contrast only 16 prettier titles named comment
*loss*.

The contrast is sharper than either tracker alone:

> A **discarding** parser puts *preservation* at risk: rustfmt loses comments in
> volume, for years. **Keeping** the comment does not buy idempotency, because
> attachment is still decided from positions that printing invalidates:
> prettier loses almost nothing and oscillates constantly.

The instability class is caused by **positional attachment**, not from
the parser discarding comments.
Discarding merely *forces* positional attachment in the formatter.

### 9.9 The exempt-rather-than-fix reflex is general

Beside gofmt's filename exemption: Black keeps an expected-*failure* fixture
set "with the unstable formattings" and ships `--unstable` as a release
channel; swift-format deletes the rule; ocamlformat iterates ten times; topiary
reserves an exit code. Five projects, five ways of declining a fix, each an
implicit judgment that the class is architectural rather than local.

Runtime idempotency checking is itself a recognized pattern; four of the
fourteen ship one. ormolu offers `--check-idempotence`; ocamlformat iterates;
topiary checks by default; Black checks by default in `--safe` mode, alongside
an AST equivalence check, and its source names the failure mode precisely:

> "We shouldn't call `format_str()` here, because that formats the string twice
> and may hide a bug where we **bounce back and forth between two versions**."

Two of those four, topiary and Black, *have* the barrier and check anyway.

---

## 10. Summary

The problem is one sentence: placement is decided from source positions, and
formatting invalidates them. We are there by choice, because reusing the
compiler's parser is worth more to us than a parser that keeps comments (§2).

Our answer is three moves. **Decide once**, while the positions are still the
author's, into one of seven roles, each of which must re-derive to itself from
the formatter's own output (§4). **Enforce it with a type**, by handing the
render stage a tree with no positions on it at all (§5). **Cover what
remains**: the obligation now lands above the line, for comments on one
function, and §6 argues that the function is right for a run of any size.

The barrier localizes the problem without ending it. Passes above the line can
move a position that was already decided from (§5.3); sorting can move the
code a comment was attached to, leaving a fixed point no idempotency gate can
see (§7); and layout bugs, the largest class in the replay of our history, are
on a side of the line the barrier does not touch (§8).

Nine of the fifteen tools surveyed have the barrier; what is new is having it
while also reconstructing attachment from a located list (§9). The tools that
must reconstruct and lack it are where the instabilities that resist a local
fix cluster (§9.3, §9.4), two ports of prettier locate the class in positional
attachment rather than in a discarding parser (§9.2), and the tools that have
the barrier ship fixes anyway (§9.5), one of them by deleting a feature to keep
the principle (§9.6).

**Decide once, behind an enforced barrier** generalizes to any pass whose output
destroys the evidence its decisions were made from: record the decision as a
value, and make re-deriving it a compile error. A barrier protects one pass,
not a pipeline of them, and it relocates the obligation rather than removing
it. Where the obligation lands is the whole question, and ours lands on a
function small enough that §6 can be an argument about all of it.
