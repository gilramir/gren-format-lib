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

The wildcard form `(..)` is left as-is. The same rules apply to `import … exposing` lists.

---

## Function body

A function body stays on the same line as its name and parameters if the whole definition fits within the page width:

```gren
double n = n * 2

version = "1.0.0"

makePoint x y = { x = x, y = y }
```

When the body is too long to fit, it moves to the next line indented by 4 spaces:

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
double n = n * 2


square : Int -> Int
square n = n * n
```

If a comment immediately precedes a function signature with no blank line between them, it is treated as belonging to that function. The two blank lines are placed before the comment, not between the comment and the signature:

```gren
circleArea : Float -> Float
circleArea radius = ...


-- Area of a square
squareArea : Int -> Int
squareArea side = side * side
```

A comment that is separated from a function by a blank line is treated as floating (not attached to any function) and gets only one blank line before it:

```gren
-- A floating comment

-- This comment is attached to foo
foo : Int -> Int
foo n = n
```

This rule applies to chains of comments: each consecutive comment line (or block) that is adjacent to the next with no blank lines is part of the same group.

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

Record types in signatures are not affected by these rules — they follow the same flow-layout rules as other types and may be inline or wrapped depending on line length.

---

## Pipelines

A pipeline that fits on one line stays on one line:

```gren
result = list |> Array.map double |> Array.first
```

When the pipeline is too long to fit on one line, each `|>` step starts on its own line, indented by 4 spaces relative to the pipeline seed:

```gren
result =
    list
        |> Array.map double
        |> Array.keepIf isValid
        |> Array.first
```

When a pipeline step's function call is too long to fit on one line with the `|>`, its continuation arguments wrap to the **next line indented by 4 spaces relative to the `|>`**:

```gren
result =
    nodes
        |> Dict.set newItemKey newValue
        |> Dict.set firstExistingKey
            (buildUpdatedLinkNodeFromOriginal originalFirstLinkEntry newItemKey)
```

A comment immediately before a pipeline step is treated as part of that step and is placed at the same indentation level as the `|>`:

```gren
result =
    list
        -- Step 1: filter
        |> Array.keepIf isValid
        -- Step 2: transform
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

A block comment inside an expression stays inline:

```gren
foo1 a = a * {- multiline inside an expression -} 100
```

A standalone block comment at the top level is treated like any other top-level item (see blank lines below).

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
foo n = n
```

### Blank lines around comments

Whether a blank line appears between a comment and an adjacent declaration mirrors the source: if there was no blank line in the original, none is inserted; if there was one, one is kept.

```gren
{- This block comment is immediately before foo1, no blank line -}
foo1 = 1

-- This line comment has a blank line after it

foo2 = 2
```
