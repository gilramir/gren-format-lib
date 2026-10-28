# Known limitations

Places where `gren-format` falls short of ideal — a shape where the parser it is
built on accepts more than today's compiler, or less, a comment-placement
choice forced by a token with no recorded source position, or a limit of the
formatter itself. See the main [README](../README.md) for the overview, and
[Gren Formatter Rules](formatterRules.md) for what the formatter does when
nothing is going wrong.

## Table of contents

- [A definition below its type annotation that doesn't start in column 1 is accepted](#a-definition-below-its-type-annotation-that-doesnt-start-in-column-1-is-accepted)
- [An `as`-alias inside brackets keeps parens around its base](#an-as-alias-inside-brackets-keeps-parens-around-its-base)
- [A `when` whose branches line up with `when` can't be formatted](#a-when-whose-branches-line-up-with-when-cant-be-formatted)
- [Output today's compiler rejects, that the next one will accept](#output-todays-compiler-rejects-that-the-next-one-will-accept)
- [Wide `when` branch patterns](#wide-when-branch-patterns)
- [Comment placement near invisible tokens](#comment-placement-near-invisible-tokens)
- [Comments near an effect module's `where` block](#comments-near-an-effect-modules-where-block)
- [A comment right after `exposing` doesn't sort with the first name](#a-comment-right-after-exposing-doesnt-sort-with-the-first-name)
- [A module `exposing` list's closing paren isn't recorded](#a-module-exposing-lists-closing-paren-isnt-recorded)
  - [A comment past a flat list](#a-comment-past-a-flat-list)
- [A comment after the last binding in a `let`](#a-comment-after-the-last-binding-in-a-let)
- [Block comment body indentation](#block-comment-body-indentation)
- [A comment run just inside a bracket doesn't keep its rows](#a-comment-run-just-inside-a-bracket-doesnt-keep-its-rows)
- [Very deep lambda or unary-minus nesting can overflow the stack](#very-deep-lambda-or-unary-minus-nesting-can-overflow-the-stack)


---

## A definition below its type annotation that doesn't start in column 1 is accepted

A top-level declaration must begin in column 1. Elm requires it, and so does the
real Gren compiler. compiler-common 4.0.0 added the check
([compiler-common#37](https://github.com/gren-lang/compiler-common/issues/37)),
but only before a declaration's first token. When the declaration has a type
annotation, the definition line below it is still accepted at any column.
Tracked at [compiler-common#47](https://github.com/gren-lang/compiler-common/issues/47).

**Without a comment, gren-format quietly repairs the file.** The name
normalizes back to column 1, and the output compiles:

```gren
-- you write (rejected by gren 0.6.6):    -- gren-format writes (compiles):
pick : a                                  pick : a
        pick =                            pick =
    pick                                      pick
```

That is benign in itself, but it means `gren-format --show` exiting 0 is **not**
evidence that the file you handed it would build.

**With a comment in front of the name, the output is refused too.** Because
gren-format never moves a comment off the row it was written on
([C7](commentHandling.md#c7--comments-written-together-stay-together-comments-written-apart-stay-apart)),
the name stays right of the comment:

```gren
foo : a
{- lead -} foo =
    one
```

is a fixed point, and gren 0.6.6 refuses both the input and that output with
`EXPECTING DEFINITION`.

It takes an annotation whose last type cannot take an argument (a type variable,
or a closing bracket): after `foo : Int`, the parser reads the next line's `foo`
as a type argument and refuses the file itself. Inside a `let` the layout is
refused as well. The elm-format side of the same fact is
[divergence #31](elmFormatComparison.md#divergence-31).

Workaround: start definitions in column 1, and give a comment above a
declaration a row of its own.

## An `as`-alias inside brackets keeps parens around its base

At the top of a `when` branch, gren-format writes an alias the way elm-format
does: `Nothing as whole ->`, `0 as z ->`. Anywhere else (inside parentheses, a
list or record pattern, a constructor payload, or a function or lambda
argument) a number or constructor base keeps a pair of parens elm-format
leaves out:

```gren
-- elm-format:                 -- gren-format:
Just (Nothing as inner) ->     Just ((Nothing) as inner) ->
f (0 as z) =                   f ((0) as z) =
```

The bare form is valid Gren, but compiler-common 4.1.1 refuses it, so
gren-format can't write it. This is the part of
[compiler-common#31](https://github.com/gren-lang/compiler-common/issues/31)
that 4.0.0 did not fix: inside brackets the parser reads the pattern without
checking for `as` after a number or constructor. Tracked at
[compiler-common#48](https://github.com/gren-lang/compiler-common/issues/48). A `let` destructure is
parsed differently again and refuses `Just n as w`, so gren-format writes
`(Just n) as w` there.

The extra parens are harmless and the output compiles. When the parser is
fixed, `aliasBaseNeedsParens` in `Formatter.Logical.InsertPatterns` can be
dropped.

---

## A `when` whose branches line up with `when` can't be formatted

gren 0.6.6 compiles a `when` whose branches start in the same column as the
`when` keyword, but compiler-common 4.1.1 reads only the first branch and then
stops. gren-format refuses the file with an error about the second branch that
doesn't point at the cause, such as `Not a valid number`:

```gren
g a =
    when a is
    Just x ->
        x

    Nothing ->    -- refused here
        0
```

Indent the branches past `when` and the file formats; gren-format then writes
them 4 in, as usual. Tracked at
[compiler-common#49](https://github.com/gren-lang/compiler-common/issues/49). It turned up in published
code: compiler-common 3.0.0's own `SemanticVersionRange.gren`.

---

## Output today's compiler rejects, that the next one will accept

`gren-format` is built on `compiler-common`, which is the parser the **next**
Gren compiler will use. Today's released compiler is the Haskell one, and the
two do not accept exactly the same language: `compiler-common` is deliberately
more permissive about indentation. Where they differ, the formatter follows
`compiler-common` — so it can hand you a file the compiler you are using right
now refuses to build.

A bracketed pattern is where this shows up. Write one broken across rows with
its continuation indented, and today's compiler is happy:

```gren
f z =
    let
        { next
          , count
          } =
            z
    in
    next
```

`gren-format` moves the `,` and the `}` back to the `{`'s own column, which is
also the column the binding starts at:

```gren
f z =
    let
        { next
        , count
        } =
            z
    in
    next
```

That is the canonical form, and `compiler-common` parses it — so the formatter's
own checks all pass: the AST is preserved and the output is a fixed point. The
Haskell compiler stops at the first continuation row, because it requires every
row inside a binding to be indented **past** the binding's start column:

```
-- UNFINISHED RECORD PATTERN ---------------------------------------- src/M.gren

I was partway through parsing a record pattern, but I got stuck here:

6|         { next
                 ^
I was expecting to see a closing curly brace next. Try adding a } here?
```

Elm rejects it too, with the same message — this is a place where Gren is
departing from what it inherited, not a bug in either parser.

The same applies to an array pattern, and in a `when` branch head as well as a
`let` binding:

```gren
g z =
    when z is
        [ a
        , b
        ] ->
            a
```

Parameter patterns are unaffected — a pattern in an argument position does not
start its line, so its continuation still clears the declaration's column.

**If you are on the released compiler**, the workaround is to keep such a
pattern on one line; `{ next, count } =` is accepted by both. This limitation
disappears when the `compiler-common`-based compiler ships, at which point the
formatted output above compiles as written.

## Wide `when` branch patterns

A `when` branch whose record (or array) pattern is too wide to fit on one line
can wrap in a way the Haskell-based Gren compiler rejects:

```gren
-- the formatter may produce:
        { aPopped = Just { first = m1, rest = ms1rest }
        , bPopped = Just { first = m2, rest = ms2rest }
        } ->
```

The Haskell-based compiler requires every continuation line of a pattern to be
indented deeper than the pattern's first character, and this layout doesn't
satisfy that. Compiling the formatted file may fail with "I was expecting to
see a closing curly brace next." Rejoin the pattern onto one line by hand
until this is resolved.

## Comment placement near invisible tokens

As described in [When the formatter can't tell what you meant](formatterRules.md#when-the-formatter-cant-tell-what-you-meant), a comment beside `=`, `:`, `|`, or an import's `as` always snaps to one
canonical side. Two different intents produce the same output.

An `if`'s `then` is one of these tokens, and it is worth naming because the
comment does not merely snap sideways — it changes branch. A comment written
between the condition's last token and `then` comes out inside the `then`
branch's body:

```gren
-- you write:                      -- gren-format writes:
if a > 0 && b {- x                 if a > 0 && b then
               y -} then               {- x
    1                                     y -}
                                          1
else
    0                              else
                                       0
```

`then` has no recorded position, so "the end of the condition" and "the start of
the body" are the same place. Nothing here distinguishes them.

`type alias` is another. The `alias` keyword has no recorded position, so a
comment between `type` and the name can't be kept on either side of it. One
written on any row before the name's row moves above the declaration, whether
you wrote it on the `type` row or on a row of its own. One on the name's row
stays in the header before the name:

```gren
-- you write:                      -- gren-format writes:
type {- c -} alias S =             {- c -}
    Int                            type alias S =
                                       Int
type
    {- c -}                        {- c -}
    alias S =                      type alias S =
    Int                                Int
```

elm-format keeps the comment where you wrote it, `type {- c -} alias S =`.

The clearest case is a `--` at a `,` or a `|`, because the two spellings that
collapse are the two you are most likely to have meant differently. These:


```gren
v =                              v =
    { rec -- c                       { rec | -- c
        | alpha = 1                      alpha = 1
    }                                }
```

reach the formatter as the same three facts — where `rec` ends, where the comment
is, where `alpha` starts — because the `|` between them has no recorded position.
Both therefore format to the first one:

```gren
v =
    { rec -- c
        | alpha = 1
    }
```

The same is true of `[ 1 -- c` ⏎ `, 2 ]` and `[ 1, -- c` ⏎ `2 ]`, and of a union
variant's `|`. A comment you wrote on a row of its own is a *third*, genuinely
distinguishable spelling and is left alone:

```gren
v =
    { rec
        -- c
        | alpha = 1
    }
```

**The rule is "a `--` keeps the row you wrote it on"**, and the row above a
line-leading separator belongs to the item (or the record update's base) above
it. That is uniform across all three separators, which is why it was chosen — but
it costs elm-format parity on the record update, because elm-format has its own
parser, does not have to collapse anything, and renders each of the two spellings
differently. See [divergence #22](elmFormatComparison.md#divergence-22).

## Comments near an effect module's `where` block

As described in [Comments in an effect module's header](formatterRules.md#comments-in-an-effect-modules-header),
a comment's placement near the `where { … }` block is determined by proximity
to the handler name. Changing the spacing can change where the comment ends up.
This stays as-is until the parser records positions for the missing tokens.

Concretely, the boundary is a two-column slack past the handler name's end
(`Formatter.Logical.Comments.commentInsideTrailingBracket`), so a couple of
extra spaces moves a comment out of the block:

```gren
-- inside the block (one space after the name):
effect module MyModule where { command = MyCmd {- note -} } exposing (..)

-- outside it (a few more spaces — same comment, same braces):
effect module MyModule where { command = MyCmd      {- note -} } exposing (..)
-- formats to:
effect module MyModule where { command = MyCmd } exposing (..) {- note -}
```

That is a guess, not a reading of the source, and the section below explains why
it has to be one. It is also the one limitation here that can change the
**layout** of the header rather than only a comment's home: a multi-line
`{- … -}` kept inside the block forces the block open across rows, and the same
comment moved out leaves it on one row.

```gren
effect module MyModule where { subscription = MySub {- forces the block to wrap
   second row -}
    } exposing {- tail -}
    ( a
    )
```

keeps the block open:

```gren
effect module MyModule where { subscription = MySub {- forces the block to wrap
                                                       second row -}
                             } exposing {- tail -}
    ( a
    )
```

while widening that one gap by three spaces collapses it:

```gren
effect module MyModule where { subscription = MySub } exposing
    {- forces the block to wrap
       second row -} {- tail -}
    ( a
    )
```

Both outputs are stable — each is its own fixed point and each preserves the
AST — so neither the idempotency checks nor the AST comparison objects. Only
`tests/fuzz-whitespace.py --mode stretch` can see it, and only if a corpus
fixture carries the shape; none does, because such a fixture would fail that
gate for as long as this limitation stands.

The sharpest form of this: a `--` comment written **inside** the braces, on its
own line, does not stay there. It is moved out of the block, below the module
line:

```gren
-- you wrote:
effect module MyModule where { command = MyCmd
                             -- line note
                             } exposing (..)

-- formats to (the comment is no longer inside the block; it is a top-level
-- comment now, at column 1):
effect module MyModule where { command = MyCmd } exposing (..)

-- line note
```

The comment survives — nothing is deleted — but it no longer sits beside the
handler name it was written next to.

Written past the two-column slack it does not go to column 1 either — it lands
on the header's own tail, after `exposing (..)`. A comment the author wrote
*after* `exposing (..)` then cannot stay on that row, because a `--` takes the
rest of its line, so it detaches to column 1: the place the reparse gives it,
and therefore the only placement that is a fixed point.

```gren
-- you wrote:
effect module MyModule where { command = MyCmd
    , subscription = MySub      -- three
    } exposing (..) {- four -}

-- formats to:
effect module MyModule where { command = MyCmd, subscription = MySub } exposing (..) -- three

{- four -}
```

Pinned by `HeaderComments/EffectHeaderLineCommentPushesTrailer`.

**This one cannot be fixed here.** The parser records a position for the handler
name and nothing else in the block: not the `where`, not the braces, not
`exposing`. So these two files:

```gren
effect module MyModule where { command = MyCmd
                             -- line note
                             } exposing (..)
```

```gren
effect module MyModule where { command = MyCmd } exposing (..)
                             -- line note
```

hand the formatter *byte-identical* information — same tree, same single
comment at row 2, column 30. There is no fact available to tell them apart, so
they format the same way. A `--` comment inside the braces has to be on a line
of its own (it would otherwise comment out the rest of the header), which is
exactly the case that needs the missing `}` position to place.

**A `{- … -}` is no better off** — it only looks better off because the two-column
slack usually guesses right for it. Line these two up so the comment starts at
the same column in both, and they are byte-identical too:

```gren
effect module MyModule where { subscription = MySub   {- c -} } exposing (..)
```

```gren
effect module MyModule where { subscription = MySub } {- c -} exposing (..)
```

`--pre-ast` on the two files produces the same bytes: the comment is at row 1,
column 49 in both, and the `}` that separates them is recorded nowhere. Whatever
the formatter answers, it answers for both — which is why the answer has to come
from the comment's column, and why widening a gap changes it.

**The fix used for the module's own `exposing ( … )` list does not transfer.**
There, `MakeLogical.moduleExposingClose` retires the same slack guess by
synthesizing a closing position and marking it elastic
(`lpnElasticBracketNode`), which says "anything that reaches this container is
inside it". That works because nothing follows the exposing list inside the
declaration. The `where { … }` block is not last — `exposing ( … )` follows it —
and comments genuinely belong out there. Giving the block an elastic close was
tried and measured: it fixes the whitespace flip above and then swallows
comments that were written past the block, including a `--` after
`exposing (..)`, which forces the block open across rows:

```gren
-- with an elastic where-block close, this fixture
effect module M where { command = CloseCmd, subscription = CloseSub } exposing (..) -- note

-- became
effect module M where { command = CloseCmd
                      , subscription = CloseSub -- note
                      } exposing (..)
```

Three fixtures moved that way (`EffectHeaderCloseRowComment`,
`EffectModuleOpenLineTrailer`, `EffectModuleFxWhereComment`), so the approach is
recorded here as disproven rather than left as an idea to retry.

Fixing this means the parser recording positions for the block's own tokens.

## A comment right after `exposing` doesn't sort with the first name

As described in [Exposed names sort automatically](formatterRules.md#exposed-names-sort-automatically),
a comment on its own line — attached to the *first* name in an
`exposing ( ... )` list — is a special case: the opening `(` has no position
in the AST, so the comment is placed as a header-level comment right after
`exposing`, not as a child of the first name. It renders in that same spot
every time, regardless of which name ends up first after sorting:

```gren
module ExposingListSort exposing
    ( -- describes zebra
      zebra
    , Kiwi
    , apple
    , Mango
    )
```

formats to:

```gren
module ExposingListSort exposing
    -- describes zebra
    ( Kiwi
    , Mango
    , apple
    , zebra
    )
```

A comment before any *other* name in the list (not the first) doesn't have
this issue — it travels with its name normally, as shown in
[Exposed names sort automatically](formatterRules.md#exposed-names-sort-automatically).

## A module `exposing` list's closing paren isn't recorded

The closing `)` of a module header's `exposing ( ... )` list has no position in
the AST — the parser records where each exposed *name* is, but nothing about the
brackets around them. Mostly that costs nothing: when you wrote the list across
rows, the `)` is on its own row below the last name, so a comment after that name
is recognised by its row and stays inside the list (see
[When the formatter can't tell what you meant](formatterRules.md#when-the-formatter-cant-tell-what-you-meant)).
One shape does pay for it.

An import's list doesn't pay at all — the parser records where an import ends,
`)` included, so a comment there stays on whichever side of the `)` you wrote it,
however much space you left, and however many comments you stack up.

### A comment past a flat list

When you wrote the list flat, everything is on one row and the row tells you
nothing. A comment written *inside* the brackets and one written *past* them look
alike. The formatter stops trying to tell them apart: it reads any comment after
the list's last name as belonging to the list, opens the list up, and pins the
comment above the `)`. Every way of writing it gives the same result —

```gren
module FlatClose exposing (apple, zebra) {- both names -}
module FlatClose exposing (apple, zebra)    {- both names -}
module FlatClose exposing (apple, zebra {- both names -})
module FlatClose exposing (zebra, apple) {- both names -}
```

— all four become:

```gren
module FlatClose exposing
    ( apple
    , zebra
    {- both names -}
    )
```

That is the point. Neither the spacing you left before the comment nor the order
you typed the names in changes the output, and the same module written any of
those four ways formats to the same bytes.

What you give up is hanging a comment off the **last** name of a flat list: it
is read as the list's, not that name's. A comment on any earlier name is
unaffected, because a name follows it and there is nothing to confuse it with:

```gren
module Mid exposing (apple {- just apple -}, zebra)
```

stays exactly as written. And if you do want a comment tied to the last name,
write the list vertically — there the `)` has a row of its own, which is enough
to tell the two apart:

```gren
module Vert exposing
    ( apple
    , zebra -- just zebra
    )
```

keeps the comment on `zebra`, through the sort and across reformats.

A chain of comments is treated as one unit and pinned together, in order, each on
its own line — including a link that spans rows:

```gren
module Chain exposing (zebra, apple) {- first link, and it
spans rows -} {- second link -}
```

becomes:

```gren
module Chain exposing
    ( apple
    , zebra
    {- first link, and it
       spans rows -}
    {- second link -}
    )
```

This also settles what used to be an ambiguity about a vertical list: these two
files are handed to the formatter as byte-identical ASTs *and* byte-identical
comment positions, so nothing could ever distinguish them —

```gren
module Amb exposing            module Amb exposing
    ( apple                        ( apple
    , zebra                        , zebra
      {- first -}                  ) {- first -}
      {- second -}                   {- second -}
    )
```

— and both now format to the same thing, with both comments inside the list
above the `)`. Previously the second comment was pushed out of the brackets and
became a free-floating comment above the declarations.

## A comment after the last binding in a `let`

The `in` keyword of a `let ... in` is another token with no recorded position:
the parsed `let` remembers only its bindings and its result expression, never
where `in` sat. So a comment written in the gap between the last binding and the
result can't be pinned to one side of `in` — it might be trailing the binding
above, or introducing the result below, and there's no fact to tell those apart.
gren-format always treats it as introducing the result, placing it on its own
line just below `in`:

```gren
-- you wrote:
x =
    let
        y =
            1 -- a note
    in
    y

-- formats to (the note moves below in):
x =
    let
        y =
            1
    in
    -- a note
    y
```

This is the one placement that stays put every time you reformat *and* never
misplaces a comment you really did write below `in`. elm-format, whose parser
records the `in` position, keeps a trailing-binding comment up with the
bindings instead — a divergence covered in
[Comparison with elm-format](elmFormatComparison.md#divergence-20) (point 20), with the
full reasoning for why gren-format can't follow suit.

## Block comment body indentation

A multi-line block comment's body is re-indented from its **own** structure: the
least-indented content line is placed a few columns in from the `{-`, and every
other line keeps its position *relative* to that. So hand-aligned content — an
ASCII diagram, an aligned table — keeps its shape; the block as a whole is
anchored under the comment's opener rather than pinned to the exact columns you
typed. Because the indentation is derived only from the body (never from the
whitespace around the comment), two inputs that differ only in that surrounding
whitespace format to the same output — it is whitespace-canonical.

This applies to every multi-line block comment, including the form where `{-`
sits alone on its first line. (An earlier version of gren-format treated that
opener-alone form as a "keep my exact columns" signal and left the body
verbatim; that was a divergence from elm-format, which re-indents the body the
same way described here, so it was removed.)

## A comment run just inside a bracket doesn't keep its rows

[Rule C7](commentHandling.md#c7--comments-written-together-stay-together-comments-written-apart-stay-apart) says a
comment run keeps the rows you wrote it on, and
[divergence #30](elmFormatComparison.md#divergence-30) says that holds in every
context. It does not hold for a run written just inside an opening bracket, or
between a pipeline step's operator and its operand. There, the whole run is laid
out all-or-nothing: if every member can share a line they are all joined onto
one, and if any member cannot, every member gets a row of its own.

```gren
-- you write:            -- gren-format writes:
a =                      a =
    [ {- p -}                [ {- p -} {- q -} 1        <- joined a run you split
      {- q -} 1            ]
    ]

b =                      b =
    [ {- p -} -- q           [ {- p -}                  <- split a run you joined
      1                        -- q
    ]                          1
                             ]
```

Both happen to be what elm-format does, so this costs nothing in parity — but it
is the rule stated elsewhere, applied where the information to apply it is
missing.

**It is not fixable in the renderer.** Which rows the author used inside a
container's comment run is not recorded anywhere the renderer can read it: rule
C1 makes one gap one attachment, so `Comments.gren` gives the whole run one role,
derived from each member's *shape* (`bracketKindRole`). `[ {- p -} -- q` and
`[ {- p -}` ⏎ `-- q` therefore arrive with identical roles (`RidesInline`,
`TrailsPrevious`), and so do the two spellings of a two-block run. Any rule
written in `glueLeadingCommentRun` lays both authorings out the same way and so
breaks C7 on one of them; grouping by `commentGluesToPrevious` the way a binop
chain's `leadingRunRowBoxes` does was measured to move the violation from one
spelling to the other rather than remove it.

Honouring C7 here means recording the authored row upstream, in the role — a
change to the comment model rather than to a layout function, and one that
contradicts the "one gap, one role" reading `Comments.gren` currently documents
at that slot.

## Very deep lambda or unary-minus nesting can overflow the stack


This one isn't a comment-placement or compiler quirk — it's an implementation
limit in the formatter itself, found by `tests/pathological-nesting.py`
(geometric-growth-and-bisect stress testing of nesting depth).

A chain of nested lambdas,

```gren
x =
    \a -> \a -> \a -> \a -> \a -> {- … repeated hundreds of times … -} a
```

or a chain of nested unary minus,

```gren
x =
    -(-(-(-(-(-( {- … repeated hundreds of times … -} 1))))))
```

crashes with `RangeError: Maximum call stack size exceeded` once nesting
passes roughly **400 levels** for lambdas or **300 levels** for unary minus.
Rendering a nested expression recurses once per level through a fairly long
call chain (dispatch → flow assembly → per-item classification → the next
level's dispatch, and so on) — around 10-15 JS stack frames per level of
*Gren* source nesting — and Node's default stack budget runs out before the
parser's own does (the parser tolerates roughly 500-700+ levels of the same
shapes, since its recursive-descent call chain is shorter per level). The
crash is immediate and clean — no hang, no corrupted output — but a file
past the threshold cannot be formatted at all.

This is not expected to matter in practice: no real Gren source this project
has ever swept (published packages, its own sources, hundreds of thousands of
generated random modules) has come anywhere near this depth, and code with
hundreds of directly nested lambdas or unary minuses is not something anyone
writes by hand. Fixing it properly means rewriting the renderer's recursive
core to use an explicit stack instead of the JS call stack (a trampoline) —
a large, invasive change judged disproportionate to a depth nothing has ever
hit. Nor would we consider changing Node's own stack size; that is out of
scope as a solution.

Deep nesting used to be worse than a depth ceiling: several constructs
rendered the same subtree twice per level, which is exponential rather than
linear, and made record literals unusable well before 25 levels. Those were
formatter bugs and are fixed — every construct now renders each subtree once,
and what is left for all of them is the ordinary stack-depth ceiling described
above. The pattern is worth knowing if you touch the renderer: a suspected
blowup here has always turned out to be a second render, not a slow function.
[`docs/testing.md`](testing.md#scaling-bench-scalingpy-and-how-to-check-a-suspected-blowup)
has the measurements and the shapes that were timed.
