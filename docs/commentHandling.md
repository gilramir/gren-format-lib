# How gren-format places your comments

`gren format` **never changes the text of a comment.** What it decides is where
each comment sits relative to the code around it — which line it lands on, and
how far it is indented.

Six rules decide every comment in a Gren file. This document states each one in
plain language and shows what it does, with a "you write / gren-format writes"
pair for every case. Every example on this page was produced by running the
formatter.

For comment rules that belong to one particular construct (block-comment body
re-indentation, doc comments, an effect module's `where` block), see the
[Comments section of the rule reference](formatterRules.md#comments). For places
where gren-format and elm-format disagree about comments, see the
[divergence catalogue](elmFormatComparison.md).

---

## Table of contents

- [The two kinds of comment](#the-two-kinds-of-comment)
- [The seven rules at a glance](#the-seven-rules-at-a-glance)
- [C1 — A comment belongs to the code you wrote it next to](#c1--a-comment-belongs-to-the-code-you-wrote-it-next-to)
- [C2 — When the parser doesn't record the punctuation, the comment leads what follows](#c2--when-the-parser-doesnt-record-the-punctuation-the-comment-leads-what-follows)
- [C3 — A comment never forces a break](#c3--a-comment-never-forces-a-break)
- [C4 — A comment changes where the lines fall, and nothing else](#c4--a-comment-changes-where-the-lines-fall-and-nothing-else)
- [C5 — gren-format adds nothing around a comment](#c5--gren-format-adds-nothing-around-a-comment)
- [C6 — An own-line comment is indented to the code it leads](#c6--an-own-line-comment-is-indented-to-the-code-it-leads)
- [C7 — A comment keeps the rows you gave it](#c7--a-comment-keeps-the-rows-you-gave-it)
- [Where the rules run out](#where-the-rules-run-out)

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

Two or more comments in one gap are a **run**, and the same question decides
their rows: gren-format never moves a member of a run between rows. Written on
one row they stay on one row; written on separate rows they stay apart. It holds
in every position — a lambda body, an `else` branch, a record field, a call's
argument list, a `let` binding, a `when` branch — so you never have to know which
one you are in.

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

## Where the rules run out

A few places can't be decided well, because the information simply isn't there:

- **A comment after the last `let` binding** goes below the `in`. `in` has no
  recorded position, so "before `in`" and "after `in`" are the same thing to the
  formatter, and below is the only choice that's stable.
- **A `--` inside an effect module's `where { … }` block** can escape the block,
  because the parser hands the formatter byte-identical information for both
  layouts.
- **A comment after the last name of a flat, one-line `exposing ( … )` list**
  is read as belonging to the list rather than to the name, because the closing
  `)` has no recorded position to measure against. Write the list across several
  lines and the two are tellable apart again.

Each of these, with a worked example, is in
**[Known limitations](knownLimitations.md)**.

Where gren-format and elm-format place a comment differently — and there are
several such places, all deliberate — the full list is in
**[Comparison with elm-format](elmFormatComparison.md)**.
