# Known limitations

Places where `gren format` falls short of ideal — a compiler/parser bug it
inherits, a shape the language no longer allows but the parser still accepts,
or a comment-placement choice forced by a token with no recorded source
position. See the main [README](../README.md) for the overview, and
[Gren Formatter Rules](formatterRules.md) for what the formatter does when
nothing is going wrong.

## Table of contents

- [A compiler bug with field access on a record-update base](#a-compiler-bug-with-field-access-on-a-record-update-base)
- [An unparenthesized constructor pattern can't be aliased with `as`](#an-unparenthesized-constructor-pattern-cant-be-aliased-with-as)
- [A continuation line at the same column as the body above it](#a-continuation-line-at-the-same-column-as-the-body-above-it)
  - [The same misparse reached by a comment, where the columns are not equal](#the-same-misparse-reached-by-a-comment-where-the-columns-are-not-equal)
- [Output today's compiler rejects, that the next one will accept](#output-todays-compiler-rejects-that-the-next-one-will-accept)
- [A binary `-` whose right operand starts at the operator's own column](#a-binary---whose-right-operand-starts-at-the-operators-own-column)
- [An integer literal just below 2^53 is silently rewritten](#an-integer-literal-just-below-253-is-silently-rewritten)
  - [Two causes, one per spelling](#two-causes-one-per-spelling)
  - [Where it showed up](#where-it-showed-up)
- [Wide `when` branch patterns](#wide-when-branch-patterns)
- [Comment placement near invisible tokens](#comment-placement-near-invisible-tokens)
- [A line break inside a declaration's head](#a-line-break-inside-a-declarations-head)
- [Comments near an effect module's `where` block](#comments-near-an-effect-modules-where-block)
- [A comment right after `exposing` doesn't sort with the first name](#a-comment-right-after-exposing-doesnt-sort-with-the-first-name)
- [A module `exposing` list's closing paren isn't recorded](#a-module-exposing-lists-closing-paren-isnt-recorded)
  - [A comment past a flat list](#a-comment-past-a-flat-list)
- [A comment after the last binding in a `let`](#a-comment-after-the-last-binding-in-a-let)
- [Block comment body indentation](#block-comment-body-indentation)
- [Two fixtures parse a custom-type shape the language no longer allows](#two-fixtures-parse-a-custom-type-shape-the-language-no-longer-allows)
- [Very deep lambda or unary-minus nesting can overflow the stack](#very-deep-lambda-or-unary-minus-nesting-can-overflow-the-stack)

---

## A compiler bug with field access on a record-update base

Field access directly after a closing paren, a plain record literal, or a
qualified name — `(getUser model).name`, `{ x = 1 }.x`,
`Config.default.timeout` — parses and formats correctly. But a
record-**update** base still hits a narrower case of the same bug:

```gren
{ model | x = 0 }.x
```

is formatted with a space before the dot (`{ model | x = 0 } .x`). That
space changes the meaning: it applies the accessor *function* `.x` to the
record update instead of reading its field, and the formatted file no
longer compiles. The parser reads both spellings as the same expression
(`Call(Update, [Accessor])`) — the same root cause as
[compiler-common#27](https://github.com/gren-lang/compiler-common/issues/27),
which fixed every other kind of base but not this one.

## An unparenthesized constructor pattern can't be aliased with `as`

```gren
f x =
    when x is
        Just y as whole ->
            whole
```

fails to parse — `Expected keyword '->'` at `as` — even though the real Gren
compiler accepts it. The parser accepts `as` after a bare variable or
wildcard (`x as step`, `_ as step`) and after a *parenthesized* constructor
application (`(Just y) as whole`), but not after an unparenthesized one. Since
parsing happens before formatting, gren-format refuses the whole file, not
just this pattern — real fallout: `core/src/String/Parser/Advanced.gren` has
this exact shape and can't be formatted until it's fixed. Tracked at
[compiler-common#31](https://github.com/gren-lang/compiler-common/issues/31).
Workaround: add the parens yourself, `(Just y) as whole`.

## A continuation line at the same column as the body above it

When the body of a block construct starts on the row *after* its keyword, the
parser scopes the body's indent to that first token's column and then requires a
strictly greater column to continue. A continuation line at exactly the **same**
column therefore ends the body early. The real Gren compiler accepts every shape
below, and elm-format agrees with the real compiler on all of them. Tracked at
[compiler-common#14](https://github.com/gren-lang/compiler-common/issues/14),
which also carries a proposed fix; the `when` half was reported separately as
[compiler-common#11](https://github.com/gren-lang/compiler-common/issues/11).

It fails two different ways. In a `let` binding or a `when` branch nothing can
absorb the stranded token, so the **file is rejected** and gren-format refuses to
format it at all:

```gren
v =
    let
        b =
            add 1
            2          -- same column as `add` -> "Expected character '='"
    in
    b
```

In a lambda body or an `if`/`else` branch an enclosing scope *can* absorb it, so
the parse **succeeds with a different AST**:

```gren
v = \q ->
        fn one
        two            -- same column as `fn`
```

is parsed as `(\q -> fn one) two` rather than `\q -> fn one two`, so `two` is not
an argument of `fn` and gets no continuation indent:

```gren
-- gren-format            -- elm-format
v =                       v =
    \q ->                     \q ->
        fn one                    fn one
        two                           two
```

The output still means the same thing to the real compiler as the input did, so
the damage is layout, plus the outright refusal in the `let` / `when` cases.

### The same misparse reached by a comment, where the columns are not equal

"Same column" is the shape that gets there without help; the general condition is
**a column that falls between the two scopes**. The body's scope is
`lineStart` — the first non-whitespace column of the row the body's first term
sits on — while the enclosing scope keeps whatever looser indent it had, and a
token in between is refused by the inner one and then absorbed by the outer one
as an argument of the lambda itself. A multi-line comment is a way to land a
token in that window, because the comment's own closing row occupies the columns
in front of it:

```gren
                    chosenSink =
                        sinks
                            |> keepIf (\{ kind, endpoint } -> not {- ¤1
   second row -} {- ¤2 -} (isEmpty endpoint) && kind /= "noop")
```

`(isEmpty endpoint)` starts at column 27: past the `let` binding's scope (21),
short of the `|>` row's `lineStart` (29). So it is not an argument of `not` — the
whole thing parses as `(\{ kind, endpoint } -> not) (isEmpty endpoint) && …`, and
`gren make` accepts the file, which settles that the real compiler reads it the
other way. Take the comment out and the same layout is a hard parse error rather
than a misparse, so the comment is not incidental to reaching it.

`tests/fuzz-idempotency.py`'s `known_upstream_issue` labels a finding
`[known: compiler-common#14]` on this shape, on two signals: a `call` whose `fn`
is a **bare** `lambda` / `if` / `when` / `let` (a parenthesized one arrives
wrapped in a `parens` node, so an unwrapped one cannot have been written), and
that call's first argument starting on a different row than its `fn` ends on.

Not seen in practice: a sweep of the 288-file `gren-format-preview/pkgs` corpus
finds no instance of any of these shapes, because real code indents the
continuation. Workaround: indent it past the first line of the body.

The full write-up, with both `--pre-ast` dumps and the `gren make` type error
that pins down the real compiler's reading, is in
`gren-format/parser-same-column-continuation-bug.md`; it was added to
compiler-common#14 on 2026-08-01.

## Output today's compiler rejects, that the next one will accept

`gren format` is built on `compiler-common`, which is the parser the **next**
Gren compiler will use. Today's released compiler is the Haskell one, and the
two do not accept exactly the same language: `compiler-common` is deliberately
more permissive about indentation. Where they differ, the formatter follows
`compiler-common` — so it can hand you a file the compiler you are using right
now refuses to build.

A bracketed pattern is where this shows up. Write one broken across rows with
its continuation indented, and today's compiler is happy:

```gren
f z =
    let
        { next
          , count
          } =
            z
    in
    next
```

`gren format` moves the `,` and the `}` back to the `{`'s own column, which is
also the column the binding starts at:

```gren
f z =
    let
        { next
        , count
        } =
            z
    in
    next
```

That is the canonical form, and `compiler-common` parses it — so the formatter's
own checks all pass: the AST is preserved and the output is a fixed point. The
Haskell compiler stops at the first continuation row, because it requires every
row inside a binding to be indented **past** the binding's start column:

```
-- UNFINISHED RECORD PATTERN ---------------------------------------- src/M.gren

I was partway through parsing a record pattern, but I got stuck here:

6|         { next
                 ^
I was expecting to see a closing curly brace next. Try adding a } here?
```

Elm rejects it too, with the same message — this is a place where Gren is
departing from what it inherited, not a bug in either parser.

The same applies to an array pattern, and in a `when` branch head as well as a
`let` binding:

```gren
g z =
    when z is
        [ a
        , b
        ] ->
            a
```

Parameter patterns are unaffected — a pattern in an argument position does not
start its line, so its continuation still clears the declaration's column.

**If you are on the released compiler**, the workaround is to keep such a
pattern on one line; `{ next, count } =` is accepted by both. This limitation
disappears when the `compiler-common`-based compiler ships, at which point the
formatted output above compiles as written.

## A binary `-` whose right operand starts at the operator's own column

`10 -` ⏎ `        3` is read by the parser as the **call** `10 (-3)` — the `-`
becomes a unary negation on the operand below instead of the subtraction
operator. The real Gren compiler reads it as subtraction (a module using it as an
`Int` compiles, which a call of `10` could not), and elm-format agrees. Tracked at
[compiler-common#35](https://github.com/gren-lang/compiler-common/issues/35),
which carries the AST dumps, the column grid and a one-line proposed fix.

The trigger is **the column**, and nothing else: the right operand starting one
past the `-`, on a later row. `argOrOperatorLoop` decides "no space after the
operator" by comparing `operator.end.col == pos.col` *after* the whitespace
parser has run, so it ignores the row. Shifting the operand one column either
way flips the parse, and no operand kind is safe:

```gren
10 -            -- MISPARSED as 10 (-3): `-` ends at col 9, `3` is at col 9
        3
10 -            -- ok: `3` is at col 10
         3
a -             -- MISPARSED: `-` ends at col 8, `b` is at col 8
       b
a -             -- ok: `b` is at col 9
        b
1.5 -           -- MISPARSED at the matching column; so are 0x10, (a), fn a
         3
10 +            -- ok at any column: only `-` has a unary form
        3
```

A blank line between the two rows makes no difference.

A comment is not needed to trigger it, but a comment is how it shows up: with
one written after the operator the misparse becomes visible in the output.
gren-format renders the negation glued to its operand, as it must, and the
comment lands between them:

```gren
subtraction =           -- input             -- gren-format's rendering
    10 - -- c                                    10
        3                                            --- c
                                                      3
```

The `-` is now inside the `--`, so the output no longer means what the input
did, and **gren-format's AST check catches this and refuses to write the file**
(`AST MISMATCH AFTER FORMATTING`) — nothing is corrupted, but the file cannot be
formatted. The message blames the formatter, which is misleading here; the
render is faithful to the tree it was handed.

**Failing is the decision, not an oversight.** There is nothing to fix on the
formatter side — a comment between `-` and its operand does not parse at all in a
*genuine* negation (`v = - -- c` ⏎ `3` is a parse error), so a negation node
carrying a leading comment can only arrive through this misparse — and no
workaround is wanted: any rendering faithful to the misparsed tree would rewrite
a subtraction the real compiler accepts. gren-format refuses the file and waits
for compiler-common#35.

**Seventeen** of `fuzz-idempotency.py`'s residual findings are this bug — the
fuzzer inserts a `--` into the gap after a `-` in `BinaryOps`, `Records` (×2),
`BinopParenOperandCommentKind` (×2), `KitchenComments` (×3), `KitchenSink`,
`LambdaPatterns`, `LetBlankLines`, `NegateParens`, `WhenBranchBody`,
`BinopLayoutByAuthor`, `BinopMixedPrecedenceBroken`, `D17PrecedenceSplit` and
`IfPredicate`. Both `fuzz-idempotency.py` and `check-decision-stability.py`
**name them on sight** (`[known: compiler-common#35]`, plus a count in the
summary) so they are not investigated again; they still count as findings and
still fail the run. They are in no baseline: when the fix ships and the
`compiler-common` dependency is bumped, they stop being reported and the
residual drops by seventeen.

Workaround for a file you need formatted today: keep the right operand on the
operator's row, or parenthesize.

## An integer literal just below 2^53 is silently rewritten

36 integers near the top of the exactly-representable range come out of
`gren format` as a *different number*. The file still parses, the AST check
passes, the output is a fixed point — and the program no longer means what it
did:

```gren
a =                             -- input               -- after gren format
    9007199254740991                                       9007199254740992
```

This is the one entry here that **corrupts** rather than refusing or laying out
awkwardly. The binary `-` bug above is caught by the AST check and the file is
left alone; this one is not caught by anything, because the corruption happens
in the *parser*, before the formatter sees a thing. Both parses agree on the
wrong number, so parse → format → reparse → AST-compare compares two identical
wrong trees, and re-formatting the corrupted output reproduces it exactly.
Nothing in this repo's gates can see it.

Nor can the formatter fix it: by the time `InsertExpressions` renders the
literal it holds `Src.Int 9007199254740992` and the author's digits are gone.

### Two causes, one per spelling

Both are the same arithmetic slip — a digit added to the accumulator *before*
being normalized, so the intermediate crosses 2^53 (where float64 spacing
becomes 2) and rounds, and the later subtraction cannot undo the rounding.

**Decimal** goes through `String.toInt`, whose kernel
(`gren-lang/core`, `src/Gren/Kernel/String.js`) does
`total = 10 * total + code - 0x30`, i.e. `(10 * total + code) - 0x30`. Tracked
at [core#134](https://github.com/gren-lang/core/issues/134); the fix is one pair
of parentheses. Affected: the **24 odd** values in
`[9007199254740945, 9007199254740991]`.

Both fixes are one-line parenthesizations, and neither can be worked around
here — when both have shipped and the `core` / `compiler-common` dependencies
are bumped, this whole entry can be deleted.

**Hex** goes through `Compiler.Parse.Number.hexFolder` in `compiler-common`,
which does `16 * acc + charCode - 48` (and `16 * acc + 10 + charCode - 65` for
`A-F`). Tracked at
[compiler-common#36](https://github.com/gren-lang/compiler-common/issues/36).
Affected: the **27** values in `[0x1FFFFFFFFFFFCA, 0x1FFFFFFFFFFFFE]` whose last
digit is `1 3 5 7 9 A C E`.

Both hex branches are broken, but on **opposite parities** — the digit branch's
intermediate is `value + 48` and the `A-F` branch's is `value + 65`, and only an
odd intermediate rounds. That is why **`0x1FFFFFFFFFFFFF` (2^53 - 1) round-trips
correctly** while 27 of its neighbours do not. It is the obvious value to probe
with and it certifies a broken path: probe one value from each branch.

The two sets overlap but neither contains the other: 9 values are broken only
when written in decimal, 12 only in hex, and **15 are broken in both**, so
"write it the other way" is not a general workaround. There is no workaround at
all for those 15 short of keeping them out of source (compute them, or read them
from data) until both fixes ship.

### Where it showed up

In this repo's own test suite. `tests/src/Test/Formatter/Format.gren` pins
`intToHex` at the `2^53 - 1` boundary:

```gren
, hexCase "2^53 - 1 (max exact JS integer)" 9007199254740991 "1FFFFFFFFFFFFF"
```

Running `gren format` over `gren-format-lib` rewrites that `…991` to `…992` and
the test then fails, because `intToHex` correctly reports `20000000000000` for
the number it was actually given. **Formatting this repo will keep re-breaking
that line** until core#134 ships and the dependency is bumped; repair it by hand
after formatting, and do not "fix" the expectation string to match.

No fuzzer here will ever reach the boundary on its own: `gen-random.py` draws
decimal literals from `0..99` and hex literals from at most 44 bits, so its
generated integers stop three orders of magnitude short.

## Wide `when` branch patterns

A `when` branch whose record (or array) pattern is too wide to fit on one line
can wrap in a way the Haskell-based Gren compiler rejects:

```gren
-- the formatter may produce:
        { aPopped = Just { first = m1, rest = ms1rest }
        , bPopped = Just { first = m2, rest = ms2rest }
        } ->
```

The Haskell-based compiler requires every continuation line of a pattern to be
indented deeper than the pattern's first character, and this layout doesn't
satisfy that. Compiling the formatted file may fail with "I was expecting to
see a closing curly brace next." Rejoin the pattern onto one line by hand
until this is resolved.

## Comment placement near invisible tokens

As described in [When the formatter can't tell what you meant](formatterRules.md#when-the-formatter-cant-tell-what-you-meant), a comment beside `=`, `:`, `|`, or an import's `as` always snaps to one
canonical side. Two different intents produce the same output.

The clearest case is a `--` at a `,` or a `|`, because the two spellings that
collapse are the two you are most likely to have meant differently. These:

```gren
v =                              v =
    { rec -- c                       { rec | -- c
        | alpha = 1                      alpha = 1
    }                                }
```

reach the formatter as the same three facts — where `rec` ends, where the comment
is, where `alpha` starts — because the `|` between them has no recorded position.
Both therefore format to the first one:

```gren
v =
    { rec -- c
        | alpha = 1
    }
```

The same is true of `[ 1 -- c` ⏎ `, 2 ]` and `[ 1, -- c` ⏎ `2 ]`, and of a union
variant's `|`. A comment you wrote on a row of its own is a *third*, genuinely
distinguishable spelling and is left alone:

```gren
v =
    { rec
        -- c
        | alpha = 1
    }
```

**The rule is "a `--` keeps the row you wrote it on"**, and the row above a
line-leading separator belongs to the item (or the record update's base) above
it. That is uniform across all three separators, which is why it was chosen — but
it costs elm-format parity on the record update, because elm-format has its own
parser, does not have to collapse anything, and renders each of the two spellings
differently. See [divergence #22](elmFormatComparison.md#divergence-22).

## A line break inside a declaration's head

A line break *inside* a declaration's keyword (e.g. `import` on one line,
the module name on the next) can cause a blank line to appear between a
comment and that declaration. The root cause is a parser bug that records the
wrong line number for keyword-led declarations:
[compiler-common#25](https://github.com/gren-lang/compiler-common/issues/25).

```gren
-- a comment
import
    String
```

formats to:

```gren
-- a comment

import String
```

with a spurious blank line pushed between the comment and the import it was
written directly above — even though writing the same import on one line
(`-- a comment` / `import String`) formats with no blank line at all. The
parser records the position of `String` (the name) rather than `import` (the
keyword), so the formatter sees a bigger gap between the comment and the
declaration than the author actually left.

## Comments near an effect module's `where` block

As described in [Comments in an effect module's header](formatterRules.md#comments-in-an-effect-modules-header),
a comment's placement near the `where { … }` block is determined by proximity
to the handler name. Changing the spacing can change where the comment ends up.
This stays as-is until the parser records positions for the missing tokens.

Concretely, the boundary is a two-column slack past the handler name's end
(`Formatter.Logical.Comments.commentInsideTrailingBracket`), so a couple of
extra spaces moves a comment out of the block:

```gren
-- inside the block (one space after the name):
effect module MyModule where { command = MyCmd {- note -} } exposing (..)

-- outside it (a few more spaces — same comment, same braces):
effect module MyModule where { command = MyCmd      {- note -} } exposing (..)
-- formats to:
effect module MyModule where { command = MyCmd } exposing (..) {- note -}
```

That is a guess, not a reading of the source, and the section below explains why
it has to be one. It is also the one limitation here that can change the
**layout** of the header rather than only a comment's home: a multi-line
`{- … -}` kept inside the block forces the block open across rows, and the same
comment moved out leaves it on one row.

```gren
effect module MyModule where { subscription = MySub {- forces the block to wrap
   second row -}
    } exposing {- tail -}
    ( a
    )
```

keeps the block open:

```gren
effect module MyModule where { subscription = MySub {- forces the block to wrap
                                                       second row -}
                             } exposing {- tail -}
    ( a
    )
```

while widening that one gap by three spaces collapses it:

```gren
effect module MyModule where { subscription = MySub } exposing
    {- forces the block to wrap
       second row -} {- tail -}
    ( a
    )
```

Both outputs are stable — each is its own fixed point and each preserves the
AST — so neither the idempotency checks nor the AST comparison objects. Only
`tests/fuzz-whitespace.py --mode stretch` can see it, and only if a corpus
fixture carries the shape; none does, because such a fixture would fail that
gate for as long as this limitation stands.

The sharpest form of this: a `--` comment written **inside** the braces, on its
own line, does not stay there. It is moved out of the block, below the module
line:

```gren
-- you wrote:
effect module MyModule where { command = MyCmd
                             -- line note
                             } exposing (..)

-- formats to (the comment is no longer inside the block; it is a top-level
-- comment now, at column 1):
effect module MyModule where { command = MyCmd } exposing (..)

-- line note
```

The comment survives — nothing is deleted — but it no longer sits beside the
handler name it was written next to.

Written past the two-column slack it does not go to column 1 either — it lands
on the header's own tail, after `exposing (..)`. A comment the author wrote
*after* `exposing (..)` then cannot stay on that row, because a `--` takes the
rest of its line, so it detaches to column 1: the place the reparse gives it,
and therefore the only placement that is a fixed point.

```gren
-- you wrote:
effect module MyModule where { command = MyCmd
    , subscription = MySub      -- three
    } exposing (..) {- four -}

-- formats to:
effect module MyModule where { command = MyCmd, subscription = MySub } exposing (..) -- three

{- four -}
```

Pinned by `HeaderComments/EffectHeaderLineCommentPushesTrailer`.

**This one cannot be fixed here.** The parser records a position for the handler
name and nothing else in the block: not the `where`, not the braces, not
`exposing`. So these two files:

```gren
effect module MyModule where { command = MyCmd
                             -- line note
                             } exposing (..)
```

```gren
effect module MyModule where { command = MyCmd } exposing (..)
                             -- line note
```

hand the formatter *byte-identical* information — same tree, same single
comment at row 2, column 30. There is no fact available to tell them apart, so
they format the same way. A `--` comment inside the braces has to be on a line
of its own (it would otherwise comment out the rest of the header), which is
exactly the case that needs the missing `}` position to place.

**A `{- … -}` is no better off** — it only looks better off because the two-column
slack usually guesses right for it. Line these two up so the comment starts at
the same column in both, and they are byte-identical too:

```gren
effect module MyModule where { subscription = MySub   {- c -} } exposing (..)
```

```gren
effect module MyModule where { subscription = MySub } {- c -} exposing (..)
```

`--pre-ast` on the two files produces the same bytes: the comment is at row 1,
column 49 in both, and the `}` that separates them is recorded nowhere. Whatever
the formatter answers, it answers for both — which is why the answer has to come
from the comment's column, and why widening a gap changes it.

**The fix used for the module's own `exposing ( … )` list does not transfer.**
There, `MakeLogical.moduleExposingClose` retires the same slack guess by
synthesizing a closing position and marking it elastic
(`lpnElasticBracketNode`), which says "anything that reaches this container is
inside it". That works because nothing follows the exposing list inside the
declaration. The `where { … }` block is not last — `exposing ( … )` follows it —
and comments genuinely belong out there. Giving the block an elastic close was
tried and measured: it fixes the whitespace flip above and then swallows
comments that were written past the block, including a `--` after
`exposing (..)`, which forces the block open across rows:

```gren
-- with an elastic where-block close, this fixture
effect module M where { command = CloseCmd, subscription = CloseSub } exposing (..) -- note

-- became
effect module M where { command = CloseCmd
                      , subscription = CloseSub -- note
                      } exposing (..)
```

Three fixtures moved that way (`EffectHeaderCloseRowComment`,
`EffectModuleOpenLineTrailer`, `EffectModuleFxWhereComment`), so the approach is
recorded here as disproven rather than left as an idea to retry.

Fixing this means the parser recording positions for the block's own tokens.

## A comment right after `exposing` doesn't sort with the first name

As described in [Exposed names sort automatically](formatterRules.md#exposed-names-sort-automatically),
a comment on its own line — attached to the *first* name in an
`exposing ( ... )` list — is a special case: the opening `(` has no position
in the AST, so the comment is placed as a header-level comment right after
`exposing`, not as a child of the first name. It renders in that same spot
every time, regardless of which name ends up first after sorting:

```gren
module ExposingListSort exposing
    ( -- describes zebra
      zebra
    , Kiwi
    , apple
    , Mango
    )
```

formats to:

```gren
module ExposingListSort exposing
    -- describes zebra
    ( Kiwi
    , Mango
    , apple
    , zebra
    )
```

A comment before any *other* name in the list (not the first) doesn't have
this issue — it travels with its name normally, as shown in
[Exposed names sort automatically](formatterRules.md#exposed-names-sort-automatically).

## A module `exposing` list's closing paren isn't recorded

The closing `)` of a module header's `exposing ( ... )` list has no position in
the AST — the parser records where each exposed *name* is, but nothing about the
brackets around them. Mostly that costs nothing: when you wrote the list across
rows, the `)` is on its own row below the last name, so a comment after that name
is recognised by its row and stays inside the list (see
[When the formatter can't tell what you meant](formatterRules.md#when-the-formatter-cant-tell-what-you-meant)).
One shape does pay for it.

An import's list doesn't pay at all — the parser records where an import ends,
`)` included, so a comment there stays on whichever side of the `)` you wrote it,
however much space you left, and however many comments you stack up.

### A comment past a flat list

When you wrote the list flat, everything is on one row and the row tells you
nothing. A comment written *inside* the brackets and one written *past* them look
alike. The formatter stops trying to tell them apart: it reads any comment after
the list's last name as belonging to the list, opens the list up, and pins the
comment above the `)`. Every way of writing it gives the same result —

```gren
module FlatClose exposing (apple, zebra) {- both names -}
module FlatClose exposing (apple, zebra)    {- both names -}
module FlatClose exposing (apple, zebra {- both names -})
module FlatClose exposing (zebra, apple) {- both names -}
```

— all four become:

```gren
module FlatClose exposing
    ( apple
    , zebra
    {- both names -}
    )
```

That is the point. Neither the spacing you left before the comment nor the order
you typed the names in changes the output, and the same module written any of
those four ways formats to the same bytes.

What you give up is hanging a comment off the **last** name of a flat list: it
is read as the list's, not that name's. A comment on any earlier name is
unaffected, because a name follows it and there is nothing to confuse it with:

```gren
module Mid exposing (apple {- just apple -}, zebra)
```

stays exactly as written. And if you do want a comment tied to the last name,
write the list vertically — there the `)` has a row of its own, which is enough
to tell the two apart:

```gren
module Vert exposing
    ( apple
    , zebra -- just zebra
    )
```

keeps the comment on `zebra`, through the sort and across reformats.

A chain of comments is treated as one unit and pinned together, in order, each on
its own line — including a link that spans rows:

```gren
module Chain exposing (zebra, apple) {- first link, and it
spans rows -} {- second link -}
```

becomes:

```gren
module Chain exposing
    ( apple
    , zebra
    {- first link, and it
       spans rows -}
    {- second link -}
    )
```

This also settles what used to be an ambiguity about a vertical list: these two
files are handed to the formatter as byte-identical ASTs *and* byte-identical
comment positions, so nothing could ever distinguish them —

```gren
module Amb exposing            module Amb exposing
    ( apple                        ( apple
    , zebra                        , zebra
      {- first -}                  ) {- first -}
      {- second -}                   {- second -}
    )
```

— and both now format to the same thing, with both comments inside the list
above the `)`. Previously the second comment was pushed out of the brackets and
became a free-floating comment above the declarations.

## A comment after the last binding in a `let`

The `in` keyword of a `let ... in` is another token with no recorded position:
the parsed `let` remembers only its bindings and its result expression, never
where `in` sat. So a comment written in the gap between the last binding and the
result can't be pinned to one side of `in` — it might be trailing the binding
above, or introducing the result below, and there's no fact to tell those apart.
gren-format always treats it as introducing the result, placing it on its own
line just below `in`:

```gren
-- you wrote:
x =
    let
        y =
            1 -- a note
    in
    y

-- formats to (the note moves below in):
x =
    let
        y =
            1
    in
    -- a note
    y
```

This is the one placement that stays put every time you reformat *and* never
misplaces a comment you really did write below `in`. elm-format, whose parser
records the `in` position, keeps a trailing-binding comment up with the
bindings instead — a divergence covered in
[Comparison with elm-format](elmFormatComparison.md#divergence-20) (point 20), with the
full reasoning for why gren-format can't follow suit.

## Block comment body indentation

A multi-line block comment's body is re-indented from its **own** structure: the
least-indented content line is placed a few columns in from the `{-`, and every
other line keeps its position *relative* to that. So hand-aligned content — an
ASCII diagram, an aligned table — keeps its shape; the block as a whole is
anchored under the comment's opener rather than pinned to the exact columns you
typed. Because the indentation is derived only from the body (never from the
whitespace around the comment), two inputs that differ only in that surrounding
whitespace format to the same output — it is whitespace-canonical.

This applies to every multi-line block comment, including the form where `{-`
sits alone on its first line. (An earlier version of gren-format treated that
opener-alone form as a "keep my exact columns" signal and left the body
verbatim; that was a divergence from elm-format, which re-indents the body the
same way described here, so it was removed.)

## Two fixtures parse a custom-type shape the language no longer allows

Since [the 24w release](https://gren-lang.org/news/161224_gren_24w), a custom
type's variant is limited to 0 or 1 argument — `type Person = Person String
Int` is no longer valid Gren; a multi-field variant must carry a record
instead (`Person { name : String, age : Int }`). The parser this project is
built on does not enforce that restriction for a chain of bare
constructor-name arguments, so `type Person = Person String Int` still parses
without error today. Tracked at
[compiler-common#32](https://github.com/gren-lang/compiler-common/issues/32).

Two of this package's own test fixtures rely on that gap —
`tests/testfiles/Declarations/TypeUnion.formatted.gren`:

```gren
type Shape
    = Circle Int
    | Rectangle Int Int
```

and `tests/testfiles/CoreExpressions/UnionLayoutByAuthor.formatted.gren`:

```gren
type WithPayloads
    = Wrap Int | Pair Int Int
```

`Rectangle Int Int` and `Pair Int Int` are both 2-argument variants — invalid
per the 24w rule, yet accepted and formatted today because of the bug above.
These fixtures will be cleaned up (reduced to 0/1-argument variants) once
compiler-common#32 is fixed and released; until then they're a known,
intentional exception to the language's current variant-arity rule, not a
formatter bug of their own. (`gen-random.py`, the property-based random-input
generator, no longer emits multi-argument variants for this same reason —
see `tests/GENERATOR.md`.)

## Very deep lambda or unary-minus nesting can overflow the stack

This one isn't a comment-placement or compiler quirk — it's an implementation
limit in the formatter itself, found by `tests/pathological-nesting.py`
(geometric-growth-and-bisect stress testing of nesting depth).

A chain of nested lambdas,

```gren
x =
    \a -> \a -> \a -> \a -> \a -> {- … repeated hundreds of times … -} a
```

or a chain of nested unary minus,

```gren
x =
    -(-(-(-(-(-( {- … repeated hundreds of times … -} 1))))))
```

crashes with `RangeError: Maximum call stack size exceeded` once nesting
passes roughly **400 levels** for lambdas or **300 levels** for unary minus.
Rendering a nested expression recurses once per level through a fairly long
call chain (dispatch → flow assembly → per-item classification → the next
level's dispatch, and so on) — around 10-15 JS stack frames per level of
*Gren* source nesting — and Node's default stack budget runs out before the
parser's own does (the parser tolerates roughly 500-700+ levels of the same
shapes, since its recursive-descent call chain is shorter per level). The
crash is immediate and clean — no hang, no corrupted output — but a file
past the threshold cannot be formatted at all.

This is not expected to matter in practice: no real Gren source this project
has ever swept (published packages, its own sources, hundreds of thousands of
generated random modules) has come anywhere near this depth, and code with
hundreds of directly nested lambdas or unary minuses is not something anyone
writes by hand. Fixing it properly means rewriting the renderer's recursive
core to use an explicit stack instead of the JS call stack (a trampoline) —
a large, invasive change judged disproportionate to a depth nothing has ever
hit. Now would we consider changing Node's own stack size; that's complete
out of scope as a solution.

Nested **record literals** used to hit a much sharper version of this
problem — not a stack overflow but an exponential-time hang, becoming
unusable well before 25 levels of nesting. That one *was* a formatter bug
(a value was rendered twice per level instead of once) and has been fixed;
record literals now nest as deep as the parser allows, same as most other
constructs.

The same double-render bug turned out to have five more sites, all needing a
*conjunction* of features rather than one construct nested deeply — which is
why the single-construct shapes above never showed it. A paren wrapping a
lambda whose body is a bracket literal (`(\q -> [ …, 1 ])`), the record
variant, a paren-wrapped pipeline as a step argument (`0 |> g ( … )`), and a
**pipeline step whose lambda argument nests through its own body**, in both the
direct-operand and the relocated form. Each rendered the same subtree two or
four times per level. All are fixed. What is left for them is only the ordinary
stack-depth ceiling described above — they run out of JS stack between 200 and
240 levels, in two cases at exactly the depth the parser gives out and in the
others slightly before it, with no exponential term anywhere.

The pipeline-lambda pair is worth spelling out, since it is the one shape here
that reads like something a person might actually write. "Direct operand" means
the parenthesized lambda is the *first* thing after the `|>`, with no function
in between, so it stays glued to the operator rather than relocating to its own
line:

```gren
x =
    v
        |> (\a0 ->
                v
                    |> (\a1 ->
                            v
                                |> (\a2 ->
                                        v
                                   )
                       )
           )
```

Written with a function in front of the lambda it takes the *relocation* path
instead — a different renderer, but it had the same duplicated render:

```gren
y =
    v
        |> f
            (\a0 ->
                v
                    |> f
                        (\a1 ->
                            v
                        )
            )
```

What multiplies is the nesting *through the lambda's body*: every level is a
pipeline step whose operand is a paren whose body is another step of the same
shape. Both forms used to render that body twice per level (the layout needs
the lambda's head line and body box separately, so it re-derived the body even
though the paren's own box already contained it). The body box now rides along
on the flow item that produced it. Depth 10 of the first form went from 21.7 s
to 0.07 s; depth 30 of either now formats in under 0.2 s.

