# How gren-format places your comments

`gren format` **never changes the text of a comment.** What it decides is where
each comment sits relative to the code around it — which line it lands on, and
how far it is indented.

This document is the whole story for a Gren developer: what the formatter is up
against, the one idea that makes it come out right twice running, and then the
seven rules that decide every comment in a file, with a "you write / gren-format
writes" pair for each case. Every example on this page was produced by running
the formatter.

If you are working on the formatter itself — function names, the state machines,
the test gates — read [The comment algorithm](commentAlgorithm.md) instead. It is
this same story in full, at four times the length.

For comment rules that belong to one particular construct (block-comment body
re-indentation, doc comments, an effect module's `where` block), see the
[Comments section of the rule reference](formatterRules.md#comments). For places
where gren-format and elm-format disagree about comments, see the
[divergence catalogue](elmFormatComparison.md).

---

## Table of contents

- [Why comments are the hard part](#why-comments-are-the-hard-part)
- [What counts as correct](#what-counts-as-correct)
- [The idea: decide once](#the-idea-decide-once)
- [Four questions, asked once](#four-questions-asked-once)
- [The two kinds of comment](#the-two-kinds-of-comment)
- [The seven rules at a glance](#the-seven-rules-at-a-glance)
- [C1 — A comment belongs to the code you wrote it next to](#c1--a-comment-belongs-to-the-code-you-wrote-it-next-to)
- [C2 — When the parser doesn't record the punctuation, the comment leads what follows](#c2--when-the-parser-doesnt-record-the-punctuation-the-comment-leads-what-follows)
- [C3 — A comment never forces a break](#c3--a-comment-never-forces-a-break)
- [C4 — A comment changes where the lines fall, and nothing else](#c4--a-comment-changes-where-the-lines-fall-and-nothing-else)
- [C5 — gren-format adds nothing around a comment](#c5--gren-format-adds-nothing-around-a-comment)
- [C6 — An own-line comment is indented to the code it leads](#c6--an-own-line-comment-is-indented-to-the-code-it-leads)
- [C7 — A comment keeps the rows you gave it](#c7--a-comment-keeps-the-rows-you-gave-it)
- [Comments in runs](#comments-in-runs)
- [Where the rules run out](#where-the-rules-run-out)
- [Trying it yourself](#trying-it-yourself)

---

## Why comments are the hard part

Comments are the hardest thing a formatter deals with, and the reason is
structural rather than fiddly: **comments are the one part of your source the
compiler does not care about, and the only part the formatter cannot afford to
get wrong.** Move a line of code and it still means the same thing. Move a
comment and it now describes something else.

`gren format` does not have its own parser. It uses the **production Gren
compiler's** parser, which matters more than it sounds: a formatter that accepts
a slightly different language than the compiler is a bug factory, so we always
parse exactly what the compiler parses.

The price is that the compiler's parser does what every compiler's parser does.
It throws comments away. They are not part of the syntax tree, and there is
nowhere in it for them to be.

What comes back instead is a second, separate list — every comment in the file,
with its text and its position, and nothing else. For this file:

```gren
module Sizes exposing (sizes)


sizes =
    [ 1 -- smallest
    , 2
    , {- then -} 3
    ]
```

the formatter is handed a tree for `sizes = [ 1, 2, 3 ]`, and, off to one side,
this (you can print it for any file with `--pre-context`):

```json
{ "comments": [
    { "type": "line",  "value": " smallest",
      "start": { "row": 5, "col": 9 }, "end": { "row": 5, "col": 20 } },
    { "type": "block", "value": " then ",
      "start": { "row": 7, "col": 7 }, "end": { "row": 7, "col": 17 } } ] }
```

That is the entire input. Nothing connects the two halves. The comment does not
know it is inside an array; the array does not know a comment exists. All the
formatter has is a row and a column, and the rows and columns of the code it is
placing them into.

Putting them back together is what the rest of this document is about.

> **Why not fix it upstream?** elm-format builds comments into its own AST, so an
> expression node physically holds the comments written around it, and it never
> has this problem. That is a real advantage, and we pay for it here — in
> exchange for never disagreeing with the compiler about what a program is.

---

## What counts as correct

Three things, and the third is the one that makes this hard.

**1. Every comment survives.** Same text, same kind, exactly once. `gren format`
never edits what you wrote inside a comment. (It does re-indent the continuation
lines of a multi-line `{- … -}` so its body hangs under its own `{-`; that is
layout, not text.)

**2. It lands where you wrote it.** Beside the code it was about — the seven
rules below are the full statement.

**3. Formatting twice changes nothing.** `format(format(x))` must equal
`format(x)`, byte for byte.

That third property is not a nicety. `gren format` runs on save and in CI, and a
file that alternates between two spellings produces phantom diffs for ever.

Here is why it is the hard one. The formatter decides where a comment goes by
looking at **the row and column you wrote it on** — that is the only signal it
has. But formatting *moves code onto different rows*. So the second format asks
the same question of different evidence, and can perfectly reasonably come to a
different answer:

```
you wrote:              a naive format¹:         …and format² would produce:

v =                     v =                      v =
    fn a b {- c             fn a b                   fn a b
   second -}                {- c                 {- c
                               second -}            second -}
```

Format¹ reasoned "this comment is on the declaration's last row" and put it at
the call's indent. But a multi-line comment cannot sit *on* that row — it brings
its own line breaks — so it landed **below** the declaration instead. Format² now
sees a comment written below a declaration, which is a different question with a
different answer, and the file has two spellings and no fixed point.

Nearly every comment bug this formatter has ever had is a variation on one
sentence: **a decision made from a fact that the formatting itself then
invalidates.**

---

## The idea: decide once

The whole design follows from taking that sentence seriously.

> Every comment's placement is decided **exactly once**, early, while the rows
> are still the ones you wrote. The answer is stored on the comment. Nothing
> downstream recomputes it.

The stored answer is called the comment's **role** — how it joins the code
around it. There are seven, and between them they cover every position a comment
can hold:

| role | means |
|---|---|
| `TrailsPrevious` | glue onto the end of the thing before it — `x = 1 -- why` |
| `LeadsOwnLine` | stand on its own line, above what follows |
| `LeadsNext` | it belongs to what comes *after* an invisible separator ([C2](#c2--when-the-parser-doesnt-record-the-punctuation-the-comment-leads-what-follows)) |
| `TrailsHead` | glue onto a container's head — a record update's base name |
| `RidesInline` | ride mid-line without breaking it — `f {- k -} x` |
| `LeadsInline` | glued to the front of a declaration — `{- c -} import Qux` |
| `Standalone` | detached: its own line, at column 1 |

You can see them for any file:

```bash
node gren-format/app --lpt MyFile.gren     # every comment leaf carries its "role"
```

Because the decision is taken once, it is taken **against the rows you wrote**,
which is exactly the evidence the next format will re-derive from the output — as
long as each role is chosen so that *the row it was decided from is the row it
renders on*. That is the rule the whole thing turns on, and it is what makes
formatting a fixed point rather than an oscillation.

The corollary is a hard internal boundary: **no code in the rendering half of the
formatter is allowed to look at a source row** to decide where a comment goes. It
reads the stored role. (This is enforced by a check that runs before the test
suite, not merely written down.)

---

## Four questions, asked once

Placement is four questions, asked in order. Here they are against the file from
the top of this page, which the formatter leaves exactly as written:

```gren
sizes =
    [ 1 -- smallest
    , 2
    , {- then -} 3
    ]
```

### Question 1 — which declaration?

The tree is a list of top-level declarations, each covering a range of source
rows. Find the one whose range contains the comment's row. Both of ours are
inside `sizes`.

**If no declaration covers it, the comment detaches** — it becomes its own item
at column 1. That is not a fallback; it is a deliberate rule, and it is why this
happens:

```gren
-- you write:                    -- gren-format writes:

b =                              b =
    1                                1
    -- detached below b          -- detached below b
```

Column 1 is trivially stable — it cannot drift. Any rule that instead claimed
such a comment for the code above would have to place it at some indent, and then
re-derive the same claim from an indent that formatting has moved. An earlier
design did exactly that, and the comment walked leftwards a few columns on every
format. elm-format detaches these too.

### Question 2 — how deep?

Descend into the declaration, picking at each level the innermost thing that
genuinely owns the comment. Both of ours descend into the array.

The interesting half of this question is knowing when to *stop*. A comment that
merely trails something must not be sucked inside it:

```gren
v =
    fn a { r | x = 1 } {- note -} b
```

That comment is about the record, and it sits past the record's closing `}` — on
the record's last row, which is the one place where "inside this" and "after
this" are both readable. Pulled inside, it would come out as
`{ r | x = 1 {- note -} }`, where it now reads as a note about the field. So the
formatter keeps it out. Written *before* the `}`, it belongs inside, and stays
there:

```gren
fn a { r | a = 1 {- c -} } b     -- about the field: goes inside
fn a { r | a = 1 } {- c -} b     -- about the record: stays out
```

### Question 3 — which gap?

Between which two things at that level does it sit? Count how many of them end
before the comment.

`-- smallest` lands after the `1`. `{- then -}` lands between the `2` and
the `3`.

### Question 4 — how does it join?

This is where the role is chosen. `-- smallest` is on the same row as the `1`
before it, so it trails it: `TrailsPrevious`. `{- then -}` sits in the gap at a
comma — and a comma is one of the separators the parser does not record a
position for, so the two ways of typing it are literally the same input to the
formatter. That case is [C2](#c2--when-the-parser-doesnt-record-the-punctuation-the-comment-leads-what-follows)
below, and the answer is `LeadsNext`: the comment leads whatever follows the
separator.

Everything after this point just draws what was decided. The renderer's only
remaining question about a comment is about its **text**, not its position: can
code follow it on the same line? That is the next section.

---

## The two kinds of comment

Almost every difference in how two comments are treated comes down to one
mechanical fact: **can the code around this comment still fit on one line?**

| | Can the surrounding code stay on one line? |
|---|---|
| `-- like this` | **No** — a `--` runs to the end of its line, so nothing can follow it there. |
| `{- like this -}` (all on one line) | **Yes** — code carries on right after the `-}`. |
| `{- like this` ⏎ `   and this -}` (several lines) | **No** — the comment itself spans lines, so whatever contains it must too. |

So a single-line `{- -}` can ride along in the middle of a line, and the other
two cannot. Wherever a rule below says "a comment that can't share the line",
it means a `--` or a multi-line `{- … -}`.

```gren
-- you write, and gren-format keeps (the block comment rides the line):
sizes =
    [ 1, {- one -} 2, 3 ]
```

```gren
-- you write:
sizes =
    [ 1 -- one
    , 2, 3 ]
```

```gren
-- gren-format writes (the `--` ends its line, so the array has to open up):
sizes =
    [ 1 -- one
    , 2
    , 3
    ]
```

---

## The seven rules at a glance

1. **C1** — A comment belongs to the code you wrote it next to.
2. **C2** — Where the parser doesn't record the punctuation, the comment leads
   what follows.
3. **C3** — A comment never forces a break.
4. **C4** — A comment changes where the lines fall, and nothing else.
5. **C5** — gren-format adds nothing around a comment.
6. **C6** — An own-line comment is indented to the code it leads.
7. **C7** — A comment keeps the rows you gave it.

The first two are about **which piece of code a comment is attached to**; the
last five are about **how the attached comment is laid out**. They never trade
against each other: attachment is settled first, and the layout works with
whatever it is given.

C1 and C2 are in order, not in competition. C1 decides wherever your source
gives the formatter enough to see what you meant; C2 is the tie-breaker for the
places where it doesn't. C2 never overrides C1.

---

## C1 — A comment belongs to the code you wrote it next to

Where a comment ends up is decided by where you wrote it, and nothing else.
Layout never re-homes a comment onto a different piece of code to make a line
fit.

```gren
-- you write:
f =
    someFunction
        argOne -- about argOne
        argTwo
```

```gren
-- gren-format writes (unchanged — the note is still on argOne's line):
f =
    someFunction
        argOne -- about argOne
        argTwo
```

Nothing about `argTwo` needing its own line, or the call being wide, can pull
that comment off `argOne`.

The same holds inside a list. The comment stays on the item it was written
after, not on the one below it:

```gren
-- you write, and gren-format keeps:
colors =
    [ "red" -- the warm one
    , "blue"
    ]
```

And in a nested list, a comment stays inside the list it was written in:

```gren
-- you write, and gren-format keeps:
b =
    [ 1
    , [ 2
      -- about three
      , 3
      ]
    ]
```

Where the source records a bracket, the side you wrote the comment on is kept —
just inside an opening bracket stays inside, just past a closing bracket stays
outside:

```gren
-- you write, and gren-format keeps both:
a =
    [ {- primary -} 1, 2 ]


b =
    fn a [ 1, 2 ] {- c -} last
```

---

## C2 — When the parser doesn't record the punctuation, the comment leads what follows

Gren's parser reads punctuation and keywords and then throws most of them away.
Of everything that can separate two pieces of an expression, only **a binary
operator** (`+`, `++`, `|>`, …) and **a bracket** (`(`, `[`, `{` and their
closers) survive with a recorded position. All of these are gone by the time the
formatter runs:

    =    :    |    ,    ->    if / then / else    when / is    let / in
    an import's `as` and alias name

That means `x {- c -} = y` and `x = {- c -} y` arrive at the formatter as
*exactly the same information*: where `x` ends, where the comment is, where `y`
starts. The formatter cannot tell which side you wrote it on. (It also does not
look at how wide the whitespace gaps are — formatting must not depend on your
spacing.)

So one of the two spellings has to move. gren-format always picks the **later**
side: the comment leads whatever comes after the punctuation.

```gren
-- you write (either one of these):
point =
    { x {- across -} = 1 }


point2 =
    { x = {- across -} 1 }
```

```gren
-- gren-format writes (both the same):
point =
    { x = {- across -} 1 }


point2 =
    { x = {- across -} 1 }
```

The same thing at a `,`:

```gren
-- you write (either one):
a =
    [ 1 {- c -}, 2 ]


b =
    [ 1, {- c -} 2 ]
```

```gren
-- gren-format writes (both the same):
a =
    [ 1, {- c -} 2 ]


b =
    [ 1, {- c -} 2 ]
```

At a signature's `:` and at a type's `->`:

```gren
-- you write:
double {- c -} : Int -> Int
double n =
    n


triple : Int {- c -} -> Int
triple n =
    n
```

```gren
-- gren-format writes:
double : {- c -} Int -> Int
double n =
    n


triple : Int -> {- c -} Int
triple n =
    n
```

At the keywords. `when … is`:

```gren
-- you write:
f sel =
    when sel {- c -} is
        Just w ->
            1

        Nothing ->
            2
```

```gren
-- gren-format writes:
f sel =
    when sel is
        {- c -}
        Just w ->
            1

        Nothing ->
            2
```

At a branch's `->`:

```gren
-- you write:
f x =
    when x is
        Red -> -- a warm one
            1

        Blue ->
            2
```

```gren
-- gren-format writes (the comment leads the branch body):
f x =
    when x is
        Red ->
            -- a warm one
            1

        Blue ->
            2
```

At `then`:

```gren
-- you write:
f x =
    if x -- why
    then
        1

    else
        2
```

```gren
-- gren-format writes:
f x =
    if x then
        -- why
        1

    else
        2
```

And at `in`:

```gren
-- you write:
f =
    let
        a =
            1
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

### The exception: a `--` or a multi-line `{- … -}` above a `,`, a `|`, or a `->`

There is one family of separators where both spellings survive, because the
formatter *can* still tell them apart: the separators that **start** their line
— a `,`, a `|`, and the `->` of a signature broken across rows.

The reason is simple. A `--` (or a multi-line `{- … -}`) ends its line, so it is
either on the previous item's line or on a line of its own — two visibly
different places. And because these separators lead their line rather than
trailing it, a comment written above one strands nothing: it just sits at the
separator's own column.

So both of these are kept exactly as written:

```gren
-- you write, and gren-format keeps:
a =
    [ apple -- the red one
    , banana
    ]


b =
    [ apple
    -- about banana
    , banana
    ]
```

A record update's `|` behaves the same way:

```gren
-- you write, and gren-format keeps:
a rec =
    { rec -- about the base
        | alpha = 1
    }


b rec =
    { rec
        -- about alpha
        | alpha = 1
    }
```

And so does a custom type's `|`:

```gren
-- you write, and gren-format keeps:
type W
    = A -- about A
    | B
```

And so does a signature's `->`, once the signature is broken across rows:

```gren
-- you write, and gren-format keeps:
trailing :
    Int -- the argument
    -> String


ownRow :
    Int
    -- the argument
    -> String
```

A single-line `{- -}` has no such pull — it doesn't end its line, so the two
spellings really are identical — and it follows the general rule at every
separator, including these:

```gren
-- you write (either one of these):
c rec =
    { rec {- inline -} | alpha = 1 }


d rec =
    { rec | {- inline -} alpha = 1 }
```

```gren
-- gren-format writes (both the same):
c rec =
    { rec | {- inline -} alpha = 1 }


d rec =
    { rec | {- inline -} alpha = 1 }
```

So `d`'s author gets their text back and `c`'s does not. A custom type's `|`
answers the same question the other way — see [Three places that don't take the
later side](#three-places-that-dont-take-the-later-side).

### Three ways to type it, two possible results

At a `,` there is a third spelling: after the comma but still on the first
item's line. That puts the comment in the same *place* as the first spelling, so
the formatter can't distinguish them, and it collapses onto the first:

```gren
-- you write:
c =
    [ apple, -- c
      banana
    ]
```

```gren
-- gren-format writes:
c =
    [ apple -- c
    , banana
    ]
```

So there are three ways to type it and only ever **two** outcomes. The `->` has
the same third spelling, and it collapses the same way:

```gren
-- you write:
pastArrow : Int -> -- the argument
    String
```

```gren
-- gren-format writes:
pastArrow :
    Int -- the argument
    -> String
```

### Three places that don't take the later side

Because both spellings collapse into one output, the side C2 picks decides
**which of the two authors gets their text back unchanged** — the one who wrote
the comment before the separator, or the one who wrote it after. There is no
choice that serves both. C2 serves the author who writes it *after*; the three
places below serve the one who writes it *before*. The first has a structural
reason; the other two are a stated preference.

- **A module's `exposing ( … )` list**, which is the one list whose items get
  **reordered**. There, a comment after a name belongs to that name and travels
  with it through the sort, so the same module written in any order lands on the
  same output:

  ```gren
  -- you write:
  module M exposing
      ( charlie
      , alpha -- about alpha
      , bravo
      )
  ```

  ```gren
  -- gren-format writes (the comment travels with `alpha`):
  module M exposing
      ( alpha -- about alpha
      , bravo
      , charlie
      )
  ```

- **A single-line `{- -}` at a custom type's `|`.** The `|` is as unrecorded
  here as anywhere, so the two spellings still collapse into one — but the side
  picked is the **earlier** one: the comment lands trailing the variant before
  it. A note written beside a variant reads as a note *about that variant*, so
  the union serves the author who writes the comment **before** the `|`. A
  record update's `|` collapses in exactly the same way and serves the author
  who writes it **after** — the two are a deliberate pair of preferences, not a
  difference in what the formatter can see. (elm-format breaks the type open
  around such a comment whichever side it is on, so neither choice would match
  it; parity does not decide this one either way.)

  ```gren
  -- you write (either one of these):
  type V
      = A {- c -} | B


  type W
      = A | {- c -} B
  ```

  ```gren
  -- gren-format writes (both trail the earlier variant):
  type V
      = A {- c -} | B


  type W
      = A {- c -} | B
  ```

- **An import's `as`.** The `as` keyword and the alias name after it are also
  unrecorded, so the two spellings collapse here too — and here as well the
  side picked is the **earlier** one: the comment lands before the `as`,
  trailing the module name it annotates. Same preference as the union's `|`,
  for the same reason.

  ```gren
  -- you write (either one of these):
  import Dict {- c -} as D
  import Math as {- c -} M
  ```

  ```gren
  -- gren-format writes (both before the `as`):
  import Dict {- c -} as D
  import Math {- c -} as M
  ```

---

## C3 — A comment never forces a break

Something you wrote on one line stays on one line, as long as every comment
inside it can share that line. A single-line `{- -}` can; a `--` and a
multi-line `{- … -}` cannot.

This is about the **code's** line. The same sentence applied to a *comment's* own
line is [C7](#c7--a-comment-keeps-the-rows-you-gave-it): two comments you wrote
on one row can share it, so they keep it.

```gren
-- you write, and gren-format keeps all of these on one line:
a =
    f {- k -} x


b =
    [ 1, {- c -} 2 ]


d =
    { x = 0, {- origin -} y = 0 }
```

This holds for a comment written in front of any kind of item — a value, a
record, a call, a lambda:

```gren
-- you write, and gren-format keeps:
a =
    [ {- c -} \q -> q + one ]


b =
    [ {- c -} { a = 1 } ]


c =
    [ {- c -} fn one ]
```

When the comment *can't* share the line, the construct opens up — but that is
the comment's own line ending, not a decision the formatter made:

```gren
-- you write:
a =
    [ 1 {- spans
           two lines -}, 2 ]
```

```gren
-- gren-format writes:
a =
    [ 1 {- spans
           two lines -}
    , 2
    ]
```

The comment spans lines, so the array cannot be a one-line array any more; and
once a construct is spread across lines, gren-format puts one item per line like
it always does. That is the only kind of break a comment ever causes — and the
next rule is what says so.

---

## C4 — A comment changes where the lines fall, and nothing else

A comment can force line breaks; that's C3, and it's unavoidable. Everything
past that — the indentation, the grouping, which piece of code owns which part —
has to be what gren-format would have produced with the comment deleted.

**The test:** write the same code without the comment and compare. If the two
disagree about anything other than where the lines fall, that's a bug.

**Grouping is unchanged.** A `--` in the middle of an operator chain has to
break the chain, but the chain breaks at the operator that *precedence* chooses,
never at whichever operator the comment happens to sit next to:

```gren
-- you write:
a =
    one + two -- c
        * three
```

```gren
-- gren-format writes (broken at the `+`, which binds loosest):
a =
    one
        + two -- c
          * three
```

```gren
-- and with the comment deleted, the same expression:
b =
    one + two * three
```

Both forms read as `one + (two * three)`. Breaking after `two` instead — which
is where the comment sits — would read as `(one + two) * three`, a grouping
gren-format never produces.

**Indentation is unchanged.** Here is the same expression with and without a
comment leading it:

```gren
-- you write, and gren-format keeps:
withComment =
    gn <|
        {- c -}
        { x = 1
        , y = 2
        }


withoutComment =
    gn <|
        { x = 1
        , y = 2
        }
```

The record is at the same column both times. The comment adds a line; it does
not add a level of indentation.

**Ownership is unchanged.** A comment that splits a function from its argument
leaves the argument indented under the function exactly as any other broken call
would:

```gren
-- you write, and gren-format keeps:
a =
    gn -- c
        arg


b =
    gn arg
```

---

## C5 — gren-format adds nothing around a comment

No blank line above or below a comment **because of** the comment. No floating a
comment out onto a line of its own to give it room.

```gren
-- you write, and gren-format keeps (no blank line inserted above the comment):
a =
    [ 1
    -- about two
    , 2
    ]
```

(elm-format does insert a blank line here — see
[the divergence catalogue](elmFormatComparison.md).)

A comment written on an operator's line stays on that line — whether you put it
before the operator or after it — rather than being given a line of its own:

```gren
-- you write, and gren-format keeps:
a =
    head
        {- c -} ++ rest


b =
    items
        {- c -} |> fn


c =
    fn <| -- c
        one
```

Two things that look like counter-examples and aren't:

- **A blank line gren-format would have added anyway.** `let` bindings always
  get one blank line between them, and a comment leading a binding travels with
  it — so the blank line lands above the comment. Delete the comment and the
  blank line is still there; it belongs to the binding, not to the comment.

  ```gren
  -- you write:
  f =
      let
          x =
              1
          -- about y
          y =
              2
      in
      x + y
  ```

  ```gren
  -- gren-format writes:
  f =
      let
          x =
              1

          -- about y
          y =
              2
      in
      x + y
  ```

- **A comment in front of a `"""…"""` string.** Gluing the comment onto the
  front of that item would shift the string's first line without shifting its
  other lines, and Gren requires every line of a multi-line string to be
  indented the same — the result wouldn't compile. Such a comment gets its own
  line instead. This is about keeping the output valid, not about giving the
  comment room.

  ```gren
  -- you write, and gren-format keeps:
  afterComma =
      [ "first"
      {- leads the second item -}
      , """
        bravo
        """
      ]
  ```

---

## C6 — An own-line comment is indented to the code it leads

A comment on its own line is indented to match the code below it — not to a
column of its own, and not to the column you happened to type it at.

Precisely: to the column where the line it leads **begins**. In a call, a `let`,
or a `when` body, that's just the code's column:

```gren
-- you write, and gren-format keeps:
a =
    someFunction
        argOne
        -- about argTwo
        argTwo
```

In a list, a record update, or a custom type, the line below begins with its
`,` or `|` prefix — so that's where the comment goes, two columns to the left of
the item text:

```gren
-- you write, and gren-format keeps all three:
b =
    [ 1
    , [ 2
      -- about three
      , 3
      ]
    ]


c rec =
    { rec
        -- about alpha
        | alpha = 1
    }


type U
    = A
    -- about B
    | B
```

This is normalizing, not preserving. Line the comment up with the item text
instead, and gren-format pulls it back to the prefix column:

```gren
-- you write:
a =
    [ apple
      -- about banana
    , banana
    ]
```

```gren
-- gren-format writes:
a =
    [ apple
    -- about banana
    , banana
    ]
```

A comment on its own line **below a top-level declaration** follows the same
rule: everything at the top level begins at column 1, so the comment moves to
column 1 — whether it trails the declaration above or introduces the one below.
What your original indentation still decides is which declaration the comment
belongs to — written indented under the code above, it stays with that
code and a blank line separates it from what follows:

```gren
-- you write:
total =
    alpha
        ++ beta
        {- trails the chain -}
next =
    1
```

```gren
-- gren-format writes (comment stays with `total`, at column 1):
total =
    alpha
        ++ beta
{- trails the chain -}


next =
    1
```

The full treatment of that case, including the version where the comment
introduces the *next* declaration instead, is in
[A comment on its own line below a declaration](formatterRules.md#a-comment-on-its-own-line-below-a-declaration).

---

## C7 — A comment keeps the rows you gave it

gren-format never joins rows you wrote apart, and never splits a row you wrote
together. Where C3 is about the **code's** line, this is about the comment's own.

Two or more comments in one gap are a **run** (there is a section on runs
[below](#comments-in-runs)), and the same question decides their rows:
gren-format never moves a member of a run between rows. Written on one row they
stay on one row; written on separate rows they stay apart. It holds in every
position — a lambda body, an `else` branch, a record field, a call's
argument list, a `let` binding, a `when` branch — so you never have to know which
one you are in.

One position is an exception: a run written *just inside* an opening bracket, or
between a pipeline step's operator and its operand, is laid out all-or-nothing
instead. The authored row isn't recorded there, so neither direction can be
honoured — see
[Known limitations](knownLimitations.md#a-comment-run-just-inside-a-bracket-doesnt-keep-its-rows).


```gren
-- you write:                        -- gren-format:
\q ->                                \q ->
    {- a -} {- b -} { x = 1              {- a -} {- b -}
    , y = 2 }                            { x = 1
                                         , y = 2
                                         }

\q ->                                \q ->
    {- a -}                              {- a -}
    {- b -}                              {- b -}
    { x = 1                              { x = 1
    , y = 2 }                            , y = 2
                                         }
```

A `--` counts as a member like any other: `{- a -} {- b -} -- c` written on one
row stays on that row, with the code below it.

This is a deliberate divergence from elm-format, which decides a run's rows from
the context around it instead — stacking one-per-row after a lambda's `->` and a
declaration's `=`, keeping them together in an `else` branch or a call argument,
and joining a run you *did* split when it sits inside a paren. See
[divergence #30](elmFormatComparison.md#divergence-30).

The two directions are not the same rule twice. Keeping a one-row run together is
[C3](#c3--a-comment-never-forces-a-break) and
[C5](#c5--gren-format-adds-nothing-around-a-comment) already — every member can
share the line, and floating one out onto a row of its own to give it room is
exactly what C5 forbids. Keeping a split run apart is the part neither covers:
C3 says a comment never *forces* a break, not that a break you made survives, and
C5 forbids *adding*, while joining two rows takes one away. C7 states both halves
as one rule so there is a single sentence to check against.

---

## Comments in runs

Two or more comments in the same gap are a **run**, and a run is a unit. C7
above is the rule about a run's *rows*; four more govern the rest, and each is
there because the run as a whole has a property no single member does.

**The reference row grows through the run.** When the formatter asks "what row
does the thing before me end on", the answer counts any comments already written
onto that row. A multi-line comment closes several rows below where it opened,
and what you wrote after its `-}` sits beside it — not on a line of its own:

```gren
fruit =
    [ Apple
    , Mango {- mango's
               comment -} -- and mango's trailing line comment
    , Pear
    ]
```

**A run crosses a separator together, or not at all.** [C2](#c2--when-the-parser-doesnt-record-the-punctuation-the-comment-leads-what-follows)
moves a single-line `{- -}` across an unrecorded separator, while a `--` and a
multi-line comment both stay with the item above. So a *mixed* run in one gap
would tear in half if each member were asked separately. It is asked of the whole
run instead. These two differ in nothing but which comments are in the gap, and
that alone decides where both of them land:

```gren
-- both members could cross, so the run crosses:
v =
    [ 1
    , {- a -} {- b -} 2
    ]


-- one member cannot, so neither does:
w =
    [ 1 {- a -} -- b
    , 2
    ]
```

**Once a run breaks, it stays broken.** If any member cannot share a line, every
member gets its own row:

```gren
c =
    [ {- 1 a
         1 b -}
      {- x -}
      1
    ]
```

**A run moves as a unit.** When a run is detached, sorted along with a name in an
`exposing` list, or given a blank line above it, it is the run that moves — never
just its first member.

Put together, here is a run of six comments of all three kinds in one gap. Every
member keeps the row you wrote it on (C7), each multi-line body is re-indented
under its own `{-`, and the file is a fixed point:

```gren
v =
    fn a {- 1 -} {- 2
                    over two rows -} {- 3 -} {- 4
                                                again -} {- 5 -} -- 6
```

### Why this works for any number of comments

A fair question about the rules above is: those are five rules about runs, so
what happens at seven members, or twelve, or with kinds nobody has tried in that
order?

Nothing new — and that is a property of how the rules are written rather than a
claim about how much testing they have had.

Most of them are rules about a **boundary**: a comment and the one thing
immediately before it. None looks two comments back. So picture a run as a chain:

```
code │ A │ B │ C │ code
     ↑   ↑   ↑   ↑
     each of these rules is a question about ONE of these
```

If that is all they read, then what a run does is decided by *which boundaries it
contains* — and its length is not an input at all. There are three comment
shapes, so there are exactly **nine possible comment-to-comment boundaries**, and
a run of any size is built out of those nine.

A longer run can therefore reach something new in only two ways: by containing a
boundary a shorter one could not, or by putting a member between **two**
boundaries at once. The first is exhausted as soon as all nine have appeared. The
second only matters if some rule looks at both sides of a member — and none does.

The other rules — crossing a separator, riding a line, moving as a unit — do read
the whole run, but they read it as a single yes-or-no question asked of *every*
member: **can they all cross? can they all ride?** Asking "does this hold for
everything in the list" does not care how long the list is either, so those are
length-independent for a different reason rather than an exception.

That is a claim that can be checked rather than asserted, and it is: the test
suite sweeps comment runs across the whole corpus, varying both how many members
they have and which kinds, in every gap of every fixture. It behaves the way the
argument says it must — the sweeps that first reach a new boundary find real
bugs, and the ones beyond that find nothing that the earlier ones had not. The
argument in full, with the numbers, is
[The comment algorithm](commentAlgorithm.md) §8 and §10.

---

## Where the rules run out

A few placements are genuinely undecidable from what the parser hands over, and
it is better to know which they are than to be surprised by them. Each is written
up with a worked example in **[Known limitations](knownLimitations.md)**.

**A comment after the last `let` binding ends up below the `in`.**

```gren
-- you write:                       -- gren-format writes:

f =                                 f =
    let                                 let
        a = 1                               a =
        -- after the last binding               1
    in                                  in
    a                                   -- after the last binding
                                        a
```

`in` has no recorded position, so "before `in`" and "after `in`" are literally
the same input. Keeping the comment with the bindings *looks* right and is not
stable — it oscillates. Below is the only choice that is both stable and
defensible. elm-format keeps it with the bindings, so this is a difference you
may notice.

**A `--` inside an effect module's `where { … }` block** can escape the block.
The parser hands back byte-identical information for both layouts, so there is
nothing to decide from.

**A comment after the last name of a flat, one-line `exposing ( … )` list** is
read as the list's rather than that name's, because the closing `)` has no
recorded position to measure against. Write the list across several lines and the
two become tellable apart again.

**A run of comments just inside an opening bracket** loses the rows you gave it
(C7's one exception, above): one gap is one attachment, so the run carries a
single role derived from its members' shapes, and nothing records which of them
you wrote together.

Where gren-format and elm-format place a comment differently — and there are
several such places, all deliberate — the full list is in
**[Comparison with elm-format](elmFormatComparison.md)**.

---

## Trying it yourself

```bash
node gren-format/app --lpt MyFile.gren          # every comment, with the role it got
node gren-format/app --pre-context MyFile.gren  # what the parser handed over
node gren-format/app --show MyFile.gren         # format, re-parse, check the meaning
                                                # is unchanged, format again, check
                                                # nothing moved
```

(`gren-format/app` is the standalone CLI, built with `./build.sh` in that
directory.)

`--show` is the useful one: a zero exit status means the file formatted without
crashing, kept its meaning, and is a fixed point.

---

## See also

- [The comment algorithm](commentAlgorithm.md) — the implementation, for people
  extending the formatter
- [Known limitations](knownLimitations.md) — the undecidable cases in full
- [Comparison with elm-format](elmFormatComparison.md) — every deliberate
  difference, with its reasoning
- [The rule reference](formatterRules.md#comments) — the comment rules that
  belong to one particular construct
