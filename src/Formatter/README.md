# Gren Formatter Rules

A guide to how `gren format` lays out your code — what it changes, what it
leaves alone, and why.

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

The three core rules:

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

Everything else follows your layout choices.

---

## Module declaration

The `module` line follows your layout for the exposing list. Written on one
line, it stays on one line:

```gren
module MyApp exposing ( Model, Msg, init, update, view, subscriptions )
```

Written across rows, each item gets its own line:

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

The wildcard `exposing (..)` is always written as `(..)` on the module line.

When the exposing list contains a comment, it is always kept vertical.

---

## Import statements

A plain import stays on one line:

```gren
import Array
```

An alias uses `as`. An exposing list follows your layout — flat if you wrote
it flat, vertical if you wrote it across rows:

```gren
-- flat:
import String exposing ( fromInt, toInt )

import Array.Extra as AE exposing ( filterMap, unique )

-- vertical:
import Dict exposing
    ( Dict
    , empty
    , fromArray
    , get
    )
```

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

The multi-line shape triggers when any `->` separator appears on a different
row than the one before it. A line break right after the `:` with the rest
still on one line is not enough — the break must fall between `->` segments.

A signature containing a comment cannot be split segment-by-segment; it falls
back to filling the flow and wrapping at word boundaries.

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

A comment separated from a function by a blank line is treated as floating —
the comment keeps the one blank line you wrote, and the function still gets its
two blank lines below:

```gren
double : Int -> Int
double n =
    n * 2

-- A floating note, kept at arm's length


square : Int -> Int
square n =
    n * n
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

### Record field values

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

Passed as an argument, a lambda is wrapped in parentheses:

```gren
doubleAll =
    Array.map (\n -> n * 2) nums
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

## Binary operators

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

#### Comments in an effect module's header

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

### Doc comments (`{-| ... -}`)

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

### Blank lines around comments

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

### A comment at the *end* of something

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

## Idempotency

Formatting already-formatted code produces exactly the same code back. Format
once or ten times — same result.

A torture test inserts a block comment into every inter-token gap of every
fixture file, formats twice, and requires byte-identical output. It currently
finds **zero** non-idempotent gaps across the whole test corpus.

---

## Known limitations

### A compiler bug with postfix field access

Field access written directly after a closing bracket or a qualified name —

```gren
(getUser model).name
{ x = 1 }.x
Config.default.timeout
```

— is formatted with a space before the dot (`(getUser model) .name`). That
space changes the meaning: it applies the accessor *function* `.name` to the
value instead of reading its field, and the formatted file no longer compiles.
The cause is in the parser: it reads both spellings as the same expression.
Until the parser is fixed, avoid formatting files that contain `).field`,
`}.field`, or `Module.value.field`. Tracking:
[compiler-common#27](https://github.com/gren-lang/compiler-common/issues/27).

### Wide `when` branch patterns

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

### Comment placement near invisible tokens

As described in [When the formatter can't tell what you meant](#when-the-formatter-genuinely-cant-tell-what-you-meant), a comment beside `=`, `:`, `|`, or an import's `as` always snaps to one
canonical side. Two different intents produce the same output.

A comment at the end of a type signature or module line always moves to the
left margin, even when it would have fit.

### A line break inside a declaration's head

A line break *inside* a declaration's keyword (e.g. `import` on one line,
the module name on the next) can cause a blank line to appear between a
comment and that declaration. The root cause is a parser bug that records the
wrong line number for keyword-led declarations:
[compiler-common#25](https://github.com/gren-lang/compiler-common/issues/25).

### Comments near an effect module's `where` block

As described in [Comments in an effect module's header](#comments-in-an-effect-modules-header),
a comment's placement near the `where { … }` block is determined by proximity
to the handler name. Changing the spacing can change where the comment ends up.
This stays as-is until the parser records positions for the missing tokens.

### Verbatim block comment bodies

When a multi-line block comment opens with `{-` alone on its first line, the
body is kept verbatim — columns are not canonicalized. Two inputs that differ
only in the body's indentation format to two different outputs. This is
deliberate (it protects ASCII art and aligned tables), but it means this class
of comment is not whitespace-canonical.
