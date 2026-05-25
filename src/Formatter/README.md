# Gren Formatter Rules

How `gren format` lays out your code.

---

## Module declaration

The `module` line is kept on a single line when it fits within the page width. The `exposing` list uses `( )` with a space inside each parenthesis.

```gren
module MyApp exposing ( Model, Msg, init, update )
```

When the list is too long to fit on the same line, it moves to the next line indented by 4 spaces. If it fits there as a single line, it stays that way:

```gren
module MyApp exposing
    ( Model, Msg, init, update, view, subscriptions )
```

If it still doesn't fit, each item gets its own line with `, ` before each subsequent item and `)` on a line by itself:

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

The wildcard form `(..)` is left as-is.

---

## Import statements

A plain import stays on one line:

```gren
import Array
```

An alias is written with `as`:

```gren
import Dict as D
```

An exposing list follows the same three-level layout as the module declaration. When the list fits on the same line it stays there (with spaces inside the parentheses); when too long it wraps to one indented line; when still too long each item gets its own line:

```gren
-- fits on one line
import String exposing ( fromInt, toInt )

-- alias and exposing can be combined
import Array.Extra as AE exposing ( filterMap, unique )

-- too long for one line: moves to indented line
import MyModule exposing
    ( AlphaType, BetaType, gammaFunction, deltaFunction, epsilonValue )

-- still too long: one item per line
import MyOtherModule exposing
    ( AlphaLongTypeName
    , BetaLongTypeName
    , GammaLongFunctionName
    , DeltaLongFunctionName
    )
```

The wildcard form `exposing (..)` is left as-is.

---

## Type signatures

A type signature (`name : Type`) is kept on one line when it fits:

```gren
add : Int -> Int -> Int

applyTwice : (a -> a) -> a -> a
```

Function types used as arguments are wrapped in parentheses in the signature.

When a signature is too long to fit on one line, it continues on the next line indented 4 spaces. The `->` stays at the end of the preceding line:

```gren
processItems : Array String -> Dict String Int -> (String -> Bool) -> Array String -> Result String
    (Array String)
```

---

## Function application

Arguments are placed on the same line as the function name. When they don't all fit, the overflow continues on the next line indented +4 from the function name — there is no "one argument per line" layout:

```gren
-- fits on one line
result = foo a b c

-- overflows: continuation indented +4 from the function name
result =
    someFunction firstLongArgument secondLongArgument thirdLongArgument fourthLongArgument
        fifthLongArgument
```

The same rule applies inside `let` bindings when the value wraps:

```gren
let
    x =
        someFunction firstLongArgument secondLongArgument thirdLongArgument fourthLongArgument
            extraLongArg
in
x
```

---

## Function body

When a function is preceded by a type signature, the body always starts on the next line, indented by 4 spaces — even if it would fit on one line:

```gren
double : Int -> Int
double n =
    n * 2

version : String
version =
    "1.0.0"

makePoint : Int -> Int -> { x : Int, y : Int }
makePoint x y =
    { x = x, y = y }
```

When there is no type signature, a function body stays on the same line as its name and parameters if the whole definition fits within the page width:

```gren
double n = n * 2

version = "1.0.0"

makePoint x y = { x = x, y = y }
```

When the body is too long to fit on one line (with or without a type signature), it moves to the next line indented by 4 spaces:

```gren
findPreferredLanguage languages =
    Array.keepIf (\lang -> Array.member lang supported) languages

gammaLongFunctionName =
    computeFromSomethingReallyLong arg1 arg2 arg3
```

Bodies that are inherently multi-line (`if`, `let`, `when`) always start on the next line:

```gren
classify n =
    when n is
        1 -> "one"
        _ -> "other"

circleArea radius =
    let
        pi = 3.14159
    in
    pi * radius * radius
```

---

## Blank lines between declarations

Two blank lines appear before every function signature (the line with the type annotation). This gives each function a clear visual boundary:

```gren
double : Int -> Int
double n =
    n * 2


square : Int -> Int
square n =
    n * n
```

If a comment immediately precedes a function signature with no blank line between them, it is treated as belonging to that function. The two blank lines are placed before the comment, not between the comment and the signature:

```gren
circleArea : Float -> Float
circleArea radius =
    ...


-- Area of a square
squareArea : Int -> Int
squareArea side =
    side * side
```

A comment that is separated from a function by a blank line is treated as floating (not attached to any function) and gets only one blank line before it:

```gren
-- A floating comment

-- This comment is attached to foo
foo : Int -> Int
foo n =
    n
```

This rule applies to chains of comments: each consecutive comment line (or block) that is adjacent to the next with no blank lines is part of the same group.

---

## Type aliases

A `type alias` declaration always has two lines: the header on the first line and the aliased type on the next line indented 4 spaces — even if the whole thing would fit on one line:

```gren
type alias Name =
    String

type alias Callback =
    Int -> String
```

Type variables appear after the name on the header line:

```gren
type alias Pair a b =
    { first : a
    , second : b
    }
```

When the aliased type is a record, each field always gets its own line — the record is never collapsed to a single line even when it would fit:

```gren
type alias Point =
    { x : Int
    , y : Int
    }

type alias Config a =
    { name : String
    , value : a
    }
```

Non-record aliased types (simple names, applied types, function types) appear on a single indented line:

```gren
type alias Id =
    Int

type alias Lookup a =
    Dict String a

type alias Transform a =
    a -> a
```

---

## Custom types

A `type` declaration has the `type` keyword, the type name, and any type variables on the first line. Each variant always gets its own line indented 4 spaces. The first variant is preceded by `=` and each subsequent one by `|`:

```gren
type Color
    = Red
    | Green
    | Blue
```

Type variables appear after the name on the header line:

```gren
type Maybe a
    = Nothing
    | Just a
```

Variant payloads appear on the same line as the variant name, space-separated. Payload types follow the same rules as type signatures:

```gren
type Shape
    = Circle Int
    | Rectangle Int Int
```

---

## Port declarations

A port declaration is written on a single line with the `port` keyword, the port name, `:`, and the type:

```gren
port outgoing : String -> Cmd msg

port incoming : (String -> msg) -> Sub msg
```

Long port type signatures follow the same wrapping rule as function type signatures — the continuation indents 4 spaces and the `->` stays at the end of the preceding line.

---

## Infix operator declarations

Infix declarations use the keyword `infix` followed by the associativity (`left`, `right`, or `non`), the precedence, the operator symbol in parentheses, `=`, and the implementing function name — all on one line:

```gren
infix left  6 (+) = add
infix left  6 (-) = sub
infix left  7 (*) = mul
infix non   4 (==) = eq
infix right 5 (<|) = apL
```

> **Note:** Infix declarations are only valid in kernel/core packages. This rule is implemented but not covered by the formatter's effectful test suite.

---

## Records

### Record literals

An empty record stays on one line: `{}`.

A record with a single field stays on one line:

```gren
singleton x = { x = x }
```

A record with two or more fields is always written across multiple lines, with `{` and the first field on the first line, `, ` before each subsequent field, and `}` on its own line:

```gren
makePoint x y =
    { x = x
    , y = y
    }
```

Records passed as function arguments follow the same rules — a single-field record stays on one line even when used as an argument:

```gren
firstX = distSq { x = 0, y = 0 } { x = 1, y = 0 }
```

### Record updates

An empty record update and a single-field update stay on one line:

```gren
withDefault r = { r | x = 0 }
```

A record update with two or more fields is always written across multiple lines. The base record name goes on the first line after `{`, the first field is on the next line prefixed with `| ` (indented by 4), and each subsequent field is on its own line prefixed with `, ` at the same indentation. The closing `}` goes on its own line aligned with `{`:

```gren
movePoint dx dy pt =
    { pt
        | x = pt.x + dx
        , y = pt.y + dy
    }
```

### Record field values

When a field's value is short enough to fit on the same line as the field name, it stays there:

```gren
compact =
    { model = { model | x = 1 }
    , command = Cmd.none
    }
```

When a field's value is too long to fit on the same line, it wraps to the next line indented by 4 spaces relative to the field name:

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

### Records in type signatures

Record types in signatures follow the same flow-layout rules as other types and may be inline or wrapped depending on line length.

### Extensible record types

An extensible record type `{ r | field : Type }` places the base variable and `|` at the start of the first field item. When the fields fit on one line the whole thing stays inline; when they don't, the first field (with its `r |` prefix) starts on the first line and remaining fields use `, ` alignment:

```gren
-- fits on one line
getName : { r | name : String } -> String

-- first field carries the base variable; rest align with ,
getInfo : { r | firstName : String, lastName : String, age : Int } -> String
```

---

## String literals

Regular single-line strings are left as-is, with escape sequences preserved:

```gren
greeting = "Hello, World!"

withEscapes = "line one\nline two\t!\\"
```

### Char literals

A char literal is written with single quotes. The five special characters are always emitted as escape sequences; everything else is emitted as the literal character:

```gren
tab            = '\t'
newline        = '\n'
carriageReturn = '\r'
singleQuote    = '\''
backslash      = '\\'
letter         = 'a'
```

---

### Multiline strings

A triple-quoted (`"""`) string always remains in triple-quoted form. The opening `"""` goes at the end of the previous line (or on a new indented line when the body wraps), the content lines appear at the same indentation as the `"""` delimiters, and the closing `"""` goes on its own line at that same indentation:

```gren
message =
    """
    Hello, World!
    """
```

Multi-line content is preserved line by line:

```gren
poem =
    """
    line one
    line two
    line three
    """
```

The indentation of the content lines in the output always matches the indentation of the `"""` delimiters, regardless of how much leading whitespace the original source had.

---

## Array literals

An empty array is always written as `[]`.

A non-empty array uses an all-or-nothing layout: either every item fits on one line or every item gets its own line. There is no partial wrapping.

The flat form has a space after `[` and before `]`:

```gren
[ 1, 2, 3 ]
```

When the array is too long to fit on one line, it breaks to the vertical form. The opening `[` and first item are on the first line, each subsequent item is prefixed with `, ` on its own line, and the closing `]` is on a line by itself:

```gren
[ "first"
, "second"
, "third"
]
```

Multi-field record items stay inline even in the vertical form, as long as each record fits on its own line:

```gren
[ { label = "first", value = 1 }
, { label = "second", value = 2 }
, { label = "third", value = 3 }
]
```

When a record item is too long to fit on one line, it breaks across multiple lines. To keep the record fields visually distinct from the array's own `, ` separators, the record's fields and closing `}` are indented 2 extra spaces — aligning them with the `{` rather than with the array's `, `:

```gren
[ { veryLongFieldNameAlpha = valueAlpha
  , veryLongFieldNameBeta = valueBeta
  , veryLongFieldNameGamma = valueGamma
  }
, { veryLongFieldNameAlpha = valueAlpha2
  , veryLongFieldNameBeta = valueBeta2
  , veryLongFieldNameGamma = valueGamma2
  }
]
```

The same 2-space offset applies to nested arrays whose inner items must break:

```gren
[ [ "alphaLonger"
  , "betaLonger"
  , "gammaLonger"
  ]
, [ "deltaLonger"
  , "epsilonLonger"
  , "zetaLonger"
  ]
]
```

A comment between items forces the vertical layout, and the comment appears between the items at the same indentation:

```gren
[ firstItem
-- a comment between items
, secondItem
, thirdItem
]
```

---

## If expressions

The body of every branch always starts on the next line, indented 4 spaces. There is no inline form — even a one-word body goes on its own line:

```gren
if n > 0 then
    "positive"
else
    "non-positive"
```

The `else` keyword appears at the same indentation level as `if`, on its own line immediately after the previous branch body.

When there are multiple conditions, `else if` is written as a single unit on one line with its condition. Each additional branch body is indented 4 spaces from the `else if`:

```gren
if n < 0 then
    "negative"
else if n == 0 then
    "zero"
else
    "positive"
```

When the condition is too long to fit on one line, it wraps to the next line indented **8 spaces** (twice the normal indent). This keeps the continuation visually distinct from the branch body, which is only 4 spaces in. `then` stays on the last line of the condition:

```gren
if model.isAuthenticated && model.hasPermission && model.featureEnabled &&
        model.subscriptionActive then
    showDashboard model
else
    showLoginPage model
```

---

## When expressions

Each branch pattern and its body are placed on the same line when they fit:

```gren
when n is
    1 -> "one"
    _ -> "other"
```

When the body is too long to fit on the same line, it moves to the next line indented by 4 spaces relative to the pattern:

```gren
when msg is
    ChangeLanguage lang ->
        { model = { model | lang = lang }
        , command = Cmd.none
        }
```

A blank line is inserted between two branches whenever either branch occupies more than one line:

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

When all branches fit on a single line each, no blank lines are inserted:

```gren
when n is
    1 -> "Monday"
    2 -> "Tuesday"
    _ -> "Unknown"
```

---

## Patterns

Patterns appear in `when` branches, lambda parameters, and `let` destructuring bindings. All pattern forms are supported:

| Pattern | Example |
|---------|---------|
| Variable | `n` |
| Wildcard | `_` |
| Integer literal | `0`, `1` |
| String literal | `"hello"` |
| Char literal | `'a'` |
| Constructor (no payload) | `Nothing` |
| Constructor (with payload) | `Just n` |
| Nested constructors | `Just Just n` |
| Qualified constructor | `Maybe.Just n` |
| Alias | `Just n as whole` |
| Record (shorthand) | `{ name, value }` |
| Record (with alias) | `{ field = alias }` |
| Array | `[]`, `[ a ]`, `[ a, b ]` |

Constructor payloads appear on the same line as the constructor name, space-separated. Parentheses around nested constructors are dropped when unambiguous — `Just (Just n)` is formatted as `Just Just n`.

Multiple patterns in a `when` branch are not supported in Gren; each branch has exactly one pattern.

---

## Let expressions

A `let` expression introduces local bindings. The `let` and `in` keywords sit at the same indentation level, bindings are indented 4 spaces under `let`, and the result expression starts on the next line after `in` at the same level as `let` and `in`:

```gren
circleArea radius =
    let
        pi = 3.14159
        rSquared = radius * radius
    in
    pi * rSquared
```

When a binding's value fits on the same line as the name and `=`, it stays there. When it is too long to fit, it moves to the next line indented 4 more spaces relative to the binding name:

```gren
wrapsToNextLine =
    let
        newDropdownState =
            AutoDropdown.mouseEnter idx model.suggestions model.suggestionDropdownState
    in
    newDropdownState
```

Bindings whose value is inherently multi-line (`if`, `let`, `when`, or a multi-field record) always start on the next line:

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

Destructuring patterns on the left-hand side of a binding use the same record-pattern syntax as elsewhere. Both the full `{ field = alias }` form and the shorthand `{ field }` form are supported:

```gren
let
    { cache = newCache, value = maybeResults } = LRUCache.get text cache
in
maybeResults
```

```gren
let
    { model, command } = update msg model
in
model
```

When a destructuring pattern is too long to fit on one line, the fields wrap to subsequent lines with `, ` aligned with the opening `{`, and the closing `}` and `=` stay together on the last line of the pattern:

```gren
let
    { veryLongFieldNameAlpha = aliasAlpha
    , veryLongFieldNameBeta = aliasBeta
    , veryLongFieldNameGamma = aliasGamma
    } = someFunction arg1 arg2
in
aliasAlpha + aliasBeta + aliasGamma
```

Comments may appear before a binding inside a `let` block and are placed at the same indentation level as the bindings:

```gren
let
    -- Convert the value to a string
    valueStr = String.fromInt value
in
label ++ ": " ++ valueStr
```

---

## Lambdas

A lambda is written with `\` immediately before the first pattern (no space), followed by any additional patterns, then `->`, then the body:

```gren
double = \n -> n * 2

add = \a b -> a + b
```

When passed as an argument, a lambda is wrapped in parentheses:

```gren
doubleAll = Array.map (\n -> n * 2) nums
```

Pattern types follow the same rules as elsewhere — simple variables, record destructures, and array destructures are all valid:

```gren
-- Record destructure: \ is placed directly before {
Array.map (\{ start, end } -> end - start) ranges

-- Array destructure: \ is placed directly before [
Array.map (\[ first, second ] -> first + second) pairs

-- Multiple parameters, first is a record destructure
Array.foldl (\{ value } acc -> acc + value) 0 items
```

When the lambda does not fit on one line, the body wraps to the next line indented 4 spaces from the `\`:

```gren
transform =
    \veryLongParameterName ->
        veryLongParameterName * 2 + someOtherValue + anotherValue
```

The `->` always stays at the end of the parameter line; it never moves to the body line.

---

## Pipelines

Both `|>` (forward) and `<|` (backward) are treated as pipeline operators. The same layout rules apply to both: a contiguous run of the *same* operator is treated as one pipeline. A chain that mixes `|>` and `<|` is not collapsed into a single pipeline — only contiguous runs of the same operator chain.

A pipeline that fits on one line stays on one line:

```gren
result = list |> Array.map double |> Array.first
```

```gren
result = String.toUpper <| String.append "Hello, " name
```

When the pipeline is too long to fit on one line, each step starts on its own line, indented by 4 spaces relative to the pipeline seed:

```gren
result =
    list
        |> Array.map double
        |> Array.keepIf isValid
        |> Array.first
```

```gren
shout name =
    String.toUpper
        <| String.append "Hello, "
        <| String.append name "!"
```

When a pipeline step's function call is too long to fit on one line with the operator, its continuation arguments wrap to the **next line indented by 4 spaces relative to the operator**:

```gren
result =
    nodes
        |> Dict.set newItemKey newValue
        |> Dict.set firstExistingKey
            (buildUpdatedLinkNodeFromOriginal originalFirstLinkEntry newItemKey)
```

A comment immediately before a pipeline step is treated as part of that step and is placed at the same indentation level as the operator. This applies to both `--` and `{- -}` comments:

```gren
result =
    list
        -- Step 1: filter
        |> Array.keepIf isValid
        {- Step 2: transform -}
        |> Array.map double
```

---

## Comments

The formatter never modifies the text of a comment — only its placement relative to code.

### Single-line comments (`--`)

A comment on the same line as code stays attached to that line:

```gren
import Dict exposing
    ( Dict
    , empty -- a comment on the same line as empty
    )
```

A comment on its own line stays on its own line, indented to match the surrounding code:

```gren
foo2 a =
    -- before the first line of the function body
    a * 100
```

### Block comments (`{- ... -}`)

A single-line block comment inside an expression stays inline:

```gren
foo1 a = a * {- inline block comment -} 100
```

A standalone block comment at the top level is treated like any other top-level item (see blank lines below).

#### Multi-line block comments

A block comment whose body spans more than one line is **unflattenable**: it forces the construct it sits in to break vertically rather than collapsing onto one line. Its continuation lines are re-indented to align under the `{-` wherever the comment ends up printing — they are dedented by the column the `{-` sat at in the source and then re-aligned, so the comment's internal shape (relative indentation, lists, ASCII art) is preserved while its absolute position follows the code:

```gren
value =
    items
        {- this comment spans
           three lines and keeps
           its shape -}
        |> process
```

This matters most for a comment that leads a binding or branch: because the comment ends its own line, the following token starts fresh at the correct column instead of being glued onto the `-}` line.

A *single-line* block comment standing alone on its own line (e.g. before a non-first `let` binding) is handled the same way — it breaks onto its own line so the binding starts fresh. A single-line block comment that *continues* a line stays inline (`a * {- c -} b`, a binop right-hand side, a `( … )` body). The flow builder distinguishes the two via an `inlineStart` flag threaded into the inline-continuation contexts.

### Doc comments (`{-| ... -}`)

A doc comment is placed immediately before the declaration it documents, with no blank line between them. The module doc comment appears immediately after the module line. Multi-line content is preserved exactly:

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

Whether a blank line appears between a comment and an adjacent declaration mirrors the source: if there was no blank line in the original, none is inserted; if there was one, one is kept.

```gren
{- This block comment is immediately before foo1, no blank line -}
foo1 = 1

-- This line comment has a blank line after it

foo2 = 2
```

### Trailing comments at a construct boundary

A comment you write *after the last token of a construct* — at the boundary where
the next thing is a sibling at a shallower indent — is placed at that **shallower
indent on its own line**, not kept at the construct's deeper indent. The deeper
placement is not a stable fixed point: on the next format the comment (now on its
own line, one row past the construct) re-attaches at the shallower level, so the
indent would oscillate between reformats. The formatter commits to the shallower
level on every format.

After a function signature's last type token, before the definition — its own
line at column 1, as a leading comment of the definition (this holds even when the
signature is short enough to fit on one line):

```gren
foo : Int -> Int
{- a note -}
foo n =
    n
```

After a non-last `let` binding's value, before the next binding — its own line at
the bindings indent, leading the next binding:

```gren
let
    first = { x | a = 1 }
    {- a note -}
    second = x
in
first + second
```

After the module `exposing ( … )` list — its own line at column 1. (The list's
closing `)` is discarded by the parser, so there is no token to anchor the comment
to; column 1 is the canonical home.)

```gren
module MyApp exposing ( alpha, beta, gamma )
{- a note -}
```

The same rule applies to a comment after the last `let` binding before `in` (see
*Comments at elided-token positions → `let … in`* below). A comment after a `when`
branch body behaves slightly differently: when it can stay on the body's last line
it does — glued there with a space, even if that pushes the line past the page
width — which keeps it on a stable row across reformats. Only when the branch body
is itself multi-line (so the comment would otherwise fall onto its own line) and
another branch follows is it lifted to sit between the branches at the branch
indent.

By contrast, a comment that is genuinely **inside** a construct stays there. A
comment before a container's closing bracket stays inside the container rather
than escaping to the enclosing level:

```gren
[ 1
, 2 {- a note -}
]
```

### Comments at elided-token positions

Comment placement is position-driven: a comment is attached according to its
source `(row, col)` relative to the positioned tokens around it. But several
structural tokens — the `as` keyword and import-alias name, `|`, `=`, `:`, the
`where`-block field keywords, bracket characters — are matched and discarded by
the parser and carry **no position in the AST**. The formatter has nothing to
attach the comment *to*: it can see the comment sits somewhere in the gap, but
not which side of the elided token the author meant.

So for these positions the formatter does **not** try to recover intent. It
commits to one canonical rendering and always produces it. Two source programs
that differ only in which side of the elided token a comment sits on are
therefore indistinguishable to the formatter — they format to the same output.
(The AST carries no token position, and we are deliberately not adding one; the
canonical rendering is the contract instead.)

**Idempotency.** The canonical rendering must be a *fixed point*: formatting it
again must reproduce it exactly. This is a hard requirement, enforced by the
`idempotency` sub-test in `assertPretty` (it re-parses and re-formats the
`.formatted.gren` and requires the `Module` *and* the parse `Context` —
including every comment position — to be identical). To get there, each elided
token is synthesized **zero-width at the end of the preceding real token** (see
`LPTHelpers.mkZeroWidthText`): `exposing` at the module name's end, `->` at the
end of its left type, `as`/alias/`:` likewise. A zero-width anchor can never
overlap a comment that happens to share its column, and anchoring it to a real
token's end means any comment in the gap *always* sorts to the same side across
reformats. The consequence is the canonical placement below: such a comment
renders **after** the elided token.

The same zero-width-anchor and descent-guard techniques also stabilise comment
placement around *implicit* brackets (record-update and record-type braces),
the `then`/`else if` keywords, the `let … in` keyword, and the `\`-glued first
lambda parameter — so **every** fixture, including the maximal "comment in every
gap" `KitchenComments` torture, is held to idempotency with no exemptions.

The pairs below each show two inputs the formatter cannot tell apart, followed
by the single output both produce. They are pinned by the `Ambiguous`,
`AmbiguousEffectModule`, and `LetInTrailingComment` tests in the effectful suite.

#### Import alias `as`

```gren
import Foo            import Foo as
    {- c -}              {- c -}
    as Bar               Bar
```

Neither `as` nor the alias name (`alias : Maybe String`, no position) is in the
AST, so the comment cannot be anchored to the import line at all. Both render as
— the comment evicted to its own line after the import:

```gren
import Foo as Bar
{- c -}
```

#### As-pattern `as`

```gren
(y {- c -} as whole)        (y as {- c -} whole)
```

Both render with the comment before `as`:

```gren
y {- c -} as whole
```

#### Extensible-record `|`

```gren
{ r {- c -} | name : String }        { r | {- c -} name : String }
```

Both render with the comment on its own line before `|`:

```gren
{ r
    {- c -}
    | name : String
}
```

#### Record-update `|`

```gren
{ rec {- c -} | name = 1 }        { rec | {- c -} name = 1 }
```

Both render with the comment evicted before the whole update expression:

```gren
{- c -} { rec | name = 1 }
```

#### Union-variant `|`

```gren
type T = A {- c -} | B        type T = A | {- c -} B
```

Both render with the comment after the preceding variant (before `|`):

```gren
type T
    = A {- c -}
    | B
```

#### Declaration `=`

```gren
foo {- c -} = 42        foo = {- c -} 42
```

Both render with the comment after `=`:

```gren
foo = {- c -} 42
```

#### Signature `:`

```gren
foo   {- c -}   : Int        foo :   {- c -}   Int
```

Both render with the comment after `:`:

```gren
foo : {- c -} Int
```

#### Effect-module `where` block

```gren
effect module M where { command   {- c -}   = MyCmd } exposing (..)
effect module M where   {- c -}   { command = MyCmd } exposing (..)
```

The `command` / `=` / `{` / `}` positions inside the block are all unanchored,
so a comment anywhere in the header collapses to one slot — between `where` and
the opening brace:

```gren
effect module M where {- c -} { command = MyCmd } exposing (..)
```

#### `let … in`

The `in` keyword carries no AST position. A comment in the gap between the last
binding and the result expression could be read two ways — as trailing the last
binding, or as leading the result — and the formatter cannot tell which:

```gren
-- written after the last binding     -- written after `in`
let                                    let
    total = subtotal + tax                 total = subtotal + tax
    {- c -}                            in
in                                         {- c -}
total                                  total
```

Both render with the comment **after `in`** (the result expression aligns with
`let`/`in`, as always):

```gren
let
    total = subtotal + tax
in {- c -}
total
```

The ramification for where you write the comment: a comment you place at the end
of your *last* let binding does not stay with that binding — it moves onto the
`in` line, the one position both readings share. (A single-line block comment
stays on the `in` line as shown; a `--` line comment or a multi-line block
comment instead drops to its own line directly below `in`, since those always
break their line.) A comment after a *non-last* binding is unaffected: the next
binding is a real positioned token, so its side of the gap is unambiguous.

### Other trailing-comment details

A few smaller cases, all driven by the same goal — keep a trailing comment on a
stable row so the layout doesn't oscillate across reformats:

**Empty containers.** A comment *after* an empty `[]` / `{}` stays outside it; a
comment *before* the close stays inside. The two are not interchangeable — the
formatter never moves a comment across the closing bracket:

```gren
x = [] {- not implemented yet -}
y = [ {- nothing here yet -} ]
```

**Last item of a wrapped container.** A comment trailing the last item of a
bracketed container stays on that item's line. The container wraps as if the
comment were not there, so the comment never changes how the items lay out:

```gren
xs =
    [ firstItem
    , secondItem
    , thirdItem -- a note on the last item
    ]
```

**A trailing single-line comment uses a soft break.** When a `--` (or single-line
`{- -}`) comment shares a row with the token before it, it attaches with a soft
break: it stays on that line when it fits and drops to its own line only when the
line overflows — and it never drags the preceding token down with it. This keeps
an elided trailing `->` (or `|`, `=`, …) on its own row even when the comment
after it wraps:

```gren
toLabels : Array { code : String, points : Int } -> -- one parameter
    Int -> String
```
