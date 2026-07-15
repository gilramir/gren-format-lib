# Required Formatting Shapes

These formatting shapes must be produced by the code. They are
recorded design decisions by the Gren team.
We must not change the logic or unit tests in such a way as
these shapes are broken.

* `module` statement : If the module statement is rendered multi-line,
the `exposing` keyword stays on the same line as `module` and is the last
word on the line. The list of exposed symbols starts on the next line.
Example:
```
module Foo exposing
    ( Configuration
    , Container
    )
```

* `import` statement : If an import statement is rendered multi-line,
the `exposing` keyword stays on the same line as `import` and is the last
word on the line. The list of exposed symbols starts on the next line.
Example:
```
import Basics exposing
    ( max
    , min
    )
```

* alignment of parens, brackets, and braces: A pair of open/close parens,
square brackets, or curly braces on different lines will always align vertically,
at the same column but on different rows.
Example:
```
parenExample =
    (x
        + y
    ) <|
        value


recordExample =
    { field = 1
    , other = 2
    } <|
        value


arrayExample =
    [ 1
    , 2
    ] <|
        value
```
