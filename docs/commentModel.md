# How gren-format handles comments

Comments are the hardest thing a formatter deals with, and the reason is
structural rather than fiddly: **comments are the one part of your source the
compiler does not care about, and the only part the formatter cannot afford to
get wrong.** Move a line of code and it still means the same thing. Move a
comment and it now describes something else.

This document explains how `gren format` decides where each comment goes. It is
written for Gren developers who are curious about the machinery, not for people
maintaining it — there is no code in it beyond a few flag names you can run
yourself, and every example was produced by running the formatter.

Two neighbours, if you want something else:

| document | question it answers |
|---|---|
| [How gren-format places your comments](commentHandling.md) | *what* it does — the seven rules, with a before/after for each |
| **this one** | *how* it does it |
| [The comment algorithm](commentAlgorithm.md) | the implementation, in full, for people extending it |

---

## 1. The problem

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

Putting them back together is what this document is about.

> **Why not fix it upstream?** elm-format builds comments into its own AST, so an
> expression node physically holds the comments written around it, and it never
> has this problem. That is a real advantage, and we pay for it here — in
> exchange for never disagreeing with the compiler about what a program is.

---

## 2. What counts as correct

Three things, and the third is the one that makes this hard.

**1. Every comment survives.** Same text, same kind, exactly once. `gren format`
never edits what you wrote inside a comment. (It does re-indent the continuation
lines of a multi-line `{- … -}` so its body hangs under its own `{-`; that is
layout, not text.)

**2. It lands where you wrote it.** Beside the code it was about — the seven
rules in [commentHandling.md](commentHandling.md) are the full statement.

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

## 3. The idea: decide once

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
| `LeadsNext` | it belongs to what comes *after* an invisible separator (see below) |
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

## 4. Following two comments through

Placement is four questions, asked in order. Here they are against the file from
§1, which the formatter leaves exactly as written:

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
comma, which is the interesting case:

> **Of everything that can separate two pieces of an expression, almost nothing
> survives parsing.** Only a binary operator and a bracket keep a recorded
> position. All of these are simply gone: `=` `:` `|` `,` `->`, and every
> keyword — `if` `then` `else` `when` `is` `let` `in`, an import's `as`.

So these two files arrive at the formatter as *the same three facts* — where `1`
ends, where the comment is, where `2` starts — and it cannot tell them apart:

```gren
v =
    [ 1, {- then -} 2 ]

v =
    [ 1 {- then -}, 2 ]
```

One of them has to move. Both of the above format to:

```gren
v =
    [ 1, {- then -} 2 ]
```

The rule is **the comment leads whatever follows the separator** — role
`LeadsNext`. Making that a decision recorded once, rather than something each
part of the renderer works out for itself, is what stops a comment flip-flopping
across an invisible comma between formats.

Everything after this point just draws what was decided. The renderer's only
remaining question about a comment is about its **text**, not its position: can
code follow it on the same line?

| | can code follow it on this line? |
|---|---|
| `-- like this` | **no** — it runs to the end of the line |
| `{- like this -}` on one row | **yes** |
| `{- like this` ⏎ `and this -}` | **no** — it brings its own line breaks |

Those three shapes are all that "what kind of comment is this" ever means here.

---

## 5. When comments come in runs

Two or more comments in the same gap are a **run**, and a run is a unit. Five
rules govern them, and each one is there because the run as a whole has a
property no single member does.

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

**A run crosses a separator together, or not at all.** The comma rule from §4
applies to a single-line `{- -}`; a `--` and a multi-line comment both stay with
the item above. So a *mixed* run in one gap would tear in half if each member
were asked separately. It is asked of the whole run instead. These two differ in
nothing but which comments are in the gap, and that alone decides where both of
them land:

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

**A run keeps the rows you gave it.** Members written on one row stay on one row;
members written apart stay apart. `gren format` never joins rows you split or
splits rows you joined. (elm-format decides this from the surrounding context
instead — one of the [deliberate differences](elmFormatComparison.md).)

**A run moves as a unit.** When a run is detached, sorted along with a name in an
`exposing` list, or given a blank line above it, it is the run that moves — never
just its first member.

Put together, here is a run of six comments of all three kinds in one gap. Every
member keeps the row you wrote it on, each multi-line body is re-indented under
its own `{-`, and the file is a fixed point:

```gren
v =
    fn a {- 1 -} {- 2
                    over two rows -} {- 3 -} {- 4
                                                again -} {- 5 -} -- 6
```

---

## 6. Why this works for any number of comments

A fair question about the previous section is: those are five rules about runs,
so what happens at seven members, or twelve, or with kinds nobody has tried in
that order?

Nothing new — and that is a property of how the rules are written rather than a
claim about how much testing they have had.

Most of those rules are rules about a **boundary**: a comment and the one thing
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
bugs, and the ones beyond that find nothing that the earlier ones had not.
There is more on that, with the numbers, in
[The comment algorithm](commentAlgorithm.md).

---

## 7. Where it cannot win

Some placements are genuinely undecidable from what the parser hands over, and it
is better to know which they are than to be surprised by them. All three are
written up with worked examples in [Known limitations](knownLimitations.md).

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

**A `--` inside an effect module's `where { … }` block** can escape the block. The
parser hands back byte-identical information for both layouts, so there is
nothing to decide from.

**A comment after the last name of a one-line `exposing ( … )`** is read as the
list's rather than that name's, because the closing `)` has no recorded position
to measure against. Write the list across several lines and the two become
tellable apart again.

There is also a longer list of places where `gren format` and `elm-format`
deliberately disagree, each with its reasoning on record:
[Comparison with elm-format](elmFormatComparison.md).

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

- [How gren-format places your comments](commentHandling.md) — the seven rules,
  with a before/after for each
- [The comment algorithm](commentAlgorithm.md) — the implementation in full
- [Known limitations](knownLimitations.md) · [Comparison with
  elm-format](elmFormatComparison.md)
