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
