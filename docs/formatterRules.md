# Gren Formatter Rules

A guide to how `gren format` lays out your code — what it changes, what it
leaves alone, and why. For a shorter tour of the core ideas, see the
["Gren Formatter Rules" section](../README.md) of the
main README; for how the formatter arrives at these decisions internally, see
[How the formatter works](howItWorks.md).

## Table of contents

- [Background](#background)
- [Module declaration](#module-declaration)
- [Exposed names sort automatically](#exposed-names-sort-automatically)
- [Import statements](#import-statements)
- [An import's exposing list sorts automatically](#an-imports-exposing-list-sorts-automatically)
- [Import statements sort within unbroken runs](#import-statements-sort-within-unbroken-runs)
- [Type signatures](#type-signatures)
- [Function application](#function-application)
  - [A record argument that renders across rows drops to its own line](#a-record-argument-that-renders-across-rows-drops-to-its-own-line)
- [Parentheses](#parentheses)
- [Function body](#function-body)
- [Blank lines between declarations](#blank-lines-between-declarations)
- [Type aliases](#type-aliases)
- [Custom types](#custom-types)
- [Ports](#ports)
  - [The `port` in `port module` follows the ports](#the-port-in-port-module-follows-the-ports)
- [Infix operator declarations](#infix-operator-declarations)
- [Records](#records)
  - [Record values](#record-values)
  - [Record updates](#record-updates)
  - [A lambda whose body is a forced-vertical record, update, or array drops it to its own line](#a-lambda-whose-body-is-a-forced-vertical-record-update-or-array-drops-it-to-its-own-line)
  - [Record field values](#record-field-values)
  - [Record types and extensible records](#record-types-and-extensible-records)
- [Array literals](#array-literals)
- [String literals](#string-literals)
  - [Character literals](#character-literals)
  - [Multi-line (triple-quoted) strings](#multi-line-triple-quoted-strings)
- [If expressions](#if-expressions)
  - [A condition that cannot fit on one line stacks anyway](#a-condition-that-cannot-fit-on-one-line-stacks-anyway)
- [When expressions](#when-expressions)
- [Let expressions](#let-expressions)
- [Patterns as arguments](#patterns-as-arguments)
- [Lambdas](#lambdas)
- [Pipelines](#pipelines)
- [Binary operators](#binary-operators)
- [Comments](#comments)
  - [Where you put a comment is meaningful](#where-you-put-a-comment-is-meaningful)
  - [Single-line comments (`--`)](#single-line-comments---)
  - [Block comments (`{- ... -}`)](#block-comments-----)
    - [Comments in an effect module's header](#comments-in-an-effect-modules-header)
  - [Doc comments (`{-| ... -}`)](#doc-comments-----)
  - [Blank lines around comments](#blank-lines-around-comments)
  - [A comment on its own line below a declaration](#a-comment-on-its-own-line-below-a-declaration)
  - [A trailing comment on a `when` branch body](#a-trailing-comment-on-a-when-branch-body)
  - [When the formatter can't tell what you meant](#when-the-formatter-cant-tell-what-you-meant)

---

## Background

The Gren formatter has one central idea: **your line breaks are your layout
decisions.** Write something on one line and it stays on one line. Put a line
break between items and the formatter keeps them on separate lines, normalizing
to one item per line.

There is **no page width.** The formatter never wraps a long line. A function
call with five arguments all on one row stays on one line no matter how wide it
is. A type signature written as one long line stays that way. If you want
something to break, put a line break in it.

The four core rules:

1. **One row → one line.** If you wrote a construct on a single row, the
   formatter keeps it on one line. Width is irrelevant.

2. **Multiple rows → one item per line.** If you put a line break between any
   two items of a construct, the formatter keeps every item on its own line.
   There is no "some items here, some there" shape — a line break anywhere
   means every item gets its own line.

3. **The formatter never changes what your code means.** It only moves
   whitespace. It never rewrites an expression, reorders anything, or edits
   text inside a comment or string.

4. **Formatting is stable.** Running the formatter on already-formatted code
   produces the same code back. Format once or ten times — same result. A
   torture test inserts a comment into every inter-token gap of every fixture
   file, formats twice, and requires byte-identical output. Nothing shifts.
   Its **19** residual findings out of some 56,000 gaps are not shifts at all:
   every one is the same upstream parser bug
   ([compiler-common#35](https://github.com/gren-lang/compiler-common/issues/35)),
   which reads `10 -` ⏎ `····3` as the call `10 (-3)`, so a `--` written after
   that `-` comes back as `---`. The formatter's own AST check catches that and
   refuses the file (see
   [knownLimitations.md](knownLimitations.md#a-binary---whose-right-operand-starts-at-the-operators-own-column)).
   Nothing left in it is the formatter's to fix; the 19 are registered by name
   and forgiven, and they stop being reported when that parser fix ships.

   The same test run with a **run of two** comments in every gap — a comment
   whose neighbour is another comment, which is where the rules are hardest —
   reports that same set and nothing else.

A few things are **always fixed**, regardless of how you wrote them:

- A binding's value always starts on its own line (see
  [Function body](#function-body)).
- A `when` branch body always starts on its own line.
- An `if` branch body always starts on its own line.
- A blank line always separates `else`/`else if` from the branch above it.
- Two blank lines always precede every top-level declaration.
- One blank line always separates `let` bindings.
- A type alias always puts the aliased type on its own line.
- A custom type always puts the variant list on its own line(s).
- **Indentation is 4 spaces.** Always spaces, never tabs.
- On a `module` line, `exposing` always stays glued to the module name —
  never on its own line — though the exposed list itself can still spread
  across multiple rows below it (see
  [Module declaration](#module-declaration)).

Everything else follows your layout choices.

---

## Module declaration

`exposing` always stays on the same line as the module name — it never drops
to its own line the way an import's `exposing` can. Written on one line, the
whole thing stays on one line:

```gren
module MyApp exposing (Model, Msg, init, update, view, subscriptions)
```

Written across rows, the list indents +4 under the module line — one item
per line — but `exposing` itself still stays glued to `module MyApp`:

```gren
module MyApp exposing
    ( Model
    , Msg
    , init
    , update
    , view
    , subscriptions
    )
```

A comment written between the module name and `exposing` always canonicalizes
to *after* `exposing` (its exact original position isn't preserved) — and
since a comment forces a break right after itself, the exposing list drops to
the next line, indented +4, while `module MyApp exposing` stays intact as one
line:

```gren
module MyApp exposing -- a note
    ( Model, Msg )
```

The wildcard `exposing (..)` is always written as `(..)` on the module line.

A comment inside the exposing list keeps it vertical only when the comment
can't share a line — a `--` comment, a `{- … -}` spread over several lines, or
one you put on a row of its own. A short `{- … -}` beside a name rides the line
you wrote:

```gren
module MyApp exposing (Model {- the state -}, Msg)
```

A custom type exposed with its constructors gets a space before `(..)`:

```gren
module MyApp exposing (Outcome (..), Model)
```

This applies anywhere an exposing list can name a custom type's
constructors, including an import's exposing list (see
[Import statements](#import-statements)).

## Exposed names sort automatically

Regardless of the order you wrote them in, an `exposing ( ... )` list sorts
into three groups — operators, then types, then plain values — and
alphabetically within each group. This is always the order, independent of the
module's doc comment. (elm-format instead reorders a module's exposing list to
follow the `@docs` directives in its doc comment when they're present, falling
back to this alphabetical order only when they're absent; gren-format
deliberately doesn't couple the two — see
[Comparison with elm-format](elmFormatComparison.md#divergence-3) (point 3).)

```gren
module ExposingListSort exposing (zebra, Kiwi, apple, Mango)
```

becomes:

```gren
module ExposingListSort exposing (Kiwi, Mango, apple, zebra)
```

A comment attached to a name — on its own line above it, or trailing on the
name's own line — travels with it when it moves:

```gren
module ExposingListSort exposing
    ( zebra -- the last one
    , Kiwi
    , apple
    , Mango
    )
```

becomes:

```gren
module ExposingListSort exposing
    ( Kiwi
    , Mango
    , apple
    , zebra -- the last one
    )
```

A comment written *past* the closing `)` of a list you wrote across rows is a
different thing: it isn't attached to any name, so it has nothing to travel
with. It's pinned to the end of the list, above the `)`, and stays there
whatever the sort does to the names:

```gren
module ClosePinned exposing
    ( zebra
    , apple
    ) -- about the list, not about a name
```

becomes:

```gren
module ClosePinned exposing
    ( apple
    , zebra
    -- about the list, not about a name
    )
```

Writing the same two names the other way round (`apple` first) gives exactly the
same result. That's the point: the output of a sort shouldn't depend on the
order you happened to type things in. A comment on its own line above the `)`
is pinned the same way.

A flat list gets the same treatment: a comment at the end of a flat header is
pinned above the `)` too, and the list opens up to make room for it. That costs
you the ability to hang a comment off the *last* name of a flat list — on one
row the formatter can't see the `)`, so it can't tell "about that name" from
"about the list" — and the trade-off is described under
[A comment past a flat list](knownLimitations.md#a-comment-past-a-flat-list).

This applies the same way to an import's own exposing list — see
[An import's exposing list sorts automatically](#an-imports-exposing-list-sorts-automatically).

---

## Import statements

A plain import stays on one line:

```gren
import Array
```

An alias uses `as`. An exposing list follows your layout — flat if you wrote
it flat, vertical if you wrote it across rows. Just like a module's `exposing`
(see [Module declaration](#module-declaration)), an import's `exposing` stays
glued to the header as its last word; when the list goes vertical it starts on
the next line, indented +4:

```gren
-- flat:
import String exposing (fromInt, toInt)

import Array.Extra as AE exposing (filterMap, unique)

-- vertical:
import Dict exposing
    ( Dict
    , empty
    , fromArray
    , get
    )
```

(elm-format instead drops the import's `exposing` onto its own line; keeping it
on the header line is a deliberate divergence — see
[Comparison with elm-format](elmFormatComparison.md#divergence-4) (point 4).)

## An import's exposing list sorts automatically

Each import's own exposing list sorts, the same way a module's does —
operators, then types, then values, alphabetically within each group (see
[Exposed names sort automatically](#exposed-names-sort-automatically)):

```gren
import Mango exposing (zebra, Kiwi, apple, Mango)
```

becomes:

```gren
import Mango exposing (Kiwi, Mango, apple, zebra)
```

(This is independent of whether the import itself is part of a sortable
run of imports — see below.)

## Import statements sort within unbroken runs

`import` statements sort alphabetically by module name, but only within a
*run* — a stretch of imports with no blank line anywhere in it. A blank line is
the only boundary; it never moves, and it splits the imports around it into
independently sorted groups.

Comments don't split a run. A comment travels with the import it belongs to:
the one on its own row for a trailing comment, the one directly below it for a
line-leading comment. Leave a blank line under a comment and it belongs to no
import, so it stays where you put it and everything below it still sorts:

```gren
-- Third-party

import Zebra
-- the fast one
import Apple
```

becomes:

```gren
-- Third-party

-- the fast one
import Apple
import Zebra
```

The full rules — for both import-statement sorting and exposing-list sorting,
including every comment case (line, single-line block, and multiline block) —
now live in **[sorting.md](sorting.md)**.

---

## Type signatures

A type signature follows your layout.

Written on one line, it stays on one line — however long it is:

```gren
add : Int -> Int -> Int

processItems : Array String -> Dict String Int -> (String -> Bool) -> Array String -> Result String (Array String)
```

Written across rows, it stays across rows. The canonical multi-line shape puts
each `->` segment on its own line, with `->` leading each continuation:

```gren
processItems :
    Array String
    -> Dict String Int
    -> (String -> Bool)
    -> Result String (Array String)
```

If you wrote it across rows and it would fit on one line, it stays multi-line:

```gren
keptMultiLine :
    Int
    -> Int
    -> Int
```

The multi-line shape triggers when you broke the type **anywhere** — between
`->` segments, inside a record type, or inside parens. A line break right after
the `:` with the rest still on one line is not enough; there has to be a break
within the type itself.

A break inside parens keeps that break too, as long as the parenthesized type is
an application:

```gren
-- you write, and gren-format keeps:
parenedApp :
    (Array
        Int
    )
    -> Int
```

A parenthesized **function** type is the exception — the signature goes
multi-line, but the arrow-joined type inside the parens is flattened back onto
one line, because an arrow-joined type has to break *before* each `->` and that
per-segment shape is not yet rendered inside parens:

```gren
-- you write:            -- gren-format writes:
parened : (Int           parened : (Int -> Int) -> Int
    -> Int) -> Int
```

The signature stays flat too, and that follows from the same rule: it goes
multi-line only for a break that **survives** rendering. One broken around a
break that vanished would read as a one-row type on reparse and flip back.

See [divergence #27](elmFormatComparison.md#divergence-27).

A `--` at a `->` keeps the row you wrote it on, and the rest of the signature
still uses the per-segment shape. Trailing the type to the arrow's left, or on a
row of its own above the arrow — both survive, because a `--` ends its row and
the `->` leads its own:

```gren
bestDiscount :
    Array { code : String, basisPoints : Int } -- comment about the result
    -> Maybe { code : String, basisPoints : Int }


bestDiscount :
    Array { code : String, basisPoints : Int }
    -- comment about the result
    -> Maybe { code : String, basisPoints : Int }
```

A multi-line `{- … -}` follows the `--`, on whichever row it opened:

```gren
convert :
    Int {- explanation that
           spans multiple lines -}
    -> Int
    -> Int
```

A `{- … -}` that fits on **one** line is the one that does not keep its row: it
doesn't end its line, so the side of the `->` you wrote it on isn't visible, and
it follows the general rule — leading the type after the arrow. This is
[C2](commentHandling.md#c2--when-the-parser-doesnt-record-the-punctuation-the-comment-lands-after-it)
and its exception; see [When the formatter can't tell what you
meant](#when-the-formatter-cant-tell-what-you-meant).

```gren
-- both of these:
convert : Int {- the input -} -> Int
convert : Int -> {- the input -} Int

-- format to:
convert : Int -> {- the input -} Int
```

Only a signature the author kept on **one row** falls back to filling the flow
and wrapping at word boundaries when it carries a comment — there's no
`->`-segment boundary to anchor a break to.

---

## Function application

A function call follows your layout.

Written on one line, all arguments stay on that line:

```gren
result =
    foo a b c

result =
    someFunction firstLongArg secondLongArg thirdLongArg fourthLongArg fifthLongArg
```

Written across rows, arguments stay across rows, each indented 4 spaces from
the function name:

```gren
result =
    someFunction
        firstLongArg
        secondLongArg
        thirdLongArg
```

A redundant pair of parens around an argument is stripped when the argument
doesn't need them to parse unambiguously — a record, array, record update,
variable, literal, or field-access chain:

```gren
-- you wrote:
view (model) ({ id = 1 }) =
    ...

-- formats to:
view model { id = 1 } =
    ...
```

Parens stay when they're load-bearing — an applied function, a lambda, an
operator chain, a negation, an `if`/`when`/`let`, or a bare operator value
like `(+)`:

```gren
result =
    Array.foldl (+) 0 (compute x) (\y -> y * 2)
```

### A record argument that renders across rows drops to its own line

A record argument with 2+ fields (or one field plus a comment) follows your
row placement, same as any other record literal — see
[Record values](#record-values). If you glued it to the function name on the
same row, it stays glued and flat, no matter how long the line ends up being —
there's no length check:

```gren
type Bar
    = Bar { name : String, value : Int }


mkBar x =
    Bar { name = x, value = 1 }
```

But once the record itself renders across rows, the formatter always moves it
to its own line, indented +4 from the function name — it never leaves the
record's first row glued to the function name while only its later fields
wrap:

```gren
-- you wrote:
mkBar x =
    Bar { name = x
        , value = 1
        }

-- formats to:
mkBar x =
    Bar
        { name = x
        , value = 1
        }
```

Any argument that follows the record also gets its own line, for the same
reason — nothing stays glued to the record's closing `}`:

```gren
-- you wrote:
foo x =
    someFunc { name = x
             , value = 1
             } extraArg

-- formats to:
foo x =
    someFunc
        { name = x
        , value = 1
        }
        extraArg
```

The same thing happens inside a pipeline step:

```gren
build x =
    x
        |> AST.TType
            { name = x
            , args = []
            }
```

This applies to a record, or anything else whose own content forces it across
rows — including a parenthesized lambda whose body wraps (an `if`/`when`/`let`,
or a record/array the lambda returns across rows), or a parenthesized pipeline.
The whole `(...)` drops to its own line, and any argument after it drops too:

```gren
foo xs =
    Array.map
        (\n ->
            if n > 0 then
                n

            else
                -n
        )
        xs
```

Here `Array.map` sits alone on the first line, the `(\n -> …)` argument is on
its own line indented +4, and `xs` follows on its own line. Inside the lambda,
the `if` body drops to its own line under `->` (see [Lambdas](#lambdas)).

---

## Parentheses

When a parenthesized expression renders across rows, the closing `)` always
gets its own line, indented to line up with the opening `(` — it never trails
the last piece of content:

```gren
topLevelParser =
    (Parser.oneOf
        [ a
        , b
        ]
    )
```

This applies wherever parens show up, not just at the top of a function body —
nested inside a call, for instance:

```gren
combine x y =
    make
        (build
            { a = x
            , b = y
            }
        )
```

...or wrapping an operator chain:

```gren
total =
    (1
        + 2
    )
```

The trigger is the parenthesized content rendering across rows — either
because you wrote it that way, or because something inside forces it (a
comment, an `if`/`when`/`let`). Either way the shape is the same: content
starts right after `(`, and `)` closes on its own line underneath.

---

## Function body

A binding's value **always** goes on the next line, indented 4 spaces from
`name args =`. There is no inline form, however short the body:

```gren
version =
    "1.0.0"


answer =
    42


double : Int -> Int
double n =
    n * 2


makePoint x y =
    { x = x, y = y }
```

This uniformity means adding an argument or wrapping a value in a call never
reshuffles the line where the body sits.

---

## Blank lines between declarations

Two blank lines always appear before every top-level declaration — functions,
type aliases, custom types, and ports alike. This is unconditional: whether you
wrote zero blank lines or five, you get exactly two.

```gren
double : Int -> Int
double n =
    n * 2


square : Int -> Int
square n =
    n * n
```

The two blank lines go before the *beginning* of the whole declaration unit.
The unit begins with any comment directly above it (with no blank line in
between); otherwise with its type signature; otherwise with the declaration
itself. So a leading comment, signature, and definition stay together, with the
two blank lines above the topmost line:

```gren
{-| Doubles its argument. -}
double : Int -> Int
double n =
    n * 2
```

A comment separated by a blank line from *both* the declaration above it and
whatever follows it is treated as genuinely floating — free-standing
commentary, not attached to anything. Once any gap at all separates it from
its neighbors, it gets the same two blank lines above it as a declaration
unit, regardless of how many blank lines the author actually wrote:

```gren
double : Int -> Int
double n =
    n * 2


-- A floating note, kept at arm's length


square : Int -> Int
square n =
    n * n
```

A *run* of comments — several on consecutive lines, of any kind — floats or
attaches as one. The run is floating when the gaps sit above the first line and
below the last, so the two blank lines go above the whole run:

```gren
double : Int -> Int
double n =
    n * 2


{-| A floating note -}
-- and a second line of it


square : Int -> Int
square n =
    n * n
```

A comment glued directly beneath the code above it, with no gap at all, stays
glued — the "floating" treatment only kicks in once the author has already
separated it from what's above:

```gren
double : Int -> Int
double n =
    n * 2
-- A note glued directly beneath, no gap


square : Int -> Int
square n =
    n * n
```

A comment that's detached above but glued to whatever *follows* it — for
example, a one-line explanation sitting directly above an import — is not
floating; it keeps the single gap-driven blank line, since it isn't
free-standing, it's introducing what comes right after it:

```gren
import Dict

-- Used for array utilities
import Array
```

---

## Type aliases

A `type alias` always puts the aliased type on its own line, indented 4
spaces, even when the whole thing would fit on one line:

```gren
type alias Id =
    Int
```

When the aliased type is a record, it follows your layout exactly like a record
value (see [Records](#records)):

```gren
type alias Point =
    { x : Int, y : Int }


type alias Model =
    { name : String
    , count : Int
    , active : Bool
    }
```

---

## Custom types

A custom type always puts the variant list on the line(s) after the name.
The variants themselves follow your layout.

Written on one line, the variants stay on one line:

```gren
type Color
    = Red | Green | Blue
```

Written across rows, each variant goes on its own line:

```gren
type Direction
    = North
    | South
    | East
    | West
```

Type variables go on the header line after the name:

```gren
type Maybe a
    = Nothing
    | Just a
```

A variant's payload sits on the same line as the variant name:

```gren
type Shape
    = Circle Int
    | Rectangle Int Int
```

A short `{- … -}` beside a variant doesn't change any of this — the one-line
form stays on one line:

```gren
type Color
    = Red {- warm -} | Green | Blue
```

Only a comment that can't share the line (a `--` comment, a `{- … -}` spread
over several lines, or one on a row of its own) splits the variants apart.

---

## Ports

A port stays on one line when you wrote it that way:

```gren
port outgoing : String -> Cmd msg

port incoming : (String -> msg) -> Sub msg
```

When the type is written across rows, it follows the same layout as a
multi-line type signature — each `->` segment on its own line:

```gren
port sendThings :
    VeryLongArgumentType
    -> AnotherArgumentType
    -> Cmd msg
```

### The `port` in `port module` follows the ports

The module keyword is written from what the module *contains*, not from what
you typed on the header line: a module that declares at least one port is
written `port module`, and one that declares none is written `module`. When the
two already agree — the usual case — nothing changes. When they disagree, the
header is rewritten to match the body.

Write `port module` and declare no ports, and the `port` is dropped:

```gren
port module Foo exposing (a, b)


a =
    1


b =
    2
```

becomes

```gren
module Foo exposing (a, b)


a =
    1


b =
    2
```

Write plain `module` and declare a port, and `port` is added:

```gren
module Foo exposing (a)


port a : String -> Cmd msg
```

becomes

```gren
port module Foo exposing (a)


port a : String -> Cmd msg
```

Neither rewrite changes what your code does — a module with no ports doesn't
need the keyword, and one with ports isn't valid without it.

This is deliberate. The parser doesn't record which keyword you wrote; it works
the keyword out from the body, and the formatter prints what the parsed module
says. Deriving it is also the direction the language is heading: the `port`
keyword on the module line may become optional, or go away entirely, and a
formatter that derived it all along keeps working when it does. The trade-off
accepted here is that dropping `port` from a file with no ports is a change you
didn't ask for, and one you'll meet again when you add a port to that file — so
until the keyword becomes optional, expect the formatter to keep the header and
the ports in agreement for you. (The discussion is
[gren-lang/compiler-common#33](https://github.com/gren-lang/compiler-common/issues/33).)

---

## Infix operator declarations

An `infix` declaration is always written on one line:

```gren
infix right 5 (++) = append
```

---

## Records

### Record values

An empty record is always `{}`.

A record follows your layout. Written on one line:

```gren
{ x = 1, y = 2 }
```

Written across rows (one or more fields on their own line), every field gets
its own line. The canonical shape puts `{` and the first field on the first
line, `, ` before each later field, and `}` alone on the last line:

```gren
{ x = 1
, y = 2
}
```

If some fields were on one line and others were on separate lines, the
formatter normalizes to fully vertical:

```gren
-- you wrote:
{ a = 1, b = 2
, c = 3
}

-- formats to:
{ a = 1
, b = 2
, c = 3
}
```

### Record updates

A single-field update stays inline when you wrote it that way:

```gren
withDefault r =
    { r | x = 0 }
```

But if that one field's value spans several lines — because it is an `if`,
`let`, `when`, or lambda, or you wrote it across rows — the update opens up the
same way a multi-field one does, rather than staying crammed onto the `{` line:

```gren
guard r =
    { r
        | field =
            if cond then
                yes

            else
                no
    }
```

This is one shared rule for every record update, regardless of how many fields
it has (matching elm-format). A lambda value follows it too: written across rows,
the whole lambda — `\arg ->` and body — drops onto its own line below `field =`,
same as any other multi-line value and same as elm-format.

A multi-field update follows your layout:

```gren
-- flat:
setOrigin pt =
    { pt | x = 0, y = 0 }

-- vertical:
movePoint dx dy pt =
    { pt
        | x = pt.x + dx
        , y = pt.y + dy
    }
```

Note the vertical shape is different from a plain record literal: the `|`/`,`
field lines indent 4 spaces *past* the opening `{`, while the closing `}`
comes back and lines up flush *with* `{` — not with the fields. This holds
regardless of what precedes the `{` on its own line — for example, a record
update glued after a field name:

```gren
wrapper x =
    { holder = { x
                   | a = 1
                   , b = 2
               } }
```

Here `{` sits wherever `holder = ` happens to end; the fields still land 4
spaces past *that* column, and `}` still lines up with it exactly.

### A lambda whose body is a forced-vertical record, update, or array drops it to its own line

A lambda's body normally follows your row placement, same as any other
lambda body (see [Lambdas](#lambdas)). A record, record update, or array
literal is an exception: once it renders across rows — because you wrote it
that way, or a comment forces it — it drops to its own line under `->`,
even if you glued it there, matching elm-format and gren's own rule for a
forced-vertical record used as a call argument
([above](#a-record-argument-that-renders-across-rows-drops-to-its-own-line)):

```gren
-- you wrote:
bumpUpdate =
    \x -> { x
        | a = 1
        , b = 2
        }

-- formats to:
bumpUpdate =
    \x ->
        { x
            | a = 1
            , b = 2
        }
```

A record literal and an array literal follow the same rule:

```gren
bumpRecord =
    \x ->
        { a = 1
        , b = 2
        }


bumpArray =
    \x ->
        [ 1
        , 2
        ]
```

A flat update, record, or array — one that fits and stays inline — is
unaffected:

```gren
bumpFlat =
    \x -> { x | a = 1 }
```

An `if`, `when`, or `let` body drops to its own line under `->` too, but by a
different rule — it *always* does so (it manages its own body indentation),
whether or not it would fit inline (see [Lambdas](#lambdas)).

### Record field values

A field value that renders across rows — a **lambda** with a multi-line body, an
**`if`**, **`when`**, or **`let`**, or a long binary-operator chain — drops onto
its own line below `field =`, so it reads the same as a top-level definition's
body. The whole value drops, header and all: a lambda's `\args ->` goes down with
its body, not left clinging to the `=`.

```gren
parser =
    { parseFn =
        \args ->
            if Array.length args == 0 then
                Ok {}

            else
                Err WrongArity
    , label = "parser"
    }
```

A value that fits on one line stays inline, including a short lambda:

```gren
{ increment = \v -> v + 1 }
```

`if`/`when`/`let` always render across rows, so they always drop; their aligned
keywords (`else`, `in`) line up 4 spaces under the field name:

```gren
choices =
    { kind =
        if isAdmin then
            Admin

        else
            Guest
    , label =
        let
            base =
                "user"
        in
        base ++ suffix
    }
```

The same rules apply in record updates:

```gren
withParser model =
    { model
        | name = "parser"
        , parseFn =
            \args ->
                if Array.length args == 0 then
                    Ok {}

                else
                    Err WrongArity
    }
```

### Record types and extensible records

A record *type* in a signature follows the same layout rules. An extensible
record type `{ r | field : Type }` follows your layout for its fields:

```gren
-- flat:
getName : { r | name : String } -> String

-- vertical:
getInfo :
    { record
        | firstNameField : String
        , lastNameField : String
        , ageInYears : Int
    }
    -> String
```

---

## Array literals

An empty array is always `[]`.

A non-empty array follows your layout. Written on one line:

```gren
[ 1, 2, 3 ]
```

Written across rows, every item goes on its own line. The canonical shape
puts `[` and the first item together, `, ` before each later item, and `]`
alone on the last line:

```gren
[ "first"
, "second"
, "third"
]
```

If items were spread across rows in any arrangement — some together, some
separate — the formatter normalizes to one item per line:

```gren
-- you wrote:
[ 1, 2
, 3, 4
]

-- formats to:
[ 1
, 2
, 3
, 4
]
```

A comment that can't share a line — a `--` comment, a `{- … -}` spread over
several lines, or one you put on a row of its own — forces the vertical layout
and sits between the items:

```gren
[ firstItem
-- a comment between items
, secondItem
]
```

A short `{- … -}` you wrote beside an item is different: it fits, so it just
stays where you put it and the array keeps the layout you gave it:

```gren
[ firstItem {- a note -}, secondItem ]
```

Records and arrays inside an array each decide their own layout independently,
following the same author-layout rules.

---

## String literals

A regular string is left as written, with its escape sequences intact:

```gren
greeting =
    "Hello, World!"


withEscapes =
    "line one\nline two\t!\\"
```

### Character literals

A character uses single quotes. Five special characters are always written as
escapes; everything else is written as the plain character:

```gren
tab = '\t'
newline = '\n'
carriageReturn = '\r'
singleQuote = '\''
backslash = '\\'
letter = 'a'
```

### Multi-line (triple-quoted) strings

A `"""` string always stays in triple-quoted form. The opening `"""` sits at
the binding's body column, and the content lines and closing `"""` sit at that
same indentation:

```gren
message =
    """
    Hello, World!
    """
```

Content lines are re-indented to line up with the `"""` delimiters. This is
safe because Gren strips the closing delimiter's column from every content line
before the formatter sees them; only relative indentation within the block is
preserved.

---

## If expressions

The `if … then` header follows your layout.

Written on one line — condition on the same row as `if` — it stays on one
line:

```gren
if x > 0 then
    "positive"

else
    "non-positive"
```

Written across rows — condition on a different row from `if` — it stacks: `if`
on its own line, the condition indented 4, `then` flush with `if`:

```gren
if
    x > 0
then
    "positive"

else
    "non-positive"
```

The condition itself follows author layout too — a multi-line binop predicate
uses the precedence-aware breaks described in
[Binary operators](#binary-operators).

### A condition that cannot fit on one line stacks anyway

"Written on one line stays on one line" holds only while the condition *can* be
one line. A condition containing a `when`, an `if`, a `let`, or any other
construct that always breaks has no one-line form, so `if <cond> then` has none
either — and the header falls back to the same stacked shape an author-broken
condition gets. Your layout is still being followed; there is simply no inline
layout to follow it to.

This is the common way to meet it, from this repo's own
`Render/FlowAssembly.gren`. Written with the condition on the `if` row:

```gren
runLeader i =
    if i > 0 && (when at (i - 1) is
                    Just prev ->
                        pairableComment prev

                    Nothing ->
                        False
                ) then
        runLeader (i - 1)

    else
        i
```

and formatted:

```gren
runLeader i =
    if
        i > 0
            && (when at (i - 1) is
                    Just prev ->
                        pairableComment prev

                    Nothing ->
                        False
               )
    then
        runLeader (i - 1)

    else
        i
```

Three rules are visible in that output, and it is worth separating them:

- **`if` takes its own line, the condition indents 4, `then` goes flush with
  `if`** — the fallback above. The `when` inside can never be one line, because
  a branch body always starts a row of its own.
- **The condition breaks at `&&` and not at `>`** — an operator chain splits
  only at its loosest operators, so `i > 0` stays glued and the `&&` operand
  drops to the next row, indented 4 from the condition (8 from `if`). See
  [Binary operators](#binary-operators).
- **The `)` lands under its `(`** — the ordinary closing-bracket rule, which is
  why it can shift a column from wherever you had aligned it by hand.

elm-format produces the same shape, with one difference that follows from the
second rule: it breaks the chain at *every* operator, giving `i` ⏎ `> 0` ⏎
`&& (…)`. That is
[divergence #17](elmFormatComparison.md#divergence-17).

Branch bodies **always** go on the next line, indented 4 spaces — even a
one-word body. `else` always lines up with `if`. A single blank line always
separates a branch body from the `else` or `else if` that follows it:

```gren
if n < 0 then
    "negative"

else if n == 0 then
    "zero"

else
    "positive"
```

---

## When expressions

Gren's `when … is` is the equivalent of Elm's `case … of`, but the grammar
is more flexible: the scrutinee may appear on the same line as `when`, or on
its own line between `when` and `is`. In Elm the scrutinee must always share
its line with `case`, so elm-format has no layout choice to preserve. In Gren
the formatter preserves whichever form you wrote.

Written on one line — scrutinee on the same row as `when`:

```gren
when msg is
    Increment ->
        model + 1
```

Written across rows — scrutinee on a different row from `when`, `is` at the
same indent as `when`:

```gren
when
    msg
is
    Increment ->
        model + 1
```

The broken form is useful when the scrutinee is a long expression that you
want to read clearly on its own line — something that in Elm you would have
to bind with a `let` first:

```gren
when
    Dict.get model.selectedId model.items
is
    Just item ->
        item.name

    Nothing ->
        "unknown"
```

Branch bodies **always** go on the next line, indented 4 spaces from the
pattern. A blank line always separates one branch from the next:

```gren
when n is
    1 ->
        "one"

    2 ->
        "two"

    _ ->
        "other"
```

The blank line is uniform regardless of whether a body is short or multi-line:

```gren
when msg is
    ChangeLanguage lang ->
        { model = { model | lang = lang }
        , command = Cmd.none
        }

    NoOp ->
        { model = model
        , command = Cmd.none
        }
```

A `--` comment on its own line between two branches belongs to the branch
below it: the blank line goes above the comment, and the comment stays
attached to the branch with no blank line between them:

```gren
when n is
    1 ->
        "one"

    -- a note about the next case
    2 ->
        "two"
```

A branch pattern that destructures a record follows your layout, same as any
other record pattern. Written on one line, the fields stay on one line:

```gren
when point is
    { x, y } ->
        String.fromInt x ++ ", " ++ String.fromInt y
```

Written across rows, the fields stay across rows, aligned directly under the
pattern's opening `{` — the same convention record literals use:

```gren
when point is
    { x
    , y
    } ->
        String.fromInt x ++ ", " ++ String.fromInt y
```

---

## Let expressions

`let` and `in` line up at the same indentation. Bindings are indented 4 spaces
under `let`, and the result expression starts on the line after `in`:

```gren
circleArea radius =
    let
        pi =
            3.14159

        rSquared =
            radius * radius
    in
    pi * rSquared
```

A binding's value always drops to the next line, indented 4 more spaces.
Arguments and a type signature make no difference:

```gren
hypotenuse x y =
    let
        square : Int -> Int
        square n =
            n * n
    in
    square x + square y
```

Exactly **one** blank line always separates bindings, regardless of how many
you wrote. A type signature sits directly on its definition. A comment sticks
to the binding below it — the blank goes above the comment:

```gren
let
    first =
        a

    second : Int
    second =
        b

    -- a note about third
    third =
        c
in
first + second + third
```

Unlike at the top level, a comment in a `let` never floats apart from the
binding below it — a blank line between a comment and the binding it precedes
is removed.

A comment written after the value of the *last* binding is a special case:
because the `in` keyword has no recorded position, gren-format can't tell a
comment trailing that binding from one introducing the result, so it places it
on its own line just below `in`. This is a deliberate divergence from
elm-format — see [Comparison with elm-format](elmFormatComparison.md#divergence-20)
(point 20), and [A comment after the last binding in a `let`](knownLimitations.md#a-comment-after-the-last-binding-in-a-let).

You can destructure on the left of a binding:

```gren
let
    { model, command } =
        update msg model
in
model
```

A single-constructor unwrap or `as`-alias in a binding is wrapped in
parentheses:

```gren
let
    (Builder bb) =
        toBuilder x

    ({ y } as point) =
        origin
in
bb point
```

---

## Patterns as arguments

Wherever patterns appear side by side as space-separated arguments — in a
function definition, a `let` definition, or a lambda — two forms are wrapped
in parentheses:

- A **constructor applied to a payload**, e.g. `(Response response)`
- An **`as`-alias**, e.g. `({ x, y } as point)`

```gren
setStatus statusCode (Response response) =
    Response { response | status = statusCode }


update ({ model } as state) msg =
    state


mapBox =
    \(Box value) -> value
```

A bare constructor with no payload (`Nothing`) takes no parentheses.

The parentheses matter because a constructor's payload parses greedily: without
them, `setStatus statusCode Response response` reads `response` as the payload
of `Response`, not as a separate argument.

---

## Lambdas

A lambda's body follows your layout.

Written on one line — body on the same row as `->`:

```gren
double =
    \n -> n * 2


add =
    \a b -> a + b
```

Written across rows — body on a different row from `->`:

```gren
transform =
    \veryLongParameterName ->
        veryLongParameterName * 2
```

A lambda whose body is an `if`, `when`, or `let` always drops that body to its
own line under `->`, indented +4 — it never stays glued to `->`:

```gren
classify =
    \n ->
        if n > 0 then
            "positive"

        else
            "other"
```

Passed as an argument, a lambda is wrapped in parentheses. A lambda with a
one-line body stays glued to the function name:

```gren
doubleAll =
    Array.map (\n -> n * 2) nums
```

But once the lambda's body wraps — an `if`/`when`/`let` body, or a
record/array it returns across rows — the whole `(...)` argument drops to its
own line, indented +4 from the function name, and any following argument drops
too (see [A record argument that renders across rows drops to its own
line](#a-record-argument-that-renders-across-rows-drops-to-its-own-line), which
this shares its rule with):

```gren
signums =
    Array.map
        (\n ->
            if n > 0 then
                1

            else
                -1
        )
        nums
```

There is one exception: a lambda that is the **direct operand** of a pipeline
step stays glued to `|>`/`<|` (only the operator precedes it, so there is no
function name to separate from), with its body dropping under `->`:

```gren
result =
    values
        |> (\n ->
                if n < 0 then
                    -n

                else
                    n
            )
```

---

## Pipelines

Both `|>` (forward) and `<|` (backward) pipelines follow your layout. A run
of the *same* operator is treated as one pipeline.

Written on one line, a pipeline stays on one line:

```gren
result =
    list |> Array.map double |> Array.first
```

Written across rows, each step stays on its own line.

**`|>` pipelines** use a leading-operator style, each step indented 4 spaces
from the seed:

```gren
result =
    nodes
        |> Array.map double
        |> Array.keepIf isValid
        |> Array.first
```

When a `|>` step's last argument is a multi-line lambda (body written on a
different row from `->`, so wrapped in parentheses), the lambda sits on its own
line indented +4 from the `|>`. The body is indented a further +4, and `)` closes
at the same column as `(`:

```gren
result =
    Time.now
        |> Task.andThen
            (\start ->
                doWork start
            )
```

This applies at every nesting level:

```gren
result =
    Time.now
        |> Task.andThen
            (\start ->
                lifecycle start
                    |> Task.andThen
                        (\outcome ->
                            Task.succeed outcome
                        )
            )
```

When the step has arguments after the lambda, they each land on their own line at
the same column as the opening `(`:

```gren
passed =
    sr.results
        |> Array.foldl
            (\r acc ->
                if isFailed r.outcome || isErrored r.outcome then
                    acc

                else
                    acc + 1
            )
            0
```

Arguments written *before* the breaking one land on their own line the same way,
so once any argument of a step breaks, every argument of that step has a line to
itself — exactly what a plain call does:

```gren
merged =
    rows
        |> Array.foldl
            seed
            { limit = 10
            , strict = True
            }
            extra
```

When the lambda comes **straight after `|>`** — the whole step is `|> (\... -> ...)`,
with no function in between — the lambda stays glued to `|>` and its body drops
below, the same as above. The closing `)` lines up directly under its own `(`,
matching `elm-format`:

```gren
summary =
    counts
        |> Dict.foldl addRow []
        |> (\rows ->
                if Array.isEmpty rows then
                    "no data"

                else
                    String.join "\n" rows
           )
```

**Your row placement is the choice.** The formatter uses the multi-line form when
the lambda body starts on a different row from `->`, and the inline form when the
body is on the same row.

A single-line lambda (body on the same row as `->`) stays inline:

```gren
result =
    list
        |> Array.map (\n -> n * 2)
```

So to get the multi-line form, put the body on the next row — even if the rest of
the lambda is otherwise on one line:

```gren
-- body on the next row from ->: formatter uses the multi-line form
sr.results
    |> Array.foldl (\r acc ->
        if isFailed r.outcome || isErrored r.outcome then
            acc
        else
            acc + 1) 0
```

Formats to the canonical multi-line form shown above.

**`<|` pipelines** use a trailing-operator style, with each step body indented 4
spaces further than the one before it:

```gren
result =
    String.toUpper <|
        String.append "Greetings, " <|
            String.append name "!"
```

A `<|` chain is right-associative — each step is an argument to the one above
it — so the staircase is what the nesting actually is, and it is what
`elm-format` produces. This is the layout however flat you wrote the chain.

When a `<|` step body is a lambda, the `<|` trails the preceding step and the
lambda sits on the next line, indented +4 from the pipeline seed. The lambda
body is indented another +4:

```gren
main =
    Node.defineSimpleProgram <|
        \env ->
            run env
```

A comment just before a `|>` step travels with that step:

```gren
result =
    list
        -- keep only the valid ones
        |> Array.keepIf isValid
        |> Array.map double
```

---

## Binary operators

A chain of operators follows your layout.

Written on one line, it stays on one line:

```gren
area =
    width * height + margin

greeting =
    "Hello, " ++ firstName ++ " " ++ lastName
```

Written across rows, the chain breaks **at its loosest-binding operators**, and
tighter-binding parts stay together on one line. Each break operator leads its
continuation line, indented 4 spaces from the first operand:

```gren
score =
    baseScore
        + bonusPoints * multiplier
        - penaltyAmount
```

`bonusPoints * multiplier` stays on one line because `*` binds tighter than `+`
and `-`, so the chain only splits at the `+` and the `-`. When several operators
of different strengths mix, the chain still splits only at the weakest ones:

```gren
eligible =
    isAdministrator
        || hasElevatedRole && accountIsActive == True
        || isOwner
```

Here `||` is the weakest operator, so the chain breaks at each `||`; the
`&&` and `==` bind tighter and stay on their line. When every operator in the
chain binds equally, they all break, since none is tighter than the rest:

```gren
greeting =
    "Hello, "
        ++ firstName
        ++ " "
        ++ lastName
```

A chain also breaks when one of its operands is **itself** multi-line — a record,
array, or parenthesized expression you wrote across rows — even if you kept the
operators on one line. The multi-line operand opens the chain up; a tighter
operator right after it stays glued to its closing `}`/`]`/`)`:

```gren
config =
    defaults
        ++ { verbose = True
           , retries = 3
           } * scale
        ++ overrides
```

(A multi-line `"""…"""` string operand is the exception — it stays glued in the
chain, since its own lines already carry the layout.)

A comment in the chain doesn't change the breaks — the chain splits at the same
operators, and each comment stays where you wrote it. A comment on its own line
sits at the operator indent; a comment trailing an operand rides that operand's
line; a comment just before an operand glues in front of it:

```gren
total =
    leftComponent
        -- start with the pieces
        ++ rightComponent {- the middle -}
        ++ trailingValue
```

A `--` does end its line, so a chain carrying one can't stay on a single row —
but it still breaks at the loosest operators, not at the one the comment happens
to precede:

```gren
-- you wrote:                  -- gren-format:
result =                       result =
    one + two -- the sum           one
              * three                  + two -- the sum
                                         * three
```

The chain splits at the `+`, and `two * three` stays the one group it always is;
the comment only decides which row inside that group the `*` lands on. Breaking
at the `*` instead would put `one + two` on a row together and read as
`(one + two) * three`.

The same precedence-aware layout applies to a stacked `if` condition (see
[If expressions](#if-expressions)).

---

## Comments

The formatter **never changes the text of a comment.** It only decides where
the comment sits relative to the code around it.

### Where you put a comment is meaningful

Whether a comment **shares a line with the code before it** or **sits on its
own line** is kept as written:

```gren
foo =
    1 {- inline: stays on the value's line -}


bar =
    { a = 1
    {- standalone: stays on its own line, before the close -}
    }
```

### Single-line comments (`--`)

A `--` comment on a line of code stays on that line:

```gren
import Dict exposing
    ( Dict
    , empty -- a comment on the same line as empty
    )
```

A `--` comment on its own line stays on its own line, indented to match the
code around it:

```gren
foo a =
    -- before the body
    a * 100
```

### Block comments (`{- ... -}`)

A short block comment inside an expression stays inline:

```gren
foo a =
    a * {- inline note -} 100
```

This holds inside a list, record, or record type too — writing one beside an
item doesn't break the brackets open:

```gren
sizes =
    [ 1 {- one -}, 2, 3 ]


point =
    { x = 0 {- origin -}, y = 0 }
```

A block comment whose body spans several lines forces the construct around it
to break vertically. When the comment's text starts on the same line as `{-`,
the body lines are re-indented to line up under the `{-`:

```gren
value =
    items
        {- this comment spans
           three lines and keeps
           its shape -}
        |> process
```

The re-anchoring uses the body's *own structure*: its shallowest line aligns
just past the `{- ` prefix, and deeper lines stay deeper by the same relative
amount. Sloppy or accidental input indentation is cleaned up.

The same re-anchoring applies when `{-` sits alone on its first line — there is
no separate "verbatim" mode. Hand-aligned content (ASCII art, an aligned table)
keeps its *relative* shape; the block is re-anchored under the `{-` rather than
pinned to the exact columns you typed:

```gren
-- you wrote (the {- alone on its line, body indented however):
config =
        {-
      an aligned diagram
         /\
        /  \
       /____\
    -}
        42

-- formats to (block re-anchored under the {-, relative shape preserved):
config =
    {-
       an aligned diagram
          /\
         /  \
        /____\
    -}
    42
```

This matches elm-format, which re-indents block comment bodies the same way.

#### Comments in an effect module's header

An effect module's `where { … }` block — the `where`, the braces, the field
name, the `=` — carries no position information from the parser. Only the
handler name (e.g. `MyCmd`) has a known position. A comment's placement is
therefore judged by how close it sits to that name: is it close enough to
still be "inside" the block, given that the block's own boundaries aren't
really known?

The `where { … }` block always collapses to one line, regardless of how the
author broke it across rows — like any other comment-free construct, it isn't
forced open just because it once spanned multiple rows.

A short `{- … -}` comment right next to the name rides that one line, exactly
where it was written:

```gren
-- you wrote (and the formatter keeps):
effect module MyModule where { command = MyCmd {- note -} } exposing (..)
```

A comment that *can't* share a line does force the block open, one field per
line, with the closing `}` and `exposing (..)` lined up under the first field's
column. A `{- … -}` spread over several lines is the case you can actually hit
here — a `--` comment inside the braces has its own problem, described in
[Comments near an effect module's `where` block](knownLimitations.md#comments-near-an-effect-modules-where-block):

```gren
-- you wrote:
effect module MyModule where { command = MyCmd {- a longer
                                                  note -} } exposing (..)

-- formats to:
effect module MyModule where { command = MyCmd {- a longer
                                                  note -}
                             } exposing (..)
```

Once the block is open like that, the `}` and the `exposing (..)` after it sit
on a row of their own that the parser records nothing about. A comment written
on that row still belongs to the module line and stays on it:

```gren
-- you wrote:
effect module MyModule where { command = MyCmd {- a longer
                                                  note -} } exposing (..) -- trailing note

-- formats to:
effect module MyModule where { command = MyCmd {- a longer
                                                  note -}
                             } exposing (..) -- trailing note
```

Concretely, "close enough" means within a couple of columns of where the
handler name ends — just enough room for a single space plus the closing `}`
that has no position of its own to check against. A comment that close is
treated as attached to the handler name and travels with it.

Wider spacing pushes the comment past that margin, so it no longer reads as
attached to the handler name. Once that link is gone, the comment falls back
to the same rule used for a comment trailing the module line in general: it
stays glued to the end of the line instead of to the handler name:

```gren
-- you wrote (only more spaces before the comment):
effect module MyModule where { command = MyCmd      {- note -} } exposing (..)

-- formats to:
effect module MyModule where { command = MyCmd } exposing (..) {- note -}
```

Everything *left* of the handler name goes the other way. The `where`, the `{`,
the field name and the `=` all have no position, and neither does anything
before them, so there is no token to measure a comment against — every comment
written left of the name collapses to the one slot between `where` and `{`,
whichever of those gaps you wrote it in. A run of them travels there together
and keeps its order:

```gren
-- all of these:
effect module MyModule where {- a -} { command = MyCmd } exposing (..)
effect module MyModule where { {- a -} command = MyCmd } exposing (..)
effect module MyModule where { command {- a -} = MyCmd } exposing (..)
effect module MyModule where { command = {- a -} MyCmd } exposing (..)

-- format to:
effect module MyModule where {- a -} { command = MyCmd } exposing (..)
```

A comment past the *first* handler's name in a two-field block still has a
position to sort against — the name it follows — so it stays inside the block,
between the two fields:

```gren
-- you wrote (and the formatter keeps):
effect module MyModule where { command = MyCmd, {- b -} subscription = MySub } exposing (..)
```

Whatever follows the module line always gets exactly one blank line before
it, regardless of how tight or loose the original spacing was — otherwise the
same file could format differently depending on how close together the
author happened to type the module line and the next line, which would work
against [idempotent formatting](#background).

### Doc comments (`{-| ... -}`)

A doc comment sits directly above the declaration it documents with no blank
line between them. A module doc comment is the exception: it comes after the
module line with one blank line in between:

```gren
module MyApp exposing ( foo )

{-|
This is the module doc comment.
-}

{-| Doc comment for foo.
-}
foo : Int -> Int
foo n =
    n
```

### Blank lines around comments

There is one rule behind everything in this section: **a blank line separates
statements and declarations — top-level units, `let` bindings, `when` cases, and
`if`/`else` branches — and never separates the parts of a single expression.** A
list, a record, a binop chain, and a pipeline are each one expression, so no
blank line ever falls between their parts, and a line-leading comment sitting
between two of those parts is kept without a blank line above it. (elm-format differs both
ways: it *adds* a blank above such a comment inside a list or record, and does
*not* add one between pipeline steps — see the divergence catalogue.)

A comment directly above a declaration stays attached — no blank line between
them:

```gren
-- about foo
foo =
    1
```

A comment separated from the code below it by a blank line stays separate. The
blank line is preserved, and the code below still gets its normal two blank
lines:

```gren
-- a loose remark


foo =
    1
```

### A comment on its own line below a declaration

A comment you write on its **own line below a declaration** — under a function
body, after the last operand of a chain, below a closing `]`/`}` — always moves
to the **left margin (column 1)**. It never stays indented under the code. This
matches elm-format.

What your original **indentation** still decides is which declaration the comment
belongs to, and that shows up in the blank lines around it. Written *indented*
under the code above, it belongs to that code: it drops to column 1 directly
below it, and a blank line sets it off from whatever comes next.

```gren
-- you write this:
total =
    alpha
        ++ beta
        {- trails the chain -}
next =
    1
```

```gren
-- gren-format produces (comment stays with `total`):
total =
    alpha
        ++ beta
{- trails the chain -}


next =
    1
```

Written at the **margin already**, it introduces what comes next: a blank line
sets it off from the code above, and it stays with the following declaration.

```gren
-- you write this:
total =
    alpha
        ++ beta
{- introduces next -}
next =
    1
```

```gren
-- gren-format produces (comment leads `next`):
total =
    alpha
        ++ beta


{- introduces next -}
next =
    1
```

A blank line above the comment always cuts it loose from the code above,
regardless of indentation.

This is only about a comment on its *own* line. A comment written on the **same
line** as the code it follows stays right there beside it — `foo : Int -> Int
{- about foo -}` keeps the comment on the signature line (see the divergence
"A comment written after code stays on that line").

### A trailing comment on a `when` branch body

A block comment at the end of a `when` branch body attaches to the body's
last line, staying inline — regardless of whether another branch follows:

```gren
describe x =
    when x is
        Foo ->
            someValue {- trailing note -}

        Bar ->
            otherValue
```

### When the formatter can't tell what you meant

Most punctuation is parsed and then **discarded**, leaving no position in the
AST. Of everything that separates two pieces of an expression, only a binary
operator (`+`, `|>`, `++`, …) and the brackets `(`, `)`, `[`, `]`, `{`, `}`
survive into the tree with a recorded position. Everything else —

    =    :    |    ,    ->    if / then / else    when / is    let / in
    an import's `as` and alias name

— is invisible by the time the formatter runs. A comment written next to one of
these could have been on either side of it and the two are *positionally
identical*: all the formatter can see is the previous token's end, the comment's
own span, and the next token's start, and both spellings produce exactly the
same three. The only thing that would separate them is how wide the whitespace
gaps are, and that is deliberately not information the formatter reads —
`format` must be insensitive to the spacing you used.

So a comment beside one of these tokens is always placed on **one canonical
side**, and two programs that differ only in which side a comment sits on format
to the *same* output. This isn't a preference; it's the only thing a formatter
without that fact can do and still be stable. Where the choice is visible in the
comparison with elm-format, it is catalogued as
[divergence #22](elmFormatComparison.md#divergence-22).

The canonical side is the **later** one — the comment lands after the token,
not before it. The worked cases below are all instances of that one rule, and
the exceptions to it (a `--` at a `,` or a `|`, a union variant's `|`, an
import's `as`, and an `exposing` list) say so where they appear.

Where the token **is** recorded, the formatter keeps the side you wrote it on.
The brackets are the useful case: a comment just inside an opening bracket
stays inside, and one just past a closing bracket stays outside.

```gren
[ {- primary -} 1, 2 ]              -- stays inside the array
{ {- the state -} rec | a = 1 }     -- stays inside the update, before the base
fn a { rec | a = 1 } {- c -} last   -- stays outside the record
```

A record update shows both halves at once. Its `{` is recorded and so is its
base name, so a comment before the base is placed exactly; past the base only
the unrecorded `|` is left, and from there the rule below takes over:

```gren
-- you wrote:
{ {- kept -} rec {- canonicalized -} | a = 1 }

-- formats to:
{ {- kept -} rec | {- canonicalized -} a = 1 }
```

A comment around a signature's `:` always lands **after** it:

```gren
foo {- c -} : Int          -->   foo : {- c -} Int
foo : {- c -} Int          -->   foo : {- c -} Int
```

A comment around a definition's `=` always lands **after** it:

```gren
-- both of these:
foo {- c -} = 42
foo = {- c -} 42

-- format to:
foo = {- c -}
    42
```

A record field's `=` follows the same rule as a definition's — the comment lands
**after** it, at the head of the value:

```gren
-- both of these:
{ field {- why -} = compute 1 }
{ field = {- why -} compute 1 }

-- format to:
{ field = {- why -} compute 1 }
```

A comment around a union `|` always lands **after the variant before it** — one
of the exceptions to the "later side" rule. Both spellings collapse into one
output, so the side chosen decides which of the two authors gets their text back
unchanged; a note beside a variant reads as a note about that variant, so the
union serves the one who writes it **before** the `|`. (elm-format breaks the
union open around such a comment on either side, so no side would match it
anyway.)

```gren
-- both of these:
type T = A {- c -} | B
type T = A | {- c -} B

-- format to:
type T
    = A {- c -} | B
```

(The comment doesn't break the union open — a single-line `{- -}` rides the
line, so variants the author wrote flat stay flat, per
[C3](commentHandling.md#c3--a-comment-never-forces-a-break).)

A `{- -}` comment around a **record update's** `|` (and an extensible record
type's) lands **after the `|`, leading the first field** — the same question as
the union's `|`, answered the other way, serving the author who writes the
comment **after** the separator. The two are a deliberate pair of preferences;
the formatter sees exactly as much at one `|` as at the other. (This is only
about the gap *after* the base name. One written before it — right after the `{`
— is in the opener slot and stays exactly where you put it.)

```gren
-- both of these:
{ rec {- c -} | a = 1 }
{ rec | {- c -} a = 1 }

-- format to:
{ rec | {- c -} a = 1 }
```

A `{- -}` comment around a `,` lands **leading the item after it**, for the same
reason:

```gren
-- both of these:
[ 1, {- c -} 2 ]
[ 1 {- c -}, 2 ]

-- format to:
[ 1, {- c -} 2 ]
```

**A `--` at a `,`, a `|`, or a broken signature's `->` is the exception** — it
keeps the row you wrote it on, and the two spellings do *not* collapse onto each
other. A `--` ends its row, so it reads as a note about that row, and it is
genuinely tellable apart: it is either on the previous item's row or on a row of
its own. All three separators lead their line, so a comment above one strands
nothing — it sits at the separator's own column:

```gren
-- you wrote, and the formatter keeps:
[ apple -- the red one
, banana
]

[ apple
  -- about banana
, banana
]

{ rec -- about the base
    | alpha = 1
}

{ rec
    -- about alpha
    | alpha = 1
}

foo :
    Int -- about Int
    -> String

foo :
    Int
    -- about Int
    -> String
```

A multi-line `{- … -}` follows the `--`: it opened on a row, and that row is what
decides it. A union `|` behaves the same way, and is covered above. A signature's
`->` is worked through in [Type signatures](#type-signatures) — it is the one
member of the family where keeping the row also *matches* elm-format, on both
spellings.

A comment around one of the keywords `then`, `else`, `is`, `in`, or a **lambda's
or branch's** `->` always lands **after** the keyword, never before it
(a *type's* `->` is the exception just described, not one of these):

```gren
-- both of these:
when sel {- c -} is
when sel is {- c -}

-- format to:
when sel is
    {- c -}
    Just w ->
        1
```

`in` is the most visible of these, because it decides whether a comment written
after the last `let` binding renders above or below the `in` — see
[divergence #20](elmFormatComparison.md#divergence-20) for the worked example.

A comment around an import's `as` always lands **before** it:

```gren
-- both of these:
import Foo {- c -} as Bar
import Foo as {- c -} Bar

-- format to:
import Foo {- c -} as Bar
```

A module header's `exposing ( ... )` list is another one of these: its closing
`)` has no position in the AST either. When you wrote the list across rows, a
comment after the last name always lands **inside** the list, whichever side of
the `)` you wrote it on:

```gren
-- both of these:
module M exposing
    ( apple
    , zebra
    -- the last one
    )

module M exposing
    ( apple
    , zebra
    ) -- the last one

-- format to:
module M exposing
    ( apple
    , zebra
    -- the last one
    )
```

Inside the list it belongs to the name it follows, so if sorting moves that name
the comment goes with it — the same thing an import's list does with a comment in
the same spot:

```gren
module M exposing
    ( zebra
    , apple
    -- follows apple, the last name written
    )
```

becomes:

```gren
module M exposing
    ( apple
    -- follows apple, the last name written
    , zebra
    )
```

A comment trailing a name on that name's *own* row works the same way — it
belongs to the name and travels with it (see
[Exposed names sort automatically](#exposed-names-sort-automatically)).

The same canonical choice can't be made when you wrote the list flat, on one
row: there, "before the `)`" and "after the `)`" are the same row, and the `)`
you'd measure against isn't recorded. See
[A module `exposing` list's closing paren isn't recorded](knownLimitations.md#a-module-exposing-lists-closing-paren-isnt-recorded).

An import's exposing list has none of this ambiguity — the parser does record
where an import ends, so both a flat and a vertical import list keep a comment
on whichever side of the `)` you wrote it.

