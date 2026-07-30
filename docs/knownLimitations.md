# Known limitations

Places where `gren format` falls short of ideal — a compiler/parser bug it
inherits, a shape the language no longer allows but the parser still accepts,
or a comment-placement choice forced by a token with no recorded source
position. See the main [README](../README.md) for the overview, and
[Gren Formatter Rules](formatterRules.md) for what the formatter does when
nothing is going wrong.

## Table of contents

- [A compiler bug with field access on a record-update base](#a-compiler-bug-with-field-access-on-a-record-update-base)
- [An unparenthesized constructor pattern can't be aliased with `as`](#an-unparenthesized-constructor-pattern-cant-be-aliased-with-as)
- [Wide `when` branch patterns](#wide-when-branch-patterns)
- [Comment placement near invisible tokens](#comment-placement-near-invisible-tokens)
- [A line break inside a declaration's head](#a-line-break-inside-a-declarations-head)
- [Comments near an effect module's `where` block](#comments-near-an-effect-modules-where-block)
- [A comment right after `exposing` doesn't sort with the first name](#a-comment-right-after-exposing-doesnt-sort-with-the-first-name)
- [A module `exposing` list's closing paren isn't recorded](#a-module-exposing-lists-closing-paren-isnt-recorded)
  - [A comment past a flat list](#a-comment-past-a-flat-list)
- [A comment after the last binding in a `let`](#a-comment-after-the-last-binding-in-a-let)
- [Block comment body indentation](#block-comment-body-indentation)
- [Two fixtures parse a custom-type shape the language no longer allows](#two-fixtures-parse-a-custom-type-shape-the-language-no-longer-allows)
- [Very deep lambda or unary-minus nesting can overflow the stack](#very-deep-lambda-or-unary-minus-nesting-can-overflow-the-stack)

---

## A compiler bug with field access on a record-update base

Field access directly after a closing paren, a plain record literal, or a
qualified name — `(getUser model).name`, `{ x = 1 }.x`,
`Config.default.timeout` — parses and formats correctly. But a
record-**update** base still hits a narrower case of the same bug:

```gren
{ model | x = 0 }.x
```

is formatted with a space before the dot (`{ model | x = 0 } .x`). That
space changes the meaning: it applies the accessor *function* `.x` to the
record update instead of reading its field, and the formatted file no
longer compiles. The parser reads both spellings as the same expression
(`Call(Update, [Accessor])`) — the same root cause as
[compiler-common#27](https://github.com/gren-lang/compiler-common/issues/27),
which fixed every other kind of base but not this one.

## An unparenthesized constructor pattern can't be aliased with `as`

```gren
f x =
    when x is
        Just y as whole ->
            whole
```

fails to parse — `Expected keyword '->'` at `as` — even though the real Gren
compiler accepts it. The parser accepts `as` after a bare variable or
wildcard (`x as step`, `_ as step`) and after a *parenthesized* constructor
application (`(Just y) as whole`), but not after an unparenthesized one. Since
parsing happens before formatting, gren-format refuses the whole file, not
just this pattern — real fallout: `core/src/String/Parser/Advanced.gren` has
this exact shape and can't be formatted until it's fixed. Tracked at
[compiler-common#31](https://github.com/gren-lang/compiler-common/issues/31).
Workaround: add the parens yourself, `(Just y) as whole`.

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

## A line break inside a declaration's head

A line break *inside* a declaration's keyword (e.g. `import` on one line,
the module name on the next) can cause a blank line to appear between a
comment and that declaration. The root cause is a parser bug that records the
wrong line number for keyword-led declarations:
[compiler-common#25](https://github.com/gren-lang/compiler-common/issues/25).

```gren
-- a comment
import
    String
```

formats to:

```gren
-- a comment

import String
```

with a spurious blank line pushed between the comment and the import it was
written directly above — even though writing the same import on one line
(`-- a comment` / `import String`) formats with no blank line at all. The
parser records the position of `String` (the name) rather than `import` (the
keyword), so the formatter sees a bigger gap between the comment and the
declaration than the author actually left.

## Comments near an effect module's `where` block

As described in [Comments in an effect module's header](formatterRules.md#comments-in-an-effect-modules-header),
a comment's placement near the `where { … }` block is determined by proximity
to the handler name. Changing the spacing can change where the comment ends up.
This stays as-is until the parser records positions for the missing tokens.

The sharpest form of this: a `--` comment written **inside** the braces, on its
own line, does not stay there. It is moved out of the block, below the module
line:

```gren
-- you wrote:
effect module MyModule where { command = MyCmd
                             -- line note
                             } exposing (..)

-- formats to (the comment is no longer inside the block):
effect module MyModule where { command = MyCmd } exposing (..)
    -- line note
```

The comment survives — nothing is deleted — but it no longer sits beside the
handler name it was written next to.

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
exactly the case that needs the missing `}` position to place. Fixing it means
the parser recording positions for those tokens; until then a `{- … -}` comment
is the one that stays put.

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
[Comparison with elm-format](elmFormatComparison.md#divergence-21) (point 21), with the
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

## Two fixtures parse a custom-type shape the language no longer allows

Since [the 24w release](https://gren-lang.org/news/161224_gren_24w), a custom
type's variant is limited to 0 or 1 argument — `type Person = Person String
Int` is no longer valid Gren; a multi-field variant must carry a record
instead (`Person { name : String, age : Int }`). The parser this project is
built on does not enforce that restriction for a chain of bare
constructor-name arguments, so `type Person = Person String Int` still parses
without error today. Tracked at
[compiler-common#32](https://github.com/gren-lang/compiler-common/issues/32).

Two of this package's own test fixtures rely on that gap —
`tests/testfiles/Formatter/TypeUnion.formatted.gren`:

```gren
type Shape
    = Circle Int
    | Rectangle Int Int
```

and `tests/testfiles/Formatter/UnionLayoutByAuthor.formatted.gren`:

```gren
type WithPayloads
    = Wrap Int | Pair Int Int
```

`Rectangle Int Int` and `Pair Int Int` are both 2-argument variants — invalid
per the 24w rule, yet accepted and formatted today because of the bug above.
These fixtures will be cleaned up (reduced to 0/1-argument variants) once
compiler-common#32 is fixed and released; until then they're a known,
intentional exception to the language's current variant-arity rule, not a
formatter bug of their own. (`gen-random.py`, the property-based random-input
generator, no longer emits multi-argument variants for this same reason —
see `tests/GENERATOR.md`.)

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
hit. Now would we consider changing Node's own stack size; that's complete
out of scope as a solution.

Nested **record literals** used to hit a much sharper version of this
problem — not a stack overflow but an exponential-time hang, becoming
unusable well before 25 levels of nesting. That one *was* a formatter bug
(a value was rendered twice per level instead of once) and has been fixed;
record literals now nest as deep as the parser allows, same as most other
constructs.

