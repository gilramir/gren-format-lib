# The Gren Formatter Library

This package is the library behind `gren-format`: given a Gren source file,
it produces a formatted version of the same file — consistent spacing,
consistent indentation, comments and blank lines kept where they belong, and
also honoring the single-line/multi-line formatting the author of the source code chose.

This README covers the essentials — an overview, a formatted example, and the
core formatting rules. Five companion documents go deeper:

- **[How the formatter works](docs/howItWorks.md)** — a conceptual,
  step-by-step tour of the pipeline (parse → Logical Printing Tree → render
  plan → text), with a worked example at each step.
- **[Gren Formatter Rules](docs/formatterRules.md)** — the full rule
  reference, with a before/after example for every construct.
- **[How gren-format places your comments](docs/commentHandling.md)** — the six
  rules that decide where every comment lands, each with a verified
  "you write / gren-format writes" example.
- **[Comparison with elm-format](docs/elmFormatComparison.md)** — every place
  `gren format` deliberately diverges from `elm-format`, and why.
- **[Known limitations](docs/knownLimitations.md)** — compiler/parser bugs
  and comment-placement choices the formatter can't do better on today.

---

## Table of contents

- [Overview](#overview)
- [A formatted example](#a-formatted-example)
- [Formatting Philosophy](#formatting-philosophy)
- [Comments](#comments)
- [Known limitations and Bugs](#known-limitations)
- [Performance](#performance)
- [Comparison with elm-format](#comparison-with-elm-format)

---

## Overview

Turning your source file into its formatted version happens through a pipeline
of steps, each step handing its result to the next. Any step can fail: a parse
error means the source itself is invalid Gren, while a failure in Step 1 or
Step 2 means the formatter has caught an internal bug in its own logic —
not that anything is wrong with your code. Either way, nothing is silently
mangled; the failure is reported instead. This is the flow:

![Formatter pipeline](docs/diagrams/formatter-pipeline.png)

**Before this library gets involved**, the compiler-common parser reads your file and
splits it into two pieces:

- the Abstract Syntax Tree (AST) — a description of which function calls which,
  what a `let` contains, what a type looks like, and so on
- a separate list of every comment you wrote, since these
  don't change what the code means, but they do matter for how it looks

This library's job starts from those two pieces and ends with the formatted
text. It never changes what your code means — it only decides how it looks
on the page.

For the full step-by-step tour of that pipeline — what a Logical Printing
Tree is, how a render plan is built from it, and a worked example at each
stage — see **[How the formatter works](docs/howItWorks.md)**.

---

## A formatted example

One function, showing several rules at once: a `let` with multiple bindings,
a pipeline, a binary-operator chain that breaks at its loosest operators, an
`if`, a `when`, a record update, and all three kinds of comment. (`order` is a
record with `isMember`, `hasCoupon`, `status`, and `total` fields; `Status` is
a custom type that includes `Cancelled`.)

```gren
{- Only members and coupon holders get the discount, and it always
   applies to the pre-tax subtotal.
-}
summarize : Order -> Array Float -> Order
summarize order prices =
    let
        subtotal =
            prices
                |> Array.keepIf (\price -> price > 0) -- refunds are recorded as negatives
                |> Array.foldl (+) 0

        eligible =
            order.isMember && subtotal > 100
                || order.hasCoupon && order.status /= Cancelled

        discount =
            if eligible then
                subtotal * {- ten percent -} 0.1

            else
                0
    in
    when order.status is
        Cancelled ->
            { order | total = 0 }

        _ ->
            { order
                | total = subtotal - discount
                , hasCoupon = False
            }
```

A few things worth noticing:

- `subtotal` is a pipeline: each `|>` step lands on its own line, indented +4
  from `prices` (see [Pipelines](docs/formatterRules.md#pipelines)).
- `eligible` is a binop chain the author wrote across two rows. `||` is the
  loosest operator here, so it's the only one that breaks; `&&` and `/=` bind
  tighter and stay glued to their operands (see
  [Binary operators](docs/formatterRules.md#binary-operators)).
- `discount`'s `if` branches always drop to their own line, whether or not
  they'd fit inline (see
  [If expressions](docs/formatterRules.md#if-expressions)).
- Both `when` branches return a record update, `{ order | ... }` — the
  `Cancelled` branch's field was written on one line and stays inline, while
  the other branch's two fields were written across rows and stay that way.
  Neither is about length; it's however the author wrote it (see
  [Record updates](docs/formatterRules.md#record-updates)).
- All three comments stay exactly where they were written. The `{- ... -}`
  above the signature keeps its own lines and its inner indentation; the `--`
  note stays trailing on the pipeline step it was written on, rather than being
  pushed to a line of its own; and the one-line `{- ten percent -}` sits *inside*
  the expression, between the `*` and its right operand, so the line it's on has
  to stay flat — a comment is never a reason to break a line, and a line is
  never re-broken around a comment (rules 3 and 4 in [Comments](#comments)
  below; see also
  [How gren-format places your comments](docs/commentHandling.md)).

Every one of these decisions follows from how the code was written.
any line-width target — see [Formatting Philosophy](#formatting-philosophy) below, and the full
[Formatter Rules](docs/formatterRules.md) for the complete reference.

---

## Formatting Philosophy

A guide to how `gren format` lays out your code — what it changes, what it
leaves alone, and why. This section covers the core ideas; for a rule
reference with a before/after example for every construct (module
declarations, records, pipelines, comments, and everything else), see
**[Formatter Rules](docs/formatterRules.md)**.

The Gren formatter has one central idea: **your line breaks are your layout
decisions.** Write something on one line and it stays on one line. Put a line
break between items and the formatter keeps them on separate lines, normalizing
to one item per line.

There is **no page width.** The formatter never wraps a long line. A function
call with five arguments all on one row stays on one line no matter how wide it
is. A type signature written as one long line stays that way. If you want
something to break, put a line break in it.

The four core rules:

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
   produces the same code back. Format once or ten times — same result. A
   torture test inserts a comment into every inter-token gap of every fixture
   file, formats twice, and requires byte-identical output. It is red today:
   **17** gaps out of some 56,000 still shift, and all 17 are the same upstream
   parser bug (see [Known Limitations](docs/knownLimitations.md)). Nothing
   left in it is the formatter's to fix; it goes green when that parser fix
   ships.

A few things are **always fixed**, regardless of how you wrote them:

- A binding's value always starts on its own line (see
  [Function body](docs/formatterRules.md#function-body)).
- A `when` branch body always starts on its own line.
- An `if` branch body always starts on its own line.
- A blank line always separates `else`/`else if` from the branch above it.
- Two blank lines always precede every top-level declaration.
- One blank line always separates `let` bindings.
- A type alias always puts the aliased type on its own line.
- A custom type always puts the variant list on its own line(s).
- **Indentation is 4 spaces.** Always spaces, never tabs.
- On a `module` line, `exposing` always stays glued to the module name —
  never on its own line — though the exposed list itself can still spread
  across multiple rows below it (see
  [Module declaration](docs/formatterRules.md#module-declaration)).

Everything else follows your layout choices.

---

## Comments

`gren format` never changes the text of a comment — it only decides where the
comment sits relative to the code around it. Six rules decide every comment in a
file:

1. A comment belongs to the code you wrote it next to.
2. Where the parser doesn't record the punctuation (`=`, `:`, `,`, `|`, `->`,
   and the keywords), the comment leads what follows it.
3. A comment never forces a break — unless it's a `--` or a multi-line
   `{- … -}`, which can't share a line with code that follows.
4. A comment changes where the lines fall, and nothing else: the indentation and
   the grouping are what you'd get with the comment deleted.
5. gren-format adds nothing around a comment — no blank line, no line of its own
   for air.
6. A comment on its own line is indented to the code it leads.

Each rule, with a "you write / gren-format writes" example for every case, is in
**[How gren-format places your comments](docs/commentHandling.md)**.

---

## Known limitations and Bugs

`gren-format` has a handful of known gaps:

* a few inherited compiler/parser bugs
* a `when` pattern shape the Haskell-based compiler can reject
* a bracketed pattern broken across rows that the Haskell-base compiler rejects
* several comment-placement choices forced by a token (`=`,
    `:`, `|`, `in`, a bracket's closing paren, an effect module's `where` block)
    that the parser doesn't record a position for
* and a stack-depth limit on extreme lambda/unary-minus nesting (hundreds of levels deep, well past
    anything real code hits).

For a full write-up, with examples, see: **[Known Limitations](docs/knownLimitations.md)**.

Furthermore, here is a list of the GitHub issues we are tracking in upstream
packages that affect the output of `gren-format`.


* [compiler-common#11 - Gren parser is more strict with indentation in when..is expressions
](https://github.com/gren-lang/compiler-common/issues/11)
* [compiler-common#14 - New parser rejects same-column arguments in multi-line function calls](https://github.com/gren-lang/compiler-common/issues/14)
* [compiler-common#25 - The wrong row number is assigned to "import", "type", "type alias", and "port" in the AST](https://github.com/gren-lang/compiler-common/issues/25)
* [compiler-common#27 - Parser misparses postfix record access after parens, record literals, record updates, and qualified variables
](https://github.com/gren-lang/compiler-common/issues/27)
* [compiler-common#31 - new parser fails to parse a constructor-application pattern aliased with "as"
](https://github.com/gren-lang/compiler-common/issues/31)
* [compiler-common#32 - Parser accepts custom-type variants with more than one bare-constructor argument
 ](https://github.com/gren-lang/compiler-common/issues/32)
* [compiler-common#34 - Expand the recording of the original string for some literals
](https://github.com/gren-lang/compiler-common/issues/34)
* [compiler-common#35 - A binops minus sign ("-") split across rows is parsed as negation when the right operand happens to start at the column immediately after the minus sign](https://github.com/gren-lang/compiler-common/issues/35)
* [core#134 - String.toInt returns the wrong value for 24 exactly-representable integers near 2^53
 ](https://github.com/gren-lang/core/issues/134)

---

## Performance

Real Gren files are small enough that formatting speed is a non-issue, but
the formatter is also checked against synthetic files pushed far past
anything realistic — thousands of top-level declarations, thousands of
stacked comments, deeply nested expressions — to catch algorithmic hot spots.
That stress suite is `tests/pathological-other.py` (size/shape probes) and
`tests/pathological-nesting.py` (depth probes).

A few representative numbers:

| Shape | Size | Time |
|---|---|---|
| Top-level function declarations | 15,131 | ~4s |
| Top-level function declarations | 40,000 | ~20s |
| Stacked top-level comments (no code — a stress case, not realistic) | 4,005 | ~0.5s |
| Stacked top-level comments (no code — a stress case, not realistic) | 32,000 | ~21s |

Those numbers reflect several `O(n²)` fixes: earlier versions of the
formatter rebuilt or rescanned the *entire* array of already-processed
declarations or comments once per new one — 15,131 declarations used to take
~15s (now ~4s), and 32,000 stacked comments used to time out entirely (now
~21s). The fix in each case was the same shape: accumulate with
`Array.Builder` (amortized O(1) per append) instead of `Array.pushLast`/`++`
in a loop, and never rescan work already known to be settled.

---

## Comparison with elm-format

`gren format` is a spiritual descendent of `elm-format`, and agree on
formatted syntax in most places. Both
formatters share the same "your line breaks are your layout decisions"
philosophy — neither reflows code to fit a page width — so they agree almost
everywhere. Where they don't, it's a catalogued choice: 26 divergences,
covering things like blank-line placement around comments, redundant parens
(the most common difference on real code), and how a multi-line operator chain
breaks. One of them isn't a choice at all — Gren's parser throws away the
position of `=`, `,`, `|`, `->` and the keywords, so a comment written beside
one of those has to snap to a canonical side
([#22](docs/elmFormatComparison.md#divergence-22)).

The full catalogue, with a real before/after example for every entry, is in
**[Comparison with elm-format](docs/elmFormatComparison.md)**.
