# Gren Formatter Rules

A guide to how `gren format` lays out your code — what it changes, what it
leaves alone, and why.

This document is meant to be read start to finish if you're new to Gren, or
dipped into by construct if you're looking for one specific rule. Every rule
comes with an example, because a sample of before/after is usually faster to
understand than a paragraph.

---

## The big idea

Most code formatters impose a single "correct" style: you write your code
however you like, run the formatter, and it rewrites everything into one fixed
shape. The Gren formatter is a little different. For many constructs it
**follows your layout choices** instead of overriding them.

The guiding rules are:

1. **If it fits on one line, and you wrote it on one line, it stays on one
   line.** The page is 80 columns wide. Short things stay short.

2. **If you spread something across several lines, the formatter keeps it
   spread** — one item per line — even if it *would* have fit on one line. The
   formatter reads a line break between two items as a signal that you wanted
   the vertical shape, and it respects that.

3. **If something is too long to fit on one line, the formatter breaks it** —
   and when it breaks, it goes *all the way*: every item on its own line. There
   is no "some items on this line, some on the next" half-and-half shape
   (with one deliberate exception: function call arguments, which fill the line).

4. **The formatter never changes what your code means.** It only moves
   whitespace around. It never rewrites an expression, reorders anything, or
   edits the text inside a comment or a string.

5. **Formatting is stable.** Running the formatter on already-formatted code
   produces exactly the same code back. Formatting twice is the same as
   formatting once. (There are a few known exceptions involving comments in
   tricky spots — see [Known limitations](#known-limitations-idempotency-gaps)
   at the end.)

So when you see a construct described below as following "your layout," it means
this: write it on one line to get the compact form, or put a line break between
its items to get the one-per-line form. Both are valid; the formatter keeps
whichever you chose (collapsing the compact one only if it's too wide to fit).

A few house rules that apply everywhere:

- **Indentation is 4 spaces.** Always spaces, never tabs.
- **The target page width is 80 columns.**
- The style descends from the [Elm Style Guide](https://elm-lang.org/docs/style-guide)
  and [elm-format](https://github.com/avh4/elm-format), tuned to keep diffs
  between commits small.

---

## Module declaration

The `module` line stays on one line when the whole thing fits. The `exposing`
list is written with a space just inside each parenthesis:

```gren
module MyApp exposing ( Model, Msg, init, update )
```

The list is **all-or-nothing**: when the line is too long, the keyword stays put
and every export goes on its own line, with `, ` before each one after the first
and the closing `)` alone on the last line. There is no in-between form where the
whole list drops to one indented line:

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

The wildcard form `exposing (..)` is left exactly as written.

---

## Import statements

A plain import stays on one line:

```gren
import Array
```

An alias uses `as`, and an `exposing` list follows the same all-or-nothing shape
as the module line — all on one line, or one export per line:

```gren
-- fits on one line
import String exposing ( fromInt, toInt )

-- alias and exposing together
import Array.Extra as AE exposing ( filterMap, unique )

-- too long: the keyword stays, every export gets its own line
import MyModule exposing
    ( AlphaType
    , BetaType
    , gammaFunction
    , deltaFunction
    , epsilonValue
    )
```

---

## Type signatures

A type signature follows your layout, just like a list. Written on one line, it
stays on one line when it fits:

```gren
add : Int -> Int -> Int

applyTwice : (a -> a) -> a -> a
```

When a one-line signature is too long, it breaks into an **all-or-nothing**
shape: the `:` moves to the end of the first line, and every `->`-separated part
goes on its own line, indented 4 spaces, with the `->` leading each line:

```gren
processItems :
    Array String
    -> Dict String Int
    -> (String -> Bool)
    -> Array String
    -> Result String (Array String)
```

If you *wrote* the signature across several lines, the formatter keeps it that
way even when it would have fit on one line:

```gren
-- kept multi-line because that's how it was written
keptMultiLine :
    Int
    -> Int
    -> Int
```

(A line break right after the `:` with everything else still on one line does
not count — only a break *between* the `->` parts makes it multi-line.)

One exception: a signature that contains a comment keeps the older "fill"
wrapping (it fills each line and wraps to the next as needed), because the
all-or-nothing break points don't give a comment a stable home. So instead of
each `->` part going on its own line, the parts pack onto a line and wrap only
when they run out of room, with the comment left inline:

```gren
update : Msg -> Model -> {- returns new state -} Result Error Model -> Cmd Msg
    -> Thing
```

---

## Function application

When you call a function, the arguments go on the same line as the function
name. If they don't all fit, the overflow continues on the next line, indented
4 spaces from the function name. Arguments **fill** the line — this is the one
place there is no "one item per line" shape:

```gren
-- fits on one line
result = foo a b c

-- overflows: the extra arguments wrap, indented +4 from the function name
result =
    someFunction firstLongArgument secondLongArgument thirdLongArgument
        fourthLongArgument fifthLongArgument
```

---

## Function body

When a function has a type signature, its body always starts on the next line,
indented 4 spaces — even a tiny body:

```gren
double : Int -> Int
double n =
    n * 2

version : String
version =
    "1.0.0"
```

When there's **no** type signature, a short body stays on the same line as the
name and `=`:

```gren
double n = n * 2

version = "1.0.0"

makePoint x y = { x = x, y = y }
```

A body that's too long, or that is inherently multi-line (an `if`, `let`, or
`when`), always starts on its own line indented 4 spaces:

```gren
findPreferredLanguage languages =
    Array.keepIf (\lang -> Array.member lang supported) languages

classify n =
    when n is
        1 ->
            "one"
        _ ->
            "other"
```

---

## Blank lines between declarations

Two blank lines appear before every top-level function (the line with the type
signature, if there is one). This gives each function clear breathing room:

```gren
double : Int -> Int
double n =
    n * 2


square : Int -> Int
square n =
    n * n
```

A comment written directly above a function (no blank line between them) is
treated as belonging to that function — the two blank lines go *above* the
comment, not between the comment and the function:

```gren
-- Area of a square
squareArea : Int -> Int
squareArea side =
    side * side
```

A comment separated from a function by a blank line is treated as "floating"
(not attached to anything) and keeps a single blank line before it. The function
below still gets its usual two blank lines, so the comment sits apart from both:

```gren
double : Int -> Int
double n =
    n * 2

-- A floating note, kept at arm's length from square below


square : Int -> Int
square n =
    n * n
```

---

## Type aliases

A `type alias` always takes two lines — the header, then the aliased type
indented 4 spaces — even if the whole thing would fit on one line:

```gren
type alias Id = Int
```

becomes

```gren
type alias Id =
    Int
```

When the aliased type is a record, it follows your layout exactly like a record
value (see [Records](#records)): on one line if you wrote it that way and it
fits, otherwise one field per line.

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

A custom type (`type`) puts its name on the first line and the variants on the
next, after `=`, with `|` before each one. The variants follow your layout:
written on one line they stay inline when they fit; written across rows — or when
they overflow — each variant goes on its own line:

```gren
type Color
    = Red | Green | Blue


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

A variant's payload sits on the same line as the variant name, space-separated:

```gren
type Shape
    = Circle Int
    | Rectangle Int Int
```

---

## Ports

A port is written on one line: the `port` keyword, the name, `:`, and the type:

```gren
port outgoing : String -> Cmd msg

port incoming : (String -> msg) -> Sub msg
```

A long port type follows the same all-or-nothing rule as a function signature —
the `:` moves to the end of the first line, and each `->` part goes on its own
line:

```gren
port sendThings :
    VeryLongArgumentType
    -> AnotherArgumentType
    -> ResultOfSending
    -> Cmd msg
```

---

## Infix operator declarations

An `infix` declaration is written on one line: the keyword, the associativity
(`left`, `right`, or `non`), the precedence, the operator in parentheses, `=`,
and the function that implements it:

```gren
infix right 5 (++) = append
```

---

## Records

### Record values

An empty record is always `{}`.

A record follows your layout:

- Written on one line and it fits → stays inline: `{ x = x, y = y }`.
- Written on one line but too long → one field per line.
- Written across rows → one field per line, even if it would have fit.

A record with only one field has no "between fields" gap, so it always collapses
to one line (when it fits):

```gren
makePoint x y = { x = x, y = y }

config =
    { name = "app"
    , version = 2
    }
```

The one-field-per-line shape puts `{` and the first field on the first line, a
`, ` before each later field, and `}` alone on the last line. A record passed as
a function argument follows the same rule:

```gren
firstX = distSq { x = 0, y = 0 } { x = 1, y = 0 }
```

### Record updates

An empty-style update or a single-field update stays inline when it fits:

```gren
withDefault r = { r | x = 0 }
```

A two-or-more-field update follows your layout, like a record value. Inline it
reads `{ base | a = 1, b = 2 }`. When it breaks, the base name stays on the first
line after `{`, the first field goes on the next line with a `| ` prefix, later
fields line up under it with `, `, and `}` closes on its own line:

```gren
setOrigin pt = { pt | x = 0, y = 0 }

movePoint dx dy pt =
    { pt
        | x = pt.x + dx
        , y = pt.y + dy
    }
```

### Record field values

A field value that fits stays on the same line as its name. One that's too long
drops to the next line, indented 4 spaces:

```gren
wrapsToNextLine =
    { model =
        { model
            | searchLang = lang
            , searchText = ""
        }
    , command = Cmd.none
    }
```

### Record types and extensible records

A record *type* in a signature follows the same layout rules. An extensible
record type `{ r | field : Type }` keeps the base variable and `|` with the
first field:

```gren
-- fits on one line
getName : { r | name : String } -> String

-- breaks: the base variable rides with the first field, the rest align under it
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

A non-empty array is **all-or-nothing**: either everything fits on one line, or
every item goes on its own line. There is no partial wrapping, and your layout
chooses between the two:

- Items on one line that fit → stay on one line.
- Items on one line that are too long → one per line.
- Items spread across rows (even several per line, like `[ 1, 2\n, 3, 4 ]`) →
  one per line, regardless of fit.

The flat form has a space just inside the brackets:

```gren
[ 1, 2, 3 ]
```

The vertical form puts `[` and the first item together, a `, ` before each later
item, and `]` alone on the last line:

```gren
[ "first"
, "second"
, "third"
]
```

Records inside an array stay inline as long as each one fits on its own line:

```gren
[ { label = "first", value = 1 }
, { label = "second", value = 2 }
]
```

When an item is itself a record (or array) that has to break, its inner lines are
indented 2 extra spaces, so its fields line up under its own `{` rather than
under the array's `, `:

```gren
[ { veryLongFieldNameAlpha = valueAlpha
  , veryLongFieldNameBeta = valueBeta
  }
, { veryLongFieldNameAlpha = valueAlpha2
  , veryLongFieldNameBeta = valueBeta2
  }
]
```

A comment between items forces the vertical layout and sits between the items:

```gren
[ firstItem
-- a comment between items
, secondItem
]
```

---

## String literals

A regular string is left as written, with its escape sequences intact:

```gren
greeting = "Hello, World!"

withEscapes = "line one\nline two\t!\\"
```

### Character literals

A character uses single quotes. The five special characters are always written
as escapes; everything else is written as the plain character:

```gren
tab            = '\t'
newline        = '\n'
carriageReturn = '\r'
singleQuote    = '\''
backslash      = '\\'
letter         = 'a'
```

### Multi-line (triple-quoted) strings

A `"""` string always stays in triple-quoted form. The opening `"""` goes at the
end of the line (or on its own indented line when the value wraps), the content
lines sit at the same indentation as the delimiters, and the closing `"""` goes
on its own line at that same indentation:

```gren
message =
    """
    Hello, World!
    """

poem =
    """
    line one
    line two
    line three
    """
```

The content lines are always re-indented to line up with the `"""` delimiters,
no matter how much leading whitespace the original had. (The *text* of each line
is never changed — only the common indentation in front of it.)

---

## If expressions

Every branch body starts on the next line, indented 4 spaces. There is no inline
form, even for a one-word body. `else` lines up with `if`:

```gren
if n > 0 then
    "positive"
else
    "non-positive"
```

`else if` is written as one unit, on one line with its condition:

```gren
if n < 0 then
    "negative"
else if n == 0 then
    "zero"
else
    "positive"
```

When the whole `if … then` doesn't fit on one line, it stacks: `if` goes on its
own line, the predicate drops to the next line indented 4 spaces, and `then` goes
on its own line flush with `if`. The branch body then follows, indented 4 spaces
under `then`.

The predicate itself is **filled**: as much as fits stays on the first line, and
only the overflow spills onto a continuation indented one more level, with the
operator leading each wrapped line:

```gren
if
    userIsActive && accountHasCredit && not isSuspended && withinQuota
        && verified
then
    showDashboard x
else
    showLoginPage x
```

It's all-or-nothing at the `if … then` level: either the whole thing fits on one
line, or it takes the stacked form above — there's no in-between where the
predicate wraps but `then` stays glued to its last line. The same applies to
`else if`.

> **Open decision — how the stacked predicate wraps.** The predicate currently
> *fills* (packs onto the line, overflow spilling to an indented continuation,
> shown above). The alternative is **precedence-aware** wrapping — the way binops
> break everywhere else in Gren: break only at the lowest-precedence operators,
> one group per line —
>
> ```gren
> if
>     userIsActive
>         && accountHasCredit
>         && not isSuspended
>         && withinQuota
>         && verified
> then
>     showDashboard x
> ```
>
> This is implementable (it would render the predicate with the precedence-aware
> binop layout rather than a filled flow). The trade-off is the mirror image of
> today's: fill keeps an `if` predicate compact but wraps it *differently* from
> how the same expression wraps anywhere else; precedence-aware wraps it
> consistently but takes more vertical space. **This has not been decided.**
>
> **Action item:** decide whether a stacked `if`/`else if` predicate should wrap
> fill-style (current) or precedence-aware, and make it consistent.

---

## When expressions

A branch body always goes on the next line, indented 4 spaces from the pattern —
even a tiny one that would fit beside the `->` — and a blank line always
separates one branch from the next:

```gren
when n is
    1 ->
        "one"

    2 ->
        "two"

    _ ->
        "other"
```

The blank line is uniform: it doesn't matter whether a body is a single value or
a multi-line block — a branch whose body is a record just looks taller, with the
same blank line before the next branch:

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

---

## Let expressions

`let` and `in` line up at the same indentation. Bindings are indented 4 spaces
under `let`, and the result expression starts on the line after `in`, back at the
`let`/`in` level:

```gren
circleArea radius =
    let
        pi = 3.14159
        rSquared = radius * radius
    in
    pi * rSquared
```

A binding value that fits stays on the same line as its name and `=`; one that's
too long, or inherently multi-line, drops to the next line indented 4 more
spaces:

```gren
complexBody =
    let
        command =
            if condition then
                doThis
            else
                doThat
    in
    command
```

You can destructure on the left of a binding with the same record/array pattern
syntax used elsewhere:

```gren
let
    { model, command } = update msg model
in
model
```

---

## Lambdas

A lambda starts with `\` directly before the first pattern (no space), then any
further patterns, then `->`, then the body:

```gren
double = \n -> n * 2

add = \a b -> a + b
```

Passed as an argument, a lambda is wrapped in parentheses:

```gren
doubleAll = Array.map (\n -> n * 2) nums
```

Patterns can be variables, record destructures, or array destructures:

```gren
Array.map (\{ start, end } -> end - start) ranges

Array.foldl (\{ value } acc -> acc + value) 0 items
```

When the lambda doesn't fit, the body wraps to the next line indented 4 spaces
from the `\`. The `->` always stays at the end of the parameter line:

```gren
transform =
    \veryLongParameterName ->
        veryLongParameterName * 2 + someOtherValue + anotherValue
```

---

## Pipelines

Both `|>` (forward) and `<|` (backward) are pipeline operators. A run of the
*same* operator is treated as one pipeline; a chain that mixes `|>` and `<|` is
not merged into one.

A pipeline follows your layout. Written on one line, it stays on one line when it
fits:

```gren
result = list |> Array.map double |> Array.first

result = String.toUpper <| String.append "Hello, " name
```

Written across rows, it stays one step per line even if it would have fit:

```gren
result =
    list
        |> Array.map double
        |> Array.first
```

When a one-line pipeline is too long, each step goes on its own line, indented 4
spaces from the seed, with the operator leading each line:

```gren
result =
    nodes
        |> Array.map double
        |> Array.keepIf isValid
        |> Array.first
```

A comment just before a pipeline step travels with that step:

```gren
result =
    list
        -- keep only the valid ones
        |> Array.keepIf isValid
        |> Array.map double
```

---

## Binary operators

A chain of operators (`a + b`, `x && y`, `s ++ t`) follows your layout. Written
on one line, it stays inline when it fits:

```gren
area = width * height + margin
```

When a one-line chain is too long — or you wrote it across rows — it breaks in a
**precedence-aware** way: it only breaks at the operators that bind *loosest*,
and keeps the tighter-binding parts together on a line. (Precedence is the usual
arithmetic idea: `*` binds tighter than `+`, `&&` tighter than `||`, and so on.)

```gren
score =
    baseScore
        + bonusPoints * multiplier
        - penaltyAmount
        + streakBonus * weight
```

Notice `bonusPoints * multiplier` stays on one line: the chain split at `+` and
`-` (the loosest operators here), and the `*` parts came along for the ride.

When every operator has the same precedence, the chain breaks at all of them:

```gren
greeting =
    "Hello, "
        ++ firstName
        ++ " "
        ++ lastName
```

With three or more precedence levels, only the loosest level splits:

```gren
eligible =
    isAdministrator
        || hasElevatedRole && accountIsActive == True
        || isOwner
```

Two situations keep the older fill-style wrapping instead of this shape: an
`if`/`else if` condition (so the first part stays beside `if` rather than the
whole condition dropping below a lone `if`), and any chain that contains a
comment (`a + {- note -} b`).

---

## Comments

The formatter **never changes the text of a comment.** It only decides where the
comment sits relative to the code around it. This section covers how it makes
that decision, and a few spots where the decision is genuinely hard.

### Where you put a comment is meaningful

Whether a comment **shares a line with the code before it** or **sits on its own
line** is treated as a real, deliberate choice — the formatter keeps whichever
you wrote, and never converts one into the other:

```gren
foo = 1 {- inline: stays on the value's line -}

bar =
    { a = 1
    {- standalone: stays on its own line, before the close -}
    }
```

A practical consequence: adding or removing the line break between a comment and
the token next to it genuinely changes your program's layout, so the two
versions are different inputs that format differently. This is intended — the
comment's position carries meaning.

### Single-line comments (`--`)

A `--` comment on a line of code stays on that line:

```gren
import Dict exposing
    ( Dict
    , empty -- a comment on the same line as empty
    )
```

A `--` comment on its own line stays on its own line, indented to match the code
around it:

```gren
foo a =
    -- before the first line of the body
    a * 100
```

### Block comments (`{- ... -}`)

A short block comment inside an expression stays inline:

```gren
foo a = a * {- inline note -} 100
```

A block comment whose body spans several lines forces the construct around it to
break vertically (it can't be collapsed onto one line). When every body line is
indented at or to the right of the `{-`, the inner lines are re-indented to line
up neatly under the `{-`, while keeping the comment's own internal shape
(relative indentation, lists, little diagrams):

```gren
value =
    items
        {- this comment spans
           three lines and keeps
           its shape -}
        |> process
```

**The body is never moved left.** If any body line sits *to the left* of the
`{-` — flush against the margin, or as part of a diagram that reaches further
left than the opener — the formatter can't slide the comment left to line the
`{-` up with the construct without dragging that line off the page or distorting
the picture. So it does the opposite: it lifts the `{-` onto its own line at the
construct's indent and leaves **every line of the body exactly where you wrote
it**, column for column. Only the `{-` moves.

```gren
-- you wrote (the {- is indented, the body is flush-left):
config =
        {- this comment opener is indented
but the body lines are written flush at the left margin
   and this one is a little deeper -}
        42

-- the formatter produces (only the {- moved — to column 4; no body text moved):
config =
    {-
           this comment opener is indented
but the body lines are written flush at the left margin
   and this one is a little deeper -}
    42
```

This is deliberate: it means hand-drawn ASCII art, tables, or any
carefully-aligned block inside a comment survives formatting untouched. The cost
is that the body's whitespace is *not* canonicalized — see the note under
[whitespace canonicalization gaps](#a-multi-line-block-comments-body-is-kept-verbatim).

### Doc comments (`{-| ... -}`)

A doc comment sits directly above the declaration it documents, with no blank
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

### Blank lines around comments

Whether a blank line sits between a comment and an adjacent declaration mirrors
your source: if you left no blank line, none is added; if you left one, one is
kept.

### A comment at the *end* of something

A comment you write *after the last token of a construct*, right where the next
thing is a sibling at a shallower indent, is placed at that shallower indent on
its own line — not tucked at the construct's deeper indent. Here a comment after
a multi-line signature lands at column 1, leading the definition, rather than at
the type's 4-space indent:

```gren
foo :
    Int
    -> Int
{- a note -}
foo n =
    n
```

Why not keep it tucked in beside the type it follows? Because that placement
isn't stable. If the formatter *did* produce the deeper form —

```gren
foo :
    Int
    -> Int
    {- a note -}
foo n =
    n
```

— then re-formatting it would pull the comment straight back out to column 1: the
comment sits on its own line one row past the signature, where it reads as a
leading comment of the definition. So the deep form isn't a fixed point — it
changes the next time you format. The shallow form re-formats to itself, so the
formatter commits to the shallow (column-1) spot up front and stays there.

By contrast, a comment that is genuinely *inside* a construct stays inside it. A
comment before a closing bracket stays in the container:

```gren
[ 1
, 2 {- a note -}
]
```

### A trailing comment on a `when` branch body

A block comment written at the end of a `when` branch body is given special
handling so it stays on a stable row, in one of two ways depending on whether
another branch follows.

If it's the **last** branch, the comment is glued to the body's last line — even
when that runs the line past the page width:

```gren
describe x =
    when x is
        Wrapped value ->
            firstComponent
                ++ secondComponent
                ++ thirdComponentThatPushesThisPastEighty {- a trailing note that runs well past the page width here -}
```

If **another branch follows**, the comment is lifted onto its own line at the
branch (pattern) indent — lined up with the patterns, *not* with the body — so it
reads as belonging between the two branches:

```gren
describe x =
    when x is
        Wrapped value ->
            firstComponent
                ++ secondComponent
                ++ thirdComponentThatPushesThisPastEighty
        {- a trailing note that runs well past the page width here -}

        _ ->
            "other"
```

(The comment stays with the branch it trails; the usual blank line then separates
that branch from the next.)

Either way the comment lands somewhere it re-parses to the same place, so it
doesn't drift.

**Why two different rules instead of one?** It's tempting to just glue the comment
to the body's last line in every branch. That works for the last branch and for
simple bodies, but it is *not* idempotent in general for a non-last branch:

- When a non-last branch's body is itself multi-line — an `if`, a `let`, or a
  long wrapped expression — there's no single clean last line to glue onto. The
  comment ends up at the body's deep indent, and on the next format it sits a row
  past the body, re-attaches outward, and the indent oscillates. Lifting it to the
  between-branches position (aligned with the patterns) is the one placement that
  re-parses to itself, so the formatter lifts *every* non-last branch's trailing
  comment, uniformly.
- The last branch has no following branch to lift above. A lifted comment there
  would dedent all the way to column 1 and read as a free-floating top-level
  comment, detached from the branch it annotates — so it's glued to the body's
  last line instead, which (with nothing after it) is stable.

This anchoring is specific to `when` branches, which have those two stable homes
(between branches, or the last branch's body line). A trailing comment on a plain
function body or a `let … in` result has neither — it's the
[idempotency gap](#known-limitations-idempotency-gaps) described below, where the
comment drifts to the margin on the second format.

### When the formatter genuinely can't tell what you meant

Some pieces of Gren syntax — `=`, `:`, `|`, the `as` keyword and the alias name
after it, and the brackets of a record — are recognized by the parser and then
**thrown away**. They leave no trace in the parsed program. If you put a comment
right next to one of these, the formatter can see the comment is *somewhere in
that gap*, but it has no way to know which side of the (now-invisible) symbol you
meant it to be on.

Rather than guess, the formatter picks **one** canonical spot and always renders
the comment there. So two programs that differ only in which side of one of these
symbols a comment sits on will format to the *same* output. In each case below,
*either* input on the left becomes the single form on the right.

A comment around a signature's `:` always lands **after** the `:`:

```gren
foo {- c -} : Int          -->   foo : {- c -} Int
foo : {- c -} Int          -->   foo : {- c -} Int
```

A comment around a definition's `=` always lands **after** the `=`:

```gren
foo {- c -} = 42           -->   foo = {- c -} 42
foo = {- c -} 42           -->   foo = {- c -} 42
```

A comment around a union `|` always lands **after the variant before it** (so if
you wrote it after the `|`, it moves up to the end of the previous variant):

```gren
-- both of these inputs:
type T = A {- c -} | B
type T = A | {- c -} B

-- format to:
type T
    = A {- c -}
    | B
```

So if you write one of the left-hand forms, expect the formatter to rewrite it
to the right-hand one. This is the one place the formatter is deliberately *not*
faithful to your exact placement — and it's unavoidable, because the information
simply isn't there in the parsed program.

---

## Known limitations: idempotency gaps

"Idempotent" is a fancy word for a simple promise: **formatting already-formatted
code gives you back exactly the same code.** Format once or format ten times —
same result. The formatter holds to this almost everywhere, including a torture
test that jams a comment into *every* gap between tokens in a large file.

There are a small number of known exceptions, all involving block comments
(`{- ... -}`) sitting at awkward spots near the 80-column boundary. In these rare
cases, the *first* format settles the comment in one place, and a *second* format
may nudge it. They don't change what your code means — only exactly where a
comment lands — and they only show up in deliberately adversarial inputs. The
known cases live in a handful of test fixtures and are tracked for a future fix;
ordinary code does not hit them.

For example, a binary-operator chain long enough to overflow, ending in a trailing
block comment that lands on its own line, formats like this the first time — the
comment sitting at the chain's indent:

```gren
joinUp input =
    let
        prefix = sanitize input
    in
    prefix
        ++ rightComponent
        ++ someTrailingValueToPushThisParticularLineOutTowardOneHundredColumns
        {- c -}
```

But formatting *that* output again slides the comment out to the margin (where it
reads as a leading comment of whatever comes next):

```gren
joinUp input =
    let
        prefix = sanitize input
    in
    prefix
        ++ rightComponent
        ++ someTrailingValueToPushThisParticularLineOutTowardOneHundredColumns
{- c -}
```

A third format leaves this second form unchanged — so it settles after one extra
pass.

If you ever notice the formatter producing a slightly different result the second
time you run it on a file, it will be a comment near a line-wrap boundary like
this — and re-running once more will settle it.

---

## Known limitations: whitespace canonicalization gaps

There's a stronger promise the formatter *tries* to keep but doesn't fully reach:
that the output depends only on what your code *means*, not on the incoming
spacing. In other words, if you take a file and mangle its blank lines and
indentation **without** changing what it parses to, re-formatting *should* give
byte-identical output.

This holds for the vast majority of code. The remaining gaps are, again, all
about comments and blank lines — not about regular code. There are two main
families.

### Blank lines near a comment-and-declaration pair

When a comment documents a declaration, the formatter decides whether to keep a
blank line between them by looking at row positions in your source. A line break
injected *inside* the declaration's head can shift that decision, so the blank
line is sometimes added or dropped.

For example, write a comment directly above an import and they stay together —
no blank line:

```gren
-- you write:
-- uses Array
import Array

-- formats to (unchanged):
-- uses Array
import Array
```

Now write the *same* import with its `import` keyword on its own line, above the
module name — identical to the parser. This time the formatter inserts a blank
line, detaching the comment (and the import still rejoins on one line in the
output):

```gren
-- you write (the import head spans two rows now):
-- uses Array
import
    Array

-- formats to:
-- uses Array

import Array
```

The root cause is upstream, in the parser:
[gren-lang/compiler-common#25](https://github.com/gren-lang/compiler-common/issues/25)
— "The wrong row number is assigned to `import`, `type`, `type alias`, and
`port` in the AST". When one of those keywords is separated from its name by a
newline, the declaration node records the *name's* row instead of the
*keyword's*. The formatter reads that row range to decide the blank line, so the
wrapped head looks one row taller than it is and the comment-adjacency test
flips. Once #25 is fixed so the keyword's own row is recorded, the formatter can
make this a pure width-and-adjacency decision and the gap should close.

### A comment's inline-vs-own-line placement flipping

A comment that trails a token is rendered *inline* (on the token's line) when it
shares that token's row, and *on its own line* otherwise — a deliberate,
meaning-bearing distinction (see [Comments](#comments)). The gap is that this can
flip when whitespace *elsewhere* changes how many rows the surrounding construct
spans, even though the comment still trails the same token.

For example, write a record-type field with a trailing comment all on one line,
and the comment stays inline beside the field:

```gren
-- you write:
foo : { a : Int {- trailing the field -} }

-- formats to:
foo :
    { a : Int {- trailing the field -}
    }
```

Now write the *same* signature with the field's `a :` and `Int` on separate
rows — identical to the parser, and the comment still sits right after `Int`.
This time the comment drops to its own line, even though the field itself rejoins
on one line in the output:

```gren
-- you write (note the field spans two rows now):
foo :
    { a :
        Int {- trailing the field -}
    }

-- formats to:
foo :
    { a : Int
    {- trailing the field -}
    }
```

The only difference between the two inputs is whitespace *inside the field* —
yet it moves the comment.

**Why this isn't fixed yet.** The formatter decides inline-vs-own-line by
comparing the comment's row to the row of the item it follows — and there is no
single "row of the item" that is right for every item:

- Use the item's **first** row, and a comment trailing the item's *last* token
  looks like it's on a different row — the bug shown above (the field starts on
  one row, the comment trails `Int` on a later row).
- Use the item's **last token's** row, and that fixes the case above but breaks a
  different one: an item that ends in a multi-line bracketed value has its closing
  `}` (or `]`) on a line *below* its last token. A comment trailing that value
  then glues onto the last token's row in one pass but sits on the bracket's row
  on the next, so it oscillates between the two — trading this gap for an
  *idempotency* gap, which is worse.

The value that would actually be correct is the item's last *rendered* row. But
that row often belongs to a closing bracket the parser discards (`}`, `]`, `)`
carry no position in the AST — see the elided-token cases under
[Comments](#comments)), so it isn't known until after layout, while the
comment-placement decision is made before. Doing this right means making that
decision against post-layout positions (or tracking the synthesized brackets'
rows) — a deeper change than swapping which row the test reads. Until then the
formatter keeps the current rule, which is stable (idempotent) everywhere but
flips this particular case.

### A multi-line block comment's body is kept verbatim

This one is intentional, not a defect — but it has the same observable shape, so
it belongs here. As described in the **Block comments** section above, when a
multi-line `{- ... -}` comment has a body line to the left of its `{-`,
the formatter lifts the `{-` onto its own line and leaves the body **exactly as
written**, column for column, rather than re-indenting it. That protects ASCII
art and hand-aligned blocks.

The consequence is that the body's leading whitespace is no longer canonicalized:
two inputs that differ only in how far the body is indented format to two
different outputs.

```gren
-- one author wrote:
x =
    {-
       diagram line
    -}
    1

-- another indented the body further:
x =
    {-
              diagram line
    -}
    1
```

Both are left untouched, so they stay different. (Formatting is still
idempotent — each output formats back to itself — and the comment's meaning is
unchanged; it's only that the body indentation you chose is preserved rather than
normalized.) This is the deliberate trade for not mangling diagrams.

None of these change the meaning of your code, and none affect code without
comments. They're documented here for completeness and tracked for future work.

---

## A note for contributors

This file is the human-readable contract for what the formatter does. If you
change a formatting rule, update the matching section here with a worked example.
The accompanying test suite (`compiler-node/effectful-tests`) pins every example
to an actual fixture and additionally checks that formatting is idempotent and
preserves the parsed program — so the behavior described here is the behavior
that ships.
