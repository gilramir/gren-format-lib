# The comment algorithm

How `gren format` places comments — the *implementation*, for people who work on
the formatter.

This is the companion to [How gren-format places your
comments](commentHandling.md), which is the reader-facing statement of *what*
the formatter does (rules C1–C6, with a before/after for each). This document
answers the next question: **how**, and — more to the point — **why we believe
it handles every case**.

It is written for someone who has never touched this codebase. It assumes you
have read [How the formatter works](howItWorks.md) and know what the Logical
Printing Tree (LPT) and the Box tree are. If you are adding a new construct,
read [DEVELOPER.md](../DEVELOPER.md) as well — it has the checklist; this
document has the model behind it.

---

## Table of contents

- [1. The problem](#1-the-problem)
  - [1.1 What the formatter is actually handed](#11-what-the-formatter-is-actually-handed)
  - [1.2 What "correct" means here](#12-what-correct-means-here)
  - [1.3 Why the fixed point is the hard half](#13-why-the-fixed-point-is-the-hard-half)
- [2. The one idea](#2-the-one-idea)
  - [2.1 `CommentRole`](#21-commentrole)
  - [2.2 The barrier](#22-the-barrier)
- [3. The pipeline](#3-the-pipeline)
- [4. Stage 1 — attachment](#4-stage-1--attachment)
  - [4.1 The fold](#41-the-fold)
  - [4.2 Phase 1 — which declaration?](#42-phase-1--which-declaration)
  - [4.3 Phase 2 — how deep?](#43-phase-2--how-deep)
  - [4.4 Phase 3 — which gap?](#44-phase-3--which-gap)
  - [4.5 Phase 4 — which role?](#45-phase-4--which-role)
  - [4.6 Repairs that need the finished tree](#46-repairs-that-need-the-finished-tree)
  - [4.7 The two passes that run after](#47-the-two-passes-that-run-after)
- [5. Stage 2 — rendering](#5-stage-2--rendering)
  - [5.1 The two decision functions](#51-the-two-decision-functions)
  - [5.2 The three shapes of comment](#52-the-three-shapes-of-comment)
  - [5.3 The glue primitives](#53-the-glue-primitives)
- [6. Runs: any number, any kinds](#6-runs-any-number-any-kinds)
- [7. A worked example](#7-a-worked-example)
- [8. Why we believe it is complete](#8-why-we-believe-it-is-complete)
- [9. The functions to be careful about](#9-the-functions-to-be-careful-about)
- [10. Debugging a comment bug](#10-debugging-a-comment-bug)
- [11. Where the rules genuinely run out](#11-where-the-rules-genuinely-run-out)

---

## 1. The problem

### 1.1 What the formatter is actually handed

`gren-format` does not have its own parser. It uses the **production Gren
compiler's** parser (`gren-lang/compiler-common`), and that parser does what
every compiler's parser does: it throws comments away. They are not nodes in
`Src.Module`. There is nowhere in the AST for them to be.

What comes back instead is a second, parallel output — the parse `Context` —
holding a flat, source-ordered list of located comment strings:

```gren
type Comment
    = Line String     -- `-- like this`, text after the `--`
    | Block String    -- `{- like this -}`, text between the delimiters

-- and the context carries: Builder (Located Comment)
-- where `Located` is a start (row, col) and an end (row, col).
```

That is the entire input. For this source (`S.gren`):

```gren
module S exposing (sizes)


sizes =
    [ 1 -- one
    , 2
    ]
```

the formatter receives an AST for `sizes = [1, 2]` and, separately, this —
which you can print for any file with `--pre-context`:

```json
{ "comments": [ { "type": "line", "value": " one",
                  "start": { "row": 5, "col": 9 },
                  "end":   { "row": 5, "col": 15 } } ] }
```

Nothing connects the two. The comment does not know it is inside an array; the
array does not know a comment exists. All the algorithm has to work with is that
`(row, col)` pair and the row ranges of the tree it is placing the comment into.

Re-attaching them is this algorithm.

> **Contrast with elm-format.** elm-format's parser is its own, and it builds
> comments *into* the AST — an expression node physically holds the comments
> written around it. It never has to solve this problem, and much of its comment
> code has no counterpart here. We take the opposite trade: we are always
> parsing exactly what the compiler parses (a formatter that accepts a
> different language than the compiler is a bug factory), and we pay for it with
> the re-attachment pass described below. This trade is discussed at length in
> [DEVELOPER.md](../DEVELOPER.md#why-the-architecture-is-comment-driven--contrasted-with-elm-format).

### 1.2 What "correct" means here

Three properties, and all three are gated (§8):

1. **Preservation.** Every comment in the input appears exactly once in the
   output, with its text unchanged and its kind unchanged. `gren format` never
   edits comment text. (It does re-*indent* the continuation lines of a
   multi-line `{- … -}`; that is layout, not text.)
2. **Faithful placement.** The comment lands beside the code the author wrote it
   beside — rules **C1–C6** in [commentHandling.md](commentHandling.md).
3. **Idempotency.** `format(format(x)) == format(x)`, byte for byte. Formatting
   is a fixed point.

Property 3 is not a nicety. `gren format` runs in editors on save and in CI. A
file that alternates between two spellings produces phantom diffs for ever, and
"the formatter is unstable" is the fastest way to lose a team's trust in it.

### 1.3 Why the fixed point is the hard half

Here is the whole difficulty in one picture. The formatter decides where a
comment goes **by looking at the row and column the author wrote it on** — that
is the only signal it has. But formatting *moves code onto different rows*. So
the second format asks the same question of different evidence.

Concretely, the recurring failure looks like this:

```
you wrote:                  format¹ produced:        format² produces:

v =                         v =                      v =
    fn a b {- c                 fn a b                    fn a b
   second -}                    {- c                 {- c
                                   second -}            second -}
```

Format¹ read "the comment is on the declaration's last row" and rendered it at
the call's indent. But a multi-line comment cannot sit *on* that row — it brings
its own newlines — so it landed **below** the declaration. Format² now reads a
comment written below a declaration, which is a different question with a
different answer (detach to column 1), and the file has two spellings.

Every comment bug this project has fixed is a variation on that sentence: **a
placement decided from a fact the formatting itself invalidates.** Keep it in
mind; §4 and §5 are largely a catalogue of the places where that can happen and
what each does about it.

(That particular shape is fixed — today's format¹ emits the column-1 form
directly, which is `detachOwnLineTrailer`'s whole job; see §4.6. It is shown
here because it is the *archetype*, and because the general strategy it forced
is the subject of the next section.)

---

## 2. The one idea

### 2.1 `CommentRole`

Every comment's placement is decided **exactly once**, in the Logical stage,
while the source rows are still the author's, and the answer is stored on the
comment leaf as a `CommentRole`. The renderer reads the stored answer. It never
recomputes it.

```gren
type CommentRole
    = TrailsPrevious   -- glue onto the previous sibling's last rendered line
    | LeadsOwnLine     -- own line, at the current flow/body indent
    | LeadsNext        -- belongs to the sibling AFTER an unrecorded separator
    | TrailsHead       -- glue onto the container's head (a record update's base)
    | RidesInline      -- rides mid-line without breaking it (`f {- k -} x`)
    | LeadsInline      -- glued to the FRONT of a declaration (`{- c -} import Qux`)
    | Standalone       -- a detached top-level comment, its own column-1 node
```

The full docstring lives beside the type in
`src/Formatter/Logical/LogicalPrintingTree.gren` and is the normative statement
of what each role means; do not restate it elsewhere, extend it there.

You can see the roles for any file:

```bash
node ../gren-format/app --lpt MyFile.gren     # JSON; each comment leaf has "role"
```

Here is one file exercising all seven. (Every example in this document was
produced by running the formatter; this one's roles are `--lpt`'s own output.)

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
| `-- start here` | `LeadsOwnLine` |
| `-- the seed` | `TrailsPrevious` |
| `{- two -}` | `LeadsNext` |
| `-- detached below b` | `Standalone` |

### 2.2 The barrier

The corollary of "decided once" is a hard architectural rule:

> **No code under `Formatter/Render/` may read a source row or position to make
> a layout or comment-placement decision.**

Placement is the stored `CommentRole`. Verticality is the *rendered box shape*
(`isSingleLine` on a box that has already been built), never "did the author
span rows". The renderer's remaining questions about a comment are about its
**text** — can this text share a line? — which is a property of the string, not
of where it sat.

This is enforced, not merely documented: `tests/check-render-invariant.py` runs
first in `run-tests.sh`, greps `Render/*` for row/position accessors and fails on
any outside a small allowlist of genuinely structural helpers. A new
render-side row read is almost always a regression back toward the oscillation
class above.

Where a renderer *does* need to know something structural about a comment, it
asks a predicate that reads LPT shape and comment text only —
`NodeClassify.commentEndsItsLine`, `commentTextCanRide`,
`subtreeEndsWithLineComment`. Each of those docstrings says explicitly that it
reads no rows, because that is the property that lets it live in `Render/`.

---

## 3. The pipeline

![The comment pipeline](diagrams/comment-pipeline.png)

*(Source: `docs/diagrams/comment-pipeline.dot`; regenerate with
`docs/diagrams/generate.sh`, which needs graphviz.)*

In words:

1. `MakeLogical.lptFromAst` builds the LPT from the AST **alone**. At this point
   there are no comments in the tree, and every node's row range is exactly what
   the author wrote.
2. `Comments.lptAddComments` folds over the comment stream in source order,
   placing each one. Four phases per comment (§4.2–§4.5).
3. Two repairs run over the finished tree, for placements that only become
   wrong once you can see the whole thing (§4.6).
4. `SortSymbols` reorders exposing lists and import groups — comments have to
   travel with the names that own them (§4.7).
5. `VerticalSpace` inserts blank lines, which is a comment question too: a
   comment run is a unit and the gap belongs to the run, not to its first member
   (§4.7).
6. Everything below the barrier renders from stored roles (§5).

---

## 4. Stage 1 — attachment

All of this is `src/Formatter/Logical/Comments.gren`. It is the single largest
module in the formatter (~3,000 lines), and roughly two thirds of that is
docstrings explaining why a branch exists. Read them; nearly every one names the
shape that broke without it.

### 4.1 The fold

The entry point:

```gren
lptAddComments : Ctx.Context -> LPNode -> LPNode
```

It folds `addOneComment` over the comment stream, threading a `CommentState`:

```gren
type alias CommentState =
    { settled : Builder.Builder LPNode   -- root children no later comment can reach
    , target  : Maybe LPNode             -- the one that might still receive comments
    , rest    : Array LPNode             -- everything after `target`
    }
```

This shape is a performance fix, and it matters on real files: comments arrive
in row order and the root's children are already in sorted, non-overlapping row
order, so once a comment's row passes a child, no *later* comment can reach that
child again. Retiring it into `settled` (an `Array.Builder`, amortized O(1))
makes the whole sweep O(n + m) instead of O(n²) on a file that is mostly
top-level comments.

`addOneComment` has one special case before the generic path:
`tryLeadingGluedAttach`, which recognises a **block comment glued to the front**
of a top-level declaration — `{- c -} import Qux` — and attaches it as a leading
child with role `LeadsInline`. It keys on the comment's **end** row (so a
multi-line comment whose last row carries the keyword is recognised too), where
everything else keys on the start row. Getting this wrong doesn't just misplace
the comment: a comment that fails to travel with its import breaks an import run
in two when the imports sort.

Everything else goes through `addCommentGeneric` → the four phases.

### 4.2 Phase 1 — which declaration?

`findOrCreateOrigRow row stype children`

The root's children are one `OriginalRows` node per top-level declaration, each
carrying a `{ first, last }` source-row range. Scan for the one whose range
covers the comment's row.

**If none does, the comment detaches**: a fresh `OriginalRows` at column 1 is
spliced in at the sorted position, and the comment's role is preset to
`Standalone`.

That is a deliberate, load-bearing choice, not a fallback. A comment written on
its own line below a declaration is **never** attached to that declaration. Why:
column 1 is trivially a fixed point — it cannot drift — whereas any rule that
claims such a comment for the construct above has to place it at some indent,
and then re-derive the same claim from an indent that formatting has moved. An
earlier design did exactly that (a `columnClaim` rule) and it drifted leftward a
few columns per format. elm-format detaches these too.

```gren
-- you write:
b =
    1
    -- detached below b


c =
    2
```

```gren
-- gren-format writes (column 1, and stays there for ever):
b =
    1
-- detached below b


c =
    2
```

### 4.3 Phase 2 — how deep?

`insertCommentIntoSubtree commentNode row col node`

Recursive descent. At each level, pick the deepest child that owns the comment:

```gren
childOwnsComment =
    childHasBody
        && (((subtreeContainsRow child row || elasticTailOwnsComment)
                && not (subtreeStartsAfter child row col)
            )
                || commentAfterBracketOpen
           )
        && not laterSiblingClaims
        && not trailingCommentEscapes
```

Four questions, and each one is a class of bug that was found the hard way:

- **`subtreeContainsRow` / `subtreeStartsAfter`** — plain row containment, plus
  "a child that starts after the comment is never its owner". Row containment
  alone is not enough because sibling subtrees routinely overlap on a row.
- **`laterSiblingClaims`** (`anyLaterSiblingStartsAtOrBefore`) — if a *later*
  sibling's first real token is at or before the comment, this child's apparent
  overlap is a false positive and the comment really sits between the two. The
  canonical shape is two record types straddling a `->` in a signature.
- **`commentAfterBracketOpen`** — a comment written just past a `[`/`{`
  (`[ {- c -} 1, 2 ]`) *looks* like it comes before the container, because a
  bracket has no leaf of its own and the subtree appears to start at the first
  item. The recorded opening position (`lpnBracketStart`) is what says otherwise.
  **Only a bracket and a binary operator carry a recorded position in the Gren
  AST**; everything else the parser discards (see §4.5). So "inside or outside
  this container" is one of the few things we genuinely *know* rather than
  choose.
- **`trailingCommentEscapes`** — the inner half of the trailing-comment boundary
  rule, below.

#### The trailing-comment boundary rule

This is the single most important rule in the descent, and it is the direct
answer to §1.3.

A comment that merely *trails* a node's last token must **not** be sucked inside
that node — it belongs beside the node, at the enclosing level. The rest of this
section is why, since "it would oscillate" is an assertion until you can say what
forces the move.

**When does this even come up?** The guard fires in one specific situation:
the comment's row *is* inside the child's row range, but the comment sits past
the child's last token. Given that ranges are `min..max` rows, that means the
comment was written **on the child's last row, after everything on it** — the
one place where "inside this node" and "after this node" are both readable.

Here is that shape. A parenthesized lambda, with a multi-line comment written on
the body's row:

```gren
-- you write:
b =
    (\q ->
        body q {- c
   second -}
    )
```

```gren
-- gren-format writes (the comment belongs to the PAREN — it renders below the
-- body, at the paren's own column, which is `(`+1):
b =
    (\q ->
        body q
     {- c
        second -}
    )
```

The alternative — the one the guard rules out — is to let the comment descend
into the lambda body it was written on, which would render it a level further in,
at `body`'s column.

#### Why format² cannot simply keep that

The natural objection is: so what? If format¹ puts the comment at the body's
column, why can't format² just leave it there?

Because **format² does not *keep* anything.** It has no memory of format¹ and no
record of what owned the comment. It re-derives every placement from the text in
front of it. So the real question is whether that text still contains the
distinction — and it does not. Once the comment is on a row of its own, the only
surviving trace of "this belonged to the lambda body rather than to the paren"
is the comment's own indentation, and **indentation is not an input to
placement.** That is not incidental; it is a separately gated property of this
formatter (`fuzz-whitespace.py`: `format(perturbed whitespace) ==
format(original)`).

You can watch that directly. Feed the formatter the same comment at three
different columns — the paren's, the lambda body's, and one that means nothing at
all:

```gren
-- A: at the paren's column
b =
    (\q ->
        body q
     {- c
        second -}
    )
```

```gren
-- B: at the body's column — i.e. exactly what "descend into the lambda" emits
b =
    (\q ->
        body q
        {- c
           second -}
    )
```

```gren
-- C: at no meaningful column at all
b =
    (\q ->
        body q
                    {- c
                       second -}
    )
```

All three format to **byte-identical output**, and that output is A. B is exactly
what the descending version would have emitted, and it formats straight back to
the paren's column. This is not a prediction about what format² would do; it is
the shipped formatter, and it takes ten seconds to re-run:

```bash
for f in A B C; do node ../gren-format/app --show $f.gren; done   # three identical outputs
```

Note what the three-way collapse proves, which is more than the one case: the
output column here is a function of the **structure alone**. So it does not
matter what column the descent would have chosen — *any* placement other than
the one the enclosing structure derives is undone by the next format, and
`format(format(x)) ≠ format(x)`. The boundary rule is what makes format¹ commit
up front to the placement format² is going to derive anyway.

The same argument in one sentence: **format² can only keep what it can
re-derive**, and after the comment takes a row of its own there is nothing left
in the file to re-derive "inside the lambda" from.

#### The same rule, without any oscillation

Not every case of this is about idempotency. `RecordUpdate` is on the
never-swallow list for a plainer reason:

```gren
-- you write, and gren-format keeps (the comment is about the record):
v =
    fn a { r | x = 1 } {- note -} b
```

The comment sits past the `}`, on the record's last row — the guard's exact
trigger. Before `RecordUpdate` was on the list, this descended and rendered as:

```gren
v =
    fn a { r | x = 1 {- note -} } b
```

A note about the record, migrated inside the braces, where it now reads as a
note about the field.

And that output is perfectly **stable**. Feed it back to today's formatter and
it comes out unchanged, exit 0 — meaning it parses, preserves the AST, and is
its own fixed point. Every idempotency gate in this repo passes it. It is simply
*wrong*, under rule C1: the author wrote the comment outside the braces and the
formatter moved it in.

So the boundary rule is not only an idempotency device. Half of what it protects
is meaning, and that half is invisible to every gate that compares the formatter
against itself — which is why §8's coverage argument leans on the elm-format
oracle and the generator's oracles, not just on the fuzzers.

Three tests make a comment stay outside:

| test | what it catches |
|---|---|
| `boxKeepsTrailingCommentOutside child` | box kinds that must never swallow a trailing comment |
| `nextSiblingIsBoundary` | the next sibling starts a new flow item — `in`, `else`, the next `let` binding (`IndentedBlock`), the next `when` branch (`WhenBranch`) |
| `containerTailKeepsCommentOutside` | a multi-line comment past the last thing a paren wraps, or past any item of a bracketed container: it belongs to the container, not to that item |

`boxKeepsTrailingCommentOutside` is **the single declared list** of such box
kinds. If you add a construct that needs this rule, add it there rather than
threading a new predicate into the guard:

| box kind | why |
|---|---|
| `AcrossOrVertical` | a function-call / argument flow |
| `IfCondition` | an `if` / `else if` condition |
| `PrefixGlue` | a `\`-glued lambda head or a unary `-` — matters even mid-flow, where the next parameter's flow separator would otherwise shift that parameter by a column |
| `ParenBlock` | a parenthesized expression |
| `AllAcrossOrAllVertical` / `AlwaysVertical` | a bracketed item list: a comment past the closing bracket is a sibling, not an item |
| `RecordUpdate` | `{ base \| f = v }` and the extensible record type — same rule as the bracket lists |

Note what `nextSiblingIsBoundary` is *not*: it is deliberately **not** "any
following sibling". A container followed by a *continuation* — a record type
before its `->` — also has a following sibling, but a comment before that
record's `}` belongs inside it. Widening the test reintroduces
non-idempotency; this was tried.

#### The exception: still inside the brackets

`commentInsideTrailingBracket` overrides all of the above. A comment written
before a container's closing bracket genuinely belongs inside it, and it
descends:

```gren
fn a { r | a = 1 {- c -} } b     -- the comment is on the field: it descends
fn a { r | a = 1 } {- c -} b     -- the comment is on the record: it stays out
```

For this to work, **a container whose closing delimiter the parser discards must
register it** with `lpnBracketNode <closePos>`. This is the number-one thing to
get right when adding a construct. An author-written paren around a *type* went
years without one, and a comment before its `)` escaped the parens and then
landed on the far side of the `->` on reparse — a genuine non-idempotency, fixed
by registering the bracket.

#### Elastic containers

Some closes have no authored position at all because their column is *derived*.
The module header's `exposing ( … )` is the case: its `)` renders below whatever
the list holds. Those register with `lpnElasticBracketNode`, and two things
follow:

- `elasticTailOwnsComment` — an elastic container with nothing after it owns
  every comment that got this far, on any row. Row containment would leave a
  comment written on the row *below* a flat list outside it, where it renders
  glued onto the header; reparsing that puts it back inside. Same shape, both
  spellings, one answer.
- `lpnExtendElasticBracket` — placing a comment inside grows the derived close
  *below* it, and `applyCommentToOrigRow` grows the declaration's own row range
  to match. Without that, the **next** comment of a trailing run reads as past
  the declaration and detaches to column 1 while its neighbour stays put.

An effect module's `where { … }` block has a second derived close, recomputed by
`moduleWhereCloseRow` for the same reason. It is deliberately *not* elastic:
"anything reaching this container is inside it" is true of the exposing list
(nothing follows it) and false here, because `exposing (..)` does.

### 4.4 Phase 3 — which gap?

`insertAmongChildren containerBox commentNode row col children`

The descent has settled on a node; now, between which two of its children does
the comment go? Count how many children end before `(row, col)` — then apply
three adjustments:

**a. Skip synthesized tokens.** Nodes the parser never gave a position
(`SynthesizedText`: a generated `->`, the `in` of a `let`, `else`) are invisible
to ranges, and a comment must never be spliced between a real token and its
synthesized neighbour. The skip walks forward over them.

This skip is what carries a comment trailing the last `let` binding *past* the
position-less `in`, down to the body column:

```gren
-- you write:
f =
    let
        a = 1
        -- after the last binding
    in
    a
```

```gren
-- gren-format writes (the comment lands below `in`, leading the result):
f =
    let
        a =
            1
    in
    -- after the last binding
    a
```

That is a **deliberate divergence** from elm-format, not a gap. `in` has no
position, so "before `in`" and "after `in`" are literally the same input;
scoping the skip to keep the comment with the bindings makes it *oscillate*
(verified: same-row → own-line at the binding indent → reparses as no-longer
same-row → back to the body column). Below `in` is the only stable-and-correct
rule.

**b. The `BodyBlock` guard.** "Position-less" is judged by `lastRealPosition`,
with one exception: a `BodyBlock` is never skipped. A `BodyBlock` wrapping an
*empty* value (`[]`, `{}`) has a real opening position but no last position, so
it looks skippable and a leading comment sails past the value to render
`[] {- note -}`. If that value later gains an inside comment it becomes
non-empty and absorbs the stranded one on reparse.

**c. Redirect into block wrappers.** If the comment lands immediately before a
wrapper that starts its content on a fresh line, push it *inside* as the first
child, so it renders at the body's own column instead of gluing onto the head:

| wrapper | redirected? |
|---|---|
| `IndentedBlock` (a `let` binding's value) | always |
| `BodyBlock` **as a declaration's value** | always |
| `BodyBlock` elsewhere (an array item, a step body, a call argument) | only when the comment cannot ride the row |
| `PipelineStep` | only when the comment cannot ride the row |
| `SoftIndentedBlock` (a lambda body on the `->` row) | only a `--` |

The split in the last three rows is rule **C3** — *a comment never forces a
break*. `seed |> f |> g` stays on one row, so a `{- c -}` written between the
seed and the operator has a flat line to ride and belongs on it. Redirecting it
inside made it the step's first child, where nothing precedes it, so it
classified `LeadsOwnLine` and forced the whole chain vertical — line breaks the
code never needed. The `SoftIndentedBlock` row is the mirror: a `--` *does* end
its line, the body is forced onto the next row regardless, and a reparse reads
that as an `IndentedBlock` — whose arm redirects. So leaving the `--` outside
emits a shape that reparses into the other structure and moves.

Read those arms as one rule: **redirect exactly when the body cannot share the
head's row anyway.**

### 4.5 Phase 4 — which role?

`classifyCommentKind containerBox commentNode before after row -> CommentRole`

The splice point is known and the neighbours are in hand. This is where the
placement is decided, and the criterion for the decision is stated in the
function's own docstring:

> A `CommentRole` is a reparse fixed point: the row it is decided from equals the
> row it renders on, so a reformat re-derives the same role.

Every branch here should be read against that sentence.

#### The unrecorded-separator problem (rule C2)

Of everything that can separate two pieces of an expression, only **a binary
operator** and **a bracket** survive parsing with a position. All of these are
gone:

```
=    :    |    ,    ->    if / then / else    when / is    let / in
an import's `as` and alias name
```

So `x {- c -} = y` and `x = {- c -} y` arrive as *the same three facts*: where
`x` ends, where the comment is, where `y` starts. The formatter cannot tell them
apart, and it must not look at whitespace width (formatting must not depend on
your spacing). One of the two spellings has to move.

The rule is: **the comment leads what follows the separator** — role
`LeadsNext`. Making that a stored role, rather than a thing each renderer
re-derives, is what stops a comment from flipping sides of an invisible `,`
between formats.

C2 has one documented exception and three documented preferences, all in
[commentHandling.md](commentHandling.md#c2--when-the-parser-doesnt-record-the-punctuation-the-comment-leads-what-follows).
The exception, in code, is `leadsAcrossItemSeparator`: it applies only to a
**single-line `{- -}`**. A `--` ends its row, so it reads as a note about that
row, and at a line-*leading* separator (`,`, a union's `|`, a record update's
`|`) a comment above one strands nothing. `[ apple -- the red one` ⏎ `, banana`
is the idiom real code uses — unanimously, in `core/` and `compiler-common/` —
and is also what elm-format emits.

#### The three branches

`classifyCommentKind` dispatches on the **container**, because the glue rule
differs by container and the differences are real:

```
container is a bracket list / record update   →  the permissive rule
container is a binop chain (Binop / OpAndRhs) →  the operand rule
anything else (the generic flow)              →  the coarse rule
```

- **Bracket branch** (`isBracketContainerBox`) — permissive: a comment on the
  same row as the previous item trails it, whatever kind of item that was; an
  own-row one leads its own line. These comments render through
  `commentBracketListBox`, never through the generic flow, so they get their own
  branch. The record update is a sub-case with its own arm, because its children
  are its *fields* — the base name is not a child at all, so a comment beside it
  needs `TrailsHead`, and the base's recorded position is what separates the
  opener slot (`{ {- c -} rec`) from the ambiguous `|` slot.
- **Binop branch** — a same-row comment trails the operand; a later-row one
  leads its own line at the operator indent. "Later row" is measured from
  `chainedRefRow`, not from the last operand: the last operand's row **grown
  through any comment run written on from it**, because a multi-line `{- … -}`
  closes several rows below its `{-` and what the author wrote after that `-}`
  sits beside it, not on a line of its own.
- **Generic flow** — the coarse rule, split by comment kind:
  `classifyLine` for a `--` and `classifyBlock` for a `{- -}`, each consulting a
  per-box-kind table of "what row does a following comment glue onto":
  `prevLineGlueRow` and `prevBlockGlueRow`.

Here is the whole function as a decision tree, in two halves — the branch
dispatch plus the two simpler branches first, then the bracket branch. Every
leaf is a `CommentRole`.

**Half 1 — the dispatch, the binop branch, and the generic flow:**

![classifyCommentKind: dispatch, binop and generic flow](diagrams/comment-classify-flow.png)

The diamonds, in code terms:

| in the diagram | in `Comments.gren` |
|---|---|
| bracket container? | `isBracketContainerBox containerBox` |
| binop chain? | `containerBox` is `Binop _` or `OpAndRhs` |
| past the operand's row? | `row > chainedRefRow` — the last non-comment operand's row, grown through any comment run written on from it |
| anything before it? | `before` is non-empty (`prev`) |
| which kind? | `SingleLineComment` → `classifyLine`, `BlockComment` → `classifyBlock` |
| on the glue row? (`--`) | `row == prevLineGlueRow prev`, or the previous node is elided, or `headerTailGlue` |
| on the glue row? (`{- -}`) | `row == prevBlockGlueRow multiline prev`, or `headerTailGlue` (single-line only) |
| multi-line? | the comment's own text contains a newline |

**Half 2 — the bracket branch:**

![classifyCommentKind: the bracket branch](diagrams/comment-classify-bracket.png)

| in the diagram | in `Comments.gren` |
|---|---|
| in a record update's base…first-field region? | `isRecordUpdateBox containerBox && Array.all isCommentNode before` |
| before the base name? | `commentPrecedesUpdateBase` — the base's start position is recorded, so this is a fact, not a guess |
| beside the `\|` separator? | `leadsAcrossUpdateSeparator` — a single-line `{- -}` on the base's row or the first field's row |
| on the base's row? | `row == updateBaseRow` |
| in the `,` gap? | `leadsAcrossItemSeparator` — a single-line `{- -}` on the previous item's row or the next item's row; **never** in an exposing list |
| on a row of its own? | `bracketPrevRow < 0 \|\| row /= bracketPrevRow` (`bracketItemRow` keys a previous comment by its *last* row) |
| opener slot before a `"""…"""` item? | `Array.isEmpty before && nextItemBlocksFrontGlue` — front-gluing there would break the string's equal indentation and stop the output parsing |
| can its text share a line? | `bracketKindRole` — a single-line `{- -}` can; a `--` or a multi-line `{- … -}` cannot |

Two roles never appear as leaves here, because they are decided before this
function runs: **`Standalone`** (`findOrCreateOrigRow`, when no declaration's row
range covers the comment) and **`LeadsInline`** (`tryLeadingGluedAttach`, a block
comment glued in front of a declaration).

*(Sources: `docs/diagrams/comment-classify-flow.dot` and
`comment-classify-bracket.dot`.)*

Three things to read off them:

- **Every path ends in a role.** There is no fallback, no "unknown", and no
  branch that defers to the renderer. That totality is what §2.1's "decided
  exactly once" means in practice.
- **Every row test in them is the same question** — *is the comment on the row the
  thing before it ends on?* — and the branches differ only in how that row is
  computed: `bracketItemRow` in a bracket list, `chainedRefRow` in a binop chain,
  `prevLineGlueRow` / `prevBlockGlueRow` in the generic flow. (Rule C2's arms ask
  it of the row *after* the separator as well, which is what makes them stable in
  both authorings.) This is the last time a source row is consulted for this
  comment — everything downstream reads the role.
- **The kind is never the question of *which code owns it*.** That is settled by
  position, in phases 2 and 3, before this function runs. Kind decides only
  whether the comment can share the owning code's line — and where that does move
  a comment (the block-wrapper redirects of §4.4c), it is because "can it share
  the line" is precisely what is at stake there too.

The two glue-row tables the generic flow consults — `prevLineGlueRow` and
`prevBlockGlueRow` — are the heart of the coarse rule, and the difference
between them is the whole point:

```
                                  --  glues?   {- -} glues?
after a bare token                 yes          yes
after a plain call `foo bar`       yes          NO   (stays own-line)
after a single-line bracket        yes          NO
after a multi-line bracket's close yes          yes
after a `"""…"""`                  yes          yes
after a lambda head                NO           NO
```

A `--` runs to end-of-line, so it is *unambiguously* trailing and glues in
strictly more places than a block comment can. This asymmetry matches
elm-format, and it is why `foo bar -- c` is a fixed point while `foo bar` ⏎
`{- c -}` is too.

Both tables key a **previous comment** by its **last** row, not its first, so
that a comment written after a multi-line comment's `-}` trails the run instead
of leading whatever is below.

#### The header's position-less tail

One branch deserves its own note because it looks like a special case and is
not: `headerTailGlue`. An effect module's header renders tokens — the `where
{ … }` block's `}`, the `exposing (..)` after it — that carry **no position at
all**, because their real column depends on the untracked width of the block. A
comment the author wrote on those rows has no recorded token anywhere on its
row, so the ordinary rule reads it as own-line while the format that produced
that row had glued it.

The fix widens the fallback from one row to the header's whole position-less
tail, under three conditions each of which is load-bearing (the container is the
module line; nothing recorded follows except the exposing list; the row is at or
past the last recorded content and inside the declaration). It is scoped to
single-line comments, since only those can ride a header row at all.

The general lesson: **wherever the renderer emits a token the parser did not
record, a comment on that token's row is a placement question rows cannot
answer.** Look for the structural fact instead.

### 4.6 Repairs that need the finished tree

Two passes run at the end of `lptAddComments`, over the whole tree. They exist
because their questions cannot be answered while placing one comment: they are
about what the *rendering* will look like, which you only know once every
comment is in.

**`detachOwnLineTrailer`** — §4.2's rule, asked of the tree instead of the rows.
`findOrCreateOrigRow` refuses a comment written on a row *below* a declaration.
But a comment written on the declaration's **last** row escapes that check — its
row is inside the range — and can still *render* below it, because a multi-line
`{- … -}` with no glue row classifies `LeadsOwnLine` and takes a fresh line.
That is exactly the §1.3 oscillation. So: once everything is placed, lift any
trailing run that renders own-line into the same column-1 `OriginalRows` the
reparse would give it. **Format¹ builds the tree format² would build.**

```gren
-- you write (the comment starts on the declaration's last row):
v =
    fn a b {- c
   second -}
```

```gren
-- gren-format writes (lifted to column 1 — and formatting it again is a no-op):
v =
    fn a b
{- c
   second -}
```

Its scoping is as important as its existence:

- Only a run whose leader is `LeadsOwnLine` **and** brings its own rows (a
  genuinely multi-line `{- … -}`). Role alone is not "renders own-line" — the
  render layer keeps a single-line block comment inline regardless, which is why
  `[ 1 ] {- one -} {- two -}` stays put. Lifting on the role alone broke nine
  fixtures.
- A `--` is deliberately excluded even though it also ends its row; several
  fixtures pin an own-line `--` staying at the construct's indent, and whether
  those should detach is a separate question with its own fixtures.
- The whole run moves together, and only the leader becomes `Standalone` — a
  `-- c` glued behind it keeps `TrailsPrevious`, because that is the tree the
  reparse produces.
- `descendsForTrailingRun` limits how deep the peel looks: only **flow
  wrappers**, ones that render their children and nothing after them. A
  container that renders a closing delimiter does not qualify — the delimiter is
  not a node, so "no node after the last child" does not mean "no token after
  it". `{ rec | fld = x -- c` ⏎ `}` is the shape that trap was found on.

Each scope is a shape that is already a fixed point where the author put it, so
the lift must leave it alone. These three come back byte-identical:

```gren
-- a trailing run that GLUES stays on the closing bracket's row (C1: it belongs
-- to the code it was written next to):
a =
    [ 1
    , 2
    ] {- c
         second -}


-- a single-line {- -} renders inline whatever its role says, so it is not
-- "renders own-line" and is not lifted — nor is a second one chained onto it:
b =
    [ 1 ] {- one -} {- two -}


-- and the peel stops at a container that still has a `}` to render, so this
-- comment is the record update's, not the declaration's:
c rec =
    { rec
        | fld = x -- c
    }
```

(The `--` scope has no example here because it is a scoping *decision* rather
than a shape: a `--` that classifies `LeadsOwnLine` and renders below its
construct is left alone, and the fixtures that pin those live under a wrapped
import and a pipeline's last step.)

**`rehomePipelineStepTrailers`** — a pipeline step's trailing own-line comment
run renders below its step, which puts it *above* the next `|>`; and a comment
on its own row before an operator is, to a reparse, that operator's step's
*leading* comment. There is no other reading available, so format¹ must own it
there too.

This one needs the tree to see, because the repair does **not** change the
output — that is the whole point of it. Write a multi-line comment after a step
whose operand is a single-line paren (a paren gives a block comment no glue row,
so the comment classifies `LeadsOwnLine`):

```gren
-- you write:
v =
    seed
        |> Array.map (\c -> c.kind) {- multi
   line -}
        |> g
```

```gren
-- gren-format writes (the comment drops to the `|>` column, above the next step):
v =
    seed
        |> Array.map (\c -> c.kind)
        {- multi
           line -}
        |> g
```

Now ask the tree who owns it. This is `--lpt`'s JSON with everything but the box
types and the comment's own fields stripped out:

```
Pipeline
  UnbreakableText 'seed'
  PipelineStep
    UnbreakableText '|>'
    UnbreakableText 'Array.map'
    ParenBlock
      …the lambda…
  PipelineStep
    BlockComment ' multi\n   line '  role=LeadsOwnLine     ← here
    UnbreakableText '|>'
    UnbreakableText 'g'
```

The comment is the **first child of the second step**, ahead of that step's own
`|>` — not a trailing child of the first step, which is where the author wrote
it. That move is `rehomePipelineStepTrailers`, and step 2 is the owner a reparse
of the output derives: in those bytes the comment is on its own row before
`|> g`, and a comment on its own row before an operator is that operator's step's
leading comment. There is no other reading available.

The boundary is one operand kind away. Give the step a plain call instead of a
paren and the same comment finds a glue row, classifies `TrailsPrevious`, and
stays exactly where it was written:

```gren
v =
    seed
        |> Array.map fn {- multi
                           line -}
        |> g
```

```
Pipeline
  UnbreakableText 'seed'
  PipelineStep
    UnbreakableText '|>'
    UnbreakableText 'Array.map'
    UnbreakableText 'fn'
    BlockComment ' multi\n   line '  role=TrailsPrevious   ← stays in step 1
  PipelineStep
    UnbreakableText '|>'
    UnbreakableText 'g'
```

This one is worth studying because a renderer-only fix was tried first and was
not enough. Making both owners produce the same **bytes** for the run left
`glueLeading` reading a different run in each format — it asks
`commentTextCanRide` of the whole leading run, all-or-nothing, and with the run
split across two steps format¹ saw only the ridable `{- c -}` while the reparse
saw both. Same comment, same role, same rendering rule, different answer, for
ever. **Making two owners render alike is not the same as making the owner not
matter**; the owner also decides what else the comment is grouped with.

### 4.7 The two passes that run after

**`SortSymbols`** reorders exposing lists and import groups. A comment must
travel with the name that owns it, or `(a {- c -}, b)` and `(b, a {- c -})` land
on different bytes — and the same module written in two author orders must
format identically (this is checked by an oracle; §8). `takeSameRowTrailing`
chains a whole trailing run onto its name; `unfoldLastTrailing` handles the
closing-`)` pinning; `hoistBracketLeadingComments` handles a comment written
before the first item.

Note that an exposing list is the **one** bracket list whose items get
reordered, and it therefore models comment ownership the *opposite* way round
from C2 — a comment after a name is that name's. `leadsAcrossItemSeparator`
excludes it explicitly.

**`VerticalSpace`** inserts blank lines, and its comment-relevant part is
`computeDetachedBelow`: whether a node has a gap under it. The subtlety is that
**detachment is a property of the comment *run*, not of a node**, and has to
propagate up through an adjacent following comment.

```gren
-- you write:
{-| doc -} -- c

foo =
    1
```

```gren
-- gren-format writes:
{-| doc -}
-- c


foo =
    1
```

The two comments are one run, detached from `foo`, so the run gets the two blank
lines below it. Now look at what each pass sees. The `-- c` is written on the doc
comment's row, so on the **first** pass it is a *child* of that node and the
node's own rows cover both — that node can see the gap. Reparsed, the `-- c` is
its own column-1 node directly below, so the doc comment's next sibling is
adjacent and the gap belongs to somebody else. Asking per node gives two blanks
and then one; asking the *run* answers the same either way.

---

## 5. Stage 2 — rendering

Below the barrier the job is narrow: a comment's *placement* is already decided,
so the renderer only materializes it, plus answers the questions that are about
the comment's text.

### 5.1 The two decision functions

Everything goes through one of two functions in
`Formatter/Render/FlowPolicy.gren`:

- **`decide`** — how an item joins a *flow* (a call's arguments, a binop chain, a
  signature). Its input is `ItemFacts`, and that type is deliberately the
  **complete legal input set**: anything richer is "not extra precision, it is a
  divergence generator". For a comment the facts are its kind and its
  `CommentRole` — no rows. `decide` returns a `Placement` (`GlueSpace`,
  `OwnLine`, `SoftSep`, `BlockJoin`, …) and the next separator state.
- **`containerCommentSlot`** — where a comment sits among the children of a
  *vertical container* (a union body's variants, a record update's fields, a
  `when`'s branches, a bracket list's items). Four answers, in priority order:
  `LeadsOpener`, `LeadsFollowing` (rule C2), `GluesPrevious`, `StandsAlone`.

`containerCommentSlot` was extracted in August 2026 from four hand-written
if-chains that agreed only by inspection — one of them said so in a code comment
("mirrors `commentBracketListBox`'s `pending`"), an invariant asserted in prose
and enforced by nothing. The four containers still *materialize* the answer
differently (a `when` stacks its lead run and separates branches with blank
lines; a bracket list glues the run onto the item's front behind a `, ` prefix;
a union body has no prefix at all) — but they no longer classify differently.
**If you find yourself writing a fifth comment-slot if-chain, call this instead.**

The renderer's own state is one field:

```gren
type alias FlowState =
    { separator : NextSeparator }
```

with `NextSeparator` distinguishing `AlreadyTerminated` (after a `--`, or a
single-line own-line comment: nothing may glue back onto that line) from
`TerminatedByBlockComment` (after a multi-line `{- … -}`: a same-row trailing
comment *does* glue onto its `-}` line). The old two-row state machine that
re-derived placement at render time is gone.

### 5.2 The three shapes of comment

Almost every remaining render-side question reduces to one table:

| | can code follow it on the same line? |
|---|---|
| `-- like this` | **no** — runs to end of line |
| `{- like this -}` on one row | **yes** |
| `{- like this` ⏎ `and this -}` | **no** — brings its own newlines |

The predicates that ask it, all in `Render/NodeClassify.gren`:

- `commentEndsItsLine` — the table itself. Shared by every "does this comment
  force a break" question so they cannot drift.
- `commentTextCanRide` — the same question about the comment's own text. **Not
  the same as `commentRidesInline`**, which asks the stored *role*. Every
  `RidesInline` comment is a single-line block comment; not every single-line
  block comment is `RidesInline` (the second comment of an opener run is
  `LeadsNext`). Three stale docstrings conflated the two and that conflation is
  the likely origin of two sibling arms in `commentBracketListBox` using
  different predicates for the same question.
- `commentBreaksFlowRow` — whether a comment among these siblings pushes a
  *later* sibling onto a new row. This one exists to keep a row-derived
  `forceVertical` honest: `insertCall`/`insertBinops` set that flag from AST
  rows, and the AST has no comments in it, so a construct written on one row is
  flagged `forceVertical = False` even when a comment inside it *will* break.
  Format¹ renders flat, the break happens anyway, and format² sees the later
  sibling on a later row and renders broken. Folding this predicate into the
  same decision makes format¹ commit to the shape format² will agree with.
- `literalCommentsRideFlatLine` — may a bracketed literal stay on one line?
- `subtreeEndsWithLineComment` — would gluing onto this box's last line land
  *inside* a `--` and be silently swallowed? It stops at bracket/paren
  containers, whose rendered last line is a synthesized closing delimiter and is
  safe to glue after.

That last one, combined with `commentForcesBracketOpen`, is what stops the
formatter from emitting source that does not parse:

```gren
-- you write, and gren-format keeps (it may NOT collapse to one line —
-- the `]` would land inside the `--`):
c =
    [ lower + margin -- inclusive
    ]
```

Note the `Array.any`, not "the last child": a `--` in a *middle* item swallows
the following `, ` just as surely.

### 5.3 The glue primitives

Two mirrored helpers in `Render/BoxOps.gren`, and their asymmetry is deliberate:

- `glueCommentSuffix` — a trailing comment onto a box's last line. Uses
  `B.addSuffixBox`, which **pads the comment's continuation lines** by the glued
  line's rendered width, so a multi-line comment's body hangs under its own
  `{-`.
- `glueLeadingCommentPrefix` — a leading comment onto a box's front: the code's
  first line continues the comment's last line, the comment's earlier lines sit
  above, and the code's continuation lines stay **untouched at their own
  column**. The declaration's own layout must not shift.

One trap worth knowing about, because it cost a real bug: `Box.prefix` used to
measure the prefix with `lineLength 0`, i.e. *as if it began at column 0*. A
`Tab` in the Box IR advances to the next multiple of 4 **from where it stands**,
so a prefix containing tabs measures differently at column 6 than at column 0,
and the padded continuation lines drifted. The fix (`Box.blankLike`) pads with a
**blanked copy of the prefix line** rather than a count of spaces: every element
keeps its offset, so every `Tab` snaps identically at any starting column.
Freezing the tabs instead was tried and reverted — it fixes the width *and*
changes the emitted line.

---

## 6. Runs: any number, any kinds

The question this document is really here to answer: what happens with *n*
comments of mixed kinds in one place?

Three rules, applied consistently everywhere:

**R1 — The reference row grows through the run.** Whenever a placement asks
"what row does the thing before me end on", the answer counts any comment run
written on from that row. A multi-line `{- … -}` closes several rows below its
`{-`, and what the author wrote after that `-}` sits beside it:

```gren
-- you write, and gren-format keeps (both comments stay with `Mango`;
-- the block comment's continuation row is re-indented under its own `{-`):
fruit =
    [ Apple
    , Mango {- mango's
               comment -} -- and mango's trailing line comment
    , Pear
    ]
```

All three of these implement it, and they must agree: `prevLineGlueRow` /
`prevBlockGlueRow` (generic flow, key on the previous comment's *last* row),
`bracketItemRow` (bracket branch), `chainedRefRow` (binop branch). The binop one
was the last to learn it, and until it did, the second comment of a chain
dropped to its own row below the chain — where the reparse re-homed it to column
1.

**R2 — Once a run breaks, it stays broken.** If any comment in a leading run
cannot share a line, then *every* comment of the run stands on its own row, and
so does the body:

```gren
-- you write, and gren-format keeps:
c =
    [ {- 1 a
         1 b -}
      {- x -}
      1
    ]
```

Not this:

```gren
    [ {- 1 a
         1 b -} {- x -}
      1
```

Letting later members jump onto the item's line is not a fixed point: on reparse
`{- x -}` sits on the item's row rather than the run's, so format³ drops it back.
`glueLeadingCommentRun` implements this (its `firstBreak` index), and
`assembleBrokenWithComments` and `renderWhenBranchesBox` apply the same
all-or-nothing rule. elm-format agrees here.

**R3 — A run moves as a unit.** `detachOwnLineTrailer`, `SortSymbols`'
`takeSameRowTrailing`, and `VerticalSpace`'s `computeDetachedBelow` all treat
the run as the thing that has an owner, a sort position, and a blank line —
never its first member alone.

Between them these three cover the general case: any number of comments, in any
mix of kinds, in any gap. The remaining per-comment question — can *this* text
ride? — is answered per member by `commentTextCanRide`, which is why
`[ 1 {- a -} {- b -} ]` stays on one line while adding a `--` anywhere in that
run opens it up.

---

## 7. A worked example

Input:

```gren
module Ex1 exposing (total)

import Dict {- c -} as D


{-| Adds things up. -}
total : Int -> Int
total n =
    let
        base = -- start here
            n + 1
    in
    [ base -- the seed
    , 2, {- two -} 3
    ] |> List.sum
```

Output:

```gren
module Ex1 exposing (total)

import Dict {- c -} as D


{-| Adds things up. -}
total : Int -> Int
total n =
    let
        base =
            -- start here
            n + 1
    in
    [ base -- the seed
    , 2
    , {- two -} 3
    ]
        |> List.sum
```

What happened to each comment:

| comment | phase 1 | phase 2 | phase 3 | role | rendered as |
|---|---|---|---|---|---|
| `{- c -}` | the `import` declaration | into the import's flow | after the module name; the `as` and alias are position-less, so C2's *earlier*-side preference for `as` keeps it here | `RidesInline` | rides the row |
| `{-\| … -}` | its own top-level slot | — | — | *(doc comments need no role — they are top-level only and unambiguous)* | own line |
| `-- start here` | `total`'s declaration | into the `let` binding | lands before the binding's `IndentedBlock` value → **redirected inside** (§4.4c) | `LeadsOwnLine` | own line at the value's column |
| `-- the seed` | `total`'s declaration | into the array | after `base`, on `base`'s row | `TrailsPrevious` | glued to `base`'s line — and, being a `--`, it forces the array open (§5.2) |
| `{- two -}` | `total`'s declaration | into the array | in the unrecorded `,` gap | `LeadsNext` (rule C2) | glued to the front of `3`, behind the `, ` prefix |

Two things to notice in the output. First, the `--` opened the array up — that
is C3 working as specified: the comment did not *choose* a break, it *is* a
break, and once a bracketed container is multi-line it gets one item per line
like always. Second, `{- two -}` moved from after the `,` to before `3`… which is
the same place. It was already leading `3`; C2 just fixed which side of the
invisible comma it is recorded on, so the next format agrees.

Verify any of this yourself:

```bash
node ../gren-format/app --lpt Ex1.gren   # roles
node ../gren-format/app --show Ex1.gren  # parse → format → reparse → AST-compare
                                         # → format again → idempotency-compare
```

---

## 8. Why we believe it is complete

"It handles every case" is a claim about coverage, so here is the coverage
argument, gate by gate. The important column is the last one — what each gate
**cannot** see — because that is what the next gate exists for.

| gate | what it varies | what it proves | blind to |
|---|---|---|---|
| **Fixture suite** (`run-tests.sh`, 342 `.formatted.gren` across 12 suites) | hand-written cases | exact bytes, AST equivalence, idempotency, per fixture | anything nobody thought to write |
| **`fuzz-idempotency.py`** | inserts `{- ¤ -}` into **every** inter-token gap of every fixture, formats twice | the fixed point, over ~56,000 comment positions | one comment at a time; only says *whether* something moved |
| **`check-decision-stability.py`** | same gaps, but diffs the *decisions* | **which** decision was unstable, as named branches with no positions in them | a decision nobody traced |
| **`fuzz-whitespace.py`** | inter-token whitespace | `format(perturbed) == format(original)` — placement must not depend on your spacing | comments |
| **`matrix-syntax.py --comments`** (68,922 cells) | 41 expression × 25 contexts + 11 type × 15 contexts, × 3 comment kinds × 2 positions, each diffed against **elm-format** | placement *divergence* — a stable, idempotent, AST-preserving wrong answer | more than one comment per cell |
| **`gen-random.py`** | random-but-legal modules, structure **and** comments | comment **multiset** preservation (drop / duplication / kind change), and **author-order invariance** | shapes outside its grammar |
| **`audit-predicates.py`** | the corpus | that a "does this break?" predicate agrees with the renderer | under-approximation (deliberate) |
| **`check-render-invariant.py`** | — | the barrier of §2.2 | nothing structural |

Two of those deserve emphasis, because they cover holes that look covered:

- **A dropped comment passes almost everything.** Deleting a comment is
  AST-equivalent, and the output is its own fixed point, so `--show` passes and
  every stability check passes. Only `fuzz-idempotency.py`'s marker count and
  `gen-random.py`'s multiset oracle can see it. This class is not hypothetical —
  it has been caught twice, both times a renderer reading a node's children
  positionally (`Array.popLast`, "the head is `[name, =, lambdaHead]`") in a
  formatter where **a comment is a child**.
- **A wrongly-*attached* comment passes even those.** The multiset oracle
  discards positions on purpose, and a wrong-but-stable attachment is a fine
  fixed point. The only gate that sees it is `gen-random.py`'s **sort-order
  oracle**: emit the same module twice with its import runs and exposing lists in
  reversed author order, with each comment still on the same owner, and require
  byte-identical output. That is something only a generator can do.

**Current state (2026-08-05):**

- `check-decision-stability.py`: **PASS**, 0 unstable decisions over the corpus.
- `fuzz-idempotency.py`: 17 findings, and **all 17 are a known upstream parser
  bug** ([compiler-common#35](https://github.com/gren-lang/compiler-common/issues/35)
  — a binary `-` whose right operand starts at the operator's own column parses
  as a negation). They are reported with that label, counted, and **not
  subtracted**: the gate stays red on purpose until the parser fix ships,
  because hiding a finding is how a gate starts lying about its coverage. The
  **formatter-side residual is zero.**
- `matrix-syntax.py --comments`: 68,922 cells, **0 failing**. The axis swept only
  two of the three comment kinds until 2026-08-05; adding the multi-line block
  found 70 non-idempotencies the same afternoon, in two causes, both now fixed —
  `containerTailKeepsCommentOutside` (16) and a glue row that
  `bracketRendersMultiline` was deriving from the author's rows for a container
  the format then collapses (54). Write-up in
  [`commentRunTesting.md`](commentRunTesting.md).

For the two kinds that had been swept, that last line is the one to quote to a
sceptic: every place `gren format` and `elm-format` put a `--` or a single-line
`{- -}` differently is a *decision on record with a reason*, not an unexamined
difference. The multi-line kind is a day old here, and most of its divergences
are one family — elm-format re-lays-out a multi-line comment's own body, `-}`
onto a row of its own, where gren keeps the delimiters you wrote
([#25](elmFormatComparison.md#divergence-25)). **3,407 cells out of 48,345 are
still unreviewed**, counted and printed on every run; sampled, they are compounds
of a comment crossing an unrecorded `|` *and* elm re-flowing the code around it,
which wants a human verdict rather than a wider auto-classifier.

**And note what a green gate is worth.** This axis ran green for months over two
of the three comment kinds; adding the third found 70 non-idempotencies the same
afternoon. A gate that runs green over the wrong axis reads exactly like a gate
that runs green. Check what a gate *varies* before trusting what it reports.

The honest caveats, stated so nobody has to discover them:

- The all-gaps fuzzers inject **one** comment per run. Multi-comment runs are
  covered by fixtures and by `gen-random.py`, not exhaustively. Two shapes have
  been found this way that neither matrix could reach; the plan for closing the
  gap systematically is [`commentRunTesting.md`](commentRunTesting.md).
- `matrix-syntax.py` does not cover an `import`'s own syntax or the module
  header against elm-format; the corpus fuzzers reach them, no oracle does.
- Multi-line string literals are excluded from the matrix by construction
  (`"""x"""` does not parse on one line, so it cannot be a one-line atom).

---

## 9. The functions to be careful about

If you are changing comment behaviour, these are the places where a small change
has a large blast radius. The right-hand column is what breaks when it is wrong
— all of them observed, none hypothetical.

### Attachment — `Formatter/Logical/Comments.gren`

| function | decides | failure mode |
|---|---|---|
| `findOrCreateOrigRow` | which top-level declaration; **detach to column 1** when none | a claimed trailing comment drifts left a few columns per format |
| `insertCommentIntoSubtree` | how deep to descend | the whole trailing-comment oscillation class |
| `boxKeepsTrailingCommentOutside` | the declared list of never-swallow boxes | a comment sucked into the construct it merely trails |
| `commentInsideTrailingBracket` / `commentInsideEmptyBracket` | the "still inside the brackets" exception | a comment escapes a container, then lands on the far side of a synthesized token |
| `insertAmongChildren` | the splice index, the synthesized-token skip, the wrapper redirects | a comment between a token and its generated `->`; a comment that forces a break C3 forbids |
| `classifyCommentKind` | the `CommentRole` | everything downstream; a role that is not a reparse fixed point oscillates for ever |
| `prevLineGlueRow` / `prevBlockGlueRow` | per-box-kind glue rows | a comment alternating between glued and own-line |
| `chainedRefRow` / `bracketItemRow` | run chaining (R1) | the second comment of a run dropping below the construct |
| `detachOwnLineTrailer` + `peelOwnLineTrailingRun` + `descendsForTrailingRun` + `hostsOwnLineTrailer` | lifting a run that renders below its declaration | §1.3's oscillation, exactly |
| `rehomePipelineStepTrailers` | which step owns a trailing run | two owners that render alike but group differently |
| `applyCommentToOrigRow` / `lpnExtendElasticBracket` / `moduleWhereCloseRow` | growing derived closes as comments land | the *next* comment of a run escapes the declaration |
| `tryLeadingGluedAttach` | `LeadsInline` | a comment that fails to travel with its import, splitting an import run |

### Ownership — `SortSymbols.gren`, `VerticalSpace.gren`

| function | decides | failure mode |
|---|---|---|
| `takeSameRowTrailing` / `takeSameRowTrailingIdx` | which name a run belongs to through a sort | two author orders, two outputs |
| `unfoldLastTrailing` | the closing-`)` pinning | a comment attached to a list instead of to its last name |
| `hoistBracketLeadingComments` | a comment before the first item | it sorts to the wrong place, or hoists when it should not |
| `computeGroupStarts` / `computeDetachedBelow` | blank lines around runs | one blank vs two, alternating |

### Rendering — `Render/*`

| function | decides | failure mode |
|---|---|---|
| `FlowPolicy.decide` | how any item joins a flow | any layout policy living outside it is a divergence generator |
| `FlowPolicy.containerCommentSlot` | the comment slot in a vertical container | four containers classifying differently by accident |
| `NodeClassify.commentEndsItsLine` / `commentTextCanRide` | the shape table (§5.2) | using the role where the shape was meant, or vice versa |
| `NodeClassify.commentBreaksFlowRow` | a comment-aware `forceVertical` | flat-then-broken oscillation |
| `NodeClassify.subtreeEndsWithLineComment` | is gluing here safe? | **output that does not parse** — a `]` swallowed by a `--` |
| `NodeClassify.subtreeEndsWithMultilineBlockComment` | is the box align-carrying? | a comment's continuation row off by a few columns |
| `MakeRenderBox.commentForcesBracketOpen` | may a literal stay flat? | as above, or a needless break |
| `MakeRenderBox.glueLeadingCommentRun` / `glueLeadBoxes` | run cohesion (R2) | a run that half-rides and never settles |
| `MakeRenderBox.commentBracketListBox` | the comment-bearing bracket layout | the biggest single materializer; most bracket comment bugs land here |
| `CommentBox.multiLineBlockCommentBox` | continuation-line reindent | comment bodies that walk right on every format |
| `CommentBox.span*` / `peelTrailingCommentNodes` | which comments a renderer peels off | peeling a comment that was supposed to glue, and vice versa |
| `FlowAssembly.softGlueAlignment` | first-line-only vs align-carrying glue | continuation rows short by the glued prefix's width |
| `Box.prefix` / `Box.blankLike` | padding width under `Tab`s | drift proportional to the enclosing column |

### The class of bug that keeps recurring

> **A comment is a child.**

Any code that reads a node's children **positionally** — `Array.popLast`,
"element 0 is the name", "the head is exactly `[name, =, lambdaHead]`" — is
wrong the moment the author writes a comment in that node. This has produced a
dropped comment (twice), a misplaced lambda head, and a stranded operand
comment. Known instances, all now split at the last **non-comment** node or
looking through the wrapper: `renderGluedLambdaField`,
`renderPipelineStepChildrenWith`, `spanOperandLeadingComments`,
`makeMultilineLambdaArgBox`, `makeParenBlockBoxWithParts`.

When you review such a site, do not stop at "this one is fine" — trace **why**
it is fine. A whole-repo audit of these found one live instance and one that was
**correct by accident**, and the accidental one is the more dangerous of the two.

---

## 10. Debugging a comment bug

The recipe, in the order that wastes the least time:

```bash
# 0. Rebuild — every tool below shells out to the built app.
cd gren-format && ./build.sh

# 1. Is it a placement bug or a stability bug?
node gren-format/app --show MyFile.gren > /dev/null && echo "stable + AST-preserving"
node gren-format/app --show-first MyFile.gren    # format¹, when --show refuses

# 2. Where did the comment end up in the tree, and with what role?
node gren-format/app --lpt MyFile.gren

# 3. If it moves between formats: which DECISION moved?
node gren-format/app --decisions MyFile.gren

# 4. Reproduce a gate's finding (`<fixture>[<kind>]@<gap>`) exactly:
cd gren-format-lib/tests
./repro.py TrickyComments.formatted.gren multi 100   # both passes + diff
./repro.py <fixture> <kind> <gap> --input            # the spliced source
./repro.py <fixture> <kind> <gap> --lpt1             # the tree pass 1 rendered from
./repro.py <fixture> <kind> <gap> --lpt2             # …and pass 2
```

Four hard-won habits:

- **Ask what the comment-free twin does.** That is rule C4 made operational: if
  the output differs from the same code with the comment deleted in anything but
  where the lines fall, that is the bug. It is also the only way to find a bug
  whose other half is *stable* — a wrong-but-idempotent placement is invisible to
  every fuzzer here, and one was found only by asking what the sibling operand
  kinds do.
- **When the instrument names nothing, read the bytes.** A recent 14-probe family
  was "unexplained" by the decision trace because the comment changed *owner*
  between passes while keeping the same role string — and there is a traced
  decision for a role but none for ownership. Fourteen diffs that move the same
  construct by the same four columns are a family whatever the trace says.
- **Renderer first, then attachment.** Where both halves need changing, land the
  renderer fix alone (usually a no-op on the corpus, hence separately verifiable)
  and only then move the attachment. The reverse order ships a comment-loss bug
  behind a green suite.
- **Check the fixtures before designing a rule.** When a shape is unstable in one
  container, look for the container that already agrees. More than once the
  fixture that pins the *opposite* shape already had the answer in its
  description.

---

## 11. Where the rules genuinely run out

Three places cannot be decided well, because the information is not there. All
three are documented with worked examples in
**[Known limitations](knownLimitations.md)**:

- **A comment after the last `let` binding** goes below the `in`. `in` has no
  recorded position, so before-`in` and after-`in` are the same input, and below
  is the only stable choice (§4.4a).
- **A `--` inside an effect module's `where { … }` block** can escape the block:
  the parser hands back byte-identical AST *and* Context for both layouts.
  Proven undecidable for `{- -}` too; the elastic-close workaround was measured
  and disproven. Do not retry it.
- **A comment after the last name of a flat, one-line `exposing ( … )` list** is
  read as the list's rather than the name's, because the closing `)` has no
  recorded position to measure against. Write the list across several lines and
  the two are tellable apart again.

Plus one that is not ours: `compiler-common#35`, §8.

Where `gren format` and `elm-format` place a comment differently on purpose —
and there are several such places, each with a reason on record — the list is
**[Comparison with elm-format](elmFormatComparison.md)**.

---

## See also

- [How gren-format places your comments](commentHandling.md) — rules C1–C6, the
  normative statement of *behaviour*
- [How the formatter works](howItWorks.md) — the pipeline, conceptually
- [DEVELOPER.md](../DEVELOPER.md) — adding a construct; the position rules
- [Testing gates](testing.md) and [Comment-run testing](commentRunTesting.md)
- `CommentRole`'s docstring in `Formatter.Logical.LogicalPrintingTree` — the
  normative statement of each role
