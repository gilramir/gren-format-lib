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
