# How the Gren formatter works

This package is the library behind `gren-format`: given a Gren source file,
it produces a formatted version of the same file — consistent spacing,
consistent indentation, comments and blank lines kept where they belong, and
also honoring the line breaks the author of the source code chose.

This section is a guided tour of *how* it does that, at a conceptual level.

---

## Overview

Turning your source file into its formatted version happens through a pipeline
of steps, each step handing its result to the next:

```
your source code (text)
        │
        │  parsed by Gren's compiler-common parser
        ▼
code structure  +  comments
        │
        │  Step 1
        ▼
a Logical Printing Tree
        │
        │  Step 2
        ▼
a render plan
        │
        │  Step 3
        ▼
formatted code (text)
```

**Before this library gets involved**, the compiler-common parser reads your file and
splits it into two pieces:

- the Abstract Syntax Tree (AST) — a description of which function calls which,
  what a `let` contains, what a type looks like, and so on
- a separate list of every comment you wrote, since these
  don't change what the code means, but they do matter for how it looks

This library's job starts from those two pieces and ends with the formatted
text. It never changes what your code means — it only decides how it looks
on the page.

---

## Step 1: building the Logical Printing Tree

The first step walks over your code's structure and builds a **Logical
Printing Tree**: one entry for every piece of your program (a function, an
expression, a list, a comment, a blank line, and so on), arranged in the
same shape as your code.

Each entry in this tree isn't the final text yet — it's a *layout decision*.
Some examples of the kinds of decisions recorded here:

- "these pieces sit on one line if you wrote them on one line, or each gets
  its own line if you spread them across rows"
- "this is a block whose body always starts on the next line, indented"
- "this is a list that's either written all on one line, or with one item
  per line — never a mix"

Where those decisions come from matters: the formatter mostly follows *your*
original line breaks. If you wrote a list across several lines, the Logical
Printing Tree records "spread this out"; if you wrote it on one line, it records
"keep this together." The tree is really a map of those choices, ready to
be turned into text later.

### Where comments and blank lines fit in

Comments and blank lines aren't part of your code's structure, so they
arrive separately, each tagged with the line and column where you wrote it.

Once the rest of the Logical Printing Tree is built from your code alone,
this step goes back through and puts each comment and blank line in place —
finding the spot in the tree that sits at that same line and column, and
inserting it next to the code it was originally written
beside. A comment on the same line as some code attaches to that code; a
comment on its own line becomes its own entry, positioned between whatever
came before and after it in your file. The same idea applies to blank
lines: the formatter notices where you left gaps and preserves them as
their own entries in the tree.

The result is a Logical Printing Tree that has everything: code, comments,
and blank lines, all in the right order and all carrying their layout
decisions.

### Example

Comments are what make this genuinely hard: they carry meaning for a human
reader, but the parser doesn't attach them to any particular piece of code —
they just sit in a separate list, tagged with a position. Take this
file where spacing is messy and non-standard:

```gren
module Sample exposing (greet)


import String


-- Greets someone by name
greet:String->String
greet name =
  "Hello, "  ++    name
```

(The parser doesn't care whether `:` and `->` have surrounding spaces —
`greet:String->String` parses exactly like `greet : String -> String`;
whitespace around most tokens is not meaningful.)

The parser splits this into an AST — which never mentions the comment at
all — and a Context that holds only the comment, tagged with the row and
column where it starts:

```
Module "Sample"
├── exports: [ greet ]
├── imports: [ String  (4:1–4:14) ]
└── values
    └── greet(name)                                (8:1–10:24)
        ├── signature: String -> String             (8:7–8:21)
        └── body: Binop "++"                        (10:3–10:24)
            ├── left:  String "Hello, "
            └── right: Var name

Context
└── comments: [ Line "-- Greets someone by name"  (7:1–7:26) ]
```

Building the Logical Printing Tree means walking that AST first, then going
back and re-inserting the comment at row 7 — right where it was written,
directly above the signature it sits beside:

```
RootBox
├── OriginalRows[module]       "module Sample exposing (greet)"
├── EmptyLine
├── OriginalRows[import]       "import String"
├── EmptyLine
├── EmptyLine
├── OriginalRows[lineComment]  "-- Greets someone by name"
├── OriginalRows[funcSig]      "greet : String -> String"
└── OriginalRows[funcDecl]
    ├── AcrossOrVertical        "greet name ="
    └── BodyBlock
        └── Binop "++"
            ├── "Hello, "
            └── OpAndRhs  "++ name"
```

Notice there's no `EmptyLine` between the comment, the signature, and the
function itself — all three stay glued together as one declaration unit.
(See [Blank lines around comments](#blank-lines-around-comments) for the
general rule.)

---

## Step 2: turning the Logical Printing Tree into a render plan

The Logical Printing Tree says *what could* happen ("these items can go on
one line or several"). The next step turns each of those decisions into
something much more concrete: a small set of building blocks that say
exactly what to print — a piece of text, a line break, or "indent
everything from here by one more level."

This step doesn't do any guessing or searching for the "best" way to lay
things out. Because the Logical Printing Tree already recorded each
decision (based on how you originally wrote the code), this step just
follows those decisions directly. That's why the same input always
produces the same output, and why there's no "line width" setting to
configure — the formatter isn't trying to fit your code into 80 columns or
any other target, it's reproducing the shape you already chose.

### Example

Continuing the same example, the Logical Printing Tree from Step 1 becomes
this render plan — one entry per root item, each a small tree of concrete
building blocks (`X › Y` means `X` wraps a single child `Y`; branches use
`├──`/`└──`):

```
[0] Seq
    ├── "module Sample exposing"
    └── Nest 4
        └── Group › Seq[ Nl, "(greet)" ]

[1] Empty

[2] Nest 4
    └── Seq
        ├── "import"
        └── Group › Seq[ Nl, "String" ]

[3] Empty
[4] Empty

[5] Text "-- Greets someone by name"

[6] Group
    └── Seq
        ├── "greet"
        ├── Group › Seq[ Nl, ":" ]
        └── Nest 4
            └── Seq
                ├── Nl
                ├── "String"
                ├── Nl
                ├── "->"
                └── Group › Seq[ Nl, "String" ]

[7] Nest 4
    ├── Nest 4
    │   └── Seq
    │       ├── "greet"
    │       ├── Group › Seq[ Nl, "name" ]
    │       └── Group › Seq[ Nl, "=" ]
    └── Seq
        ├── HardNl
        └── Group › Seq[ "\"Hello, \"", Nest 4 › Seq[ Nl, "++ name" ] ]
```

The comment (entry `[5]`) is just a bare `Text` node sitting between two
`Empty` placeholders and the signature's `Group` — nothing left to decide
about it. The whole signature (entry `[6]`) sits inside one outer `Group`,
so every `Nl` inside it — around `:`, `->`, even the ones separating
`String` from its neighbors — collapses to a single space no matter what;
that's what "written on one line, stays on one line" (see [Type
signatures](#type-signatures)) looks like at this stage. And every choice
about line breaks in the function itself is already made here too, not in
Step 3: `Group` always renders flat, so every `Nl` above (around `name`,
`=`, and `++ name`) is really just a space — because you wrote `greet
name = "Hello, " ++ name` on one row, nothing here forces those breaks
open. The one break that *does* happen, the `HardNl` between `greet name
=` and its body, is unconditional regardless of `Group` — that's the
"function body always starts on the next line" rule, and it fires no
matter how the call was written. Step 3 doesn't choose between staying
flat or breaking; it just executes whichever this tree already committed
to.

---

## Step 3: turning the render plan into text

The last step is the simplest: walk over the render plan from the previous
step and produce the actual characters of the formatted file — inserting
real newlines, real spaces, and the right amount of indentation at each
level. What comes out the other end is the finished, formatted source file.

### Example

Rendering the plan from Step 2 produces the finished file:

```gren
module Sample exposing (greet)

import String


-- Greets someone by name
greet : String -> String
greet name =
    "Hello, " ++ name
```

The two blank lines around `module`/`import` collapsed to one, `:` and
`->` each got a surrounding space, the four spaces around `++` collapsed
to one, the 2-space body indent became 4, and the comment landed exactly
where it started — still glued to the signature, with no blank line
between them.

---

## Why this design?

The formatter's guiding idea is: **your line breaks are your layout
decisions.** Rather than trying to choose the "best" way to
arrange your code, it honors how you already wrote it and simply makes that
consistent everywhere. This keeps the whole process predictable — running
the formatter twice in a row always produces the same result, and a change
to one part of a file never surprises you by reshuffling an unrelated part.

---

## Where to go next

- [Gren Formatter Rules](#gren-formatter-rules) below — a full reference of
  formatting rules with worked examples, for anyone using `gren format` day
  to day.
- [`DEVELOPER.md`](DEVELOPER.md) — an orientation guide for anyone extending
  the formatter with new syntax.

---

## Gren Formatter Rules

A guide to how `gren format` lays out your code — what it changes, what it
leaves alone, and why.

---

### Background

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
   produces the same code back. Format once or ten times — same result.

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

### Module declaration

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

When the exposing list contains a comment, it is always kept vertical.

A custom type exposed with its constructors gets a space before `(..)`:

```gren
module MyApp exposing (Outcome (..), Model)
```

This applies anywhere an exposing list can name a custom type's
constructors, including an import's exposing list (see
[Import statements](#import-statements)).

### Exposed names sort automatically

Regardless of the order you wrote them in, an `exposing ( ... )` list sorts
into three groups — operators, then types, then plain values — and
alphabetically within each group. This matches `elm-format`.

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

This applies the same way to an import's own exposing list — see
[An import's exposing list sorts automatically](#an-imports-exposing-list-sorts-automatically).

---

### Import statements

A plain import stays on one line:

```gren
import Array
```

An alias uses `as`. An exposing list follows your layout — flat if you wrote
it flat, vertical if you wrote it across rows. Unlike a module's `exposing`
(which always stays glued to the module name — see
[Module declaration](#module-declaration)), an import's `exposing` drops to
its own line, indented +4, when the list goes vertical, with the list itself
indented +8 below that:

```gren
-- flat:
import String exposing (fromInt, toInt)

import Array.Extra as AE exposing (filterMap, unique)

-- vertical:
import Dict
    exposing
        ( Dict
        , empty
        , fromArray
        , get
        )
```

### An import's exposing list sorts automatically

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

### Import statements sort within unbroken runs

`import` statements sort alphabetically by module name, but only within a
*run* — a stretch of imports with nothing between them: no blank line, no
comment on its own line. A blank line or an own-line comment is a boundary:
it never moves, and it splits the imports around it into independently
sorted groups. A run is fine with multi-row imports (a wrapped exposing
list doesn't break it) — only a blank line or a comment does.

```gren
import Zebra
import Mango
-- a section note
import Kiwi
import Apple

import Delta
```

becomes:

```gren
import Mango
import Zebra
-- a section note
import Apple
import Kiwi

import Delta
```

`[Zebra, Mango]` and `[Kiwi, Apple]` are separate runs (split by the
comment), each sorted independently; `Delta` is alone in its own run (blank
line above it), so there's nothing to sort. The comment and the blank line
stay exactly where they were.

A comment trailing an import on that import's *own* source row is the one
exception — unlike an own-line comment, it does not break the run, and it
travels with its import if that import moves within the group:

```gren
import Foo -- deprecated, remove soon
import Bar
import Baz
```

becomes:

```gren
import Bar
import Baz
import Foo -- deprecated, remove soon
```

---

### Type signatures

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

The multi-line shape triggers when any `->` separator appears on a different
row than the one before it. A line break right after the `:` with the rest
still on one line is not enough — the break must fall between `->` segments.

A comment doesn't change this: a signature written across rows uses the same
per-segment shape whether or not it carries a comment. A comment leading a
segment (right after its `->`) drops to its own indented line above the type:

```gren
bestDiscount :
    Array { code : String, basisPoints : Int }
    ->
        -- comment about the result
        Maybe { code : String, basisPoints : Int }
```

Only a signature the author kept on **one row** falls back to filling the flow
and wrapping at word boundaries when it carries a comment — there's no
`->`-segment boundary to anchor a break to. A multi-line block comment forces
a break right after itself, and whatever follows just continues to fill the
same line rather than starting a new per-segment line:

```gren
convert : Int -> {- explanation that
                    spans multiple lines -}
    Int -> Int
```

Compare this to the canonical per-segment shape a few lines up: there, every
`->` starts its own line. Here, `Int -> Int` stays together on one
continuation line — the break landed where the comment ended, not at a
`->` boundary.

---

### Function application

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

#### A record argument that renders across rows drops to its own line

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

This only applies to a record, or anything else whose own content forces it
across rows. A parenthesized lambda argument follows its own rule instead
(see [Parentheses](#parentheses)) and stays glued to the function name even
when its body wraps:

```gren
foo xs =
    Array.map (\n -> if n > 0 then
                      n

                  else
                      -n
              ) xs
```

Above, `if` sits wherever `\n ->` happens to end, so `then`/`else` line up under
that column instead of under `if` itself. If the author instead puts a line
break right after `->`, the lambda body — and with it, `if` — drops to its own
line. Now `if` and `else` line up vertically, because `else` indents relative
to `if`'s own column rather than to wherever `if` sits mid-line:

```gren
foo xs =
    Array.map (\n ->
            if n > 0 then
                n

            else
                -n
        ) xs
```

---

### Parentheses

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

### Function body

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

### Blank lines between declarations

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

### Type aliases

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

### Custom types

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

---

### Ports

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

---

### Infix operator declarations

An `infix` declaration is always written on one line:

```gren
infix right 5 (++) = append
```

---

### Records

#### Record values

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

#### Record updates

A single-field update stays inline when you wrote it that way:

```gren
withDefault r =
    { r | x = 0 }
```

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

#### A lambda whose body is a forced-vertical record, update, or array drops it to its own line

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

An `if`, `when`, or `let` body is not covered by this rule — it always keeps
its keyword glued to `->`, managing its own body indentation separately (see
[Lambdas](#lambdas)).

#### Record field values

A field value that is itself a **lambda** keeps the `\args ->` header on the
name's line; the body goes on its own line 4 spaces under the field — never 8:

```gren
parser =
    { parseFn = \args ->
        if Array.length args == 0 then
            Ok {}

        else
            Err WrongArity
    , label = "parser"
    }
```

A short lambda body stays inline:

```gren
{ increment = \v -> v + 1 }
```

When the field value is an **`if`**, **`when`**, or **`let`**, those
constructs drop to the next line so their aligned keywords line up 4 spaces
under the field name:

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
        , parseFn = \args ->
              if Array.length args == 0 then
                  Ok {}

              else
                  Err WrongArity
    }
```

#### Record types and extensible records

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

### Array literals

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

A comment between items forces the vertical layout and sits between the items:

```gren
[ firstItem
-- a comment between items
, secondItem
]
```

Records and arrays inside an array each decide their own layout independently,
following the same author-layout rules.

---

### String literals

A regular string is left as written, with its escape sequences intact:

```gren
greeting =
    "Hello, World!"


withEscapes =
    "line one\nline two\t!\\"
```

#### Character literals

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

#### Multi-line (triple-quoted) strings

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

### If expressions

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

### When expressions

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

### Let expressions

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

### Patterns as arguments

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

### Lambdas

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

Passed as an argument, a lambda is wrapped in parentheses:

```gren
doubleAll =
    Array.map (\n -> n * 2) nums
```

---

### Pipelines

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

**`<|` pipelines** use a trailing-operator style, each step body indented 4
spaces from the seed:

```gren
result =
    String.toUpper <|
        String.append "Greetings, " <|
        String.append name "!"
```

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

### Binary operators

A chain of operators follows your layout.

Written on one line, it stays on one line:

```gren
area =
    width * height + margin

greeting =
    "Hello, " ++ firstName ++ " " ++ lastName
```

Written across rows, it breaks at the **lowest-precedence operators** in the
chain, keeping tighter-binding sub-terms together. The operator leads each
continuation line, indented 4 spaces from the seed:

```gren
score =
    baseScore
        + bonusPoints * multiplier
        - penaltyAmount
```

`bonusPoints * multiplier` stays together because `*` binds tighter than `+`
and `-`.

When every operator has the same precedence, all of them split:

```gren
greeting =
    "Hello, "
        ++ firstName
        ++ " "
        ++ lastName
```

With multiple precedence levels, only the loosest splits:

```gren
eligible =
    isAdministrator
        || hasElevatedRole && accountIsActive == True
        || isOwner
```

A chain with a comment in it uses the fill-style flow renderer instead of the
precedence-aware one (the comment can't be reordered to fit the structure).

The same precedence-aware layout applies to a stacked `if` condition (see
[If expressions](#if-expressions)).

---

### Comments

The formatter **never changes the text of a comment.** It only decides where
the comment sits relative to the code around it.

#### Where you put a comment is meaningful

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

#### Single-line comments (`--`)

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

#### Block comments (`{- ... -}`)

A short block comment inside an expression stays inline:

```gren
foo a =
    a * {- inline note -} 100
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

**Want the body kept verbatim? Put `{-` alone on its first line.** That is
the signal for *verbatim mode*: every body line keeps its exact columns. Only
the `{-` itself moves. This protects ASCII art and hand-aligned tables:

```gren
-- you wrote (the {- alone on its line):
config =
        {-
      this body is kept verbatim
         /\
        /  \
       /____\
-}
        42

-- formats to (only the {- moved to column 4; body untouched):
config =
    {-
      this body is kept verbatim
         /\
        /  \
       /____\
-}
    42
```

##### Comments in an effect module's header

An effect module's `where { … }` block — the `where`, the braces, the field
name, the `=` — carries no position information from the parser. Only the
handler name (e.g. `MyCmd`) has a known position. A comment's placement is
therefore judged by how close it sits to that name.

Right next to the name, the comment stays with it:

```gren
-- you wrote:
effect module MyModule where { command = MyCmd {- note -} } exposing (..)

-- formats to:
effect module MyModule where
    { command = MyCmd {- note -}
    } exposing (..)
```

Wider spacing severs the link and the comment lands below the module line:

```gren
-- you wrote (only more spaces before the comment):
effect module MyModule where { command = MyCmd      {- note -} } exposing (..)

-- formats to:
effect module MyModule where { command = MyCmd } exposing (..)

{- note -}
```

#### Doc comments (`{-| ... -}`)

A doc comment sits directly above the declaration it documents with no blank
line between them. A module doc comment comes right after the module line:

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

#### Blank lines around comments

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

#### A comment at the *end* of something

A comment's **indentation** determines which declaration it belongs to. Indented
like the code above it, it belongs to that code and stays there. At the left
margin, it introduces what comes next:

```gren
-- indented: part of the chain above
total =
    leftComponent
        ++ rightComponent
        ++ trailingValue
        {- still part of the chain -}
```

```gren
-- at the margin: belongs to whatever comes next
total =
    leftComponent
        ++ rightComponent
        ++ trailingValue
{- about the next declaration -}
```

A blank line always cuts the comment loose regardless of indentation.

Two spots are handled specially: a comment at the end of a **type signature**
or the **module line** always moves to the left margin, even when it would
have fit inline:

```gren
-- you wrote:
foo : Int -> Int {- about foo -}
foo n =
    n

-- formats to:
foo : Int -> Int
{- about foo -}
foo n =
    n
```

#### A trailing comment on a `when` branch body

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

#### When the formatter can't tell what you meant

Some tokens — `=`, `:`, `|`, an import's `as` and alias name — are parsed and
then discarded, leaving no position in the AST. A comment beside one of these
is always placed on **one canonical side**, so two programs that differ only in
which side of such a token a comment sits on format to the *same* output.

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

A comment around a union `|` always lands **after the variant before it**:

```gren
-- both of these:
type T = A {- c -} | B
type T = A | {- c -} B

-- format to:
type T
    = A {- c -}
    | B
```

A comment around an import's `as` always lands **before** it:

```gren
-- both of these:
import Foo {- c -} as Bar
import Foo as {- c -} Bar

-- format to:
import Foo {- c -} as Bar
```

---

### Idempotency

Formatting already-formatted code produces exactly the same code back. Format
once or ten times — same result.

A torture test inserts a block comment into every inter-token gap of every
fixture file, formats twice, and requires byte-identical output. It currently
finds **zero** non-idempotent gaps across the whole test corpus.

---

### Known limitations

#### A compiler bug with field access on a record-update base

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

#### Wide `when` branch patterns

A `when` branch whose record (or array) pattern is too wide to fit on one line
can wrap in a way the Haskell-based Gren compiler rejects:

```gren
-- the formatter may produce:
        { aPopped = Just { first = m1, rest = ms1rest }
        , bPopped = Just { first = m2, rest = ms2rest }
        } ->
```

The Haskell compiler requires every continuation line of a pattern to be
indented deeper than the pattern's first character, and this layout doesn't
satisfy that. Compiling the formatted file may fail with "I was expecting to
see a closing curly brace next." Rejoin the pattern onto one line by hand
until this is resolved.

#### Comment placement near invisible tokens

As described in [When the formatter can't tell what you meant](#when-the-formatter-genuinely-cant-tell-what-you-meant), a comment beside `=`, `:`, `|`, or an import's `as` always snaps to one
canonical side. Two different intents produce the same output.

A comment at the end of a type signature or module line always moves to the
left margin, even when it would have fit.

#### A line break inside a declaration's head

A line break *inside* a declaration's keyword (e.g. `import` on one line,
the module name on the next) can cause a blank line to appear between a
comment and that declaration. The root cause is a parser bug that records the
wrong line number for keyword-led declarations:
[compiler-common#25](https://github.com/gren-lang/compiler-common/issues/25).

#### Comments near an effect module's `where` block

As described in [Comments in an effect module's header](#comments-in-an-effect-modules-header),
a comment's placement near the `where { … }` block is determined by proximity
to the handler name. Changing the spacing can change where the comment ends up.
This stays as-is until the parser records positions for the missing tokens.

#### A comment right after `exposing` doesn't sort with the first name

As described in [Exposed names sort automatically](#exposed-names-sort-automatically),
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
[Exposed names sort automatically](#exposed-names-sort-automatically).

#### Verbatim block comment bodies

When a multi-line block comment opens with `{-` alone on its first line, the
body is kept verbatim — columns are not canonicalized. Two inputs that differ
only in the body's indentation format to two different outputs. This is
deliberate (it protects ASCII art and aligned tables), but it means this class
of comment is not whitespace-canonical.

---

### Comparison with elm-format

Gren is a spiritual descendant of Elm, so `gren format` and `elm-format` should agree on
shared syntax unless there's a deliberate reason not to. We ran an audit on the
formatter's own test fixtures (`gren-format-lib/tests/testfiles/Formatter/`),
converting the Gren code to Elm, ran them through
`elm-format` and catalogued every divergence. Each finding below
records the decision made and why.

1. **Blank lines: comment-attached vs. declaration-attached — keep as is.**
   elm-format always puts its 2-blank-line separator immediately above the
   declaration itself, splitting a leading comment away from the code it
   documents. gren-format treats the comment as part of the declaration's
   group and puts the 2 blank lines before the comment instead (see
   [Blank lines around comments](#blank-lines-around-comments)). Keeping the
   comment glued to its declaration is the more useful behavior for a doc
   comment or an explanatory note — splitting them apart the way elm-format
   does would be a regression, not a fix.

2. **Doc/block comment closing `-}` placement — keep as is.** elm-format
   always puts a multi-line comment's closing `-}` on its own line, even when
   the body is one short line. gren-format keeps the closer glued to the last
   content line when it fits, and otherwise follows the comment's own
   structure (see [Comments](#comments)). This is consistent with gren-format's
   broader "your line breaks are your layout decisions" philosophy; elm-format's
   rule is a fixed convention, not obviously better.

3. **Exposing list sorting — changed to match; import statement sorting —
   changed, but narrower.** gren-format now alphabetizes every
   `exposing ( ... )` list the same way elm-format does — operators, then
   types, then values, alphabetically within each group. See
   [Exposed names sort automatically](#exposed-names-sort-automatically).
   `import` statements sort alphabetically too, but — unlike elm-format,
   which always alphabetizes the whole `import` block regardless of the
   author's spacing — gren-format only sorts within a run of imports that
   sit with nothing between them; a blank line or a comment on its own
   line is a boundary the sort never crosses. See
   [Import statements sort within unbroken runs](#import-statements-sort-within-unbroken-runs).

4. **`import X exposing (...)` wrapping style — changed.** gren-format used to
   keep `import Dict exposing` together with the list indented +4. It now
   matches elm-format: `import Dict` alone on the first line, `exposing` on
   its own line indented +4, and the list indented +8. See
   [Import statements](#import-statements) for the new canonical shape.

5. **Type-signature wrapping when a comment is present — changed.** A
   multi-row signature now always uses the canonical per-`->`-segment vertical
   layout (see [Type signatures](#type-signatures)), even when it contains a
   comment. The old special-case fallback to a fill-style flow renderer for
   comment-bearing signatures is gone — comments no longer produce a
   differently-shaped signature than a comment-free one would.

6. **Union type declarations always stack one variant per line in
   elm-format — keep as is.** Even when the author wrote
   `= Red | Green | Blue` on one line and it fits, elm-format always splits to
   one `| Variant` per line — contradicting elm-format's own general
   "respects the author's newlines" design. gren-format's author-driven rule
   (see [Custom types](#custom-types)) is preferred and stays.

7. **Record patterns (destructuring) aren't author-driven in
   elm-format — keep as is.** elm-format collapses a multi-line record/array
   *pattern* back to one line if it fits, even overriding an embedded comment
   that forces gren-format to stay vertical. Same reasoning as #6: gren-format's
   consistent author-driven layout is preferred.

8. **`where { ... }` effect-module clauses collapse to one line in
   elm-format — keep as is.** Likely a corollary of the same
   multiline-tracking gap as #6/#7 rather than a deliberate elm-format rule.
   gren-format's behavior (see
   [Comments in an effect module's header](#comments-in-an-effect-modules-header))
   stays.

9. **Verbatim literal preservation vs. normalization — keep as is.**
   elm-format normalizes scientific-notation floats (`1e5` → `1.0e5`,
   `1.5E3` → `1.5e3`, `1.5e+3` → `1.5e3`), uppercases `\u{...}` hex escapes,
   expands named escapes like `\r` to `\u{000D}`, and drops unnecessary `\"`
   escaping inside triple-quoted strings. gren-format deliberately preserves
   the author's exact original literal spelling (see
   [String literals](#string-literals)) — this was already a considered
   design choice, not an oversight.

10. **Redundant parens around a call argument — fixed.** `node "div"
    ({ foo = 1, bar = 2 }) []` kept the author's parens verbatim around the
    record literal, even though they're unnecessary for a record literal in
    argument position. The formatter now strips a `Parens` wrapper around an
    argument expression when the wrapped expression doesn't need parens to
    parse unambiguously in that position (see
    [Function application](#function-application)). Covered by new fixtures
    `RedundantArgParens.dirty.gren` / `.formatted.gren`.

#### Minor/cosmetic — not acted on

- `infix left  6 (+) = add` — elm-format inserts a double space after `left`;
  looks like an elm-format quirk on a construct Elm 0.19 itself no longer
  supports at the compiler level. Not worth matching.
- A handful of comment-attachment micro-differences around pipeline steps,
  binop operands, and lambda arrows/`in` — elm-format sometimes pushes a
  trailing comment to its own line where gren-format keeps it inline, or vice
  versa. These are construct-specific and would need one-off matching rather
  than a single rule; left as is for now.

#### Out of scope for comparison

Some fixtures use Gren syntax with no valid Elm equivalent, so they can't be
mechanically translated and run through `elm-format` at all:

- A record-update base that's a parenthesized call or a dotted field-access
  chain (`{ (someTransform base) | ... }`, `{ model.sub | ... }`) — Elm's
  grammar only allows a bare variable there.
- Gren's record-pattern field-renaming syntax, `{ field = alias }` in pattern
  position (e.g. `Just { endpoint = sinkEndpoint } ->`) — Elm patterns only
  support bare `{ field }`. `elm-format` hard-errors on this construct (or, if
  the renamed identifier looks like a wildcard such as `_x`, silently
  mis-parses it into two separate patterns instead of erroring) — so this
  whole class of fixtures is fundamentally outside the scope of an
  elm-format comparison.
