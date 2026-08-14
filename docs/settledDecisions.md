# Settled formatting decisions

These are closed questions. Each one is a recorded design decision by the Gren
team, and the formatter's output must keep honouring it: do not change the
logic, the fixtures, or the unit tests in a way that breaks one of these shapes.
Every entry names the test fixture that pins it, so a change trips a test rather
than needing a reviewer to notice.

The first three are about *layout* — the shape the formatter must produce. The
fourth is about *content* — a rewrite the formatter must not perform.

- [`module … exposing` stays on the `module` line](#module--exposing-stays-on-the-module-line)
- [`import … exposing` stays on the `import` line](#import--exposing-stays-on-the-import-line)
- [Open and close brackets align vertically](#open-and-close-brackets-align-vertically)
- [Redundant parens are never stripped](#redundant-parens-are-never-stripped)

---

## `module … exposing` stays on the `module` line

**The decision.** When the module statement renders multi-line, the `exposing`
keyword stays on the same line as `module` and is the last word on that line.
The list of exposed names starts on the next line, indented +4.

**What it looks like.**

```gren
module Foo exposing
    ( Configuration
    , Container
    )
```

**Pinned by.** `tests/testfiles/HeaderComments/ModuleExposingClosePinned.formatted.gren`
(and every other multi-line header fixture in that suite). See also
[Module declaration](formatterRules.md#module-declaration).

## `import … exposing` stays on the `import` line

**The decision.** Same rule, one line down: when an import renders multi-line,
`exposing` is the last word on the `import` line and the list starts on the next
line at +4. A wrapped `import` therefore looks exactly like a wrapped `module`.

**What it looks like.**

```gren
import Basics exposing
    ( max
    , min
    )
```

**Why.** The two header forms read the same way, so a reader learns one shape
instead of two. This is also where gren-format parts company with elm-format,
which drops `exposing` onto a row of its own at +4 and the list at +8 —
[divergence #4](elmFormatComparison.md#divergence-4).

**Pinned by.** `tests/testfiles/Divergence/D04ImportExposingWrap.formatted.gren`.
See also [Import statements](formatterRules.md#import-statements).

## Open and close brackets align vertically

**The decision.** A pair of open/close parens, square brackets, or curly braces
that lands on different lines always aligns vertically — same column, different
rows.

**What it looks like.**

```gren
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

**Pinned by.** `tests/testfiles/Divergence/D14BackPipeMultilineSeed.formatted.gren`
and `tests/testfiles/PipelineComments/BackwardPipeMultilineSeed.formatted.gren`.

## Redundant parens are never stripped

**The decision.** Parens that carry no meaning are kept exactly where you wrote
them — in every position, at every nesting depth, with no exceptions. Around a
`when`, `if`, or `let`; around a binary operator's operand; around a call
argument; stacked two or three deep. gren-format never removes one.

**What it looks like.**

```gren
-- you wrote, and gren-format keeps, all of these:
v =
    (if cond then
        one

     else
        two
    )

logBase base number =
    (Gren.Kernel.Math.log number) / (Gren.Kernel.Math.log base)

attrs =
    node "div" ({ foo = 1, bar = 2 }) []
```

**Why.** Stripping a paren means proving it carries no meaning. For an operand
that takes the operator's precedence; in general it takes knowing what each
syntactic position can hold. gren-format doesn't do that analysis, and won't.
Nothing about the output is *wrong* — only more explicit than elm-format's,
which normalizes parens down to the minimum the meaning requires.

A call argument is not an exception, even though a positional slot can never
make parens load-bearing: consistency across every position is the point of the
rule.

Note that the resulting indentation is not a second difference from elm-format
— it follows from the parens. Once the `(` is there the block hangs off it, so
`else` and `in` sit one column right of the `(` and the `)` gets a line of its
own. Take the paren away and the block simply starts the line. You cannot keep
the parens *and* get elm-format's columns; it is one difference, not two.

**Pinned by.** `tests/testfiles/Divergence/D10RedundantParens.formatted.gren`.
This is [divergence #10](elmFormatComparison.md#divergence-10), the most common
difference between the two formatters on real code; that entry has the full
side-by-side table of what each formatter strips. See also
[Parentheses](formatterRules.md#parentheses).
