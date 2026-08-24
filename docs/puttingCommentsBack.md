# Putting the comments back

*How `gren-format` places comments when the parser it uses throws them away —
and which parts of that transfer to a formatter for some other language.*

This is an essay, not a reference. It is written for people who build or
maintain code formatters, especially anyone whose formatter is built on a parser
that does not keep comments. If that is you, the problem below is yours too, and
the useful part of this document is what it cost us to solve it and what we
measured afterwards.

It is deliberately not normative. The rules that decide where a comment actually
lands are stated in **[How gren-format places your comments](commentHandling.md)**
(C1–C7, with a worked example for each). The implementation is stated in
**[The comment algorithm](commentAlgorithm.md)** (attachment fold, roles, state
machines, the gates). This document restates just enough of both to make an
argument, and links down whenever the detail matters. Where the two disagree with
this one, they are right.

Every code example here was produced by running the formatter.

---

## Table of contents

- [1. Find your formatter on this ladder](#1-find-your-formatter-on-this-ladder)
- [2. The trade: no parser of our own](#2-the-trade-no-parser-of-our-own)
- [3. The archetype](#3-the-archetype)
- [4. Decide once](#4-decide-once)
- [5. The barrier, and how to make it a type](#5-the-barrier-and-how-to-make-it-a-type)
- [6. Why forty comments in a row is not forty cases](#6-why-forty-comments-in-a-row-is-not-forty-cases)
- [7. The anchor — what that argument does not cover](#7-the-anchor--what-that-argument-does-not-cover)
- [8. Invariants and expected bytes divide the bug space](#8-invariants-and-expected-bytes-divide-the-bug-space)
- [9. What fourteen other formatters do](#9-what-fourteen-other-formatters-do)
- [10. If you are building one](#10-if-you-are-building-one)
- [Sources](#sources)
- [See also](#see-also)

---

## 1. Find your formatter on this ladder

Before anything else, work out whether this document is about you. The single
variable that decides is **what your front end hands your formatter**. We read
the sources of fourteen production formatters besides our own (§9); they sort
into five rungs, in decreasing order of how much work the parser has already
done for you.

| rung | what arrives | who |
|---|---|---|
| **A0** | comments are ordinary nodes of a concrete syntax tree; the formatter needs no comment concept at all | [topiary](https://github.com/tweag/topiary) (over [tree-sitter](https://github.com/tree-sitter/tree-sitter)) |
| **A1** | named, typed comment slots on AST nodes — and no source positions anywhere | [elm-format](https://github.com/avh4/elm-format) |
| **A2** | *trivia* hanging off tokens | [dart_style](https://github.com/dart-lang/dart_style), [google-java-format](https://github.com/google/google-java-format), [swift-format](https://github.com/swiftlang/swift-format), [CSharpier](https://github.com/belav/csharpier), [biome](https://github.com/biomejs/biome), [Black](https://github.com/psf/black) |
| **A3** | a **comment-free AST**, beside a flat source-ordered list of located comments | **gren-format**, [ormolu](https://github.com/tweag/ormolu), [ocamlformat](https://github.com/ocaml-ppx/ocamlformat), [gofmt](https://github.com/golang/go/tree/master/src/go/printer), [prettier](https://github.com/prettier/prettier) |
| **A4** | nothing; comments are recovered by re-reading the raw source between two nodes' byte offsets | [rustfmt](https://github.com/rust-lang/rustfmt), [zig fmt](https://github.com/ziglang/zig/blob/master/lib/std/zig/Ast/Render.zig) |

If you are on **A0–A2**, your comment is already attached to something before
your formatter starts. Placement is a question about a tree the comment is
already in, and most of this document is background reading. You may still want
§8 (which kinds of bug your test gates cannot see) and §9 (four of the eight
tools on those rungs have shipped idempotency fixes anyway).

If you are on **A3 or A4**, attachment does not exist yet and you have to
reconstruct it — from source positions, which is the one piece of evidence
formatting destroys. This document is about you. So is every instability story
in §9.

A3 is not an exotic place to be. It is simply **what a production compiler's
parser hands you**, and five of the fifteen tools surveyed are there.

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

**Who should take this trade.** Anyone whose language has a moving front end and
a formatter that must never disagree with it, and anyone who would otherwise be
committing to maintain a second parser for the rest of the project's life.

**Who should not.** A project whose compiler already carries trivia — take it,
it is free. A project that can afford the second grammar and would rather have
comment placement be a non-problem. The rest of this document is the size of the
bill; read §7 and §8 before deciding you want it.

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
property gate can see — 16 of the 37 replayable bugs in §8.4.

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
give you attachment. Black keeps every comment — they ride in a leaf's
whitespace prefix — and still says so in the docstring of the function that has
to place them:

> "The sad consequence for us though is that comments don't 'belong' anywhere.
> … We simply don't know what the correct parent should be."

### 3.4 One thing we are not doing

`gren-format` has **no page-width limit and no fitter**. Layout is author-driven:
a construct goes vertical only if the author wrote it across rows, or if
something inside it is multi-line and forces the rest open. There is no search
for a best arrangement and no reflow.

State that early, because it invites a reading we have to refute: that
author-driven layout is *why* the instability class exists, since an optimiser
that recomputes layout from scratch has no stale authorial rows to be wrong
about. That reading is intuitive and the evidence in §9 refutes it in both
directions. prettier *is* that optimiser and has the class anyway, at a
nine-year scale; gofmt has no fitter at all and is also not stable. Five
width-aware formatters sit behind a barrier and are stable. The responsible
variable is whether **placement** reads positions, not whether **layout** is
author-driven.

What author-driven layout really does is make the authorial rows *more tempting
to read*. They are right there, and they are usually correct. That is precisely
why the ban in §5 has to be mechanical rather than advisory.

---

## 4. Decide once

Every comment's placement is decided **exactly once**, in the logical stage,
while every row in the tree is still the author's. The answer is stored on the
comment leaf as a `CommentRole`:

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

**The criterion the classifier is written to** is the thing to steal, if you
steal one sentence from this document:

> A role is a reparse fixed point: **the row it is decided from equals the row it
> renders on.**

Where a role could not be made to satisfy that in place, the fix is never a
cleverer rule — it is to make **format¹ build the tree format² would build**.
That is what the two whole-tree repairs are for; they are repairs precisely
because they need the finished tree, which a fold by construction cannot have.

**Totality matters more than it looks.** `classifyCommentKind` returns a
`CommentRole` — not a `Maybe`, not a `Result`. Every path through both decision
diagrams ends in a role. There is no fallback arm, no "unknown", and no branch
that defers the question to the renderer. The renderer's own layout policy is
total in the same way.

The consequence is what §6 needs: after *n* comments the tree carries *n*
**independent** annotations drawn from a 7-element set. That is what rules out
the failure mode people expect from a comment placer — a combinatorial case
analysis over configurations, with the uncovered corner. There are no
configurations. There are *n* leaves.

Deciding once is, incidentally, *not* the unusual part: ten of the fifteen tools
surveyed in §9 do it, into role sets of two to nine members. What differs is what
the classifier may read, and what happens downstream.

---

## 5. The barrier, and how to make it a type

### 5.1 The rule

> **No code in the rendering stage may read a source row or position to make a
> layout or comment-placement decision.**

Placement is the stored role. *Verticality* — the other thing a renderer is
tempted to ask the source about — is read off the **rendered box shape**
(`isSingleLine` applied to a box that has already been built), never off "did the
author span rows". The renderer's remaining questions about a comment are about
its **text** ("can this text share a line?"), which is a property of the string
and not of where it sat.

### 5.2 Making it a type error

This is enforced by the type checker, not by documentation and not by a linter.

The rendering stage does not consume the logical printing tree at all. It
consumes `RenderNode`, a **mirror of the node type with the seven cached position
fields removed**, reachable only through a `lower` function that drops them.
`RenderShape` strips the `Located` payloads off eight shape constructors too, so
there is no position anywhere in the renderer's view of the tree.

A render-side row read is therefore not a lint failure. It is a type error, and
there is no allowlist to grow.

Five render-side decisions genuinely needed the author's rows. `lower` computes
those once, at the barrier, as booleans — `rnSharesRowWithPrevItem`,
`rnHasSourceContent`, `rnVariantsSpanRows`, `rnTypeSegmentsBroken` — and the
renderer reads the answer rather than the evidence. `RenderShape` is total over
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

### 5.3 What the barrier does not cover

Two gaps, both of which have produced real bugs here. Neither is a reason not to
build the barrier; both are reasons not to think you are finished.

**On the renderer's own side of the line: a role is not a row.** Turning a role
into output still requires a fact no role can carry — *is there a line here to
glue onto?* A `TrailsPrevious` comment glues onto the previous item's last line,
but whether the previous sibling ended on a line this comment may join is a fact
about what the renderer has just emitted. That question is legitimately below the
barrier, it is answered from render state rather than from positions, and it can
simply be got wrong. A container fold that admitted gluing onto a *field* but not
onto a comment that had itself just glued onto that field split a two-comment run
written on one row, dropping its second member eight columns left. No position
was read; the placement was wrong anyway. The barrier removes *positional
re-derivation* from the renderer. It does not make the renderer's residual
decisions trivial.

**On the other side: a pass that runs before the renderer and reasons about what
the renderer will do.** This is the one that catches people, and it caught
swift-format in exactly the same place (§9). Our criterion in §4 is stated *for
roles*, and that scoping was itself the defect. The sorting pass and the
vertical-space pass are row-keyed decisions too; neither produces a role, so
neither was ever held to the sentence. Both were later found violating it. The
vertical-space pass decided whether a comment was free-floating from the gap
*below* it — and then closed that very gap two steps later when it pulled a
definition up under its signature. First format saw a gap and emitted two blank
lines; second saw none and emitted one.

Read correctly, the sentence was never about roles:

> **Every** row-keyed decision in the logical stage must be decided from a row
> that this pipeline does not itself move.

Roles are simply the case where we noticed first.

### 5.4 What the barrier is actually for

It does not *prove* the fixed point. It **localises the obligation**.

With no positional read downstream, the only thing that can differ between run 1
and run 2 is the classifier's answer — so the whole fixed-point obligation
collapses onto one total function with a finite codomain. That is what makes the
coverage argument in §6 possible at all. Without it, the obligation is spread
over every layout rule that reads a row: 19 of prettier's 73 print modules, 14
source reads in ocamlformat's printer. There is no corresponding argument to be
made about those, and §9 shows what gets shipped instead.

§9's fifth finding is the necessary counterweight: **the barrier is necessary and
demonstrably not sufficient.** Four of the five barrier-holding tools whose full
histories we mined have shipped idempotency fixes anyway. The barrier hands you a
small enough obligation to argue about. Something then has to discharge it.

---

## 6. Why forty comments in a row is not forty cases

The question every reader of a comment placer asks is "what happens if I write
forty comments in a row?". This section argues that it is the wrong shape of
question, and §8 reports what happened when we tested the argument as a
prediction.

The claim:

> For any *n* ≥ 0 comments of any kinds in one place, the algorithm assigns
> exactly one placement to each; and a run of *n* reaches no decision that a run
> of two does not.

It rests on four properties of the implementation, plus one honest boundary
(§7). The full version, with the code sites for each, is
[commentAlgorithm.md §8](commentAlgorithm.md#8-why-this-covers-every-run--the-argument-not-the-test-suite).

### 6.1 Placement is prefix-determined, so *n* is never an input

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
not distinguish the two.

### 6.2 The answer set is finite and the classifier is total

From §4: seven roles, no fallback arm, every path ends in a role. The state the
tree carries after *n* comments is *n* independent draws from a 7-element set —
not a configuration space.

### 6.3 "Any kind" is a three-letter alphabet

The language has two comment syntaxes, but every layout question about a
comment's kind routes through **one** predicate, which reads the comment's own
*text* and distinguishes three shapes:

| | can code follow it on the same line? |
|---|---|
| `-- like this` | no |
| `{- like this -}` on one row | **yes** |
| `{- like this` ⏎ `and this -}` | no |

Nothing else about a comment — its length, its content, its original indentation
— is read by any decision. "Any variety of comments" is not an unbounded axis. It
is three letters.

If you take one design idea from this section, take this one: **route every
kind-sensitive question through a single predicate**, so that the size of your
alphabet is a fact you can state rather than a property of scattered `case`
arms. Ours is three because that predicate says so.

### 6.4 Every local rule reads at most one neighbour

This is the load-bearing property. Every place a run's members interact locally:

| rule | what it reads |
|---|---|
| the reference row (four sites) | the **previous** member's last row |
| the render fold | a 6-value state summarising the row in front |
| the peel scanner | the **previous** member's kind |
| "can this text ride?" | the member's **own** text |

Not one looks two members back, or forward. So a run is a chain of boundaries,
and every rule is a function of exactly one of them:

```
code │ A │ B │ C │ code
     ↑   ↑   ↑   ↑
     each rule above is a function of ONE of these
```

With a three-letter alphabet there are exactly **nine** possible
comment→comment boundaries, and a run of any size is built from those nine.

A longer run can therefore reach something new in only two ways: by containing a
boundary a shorter one could not — impossible once all nine have appeared — or by
putting a member at **two** boundaries at once, which is observable only if some
rule reads both sides. By the table above, none does.

### 6.5 Where one neighbour is not enough

Three rules genuinely cannot be decided from one neighbour, because they are
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
as a bug.** A gap holding a mixed run tore in half, and the two halves came out
in the author's *reverse* order. A run half-rode a flat line and never settled.
The blank line above a floating run alternated between one and two. The
per-member version is the natural thing to write, it is correct for a run of one,
and a corpus of hand-written fixtures will not contain the mixed run that breaks
it. §9 shows two other projects learning the same thing independently.

### 6.6 What it looks like

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

### 6.7 The honest form

> Given that no rule reads more than one neighbour except the three that quantify
> over the whole run, length and composition add nothing past two members.

The premise is a property that must be **maintained**, not one that anything
enforces. That is exactly what the run sweep axes in §8 are for: they do not
prove the argument, they test its premise. A fourth all-or-nothing rule
discovered tomorrow gets added to §6.5's table, and the reasoning carries on
unchanged.

This is a small-scope-hypothesis result, but with a **mechanism**. We are not
asserting that small scopes suffice; we are naming the structural property
(boundary locality) that makes the small scope sufficient, and then testing that
property directly.

---

## 7. The anchor — what that argument does not cover

This is the most useful section of this document for anyone on rungs A3–A4, and
it is the one whose lesson we learned last and most expensively. The
implementation-facing version, naming the passes involved, is
[commentAlgorithm.md §8.7](commentAlgorithm.md#87-the-anchor--the-obligation-this-argument-does-not-discharge).

### 7.1 Two obligations, one discharged

Everything in §6 quantifies over *the run*, holding the tree fixed. It says
nothing about the tree. Look again:

```
role(k) = f(code, comments 1 … k−1)
```

§6 shows that `f` is length-independent in its **second** argument. But `code` is
an argument too. The fixed-point obligation is therefore two obligations, and §6
discharges only one:

> **(i)** given the same `code`, `f` returns the same roles when re-asked over the
> output's rows; and
> **(ii)** the second run *is given the same* `code`.

(ii) is not free. It holds exactly insofar as formatting does not rewrite
concrete syntax — and ours does, in three ways that delete, insert or reorder a
token:

- it **sorts** exposing lists and import groups;
- it strips redundant parentheses **from patterns** (expressions are exempt by an
  explicit design decision; patterns were never considered);
- it adds or drops the `port` keyword on a module header.

Two further rewrites — uppercasing hex digits, normalising string escapes —
change bytes only *inside* a token, so they cannot move an anchor.

**If your formatter rewrites tokens at all — sorts imports, removes redundant
syntax, normalises a keyword — you have obligation (ii) and an idempotency gate
will not discharge it.** Read on for why.

### 7.2 The gate that could not have seen it

We did not find this by reasoning. We found it because the gate that was most
sensitive to it had never been shown an input where it could fire.

Every comment-gap sweep in this project ran against the corpus of
**already-formatted** fixtures. On those, by construction, no rewrite occurs: the
imports are already sorted, the parentheses already stripped. **The rewrite that
can move an anchor cannot happen on a fixed point.** So the instrument was
correct, exhaustive, and pointed at an input class that excluded the bug.

Running the same instrument, unchanged, over the 391 *unformatted* halves of the
same fixture pairs — 66,252 probe sites — produced **24 findings in 10 fixtures**
on the first sweep. 22 of them were one class.

That axis is now a standing default: `fuzz-idempotency.py --corpus both`, and the
same flag on `check-decision-stability.py` and `audit-predicates.py`. See
[testing.md](testing.md#idempotency-fuzzer-fuzz-idempotencypy).

### 7.3 The mechanism is narrower than "the anchor moved"

Worth stating exactly, because the precise shape is what makes the class hard to
test for.

Attachment promotes a comment written *inside* a statement to a **sibling** of
that statement. The sibling's row range then **straddles** the node it was
written in — the comment spans rows 3–5 while the import it sits inside is
recorded at row 4. Nine lines reproduce it:

```gren
import {- k0
    tango -} Qux0 exposing (..) {- k1
            tango -}
import Bar3
```

A block comment written between `import` and the module name is hoisted onto its
own rows by the first pass. That makes it a **leading** comment of that import on
the next parse, which moves the import-group boundary, which changes the sort
order.

Every downstream row test was written under an invariant nobody had stated:

> **siblings are disjoint and in row order.**

"Does the next import unit begin on the row after this one ends?" and "did the
author leave a blank line here?" are both correct under that invariant and both
wrong without it. Attachment is what breaks it — and it breaks it only for a
comment written *inside* a statement, which is precisely a shape the formatter's
own output never contains, because the first pass hoists such comments to column
1 where the ranges are disjoint again.

So the invariant holds on every fixed point **by construction**, and the
already-formatted corpus could not have contained a single counterexample. That
is sharper than "no rewrite occurs on a fixed point": the input class is not
merely absent from that corpus, it is *excluded from it*.

### 7.4 The part that no idempotency gate can see

We first read this defect as a format¹-vs-format² disagreement and fixed it that
way. It is not only that.

When the same misreading also causes the **vertical-space** pass to emit a blank
line — which it does, since that pass asks the same adjacency question of the
same overlapping range — the phantom blank becomes a **real run boundary on the
next parse**, and the wrong grouping is *self-consistent*. Formatting is then a
fixed point that has silently declined to sort two adjacent imports.

No idempotency gate can see that, and ours did not. The shape survived the first
fix, survived a second fix to the sorter's row rule, and was finally caught by
the **author-order invariance** oracle: emit the same module with its import run
in the other order and require byte-identical output. That is the one gate in the
portfolio that does not ask about repetition at all.

The fix cost three attempts over two rounds, because the same misread row range
is consulted by two passes and correcting either alone leaves a bug — correcting
the vertical-space pass alone leaves the sort silently disabled, correcting the
sorter alone re-opens the oscillation.

### 7.5 The scope, stated exactly

> §6 establishes that run length and composition are not inputs to placement. It
> does **not** establish that placement is a fixed point, because it assumes an
> anchor the formatter is free to move.

Obligation (ii) is discharged by testing rather than by argument — and, more
precisely, it **cannot be discharged by idempotency testing at all**, since §7.4
exhibits a member of the class that *is* a fixed point.

Covering (ii) requires an oracle that varies the input's **authoring** rather
than repeating the formatter. We have exactly one, and building a second is the
open work. If you are designing a test portfolio for an A3/A4 formatter, put one
in from the start: *emit the same program spelled two legal ways and require the
same bytes.* It is the only kind of oracle that can see a stable wrong answer
about an anchor.

This is not a parochial worry. Black shipped the same shape in July 2026 —
omitting optional parentheses "re-parents the comment onto a different leaf after
the next parse" — in a tool that runs a stability assertion on every format, and
swift-format's `OrderedImports` rule has the ingredients today (§9).

---

## 8. Invariants and expected bytes divide the bug space

Two claims in this section. The first is that a portfolio of property gates has
*shaped* holes you can enumerate in advance. The second is measured rather than
argued, and it is the sharpest thing we know: **the property gates and the
hand-written expected-bytes fixtures are complementary, not redundant, and the
boundary between them is a bug-class boundary.**

### 8.1 The argument of §6, tested as a prediction

§6.4 is falsifiable. It says in advance which sweeps will pay for themselves and
which will not. Read "**finds bugs**" as "this probe reaches something no earlier
probe did", and "**nothing new**" as "everything it reported was already known".

| probe | what it newly reaches | predicted | measured |
|---|---|---|---|
| one comment | nothing — every neighbour is *code* | (the baseline) | the baseline every gate started from |
| a run of 2 | the first comment→comment boundary ever tested | **finds bugs** | **20 findings** in 19,081 gaps; one real family, fixed the same day |
| a run of 3 | nothing — `block│block` already appeared at *n*=2 | **nothing new** | 17 findings in 57,885 gaps, **all 17** a known upstream parser bug |
| mixed pairs | the other eight boundaries — a *different* kind on each side | **finds bugs** | **1,752 findings** in 115,770 gaps, **1,718 formatter-side**, in three bugs |
| mixed triples | nothing — every ordered pair already appeared | **nothing new** | 154 findings in 475,824 gaps, **all 154** known upstream |

668,560 probe sites across the four axes. Two axes — run *length* and run
*composition* — swept independently, each finding real bugs at exactly the size
where a new boundary first becomes expressible and nothing beyond it, twice.

The two "nothing new" rows carry the weight. If any rule *had* been reading both
sides of a member, mixed triples was 475,824 chances to catch it, and it caught
nothing formatter-side.

The probe is mechanical, which is what makes the numbers comparable across axes:
for every fixture in the corpus, insert a marked comment (or a run of *N*, or a
run of a given composition) into **every** inter-token gap, format twice, and
require the two outputs to be byte-identical.

### 8.2 What each gate cannot see

The last column is the important one, because it is what the next gate exists
for. The runnable version of this table, with flags, is
[testing.md](testing.md); the code-level version is
[commentAlgorithm.md §10](commentAlgorithm.md#10-coverage-what-each-gate-actually-varies).

| gate | what it varies | blind to |
|---|---|---|
| fixture suite (368 `.formatted.gren` across 12 suites) | hand-written cases | anything nobody thought to write |
| gap fuzzer | a comment in **every** inter-token gap of every fixture | only says *whether* something moved |
| ⤷ run of *N* | the same, with a run per gap | a uniform run has one boundary shape |
| ⤷ mixed pairs / triples | run **composition** | — |
| decision-stability checker | the same gaps and axes, but diffs the **decisions** | a decision nobody traced |
| whitespace fuzzer | inter-token whitespace | comments |
| syntax matrix vs. elm-format (68,922 comment cells) | 41 expression × 25 contexts + 11 type × 15 contexts, × 3 kinds × 2 positions | shapes outside its vocabulary |
| ⤷ comment runs (113,796 cells) | the same cells with a two-member run, all nine compositions | elm-format parity (deliberately not baselined) |
| random module generator | random-but-legal modules, structure **and** comments | shapes outside its grammar |
| the `RenderNode` type (§5.2) | — | only *positional* re-derivation, not the renderer's residual decisions |

Three holes that **look** covered:

- **A dropped comment passes almost everything.** Deleting a comment is
  AST-equivalent and the output is its own fixed point, so the end-to-end check
  passes and every stability check passes. Only a marker count and a multiset
  oracle can see it. Caught twice here, both times a renderer indexing a node's
  children positionally — in a formatter where **a comment is a child**. Not ours
  alone: as of 2026-08-23, rustfmt carried 89 issue titles reporting a deleted,
  removed, eaten or lost comment, 29 still open, one of which its reporter titled
  "*silently* removes comments".
- **A wrongly *attached* comment passes even those.** A multiset oracle discards
  positions on purpose, and a wrong-but-stable attachment is a perfectly good
  fixed point. The only gate that sees it is the author-order invariance oracle of
  §7.4, and that is something only a generator can do.
- **A run reassembled backwards is a perfectly good fixed point.** Tear a run
  across a separator with the mover written *first* and the output is stable,
  AST-equivalent and comment-preserving. Only the *ordered* marker oracles see it.
  The residual hole is stated rather than papered over: torn with the mover
  written *second*, the output comes out in source order and nothing in the
  portfolio can see it at all. That case is pinned by a fixture, and it was found
  by enumerating the grid, not by a gate. [prettier #10108](https://github.com/prettier/prettier/issues/10108) — "Comments in array:
  idempotence violation *and change of order*" — is the same shape, reported
  externally.

And one methodological finding that generalises well past formatters:

> **A gate green over the wrong axis is indistinguishable from a correct
> implementation.**

Our comment axis ran green for months over two of the three comment kinds. Adding
the third found 70 non-idempotencies the same afternoon. Check what a gate
*varies* before trusting what it reports.

### 8.3 Replaying our own history

The portfolio above is a design. This is it measured against the project's own
git history.

**Method.** Extract every fix commit from the formatter library's history (1,032
commits, 2026-03-01 → 2026-08-22) under two commit conventions — 113 subjects
beginning `Fix:` and 22 of the earlier `fix(formatter):` form — giving **135
candidates**. Hand-classify each for *what was wrong* and *whose bug it was*,
deliberately **never from the firing oracle**, since deriving the class from the
detector would make the cross-tab a tautology. Then replay: check out the parent
commit (bug present), build there, run today's oracle portfolio against the one
triggering input, record fires / does not. Build the fix commit and re-run. An
oracle is a **witness** when it fires at the parent and is clean at the fix.

Four aspects of that method were forced by confounds the first runs exposed, and
each is worth copying:

- **The candidate count is an upper bound on bugs, not a bug count.** It includes
  chores whose subject begins `Fix` — "Fix up test scripts", "Fix stale module
  names in the docs". Curation reclassified **16 of 135** as non-bugs (docs 8,
  test-tooling 4, chore 2, build 1, test-fixture 1). The raw extractor count was
  13% too high.
- **The fixture-suite column is excluded from the oracle vector**, because the
  pinned fixture is *added by the fix commit*: "the fixture suite catches it" is
  vacuously true at the child and vacuously false at the parent. For the same
  reason the harness checks out the parent's formatter source but takes the
  *test-side* tooling from HEAD — the measurement wanted is today's oracle against
  the pre-fix formatter.
- **The crash confound.** When the formatter dies there is no output to judge, so
  every oracle that runs it reports failure. The first replayed row came back with
  five apparent co-witnesses; that is not five independent detections. A crash is
  attributed to the end-to-end check alone. Formally, the oracles are conditionally
  independent only *given that the formatter produced output*.
- **The unavailable-oracle confound.** Under a contemporaneous build, flags added
  later do not exist, and "I don't recognise this flag" is not the same as an
  oracle running clean. Such oracles are recorded unavailable and excluded from
  both the witness and persistent sets, with the exclusion visible on every row.

One further finding qualifies every number below, and it is the one most likely
to bite anyone attempting this: **a pinned fixture is not necessarily the
trigger.** One oscillation fix has a commit message spelling out the input
exactly; the fixture it pinned writes the same case with the body on the row
*below*. Replayed at the parent, the fixture's spelling is stable and clean at
both ends, while the message's spelling reproduces the oscillation. The reason is
structural, not accidental: a fixture is authored *after* the fix, to illustrate
the case for a reader, and it pins the fixed *output*. The unit of measurement is
a **(bug, input)** pair, not a bug.

**Replay status over the 135 candidates:**

| status | n | share |
|---|---:|---:|
| `documentary` (pre-package era; prose provenance only) | 53 | 39% |
| `measured` (an oracle fired at the parent, clean at the fix) | 37 | 27% |
| `stable-divergence` (output changed; **nothing** fired at either end) | 21 | 16% |
| `non-bug` (curated out) | 16 | 12% |
| `unreplayable` (build failed at both ends) | 5 | 4% |
| `not-reproduced` (identical output at both ends) | 3 | 2% |

The last two rows are **reported, never dropped**. A harness that quietly
discards the rows it cannot explain is exactly the vacuous-coverage failure this
project has documented one level down.

### 8.4 The headline

Of the **61** rows that built at both ends with a usable input: 37 (61%) were
witnessed by some property oracle. **21 (34%) were invisible to the entire
property portfolio** — the output changed, and was *wrong but stable*:
AST-equivalent, its own fixed point, every comment preserved, the end-to-end
check exiting 0 at the parent.

**Class × visibility to the property portfolio:**

| class | `measured` | `stable-divergence` | `not-reproduced` | invisible |
|---|---:|---:|---:|---:|
| **layout** | 0 | 16 | 1 | **94%** |
| oscillation | 16 | 0 | 1 | 0% |
| crash | 8 | 0 | 0 | 0% |
| wrong-attachment | 5 | 0 | 1 | 0% |
| performance | 4 | 0 | 0 | 0% |
| mixed | 1 | 3 | 0 | 75% |
| dropped-content | 2 | 1 | 0 | 33% |
| blank-lines | 0 | 1 | 0 | 100% |
| literal-corruption | 1 | 0 | 0 | 0% |

The separation is almost perfect. The property portfolio caught **every** crash,
oscillation, wrong-attachment and performance bug it was given — and **none of
the seventeen layout bugs**, the single largest class in the corpus. Spot-checked
by hand: at one such parent, a `when` used as a call argument has its branch
bodies indented by 2 instead of 4 — 22 lines different from the fix's output, and
invisible to every property oracle at both ends.

> A property oracle asks "did the output violate an invariant?" A layout bug
> violates none. The output is complete, stable, AST-equivalent and
> comment-preserving, and merely **wrong**. Only an expected answer, or a second
> implementation to compare against, can say so.

That is a *stronger* claim than this project's own documentation made. The blind
spot our docs emphasise is the dropped comment; the largest measured one is
wrong-but-stable layout, at a third of the replayable corpus.

**Two details that the oracle name alone misreports.** Over the 37 measured rows
the end-to-end check witnessed 36 and was the *sole* witness for 33; 34 rows have
a single witness. And detection *mode* matters:

| mode | n |
|---|---:|
| `property` — an oracle detected a violated property | 31 |
| `timeout` — a wall-clock bound; the bug was a hang | 4 |
| `crash` — the formatter died; attributed to one oracle | 2 |

The four performance bugs are caught by **the clock**, not by any property, and
the bound rides on whichever oracle happens to run the formatter. One
deeply-nested record literal reads as "sole witness: the predicate auditor" only
because the end-to-end check finished inside the limit and the predicate auditor,
which renders more, did not. Crediting a predicate auditor with catching a hang
would be wrong. Performance is a **distinct detection channel**: a hang produces
no wrong output, it produces no output, so no correctness oracle subsumes the
gates that time things.

### 8.5 The one gate whose inputs nobody here chose

Everything above is synthetic. The matrix builds cells from a vocabulary this
project authored, the fuzzers perturb a corpus it wrote, and the generator emits
from a grammar it specified — so all of them reach shapes somebody here thought
of.

A sweep over ten published packages found **nine bugs in five classes**, every
one a *feature conjunction* no single-axis gate could produce: multi-line string
× trailing whitespace × nesting; author-broken record × arrow position; pipe ×
record argument × `else if`; binop × comment × bracket operand; call × three-or-
more multi-line block arguments.

Keep one gate whose inputs came from outside the project. It is the only defence
against a portfolio that is exhaustive over your own imagination.

### 8.6 Differential comparison, and what it costs

Gren is a fork of Elm, so for shared constructs `gren-format` and `elm-format`
should agree — and where they do not, the disagreement should be a decision on
record. The matrix translates each generated cell to Elm, runs elm-format, and
diffs.

elm-format is **not an oracle**: the two tools diverge on purpose in 34
catalogued places, each with a rationale and a fixture. So parity is gated
against a **reviewed baseline**. An unregistered divergence fails; a registered
divergence that *disappears* also fails; every reviewed cell names a catalogue
entry; and `UNREVIEWED` is a debt counter printed on every run.

Over 68,922 comment cells: **0 failing, 0 UNREVIEWED**.

The cost is the part worth knowing before adopting the technique. Getting to that
zero meant reading 16,141 unreviewed cells down to none over several sittings —
and doing so **found two formatter bugs on the way**. That is the argument for
reading the debt rather than widening a classifier until the counter reaches
zero.

**The comparison runs both ways.** One divergence, triaged, turned out to be the
*reference* implementation's bug: for a pipeline whose last step forces a
multi-line block, elm-format produces two different outputs for semantically
identical code, depending only on how the author happened to break the source
lines. We reported it as
[elm-format#842](https://github.com/avh4/elm-format/issues/842), where the
discussion established something the divergence itself had not shown — running
elm-format again on the first output yields the second, so the first output is
**not a fixed point**. elm-format converges in two passes on that input.

It is worth dwelling on, because it lands on three separate claims here. It is
the classic differential-testing result reproduced in miniature. It is the
strongest justification for treating a second implementation as a *reviewed
baseline* rather than an oracle: either side can be the wrong one, and once it
was. And it is §3.2's formula appearing in a mature, **trivia-preserving**
implementation — an output whose layout was decided from incidental input line
breaks that the formatting then changed.

---

## 9. What fourteen other formatters do

**All counts and repository states in this section are as of 2026-08-23**, the
date of the pull. Trackers are live and the figures move; the frozen pulls and
the per-claim `file:line` citations are in
`gren-format-papers/related/` — `formatter-survey.md`, `tracker-mining.md`, and
three `.tsv` exports. That is a sibling working repository, not part of this
package, so the references below are paths rather than links.

We read the sources of fourteen production formatters. Beyond §1's input rung,
three axes separate them: *when* placement is decided; whether layout is
width-aware or author-driven; and — the axis that is almost never named —
whether the stage that decides breaks and verticality may read a source position
at all. We call that last one **the barrier** (§5).

The barrier is not implied by deciding once. A tool can decide attachment
exactly once and still let every layout rule re-derive verticality from the
author's rows. It is also a stronger bar than "the fitter is positionless" —
several tools reach their fitter through a tree walk that is itself full of
layout choices, and it is that walk the barrier has to cover.

| formatter | comments arrive as | placement decided | layout | barrier? |
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

| | **barrier** | **no barrier** |
|---|---|---|
| **A0–A2** — comment arrives attached | all eight (google-java-format: partial) | — |
| **A3–A4** — attachment must be reconstructed | **gren-format, alone** | prettier, ocamlformat, ormolu, gofmt, rustfmt, zig |

**Every tool whose front end hands it attached comments has the barrier, and gets
it for free** — there is no positional question left for the layout stage to ask,
so nothing had to be forbidden. **Every tool that must reconstruct attachment
lacks it, except ours.** That column is also where every instability in this
survey lives.

The point of stating it that way is not to claim a trophy. It is to be precise
about what is worth copying: the barrier is not an idea (swift-format wrote it
down in 2020) and not a rarity (eight of fifteen have it). What is worth copying
is having it in the **position where it has to be built rather than inherited**,
and enforcing it with the type checker so that keeping it does not cost you a
feature.

### 9.1 Deciding once is the norm, not the contribution

Ten of the fifteen classify each comment exactly once, before printing, into a
small finite role set: Black's two (`COMMENT` / `STANDALONE_COMMENT`),
ocamlformat's three, dart_style's four, elm-format's five slots, ours seven,
swift-format's four kinds crossed with a boolean, prettier's and biome's 3×3.
Every one is small, finite and fixed before layout.

So §6.2's premise is not an assumption anyone needs to defend; it is what
production formatters already do. What differs is **what the classifier may
read** and **what happens downstream**. Two of the tools that decide once still
oscillate — prettier and ocamlformat — and they are exactly the two whose
*layout* stage reads positions.

### 9.2 biome and CSharpier are the controlled experiment

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

### 9.3 ocamlformat ships an iteration instead of an argument

ocamlformat decides once, and its printer still reads `Source.begins_line` and
`Source.empty_line_between`. So `Translation_unit.ml` does not format the file;
it formats it repeatedly. The comment above the loop is `(* iterate until
formatting stabilizes *)`, bounded by a user-facing `--max-iters`, default
**10**, after which it emits `BUG: formatting did not stabilize after %i
iterations`. Comment preservation is re-checked on every iteration.

This is the most direct external evidence that the instability class of §3.2 is
real, general, and unsolved: a mature, widely used formatter's shipped answer is
*run it up to ten times and report a bug if it still moves.*

### 9.4 gofmt has the strongest corpus gate in the survey, and an exemption inside it

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

[golang/go#24472](https://github.com/golang/go/issues/24472) is open, labelled `NeedsInvestigation`, seven and a half years
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

This is §6.7's argument made by the Go project against itself, and it is the
sharpest form of the thesis available anywhere in the survey: **the corpus gate is
not the missing piece.** gofmt has a bigger one than we do. What it does not have
is an architecture in which the answer cannot go stale — so the gate finds the
instance, the instance resists fixing, and the file gets an exemption.

### 9.5 The barrier is necessary and demonstrably not sufficient

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

Read that last row of the table together with §5.4: four tools have the barrier
and no coverage argument, all four still ship the bug, and three compensate with
a runtime check. Black's case is additionally the shape §6 does not cover — the
classifier answering differently because its *anchor* moved, not its rows — which
is §7.

### 9.6 swift-format is the control condition

This is the survey's single most useful external datapoint, and it deserves the
whole arc rather than the commit message, because each stage of it is a stage of
this document's argument.

**The rule.** `BlankLineBetweenMembers` shipped in swift-format's initial
implementation (2019-07-10): at least one blank line between each member of a
type. It is a phase-1 rule — it rewrites the syntax tree before the Oppen printer
runs — and it decides whether to insert the blank line from
`isSingleLine(includingLeadingComment:sourceLocationConverter:)`, whose body ends:

```swift
let startLocation = sourceLocationConverter.location(for: startPosition)
let endLocation = sourceLocationConverter.location(
  for: lastToken.endPositionBeforeTrailingTrivia)
return startLocation.line == endLocation.line
```

That is "did the author span rows", read off the input, consumed by a layout
decision — §3.2's mechanism exactly, in a tool that otherwise holds the barrier
cleanly (every line-number read in `PrettyPrint.swift` is
`outputBuffer.lineNumber`, the line being *written*). The rule is the one place
the barrier was broken, and swift-format broke it in the same place we did (§5.3):
not in the printer, but in a pass that runs before it and reasons about what the
printer will do.

**Two attempts to patch it, both about comments.** The failures did not present
as "non-idempotent". They presented as comment bugs, which is why they were
patched locally twice before the class was recognised:

> "The rule was overly eager in adding blank lines around members that have
> **comments in their leading trivia**, because it didn't always correctly
> determine whether a given comment should trigger blank lines around the
> member." — 2019-11-12

> "When we were doing the is-single-line check, we weren't considering that the
> first comment in the trivia might precede any newlines in that trivia,
> **meaning it's an end-of-line comment for a previous line, not a line comment
> that we should consider part of the current decl.**" — 2019-11-20

The second is this document's §3 in another language: deciding, from row
adjacency in the trivia, whether a comment belongs to the declaration that
follows it or to the line above. Eight days separate the two patches. Both are
local fixes to instances of a class, and both hold.

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

The first paragraph is §5.1's sentence, reached independently, six years earlier.

**The capability was withdrawn too, two days later.** 2020-02-01, "Delete some
dead code", removes the `isSingleLine(includingLeadingComment:…)` accessor.
Deleting the rule left it unused; deleting the accessor is a separate and
stronger decision — it makes the question *unaskable* rather than merely unasked.
That is the same remedy as §5.2's, arrived at independently and enforced by
review rather than by a type.

**And the correct fix was never performed.** At the survey tip (2026-08-21, six
years and seven months later) there is no `BlankLineBetweenMembers` rule, no
configuration key, and nothing in `PrettyPrint.swift` that inserts a blank line
between members. The break-based reimplementation the commit proposed does not
exist. swift-format no longer enforces the "at least one blank line between each
member of a type" requirement it was built to enforce. What it does instead is
preserve: `respectsExistingLineBreaks` keeps whatever the author wrote, clamped by
`maximumBlankLines`. The feature was traded for the invariant and the trade was
never revisited.

**The principle held, though**, and that is checkable: of swift-format's 44 rules,
exactly three mention `sourceLocationConverter` today, and all three use it to
build a diagnostic's `Finding.Location`. Not one phase-1 rule reads an input row
to decide layout. Six years, no regression, no allowlist, no exemption file.

**How to read this.** swift-format is not a counterexample and not a precedent
that dissolves anything here. It is the *control condition*. It shows that the
principle is discoverable without the architecture (they found it), that finding
it does not by itself yield the feature (they lost it), and that holding it by
review is possible but expensive (six years of not re-adding a rule the style
guide asks for).

The claim worth making is not that the rule is novel. It is that **enforcement
makes the rule affordable**: `Formatter.RenderTree` deletes the *field* rather
than the *feature*, so our analogue of `BlankLineBetweenMembers` — the
vertical-space pass — can exist, because the fact it needs is computed once at
the barrier as author intent and does not go stale. swift-format had to choose
between the rule and the invariant. The point of the barrier being a type is not
having to choose.

*One open lead we could not close.* swift-format's `OrderedImports` still
reconstructs comment attachment from row adjacency — `generateLines` walks the
leading trivia and emits a `Line` per newline, so a standalone comment becomes its
own `Line` and a trailing comment rides with its code — and it is also the only
one of the 44 rules that **sorts**. That is §7's anchor-move shape exactly. Two
further rules relocate syntax that can carry comments without sorting it
(`FullyIndirectEnum` hoists a modifier, `NoCasesWithOnlyFallthrough` merges
cases), so it is a small family rather than a single site. We flag it as a lead,
not a finding: we read the source but did not run the tool, which needs a Swift
toolchain we did not have. It is recorded because it is exactly where our own
equivalent defects have been, and because it is cheap for someone with a Swift
toolchain to check.

### 9.7 Runs, independently corroborated

§6 spends a long time on runs of comments. Four of the surveyed tools model runs
explicitly and three have shipped run bugs.

dart_style's `CommentSequence` is documented as *n* comments and *n+1* newline
counts — §6.4's boundary counting, arrived at independently. ocamlformat groups
adjacent comments before deciding and decides the group as a unit. gofmt passes
`prev *ast.Comment`, "the previous comment in a group" — **exactly one
neighbour**, which is §6.4's rule. Black's `list_comments` is itself a run scanner
in which only the *first* line of a prefix can be a trailing comment and every
later one is standalone — §6.4 in its most reduced possible form.

And the bugs. Ormolu's 0.1.0.0 changelog fixes **five** comment-idempotence bugs
in a single release, and two of them are run bugs in the project's own words:
comments "picked up as 'continuation' of a series of comments"
([#449](https://github.com/tweag/ormolu/issues/449)) and "different indentation levels in a
comment series" ([#512](https://github.com/tweag/ormolu/issues/512)). rustfmt
[#7019](https://github.com/rust-lang/rustfmt/issues/7019), "Non-idempotency in
consecutive block comment", was filed 2026-08-10 — in a formatter with a
400-file idempotency gate.

That is §6.5's sentence — *a corpus of hand-written fixtures will not contain the
mixed run that breaks it* — coming true twice, independently, in other projects.

### 9.8 The two trackers, read against each other

*rustfmt is in the identical position to ours.* It reuses the production Rust
compiler's parser, whose AST carries no ordinary comments, and recovers them from
**"missing" source snippets** — the raw text between the last emitted byte
position and the next node's span. Its `A-comments` label carried **447 issues,
147 open**; **89 titles report a comment being removed, deleted, eaten or lost**,
29 still open, the oldest from 2019. Twelve titles report non-idempotency,
including [#7019](https://github.com/rust-lang/rustfmt/issues/7019) above and [#6347](https://github.com/rust-lang/rustfmt/issues/6347), "rustfmt forcefully moves trailing comments to
irrelevant code above (and not idempotent either)" — which is §3.2's picture in
Rust. Three more report a comment migrating between owners across an import
reordering ([#5485](https://github.com/rust-lang/rustfmt/issues/5485), [#6241](https://github.com/rust-lang/rustfmt/issues/6241), [#3127](https://github.com/rust-lang/rustfmt/issues/3127)) — §7's class, and the one §8.2 says only an
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

### 9.9 The exempt-rather-than-fix reflex is general

Beside gofmt's filename exemption: Black keeps an expected-*failure* fixture set
"with the unstable formattings" and ships `--unstable` as a release channel;
swift-format deletes the rule; ocamlformat iterates ten times; topiary reserves an
exit code. Five projects, five ways of declining a fix, each an implicit judgement
that the class is architectural rather than local.

Runtime fixed-point checking is itself a recognised pattern — four of the fourteen
ship one. ormolu offers `--check-idempotence`; ocamlformat iterates; topiary
checks by default (`--skip-idempotence` is the opt-out, with its own exit code);
Black checks by default in `--safe` mode, alongside an AST equivalence check. Its
source names the failure mode precisely:

> "We shouldn't call `format_str()` here, because that formats the string twice
> and may hide a bug where we **bounce back and forth between two versions**."

Three of those four tools *have* the barrier and check anyway.

---

## 10. If you are building one

Distilled, in the order we would do it again.

1. **Locate yourself on §1's ladder before anything else.** If your parser can be
   made to carry trivia, do that instead and stop reading. Everything below is the
   cost of the other branch.

2. **Decide every placement once, while the rows are still the author's, and
   store the answer as a value in the tree.** Not a cache — the only copy. This is
   the norm among production formatters, not an innovation (§9.1).

3. **Make the classifier total.** No `Maybe`, no fallback arm, no "the renderer
   will figure it out". A total classifier turns *n* comments into *n* independent
   annotations instead of a configuration space, which is what makes §6's argument
   available at all.

4. **Write the classifier to a stated criterion**, and make it the one in §4: *the
   row a placement is decided from must equal the row it renders on.* When a rule
   cannot satisfy it in place, do not write a cleverer rule — make the first pass
   build the tree the second pass would build.

5. **Build the barrier, and make it a type rather than a lint.** Give the
   rendering stage a *different type* with the positions removed, reachable only
   through a lowering function. Precompute, at that boundary, the handful of facts
   that genuinely need the author's rows. A grep-based barrier is a reviewed
   convention wearing a script's clothes; ours was, and we deleted it.

6. **Apply the criterion to every row-keyed decision, not just the ones that
   produce a placement.** Our sorting and blank-line passes escaped it for months
   because they do not emit a role. swift-format's deleted rule escaped it in the
   same way — a pass that runs *before* the printer and reasons about what the
   printer will do (§5.3, §9.6).

7. **Enumerate what each gate cannot see, and write the column down.** The three
   holes in §8.2 all look covered. A dropped comment passes almost everything; a
   wrongly-attached comment passes even the multiset oracle; a run reassembled
   backwards is a perfectly good fixed point.

8. **If your formatter rewrites tokens, build an oracle that varies the input's
   authoring** — emit the same program spelled two legal ways and require the same
   bytes. Idempotency testing cannot discharge that obligation, because the class
   has members that *are* fixed points (§7.4). Ours has exactly one such oracle,
   and it found the two bugs the six idempotency axes read as zero.

9. **Keep both kinds of test.** Property gates caught every crash, oscillation,
   wrong-attachment and performance bug in our history — and none of the seventeen
   layout bugs (§8.4). Expected-bytes fixtures and a second implementation are the
   only things that see a wrong-but-stable answer.

10. **Keep one gate whose inputs nobody on the project chose.** A real-corpus
    sweep found nine bugs, every one a feature conjunction no single-axis gate
    could generate (§8.5).

11. **Time things, and treat the clock as its own detection channel.** A hang
    produces no wrong output; it produces no output. No correctness oracle
    subsumes it (§8.4).

12. **Expect the barrier not to be sufficient.** Four of five barriered tools we
    mined still shipped idempotency fixes (§9.5). What the barrier buys is
    *localisation*: the whole obligation collapses onto one total function with a
    finite codomain, which is small enough to argue about. Something still has to
    discharge it.

**The transferable shape, stated without formatters.** "Decide once, behind an
enforced barrier" is a pattern for any pass whose *own output destroys the
evidence its input decisions were made from*. Two components are load-bearing and
separable: the decision must be recorded as a value (not recomputed on demand),
and the ban on re-deriving it must be mechanically checked rather than
documented. The second is what turns "we were careful" into "the mistake is
unrepresentable".

Two limits, both visible in §9. The pattern **composes only if the composition is
inside the barrier** — biome's only idempotency bugs are at embedded-language
seams, where one barriered formatter's output becomes another barriered
formatter's input and the outer one re-reads it. And the barrier **relocates** the
obligation rather than removing it — topiary's engine cannot have the bug, so it
lives in the per-language query files instead, for four years and counting.
Relocating an obligation onto a small, total, finite-codomain function is progress
precisely because something can then be argued about it. Relocating it onto a
declarative rule language is not.

---

## Sources

Every formatter named in this document, with the commit the survey behind §9 was
read at (2026-08-13 → 2026-08-23) and the code this document actually cites. They
are live repositories: expect the file paths to have moved, and re-read at the
tip rather than trusting a line number here.

| formatter | language | repository | read at | what is cited above |
|---|---|---|---|---|
| [topiary](https://github.com/tweag/topiary) | generic, over tree-sitter | `tweag/topiary` | `a307aee` | the engine's atom resolution; the per-language `.scm` query files where the obligation ended up (§9.5) |
| [elm-format](https://github.com/avh4/elm-format) | Elm | `avh4/elm-format` | `e7e5da37` | `AST/V0_16.hs` — named comment slots, no positions anywhere (§1, §9) |
| [dart_style](https://github.com/dart-lang/dart_style) | Dart | `dart-lang/dart_style` | `39edc2d9` | the positionless `Piece` IR; `CommentSequence`, *n* comments and *n+1* newline counts (§9.7) |
| [swift-format](https://github.com/swiftlang/swift-format) | Swift | `swiftlang/swift-format` | `9c9a9fa` | `SyntaxProtocol+Convenience.swift`, `PrettyPrint.swift`, the removed `BlankLineBetweenMembers` rule, `OrderedImports.swift` (§9.6) |
| [google-java-format](https://github.com/google/google-java-format) | Java | `google/google-java-format` | `b291d95` | the `Doc` fitter and the two positional reads in the op-builder (§9) |
| [CSharpier](https://github.com/belav/csharpier) | C# | `belav/csharpier` | `c8ac0cb` | the [Roslyn](https://github.com/dotnet/roslyn)-trivia walk; half of the controlled experiment (§9.2) |
| [biome](https://github.com/biomejs/biome) | JS/TS/CSS | `biomejs/biome` | `7a111ba7` | `piece.is_newline()` over [rowan](https://github.com/rust-analyzer/rowan) trivia, where prettier reads `originalText` (§9.2) |
| [Black](https://github.com/psf/black) | Python | `psf/black` | `8947c48` | `list_comments`, the comment-placement docstring quoted in §3.3, `assert_stable` and the "bounce back and forth" comment (§9.9) |
| [prettier](https://github.com/prettier/prettier) | JS/TS and more | `prettier/prettier` | `d9969c573` | `src/main/comments/attach.js`; the 19 of 73 print modules that read the source (§9.2, §9.8) |
| [ocamlformat](https://github.com/ocaml-ppx/ocamlformat) | OCaml | `ocaml-ppx/ocamlformat` | `20c45431` | `Cmts.init`; `Source.begins_line` / `empty_line_between` in the printer; `Translation_unit.ml`'s `--max-iters` loop (§9.3) |
| [ormolu](https://github.com/tweag/ormolu) | Haskell | `tweag/ormolu` | `d5727c0` | `spitPrecedingComment`, `--check-idempotence`, the five 0.1.0.0 comment fixes (§9.7, §9.9) |
| [gofmt / `go/printer`](https://github.com/golang/go/tree/master/src/go/printer) | Go | `golang/go` | `c97cfcb37f` | `writeCommentPrefix`; `cmd/gofmt/long_test.go` and its exemption; `go/printer/printer_test.go`'s per-file opt-out (§9.4) |
| [rustfmt](https://github.com/rust-lang/rustfmt) | Rust | `rust-lang/rustfmt` | `1191d91d` | `src/missed_spans.rs` (`format_missing`); `src/test/mod.rs`'s 400-file gate (§9.8) |
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

**Issue trackers.** The counts in §8.2 and §9.8 are frozen pulls dated
2026-08-23, filed under `gren-format-papers/related/` as
`rustfmt-comments.tsv`, `prettier-area-comments.tsv` and
`prettier-area-idempotency.tsv`. The labels they were pulled from are
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
  behind §5.2's second tree.
- [Comparison with elm-format](elmFormatComparison.md) — the 34 catalogued
  divergences, each with a reason and a fixture.
- [Known limitations](knownLimitations.md) — including the upstream parser bugs
  that make up the entire residual non-idempotency.

**Outside it** (the sibling `gren-format-papers/` working repository, not
published with this package)

- `gren-format-papers/related/formatter-survey.md` — the full survey behind §9,
  with repository commits and per-claim `file:line` citations.
- `gren-format-papers/related/tracker-mining.md` and the three `.tsv` pulls beside
  it — the frozen tracker exports behind §8.2 and §9.8.
- `gren-format-papers/bugReview.md`, `bugs.jsonl`, `results.jsonl` — the method
  and data behind §8.3–§8.4; the tables regenerate from the repositories.
