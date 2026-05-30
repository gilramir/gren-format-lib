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
(not attached to anything) and keeps a single blank line before it.

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

When a condition is too long, it wraps to lines indented **8 spaces** — twice the
usual indent, so the wrapped condition stays visually distinct from the 4-space
branch body. The first part stays beside `if`, each operator leads a wrapped
line, and `then` stays on the last line of the condition:

```gren
if model.isAuthenticated
        && model.hasPermission
        && model.featureEnabled && model.subscriptionActive then
    showDashboard model
else
    showLoginPage model
```

---

## When expressions

A branch body always goes on the next line, indented 4 spaces from the pattern —
even a tiny one that would fit beside the `->`:

```gren
when n is
    1 ->
        "one"
    _ ->
        "other"
```

A blank line is inserted between two branches when either branch's body is a
container or block — a record, a record update, a list, or an `if`/`let`/`when`.
(This is based on the *kind* of body, not on whether it wrapped: a record body
gets the blank line even when the record itself is short enough to stay on one
line.)

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

Branches with simple bodies are *not* blank-separated, even though each body sits
on its own line:

```gren
when n is
    1 ->
        "Monday"
    2 ->
        "Tuesday"
    _ ->
        "Unknown"
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
break vertically (it can't be collapsed onto one line). Its inner lines are
re-indented to line up neatly under the `{-`, while keeping the comment's own
internal shape (relative indentation, lists, little diagrams):

```gren
value =
    items
        {- this comment spans
           three lines and keeps
           its shape -}
        |> process
```

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
its own line — not tucked at the construct's deeper indent. (Keeping it deep
would not be stable: on the next format it would drift back out, and the indent
would flip-flop. The formatter commits to the shallower spot every time.)

```gren
foo : Int -> Int
{- a note -}
foo n =
    n
```

By contrast, a comment that is genuinely *inside* a construct stays inside it. A
comment before a closing bracket stays in the container:

```gren
[ 1
, 2 {- a note -}
]
```

### When the formatter genuinely can't tell what you meant

Some pieces of Gren syntax — `=`, `:`, `|`, the `as` keyword and the alias name
after it, and the brackets of a record — are recognized by the parser and then
**thrown away**. They leave no trace in the parsed program. If you put a comment
right next to one of these, the formatter can see the comment is *somewhere in
that gap*, but it has no way to know which side of the (now-invisible) symbol you
meant it to be on.

Rather than guess, the formatter picks **one** canonical spot and always renders
the comment there. So two programs that differ only in which side of one of these
symbols a comment sits on will format to the *same* output. A few examples of
the canonical choice it makes:

```gren
-- a comment around a signature's `:` always lands after the `:`
foo : {- c -} Int

-- a comment around `=` always lands after the `=`
foo = {- c -} 42

-- a comment around a union `|` lands after the variant before it
type T
    = A {- c -}
    | B
```

This is the one place the formatter is deliberately *not* faithful to your exact
placement — and it's unavoidable, because the information simply isn't there in
the parsed program.

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
about comments and blank lines — not about regular code. The main families are:

- **Blank lines near a comment-and-declaration pair.** When a comment documents
  a declaration, the formatter decides whether to keep a blank line between them
  by looking at row positions in your source. A line break injected *inside* the
  declaration's head can shift that decision, so the blank line is sometimes
  added or dropped. The blank-line choice really ought to be a pure
  width-and-adjacency decision; making it fully independent of incoming
  whitespace is in progress (and partly blocked on an upstream parser issue).

- **Re-attaching a comment to a different owner.** A comment whose position is
  ambiguous between two neighbors — for instance one nudged onto the line just
  *below* a declaration — can attach to the enclosing construct in one layout and
  to the top level in another. Resolving every ambiguous case to a single
  canonical owner is an open problem, related to the elided-token cases described
  in the [Comments](#comments) section above.

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
