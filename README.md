# The Gren Formatter Library

This package is the library behind `gren-format`: given a Gren source file,
it produces a formatted version of the same file — consistent spacing,
consistent indentation, comments and blank lines kept where they belong, and
also honoring the single-line/multi-line formatting the author of the source code chose.

This README covers only how to call the library. Everything else — a formatted
example, the formatting philosophy, the seven comment rules, known limitations,
performance, and the comparison with `elm-format` — is in **[the documentation
index](https://github.com/gilramir/gren-format-lib/blob/main/docs/index.md)**, which also
links to every companion document.

---

## Overview

The whole library is one function, `Formatter.prettyPrint`. It takes the two
things the [compiler-common](https://github.com/gren-lang/compiler-common)
parser gives you for a source file — the syntax tree and the parse context (the
comments) — and hands back the formatted text:

```gren
prettyPrint : Src.Module -> Ctx.Context -> Result String String
```

So calling it means parsing first, then passing both halves along. This is
what `gren-format` itself does, minus the file I/O:

```gren
module FormatFile exposing (format)

import Compiler.Parse.Context as Context
import Compiler.Parse.Module as PM
import Formatter
import String.Parser.Advanced as Parser


{-| Format the contents of one Gren source file.
-}
format : String -> Result String String
format source =
    let
        parser =
            Parser.succeed (\ast context -> { ast = ast, context = context })
                |> Parser.keep PM.parser
                |> Parser.keep Parser.getPayload
    in
    when Parser.run parser Context.empty source is
        Err errs ->
            -- the source isn't valid Gren
            Err (PM.errorsToString source errs)

        Ok { ast, context } ->
            -- the AST says what the code means; the context holds every
            -- comment and blank line
            Formatter.prettyPrint ast context
```

The parser is run with `Context.empty` as its starting payload; it fills that
payload in as it goes, and `Parser.getPayload` retrieves the finished context
once the module is parsed. Both results are needed — the AST alone has no
comments in it.

---

## More Extensive Documentation

**[The documentation index](https://github.com/gilramir/gren-format-lib/blob/main/docs/index.md)**
is the place to start. It has a pipeline diagram, a formatted example,
the formatting philosophy, the comment
rules, known limitations, performance, and the `elm-format` comparison — and
links out to the full reference documents for using the formatter and for
working on it.
