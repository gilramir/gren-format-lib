# Comparison with elm-format

`gren-format` is a spiritual descendent of `elm-format`, and agree on
formatted syntax in most places. We ran an audit on the
formatter's own test fixtures (`gren-format-lib/tests/testfiles/`),
converting the Gren code to Elm, ran them through
`elm-format` and catalogued every divergence. This is the full catalogue.
[Divergence #10](#divergence-10) — redundant parens — is the one that shows up
most on real code, and it is a
[settled decision](settledDecisions.md#sd4-redundant-parens-are-never-stripped)
rather than a gap.

Before the specific findings, it helps to see how alike the two tools are
underneath — that's what explains why they agree on the overwhelming majority of
code, and why the places they *don't* look the way they do.

## Table of contents

- [The idea both formatters share](#the-idea-both-formatters-share)
- [The one way they actually differ](#the-one-way-they-actually-differ)
- [Line endings: both normalize to LF](#line-endings-both-normalize-to-lf)
- [Divergence catalogue](#divergence-catalogue)
  - [#1 Blank lines: comment- vs declaration-attached](#divergence-1)
  - [#2 Multi-line block comment's closing `-}`](#divergence-2)
  - [#3 Exposing-list & import ordering](#divergence-3)
  - [#4 `import … exposing` wrapping style](#divergence-4)
  - [#5 Single-line comment after `->` in a signature](#divergence-5)
  - [#6 Union variants one-per-line (elm)](#divergence-6)
  - [#7 Record/array patterns aren't author-driven (elm)](#divergence-7)
  - [#8 `--` comment inside effect `where { }`](#divergence-8)
  - [#9 Verbatim literals vs normalization](#divergence-9)
  - [#10 Redundant parens kept](#divergence-10)
  - [#11 Doc-comment body contents](#divergence-11)
  - [#12 Comment after code stays on the line](#divergence-12)
  - [#13 Comment between two binop operands](#divergence-13)
  - [#14 Backward `<|` with a multi-line seed](#divergence-14)
  - [#15 Comment trailing a pipeline step](#divergence-15)
  - [#16 Comment just after a lambda's `->`](#divergence-16)
  - [#17 Operator chain splits at loosest ops](#divergence-17)
  - [#18 One-line `{- … -}` inside a list/record](#divergence-18)
  - [#19 Broken pipeline aligns every `|>`](#divergence-19)
  - [#20 Comment trailing the last `let` binding](#divergence-20)
  - [#21 Single-item container collapse](#divergence-21)
  - [#22 Comment beside unrecorded punctuation snaps to one side](#divergence-22)
  - [#23 A comment doesn't open the construct around it](#divergence-23)
  - [#24 Record update's line-leading comment indent](#divergence-24)
  - [#25 Blank rows added or removed around a comment](#divergence-25)
  - [#26 A `--` trailing a `<|` moves the operator](#divergence-26)
  - [#27 A parenthesized function type flattens](#divergence-27)
  - [#28 A type break gren-format doesn't record](#divergence-28)
  - [#29 A `let` annotation doesn't lift a broken type below the `:`](#divergence-29)
  - [#30 A comment run keeps the rows you wrote it on](#divergence-30)
  - [#31 A declaration that doesn't start in column 1 (an upstream bug, not a choice)](#divergence-31)
  - [#32 A lambda head broken across rows keeps its `->`](#divergence-32)
  - [#33 A lambda after `<|` keeps its head on the operator's row](#divergence-33)
  - [#34 A bare operand after `|>` keeps its head; elm-format adds parens](#divergence-34)
- [Out of scope for comparison](#out-of-scope-for-comparison)


---

## The idea both formatters share

Neither formatter reflows your code to fit a page width. There is no line-length
limit and no search for the "best" arrangement in either tool. Both lay a piece
of code out vertically only when **(a)** you wrote it across multiple lines, or
**(b)** something inside it is itself multi-line and forces the rest open.
Everything else is kept the way you wrote it. This shared "your line breaks are
your layout decisions" philosophy (see [Why this design?](howItWorks.md#why-this-design)) is
why the two formatters produce the same output almost everywhere — and why, when
they *do* differ, it is never because one of them decided a line "got too long."

## The one way they actually differ

Given that shared foundation, the mechanics really only differ in one place:
**how comments are tracked.** elm-format has its own parser that keeps every
comment pinned to the exact spot in the program where it was written, and
carries it through untouched. gren-format is built on top of the *real Gren
compiler's* parser — the same one that compiles your code — and that parser
throws comments away, because the compiler doesn't need them. So gren-format
has to put each comment back afterwards by looking at where in the source it
sat and matching that against the surrounding code. That reconstruction is the
reason the formatter is so careful about source positions, and the reason
running it twice must produce byte-for-byte identical output (see
[Background](formatterRules.md#background)). elm-format never has to solve this, because its
comments never leave the spot they were parsed into. The upside of
gren-format's choice is that it always agrees with the real language — it can
never drift from what the compiler actually accepts.

## Line endings: both normalize to LF

`gren-format` rewrites CRLF line endings to LF, even where the author wrote
CRLF. This is not a divergence: it is what `elm-format` has always done, and
the two agree on every part of it. Neither tool has a flag to preserve the
author's line endings.

**Both accept CRLF on input.** elm-format's newline parser takes either form
(`elm-format-lib/src/Parse/Whitespace.hs`):

```haskell
simpleNewline =
  do  _ <- try (string "\r\n") <|> string "\n"
```

and its string-literal scanner special-cases a `0x0D` followed by a newline
separately, in both the single-quoted and the triple-quoted reader
(`elm-format-lib/src/Parse/String.hs`), so a CRLF *inside* a literal is
consumed as one newline as well — the 0.6.0-alpha changelog entry "For Windows, CRLF
newlines no longer corrupt literal strings". gren-format gets there a different
way: it normalizes once at the read boundary (`Format.normalizeLineEndings`,
called from both places a source string enters the tool) rather than teaching
each construct about `\r`. The result is the same, multiline strings included.

**Both write LF, unconditionally.** elm-format's renderer emits `'\n'`
(`render` in `elm-format-lib/src/Box.hs`), and its writers are byte-oriented,
so the Haskell runtime does no newline translation of its own even on Windows:

```haskell
writeUtf8File path content = writeFileBS path $ encodeUtf8 content
writeStdout content = liftIO $ putBS $ encodeUtf8 content
```

**A bare CR is not a line ending in either tool.** Classic-Mac `\r`-only files
are a parse error on both sides. elm-format is explicit about it: its parser
advances the column, not the row, on a `\r`
(`elm-format-lib/src/Parse/ParsecAdapter.hs`), following parsec's actual
behaviour rather than parsec's documentation. Gren's parser does the same, so
a `\r`-only file reads as one enormous line and fails.

**A CRLF-but-otherwise-formatted file counts as unformatted in both.** This is
the consequence worth stating, because it is the one a caller can get wrong.
Take a file that is byte-identical to the formatter's output except that its
lines end in CRLF:

- `elm-format --validate` reports `File is not formatted with elm-format` and
  exits 1; `elm-format --yes` rewrites it to LF.
- `gren-format` rewrites it too, and `gren-format --diff` reports it — with an
  empty line diff, so it names the reason on a `\ ` note line instead:
  `\ Only the line endings differ (CRLF becomes LF).`

gren-format asks that question of the **raw** bytes read from disk, never of
the normalized text (`Format.isAlreadyFormatted`). Comparing normalized text
against formatted output makes such a file equal to its own output, so it is
declared already-formatted and keeps its `\r`s forever — which is exactly what
the path-argument mode did until the read boundary started returning both the
raw and the normalized form.

Line endings are therefore the one piece of the author's whitespace that
gren-format does not preserve. Everything else about how the code was written
is layout the formatter honours; see
[The idea both formatters share](#the-idea-both-formatters-share), and
[#9](#divergence-9) for the separate question of what a *literal's* spelling is
allowed to be normalized to.

## Divergence catalogue

The rest of this section catalogues the places where, given all of the above, we
made a deliberately different choice from elm-format. Each finding records the
decision and why. There is one exception: [#31](#divergence-31) is not a choice
at all but an upstream parser bug
([compiler-common#37](https://github.com/gren-lang/compiler-common/issues/37)),
kept here because it is real today and a fixture pins it. It says so at the top
of the entry, and it retires when the fix ships.

**Every entry has a fixture**, in `tests/testfiles/Divergence/`, built from that
entry's own example: the `.dirty.gren` is what the entry says you wrote
and the `.formatted.gren` is what it says gren-format produces. The mapping is
1:1 in both directions and `tests/check-divergence-index.py` fails the test run
if it stops being — an entry with no fixture, or a fixture with no entry.

| # | fixture | # | fixture |
|---|---|---|---|
| 1 | `D01BlankLinesAroundComment` | 14 | `D14BackPipeMultilineSeed` |
| 2 | `D02BlockCommentCloser` | 15 | `D15PipelineStepTrailingComment` |
| 3 | `D03ExposingAndImportOrder` | 16 | `D16LambdaArrowComment` |
| 4 | `D04ImportExposingWrap` | 17 | `D17PrecedenceSplit` |
| 5 | `D05SignatureArrowComment` | 18 | `D18InlineCommentInContainer` |
| 6 | `D06UnionOnOneLine` | 19 | `D19PipelineAlignment` |
| 7 | `D07RecordPatternAuthorDriven` | 20 | `D20LetLastBindingComment` |
| 8 | `D08EffectWhereLineComment` | 21 | `D21SingleItemCollapse` |
| 9 | `D09VerbatimLiterals` | 22 | `D22UnrecordedPunctuation` |
| 10 | `D10RedundantParens` | 23 | `D23CommentDoesNotOpen` |
| 11 | `D11DocCommentBody` | 24 | `D24RecordUpdateOwnLineComment` |
| 12 | `D12TrailingCommentStays` | 25 | `D25CommentKeepsItsRows` |
| 13 | `D13BinopOperandTrailingComment` | 26 | `D26BackPipeLineComment` |
| | | 27 | `D27ParenFunctionTypeFlattens` |
| | | 28 | `D28TypeBreakNotRecorded` |
| | | 29 | `D29LetAnnotationHeadGlue` |


1. <a id="divergence-1"></a>**Blank lines: comment-attached vs. declaration-attached**
   elm-format always puts its 2-blank-line separator immediately above the
   declaration itself, splitting a leading comment away from the code it
   documents. gren-format treats the comment as part of the declaration's
   group and puts the 2 blank lines before the comment instead (see
   [Blank lines around comments](formatterRules.md#blank-lines-around-comments)). Keeping the
   comment glued to its declaration is the more useful behavior for a doc
   comment or an explanatory note — splitting them apart the way elm-format
   does would be a regression, not a fix.

   ```gren
   -- gren-format (comment stays glued to bar; the 2 blank lines go above the comment):
   foo : Int
   foo =
       1


   -- explains bar
   bar : Int
   bar =
       2

   -- elm-format (2 blank lines land immediately above bar, splitting the comment away):
   foo : Int
   foo =
       1



   -- explains bar


   bar : Int
   bar =
       2
   ```

2. <a id="divergence-2"></a>**Multi-line block comment's closing `-}` placement**
   gren-format has no gluing or collapsing logic for a block comment at all:
   whatever line shape the author wrote — `-}` glued to the last content
   line, or on its own line — is reproduced exactly (see
   [Comments](formatterRules.md#comments)). elm-format instead always detaches `-}` onto a
   trailing line of its own as soon as a comment spans more than one source
   line — even if the author glued it to the last content line and the whole
   comment would still fit there. (A comment the author wrote on a single
   source line, `{- short -}`, is untouched by either tool — there's nothing
   to detach.)

   ```gren
   -- you wrote (and gren-format keeps, only re-anchoring "body" under "short"):
   {- short
   body -}
   foo : Int

   -- elm-format detaches to:
   {- short
      body
   -}
   foo : Int
   ```

   This is consistent with gren-format's broader "your line breaks are your
   layout decisions" philosophy; elm-format's normalizing rule is a fixed
   convention, not obviously better.

3. <a id="divergence-3"></a>**Exposing list ordering — alphabetical, deliberately independent of
   `@docs`; import sorting — narrower than elm-format.** gren-format
   alphabetizes every `exposing ( ... )` list — operators, then types, then
   values, alphabetically within each group (see
   [Exposed names sort automatically](formatterRules.md#exposed-names-sort-automatically)).

   elm-format does something different for a **module's** exposing list: when
   the module's doc comment carries `@docs` directives — as every published
   package's does — elm-format orders and groups the exposing list to *match
   the `@docs`*: one exposing line per `@docs` line, listing exactly that
   line's names in that order, so the public-API list mirrors the rendered
   documentation. (It falls back to alphabetical only when the module has no
   `@docs` at all.) **gren-format deliberately does not copy this.** Tying the
   exposing list's order to doc-comment prose would make a purely structural
   part of the file depend on documentation content — and would need a policy
   for exposed names that no `@docs` line mentions. gren-format instead keeps
   the list alphabetical and independent of the doc comment: simpler,
   predictable, and unaffected by how the docs happen to be written.

   `import` statements sort alphabetically too (they carry no `@docs`, so both
   formatters just alphabetize them) — and neither formatter lets a comment split
   that sort. Where they part company is spacing and comment placement:
   elm-format alphabetizes the whole `import` block whatever the author wrote,
   discarding blank lines and hoisting every comment above the block, while
   gren-format treats a blank line as a boundary the sort never crosses and keeps
   each comment with the import it belongs to. See
   [Import statements sort within unbroken runs](formatterRules.md#import-statements-sort-within-unbroken-runs).

4. <a id="divergence-4"></a>**`import X exposing (...)` wrapping style** When an
   import's exposing list wraps, gren-format keeps `exposing` on the `import`
   line as its last word and indents the list +4 below it:

   ```gren
   import Dict exposing
       ( Dict
       , empty
       )
   ```

   elm-format instead drops `exposing` onto its own line (`import Dict` /
   `exposing` at +4 / list at +8). gren-format deliberately does not: this makes
   a wrapped `import` line look exactly like a wrapped `module` line (both keep
   `exposing` as the header's last word, list at +4), so the two statement kinds
   are consistent. See [Import statements](formatterRules.md#import-statements) for the canonical
   shape.

5. <a id="divergence-5"></a>**A `--` written *past* a wrapped signature's `->` snaps back to the
   row above the arrow.** A `--` (or a multi-line `{- … -}`) at a type's `->`
   keeps the row it was written on — [#22](#divergence-22)'s line-leading-separator
   exception, the same rule that holds at a `,` and a `|`. Of the three ways to
   type it, the first two now match elm-format byte-for-byte:

   ```gren
   -- you wrote, and BOTH formatters produce:
   bestDiscount :
       Array { code : String, basisPoints : Int } -- comment about the result
       -> Maybe { code : String, basisPoints : Int }

   bestDiscount :
       Array { code : String, basisPoints : Int }
       -- comment about the result
       -> Maybe { code : String, basisPoints : Int }
   ```

   The third — the comment written after the arrow, still on the previous
   type's row — is where they part. The `->` carries no source position, so
   that spelling reaches gren-format as the *first* one and collapses onto it;
   elm-format has its own parser and keeps the comment below the arrow:

   ```gren
   -- you wrote:
   bestDiscount :
       Array { code : String, basisPoints : Int }
       -> -- comment about the result
       Maybe { code : String, basisPoints : Int }

   -- gren-format:                          -- elm-format:
   bestDiscount :                           bestDiscount :
       Array { … } -- comment about …           Array { … }
       -> Maybe { … }                           ->
                                                    -- comment about the result
                                                    Maybe { … }
   ```

   A **single-line** `{- … -}` at the arrow is not the exception and follows
   the general [C2](commentHandling.md#c2--when-the-parser-doesnt-record-the-punctuation-the-comment-lands-after-it)
   rule instead, leading the type after the arrow (`-> {- c -} Int`) whichever
   side of the `->` it was typed on. Written on the later side that agrees with
   elm-format; written on the earlier side it is the ordinary #22 trade.

6. <a id="divergence-6"></a>**Union type declarations always stack one variant per line in
   elm-format** Even when the author wrote
   `= Red | Green | Blue` on one line and it fits, elm-format always splits to
   one `| Variant` per line — contradicting elm-format's own general
   "respects the author's newlines" design. gren-format's author-driven rule
   (see [Custom types](formatterRules.md#custom-types)) is preferred and stays.

7. <a id="divergence-7"></a>**Record patterns (destructuring) aren't author-driven in
   elm-format** When you break a record (or array) *pattern*
   across multiple lines with no comment inside it, elm-format collapses it
   back onto one line if it fits — unlike everywhere else, your line break
   isn't preserved. gren-format's pattern layout stays author-driven like
   every other construct: written multi-line, it stays multi-line.

   ```gren
   -- you wrote (and gren-format keeps):
   view { name
        , age
        } =
       name

   -- elm-format collapses to:
   view { name, age } =
       name
   ```

   (A comment inside the pattern forces both tools to keep it open — that
   case isn't a divergence.) Same reasoning as #6: gren-format's consistent
   author-driven layout is preferred.

8. <a id="divergence-8"></a>**A `--` comment inside an effect module's `where { ... }` block escapes it**
   Both tools collapse a `where { ... }` clause to one line regardless of how
   the author wrote it, and both keep a short `{- … -}` comment on that line (see
   [Comments in an effect module's header](formatterRules.md#comments-in-an-effect-modules-header)).
   They part company on a `--` comment written inside the braces. elm-format
   breaks the whole module header apart to give the comment a line:

   ```gren
   -- you wrote:
   effect module MyModule where { command = MyCmd
                                -- line note
                                } exposing (..)

   -- elm-format:
   effect module MyModule
       where
           { command =
               MyCmd
               -- line note
           }
       exposing
       (..)

   -- gren-format (the comment leaves the block, and lands under the header at
   -- column 1 — it is a top-level comment now, not part of the header):
   effect module MyModule where { command = MyCmd } exposing (..)

   -- line note
   ```

   Fixture: `Divergence/D08EffectWhereLineComment`.

   This one is not a preference. gren-format cannot reproduce either shape,
   because the two files it would have to tell apart are byte-identical as far
   as the parser reports them — see
   [Comments near an effect module's `where` block](knownLimitations.md#comments-near-an-effect-modules-where-block).
   That elm-format can still place the comment inside the block shows its parser
   keeps something about the block's extent that Gren's does not. Fixing this is
   a matter of recording that information, not of choosing a layout.

9. <a id="divergence-9"></a>**Float literals keep the spelling you gave them; elm-format
   normalizes them.** elm-format rewrites scientific notation (`1e5` → `1.0e5`,
   `1.5E3` → `1.5e3`, `1.5e+3` → `1.5e3`); gren-format prints a float exactly as
   written (see [String literals](formatterRules.md#string-literals)). This was a
   considered design choice, not an oversight.

   **This does not extend to every literal, and the entry used to say it did.**
   Two kinds are canonicalized before the formatter ever sees them, because the
   parser hands it a decoded value rather than the source text:

   - **Integers.** `0xff` and `0x00Ff` both print as `0xFF`. elm-format does the
     same, so this is not a divergence at all — just not preservation.
   - **String and character escapes.** These are re-emitted in their shortest
     form: `"\u{000d}"` prints as `"\r"`, `'\u{0041}'` as `'A'`, and
     `"\u{1F600}"` as a literal `"😀"`. This *is* a divergence, and it runs the
     **opposite** way from the rest of this entry — elm-format expands named
     escapes (`\r` → `\u{000D}`) where gren-format contracts them, so on escapes
     it is elm-format that is closer to what a `\u{…}`-writing author typed.

   Fixture: `Divergence/D09VerbatimLiterals`, which pins all three. Both sides
   are verified against the `elm-format` binary.

   Filed upstream as
   [compiler-common#34](https://github.com/gren-lang/compiler-common/issues/34);
   the write-up is `parser-literal-spelling-bug.md`.

10. <a id="divergence-10"></a>**Redundant parens: gren-format keeps the ones you wrote, elm-format
    strips them.** If you put parens somewhere they aren't needed, gren-format
    leaves them exactly as written, in every position, with no exceptions.
    elm-format works out that they're redundant and removes them. This is a
    deliberate, settled choice, not an oversight, and it is the single most
    common difference between the two on generated code. It shows up in two
    places.

    **Around a `when`, `if`, or `let`.** Wrap one of these in parens anywhere a
    single expression is expected — at the top of a definition, as a record field,
    an array item, a lambda body, a `let` binding, a `when` or `if` branch, or the
    body of a `<|` — and gren-format keeps the parens:

    ```gren
    -- you wrote (and gren-format keeps):
    v =
        (if cond then
            one

         else
            two
        )

    -- elm-format strips to:
    v =
        if cond then
            one

        else
            two
    ```

    ```gren
    -- gren-format:
    v =
        { fld =
            (when sel is
                Just w ->
                    w
            )
        }

    -- elm-format:
    v =
        { fld =
            case sel of
                Just w ->
                    w
        }
    ```

    Note the indentation isn't the difference — it follows from the parens. Once
    the `(` is there, the block hangs off it (see [Parentheses](formatterRules.md#parentheses));
    take it away and the block starts the line instead. You can't keep the parens
    and get elm-format's columns.

    **Around a binary operator's operand.** When you parenthesize an applied
    function that an operator is applied to, gren-format keeps the parens;
    elm-format strips them, because application already binds tighter than any
    operator:

    ```gren
    -- you wrote (and gren-format keeps):
    logBase base number =
        (Gren.Kernel.Math.log number) / (Gren.Kernel.Math.log base)

    -- elm-format strips to:
    logBase base number =
        Gren.Kernel.Math.log number / Gren.Kernel.Math.log base
    ```

    Stripping in either case means working out that the parens carry no meaning —
    for an operand, that needs the operator's precedence. gren-format doesn't do
    that analysis, and won't: your grouping is preserved exactly as written, and
    nothing about the output is wrong, only more explicit than elm-format's.

    Parens around a *call argument* are not an exception — a positional slot can
    never make parens load-bearing, but gren-format keeps them anyway, for the
    same reason it keeps every other redundant paren: consistency. What you wrote
    is what you get, everywhere.

    **Side by side.** Every row below is real output — the input was run through
    both formatters, and elm-format's column is shown in Gren syntax (`case … of`
    written back as `when … is`) so the two are directly comparable.

    | you write | gren-format | elm-format |
    |---|---|---|
    | `((a)) + ((b))` | `((a)) + ((b))` | `a + b` |
    | `(a) + (b)` | `(a) + (b)` | `a + b` |
    | `(((a)))` | `(((a)))` | `a` |
    | `((f x)) + ((g y))` | `((f x)) + ((g y))` | `f x + g y` |
    | `((a))` | `((a))` | `a` |
    | `{ fld = ((a)) }` | `{ fld = ((a)) }` | `{ fld = a }` |
    | `[ ((a)), ((b)) ]` | `[ ((a)), ((b)) ]` | `[ a, b ]` |
    | `node "div" ({ foo = 1, bar = 2 }) []` | `node "div" ({ foo = 1, bar = 2 }) []` | `node "div" { foo = 1, bar = 2 } []` |
    | `fn (a) last` | `fn (a) last` | `fn a last` |
    | `fn ((a)) last` | `fn ((a)) last` | `fn a last` |
    | `fn (((a))) last` | `fn (((a))) last` | `fn a last` |
    | `fn ((f x)) last` | `fn ((f x)) last` | `fn (f x) last` |
    | `fn (({ a = 1 })) last` | `fn (({ a = 1 })) last` | `fn { a = 1 } last` |

    Two things worth reading off that table. **elm-format's stripping is about
    meaning, not appearance**: `fn ((f x)) last` keeps exactly one paren, because
    a call argument that is itself a call genuinely needs it — while
    `((f x)) + ((g y))` keeps none, because an operator's operand doesn't. It
    strips to the minimum and stops there. **gren-format's column has no
    exceptions**: every row keeps exactly what was written, at every nesting
    depth and in every position, including call arguments.

    Keeping them is a [settled decision](settledDecisions.md#sd4-redundant-parens-are-never-stripped),
    not a gap. See also [Function application](formatterRules.md#function-application).

11. <a id="divergence-11"></a>**Doc-comment body contents** elm-format reaches *inside*
    a `{-| … -}` doc comment and reformats its contents: it re-spaces `@docs`
    lines (inserting blank lines between groups), rewrites Markdown (bullet
    style `*` → `-`, single emphasis `*italic*` → `_italic_`, strong emphasis
    `__bold__` → `**bold**`), re-indents fenced example code, and inserts
    blank lines between example statements.
    gren-format leaves the entire doc-comment body exactly as the author wrote
    it. This is the largest single difference in output on real library source
    (module doc comments are long), and it is a deliberate choice: gren-format
    never rewrites the contents of a comment, only its placement. Matching
    elm-format here would mean embedding a Markdown-and-code reflow engine and
    tying code formatting to prose conventions — out of scope, and inconsistent
    with the verbatim-preservation stance in point 9. (This applies to plain
    `{- … -}` block comments and `--` line comments too — their text is always
    preserved verbatim.)

12. <a id="divergence-12"></a>**A comment written after code stays on that line; elm-format floats it
    away** When you put a comment after the last code on a line —
    after a value, or after the closing `]`/`}` of a list or record — gren-format
    keeps it right there beside the code:

    ```gren
    x =
        1 {- note -}
    ```

    ```gren
    x =
        [ 1
        , 2
        ] {- the list -}
    ```

    elm-format instead moves every such comment down and turns it into a
    separate comment below the whole definition, set off by blank lines:

    ```gren
    x =
        [ 1
        , 2
        ]



    {- the list -}
    ```

    gren-format keeps the comment next to the code it was written beside, which
    is where it is most useful — the same reasoning as point 1. This holds
    wherever a comment follows code: after a value, after a variant of a custom
    type, after a step of a `|>`/`<|` pipeline, and after the closing bracket of
    a list, a record, or a record *update* — whether that container is the whole
    definition or an argument to a call. If you write two or more comments in a
    row at the same spot, they all stay on that line together.

    The record update reached this rule late. Its closing `}` was the one
    container whose row a trailing `{- -}` could not glue onto, so the comment
    dropped to a row of its own and matched elm-format here while the list, the
    record literal and the paren all diverged. At the tail of a declaration that
    row is past the declaration's last token, which a reparse re-homes to column
    1 — so the update oscillated for ever, and the shape that agreed with
    elm-format was the broken one. It now follows the same rule as its three
    siblings.

13. <a id="divergence-13"></a>**A comment between two operands of a binop chain**
    When a broken operator chain has a comment sitting between an
    operand and the next operator, gren-format keeps it on the operand it trails;
    elm-format re-homes it to lead the following operator:

    ```gren
    -- you wrote (and gren-format keeps):        -- elm-format moves it:
    total =                                      total =
        alpha                                        alpha
            ++ beta {- note -}                           ++ beta
            ++ gamma                                     {- note -} ++ gamma
    ```

    This is the same "a comment sticks to what it trails" rule gren-format applies
    everywhere (point 12) — it isn't a binop-specific choice, so keeping it uniform
    is simpler than a special case just for operator chains. (A comment the author
    put on its *own* line, or one leading an operand, already lands the same in
    both formatters — on its own line at the operator indent, or glued in front of
    the operand.)

14. <a id="divergence-14"></a>**A multi-line seed keeps `<|` on its last line;
    elm-format drops the operator below it** For the *nesting* of a `<|` chain
    the two agree: both treat a run of `<|` as right-associative and step each
    body one indent deeper than the one above it (see
    [Pipelines](formatterRules.md#pipelines)).

    ```gren
    -- both formatters:
    result =
        String.toUpper <|
            String.append "Greetings, " <|
                String.append name "!"
    ```

    They part company when the SEED itself spans rows — a parenthesized
    expression, a multi-line record or array literal. elm-format runs `<|`
    through the same recursive machinery it uses for any other
    right-associative operator chain, and that machinery stacks rather than
    appends once the left side is no longer single-line, so the operator always
    drops to its own line below the seed's closing bracket. gren-format keeps
    `<|` glued to the seed's last line:

    ```gren
    -- elm-format:
    parenSeed =
        (x
            + y
        )
        <|
            value

    -- gren-format:
    parenSeed =
        (x
            + y
        ) <|
            value
    ```

    **The same holds in the forward direction**, where it is the *right* side
    that spans rows: gren-format keeps `seed |> items` on the row the author gave
    it and breaks only what has to break, while elm-format drops the seed and
    stacks every step below it.

    ```gren
    -- you write:            -- gren-format:          -- elm-format:
    v =                      v =                      v =
        seed |> items            seed |> items            seed
            ++ rest                  ++ rest                  |> items
                                                              ++ rest
    ```

    One rule underneath both directions: the operator's row is the author's, and
    a side that spans rows does not move it.

    gren-format's choice keeps `<|` visually consistent with `|>` — a pipeline
    reads as a pipeline regardless of direction — rather than letting the
    operator's position depend on the precedence machinery shared with unrelated
    binary operators like `++` or `::`. Verified against the `elm-format` binary
    and its `ElmFormat.Render.Box`/`ElmStructure` source
    (`forceableSpaceSepOrIndented`/`forceableSpaceSepOrStack`, which stack rather
    than append once the left side isn't single-line). Covered by the
    `BackwardPipeMultilineSeed` fixture.

15. <a id="divergence-15"></a>**A comment trailing a pipeline step** gren-format keeps it
    on that step; elm-format moves it to lead the next step (the same
    trailing-vs-leading choice as point 13, here for `|>`/`<|` instead of a
    binop operator):

    ```gren
    -- gren-format:
    x =
        value
            |> stepOne {- note -}
            |> stepTwo

    -- elm-format:
    x =
        value
            |> stepOne
            {- note -} |> stepTwo
    ```

16. <a id="divergence-16"></a>**A comment just after a lambda's `->`, on a body that
    stays on one line** gren-format keeps it inline; elm-format drops the `->`,
    the comment, and the body each onto their own line:

    ```gren
    -- gren-format:
    f =
        \x -> {- note -} x + 1

    -- elm-format:
    f =
        \x ->
            {- note -}
            x + 1
    ```

    Once the body wraps, the comment goes down **with** it and the two
    formatters agree. This holds whatever made the body wrap — you wrote it
    across rows, it contains an `if`/`when`/`let`, or a comment inside it forces
    the break:

    ```gren
    f =
        \x ->
            {- note -}
            [ 1
            , 2 -- why two
            ]
    ```

    The comment cannot stay on the `->` row here: reparsed, it is no longer on
    the body's row, so it would move down on the next format and the file would
    never settle.

17. <a id="divergence-17"></a>**A multi-line operator chain splits only at its loosest operators;
    elm-format splits at every operator.** gren-format keeps tighter-binding
    parts of a chain on one line and breaks only at the weakest operators (see
    [Binary operators](formatterRules.md#binary-operators)); elm-format puts every operator on its
    own line regardless of precedence:

    ```gren
    -- gren-format:                    -- elm-format:
    score =                            score =
        baseScore                          baseScore
            + bonusPoints * multiplier         + bonusPoints
            - penaltyAmount                    * multiplier
                                               - penaltyAmount
    ```

    This is a deliberate layout choice, not a comment-placement one: grouping by
    precedence keeps the visual structure of a chain (a `flags.x /= Nothing`
    guard, a `b * c` term) intact instead of shredding it into one line per
    operator. The break tier is the same one the formatter uses to decide the
    chain went across rows in the first place, so the two stay in lockstep. The
    layout is stable when reformatted and a comment anywhere in the chain never
    changes which operators break.

    That last clause is load-bearing, and it took two fixes to make true. A `--`
    ends its line, so a chain carrying one *has* to break — but it breaks where
    precedence says, not where the comment sits:

    ```gren
    -- what the author wrote:
    one + two -- c
              * three

    -- gren-format:                    -- and NOT:
    one                                one + two -- c
        + two -- c                         * three
          * three
    ```

    The right-hand form breaks at the tighter `*` while gluing across the looser
    `+`, so the first row reads as `(one + two) * three` — a grouping the code
    does not have, and one gren-format never produces without a comment in the
    way. It came from asking "does a comment end a row here?" of each operand on
    its own: the comment is last *within its operand*, so nothing appeared to
    follow it, and the chain missed the precedence-aware renderer entirely.
    `BinopLayout.commentBreaksBinopChain` asks it of the whole chain instead, with
    the operators interleaved back between the operands. Fixture:
    `BinopCommentPrecedenceBreak`.

    A real example from this codebase makes the case well —
    `gren-format/src/Main.gren`'s `anyFlagSet` check ORs together a run of
    `/=` comparisons (abridged here to four; the real check has more):

    ```gren
    -- gren-format:
    anyFlagSet =
        flags.show /= Nothing
            || flags.preAst /= Nothing
            || flags.postAst /= Nothing
            || flags.lpt /= Nothing
    ```

    elm-format breaks after *every* operator, including the tighter `/=`
    inside each disjunct, separating each flag from the `Nothing` it's being
    compared against:

    ```gren
    -- elm-format:
    anyFlagSet =
        flags.show
            /= Nothing
            || flags.preAst
            /= Nothing
            || flags.postAst
            /= Nothing
            || flags.lpt
            /= Nothing
    ```

    Grouping by precedence keeps each `flag /= Nothing` check reading as the
    single comparison it is; elm-format's per-operator splitting scatters it
    across two lines apiece and buries the `||` structure that's actually the
    point of the expression.

18. <a id="divergence-18"></a>**A one-line `{- … -}` inside a list or record stays on the line the author
    wrote; elm-format breaks the whole thing open.** When a comment sits inside a
    list, record, record update, or record type and the author wrote the whole
    thing on one line, gren-format leaves it alone — the comment fits, so nothing
    has to move:

    ```gren
    -- gren-format:
    arr =
        [ 1, {- one -} 2, 3 ]
    ```

    (The comment is past the comma because the comma has no source position and
    [C2](commentHandling.md#c2--when-the-parser-doesnt-record-the-punctuation-the-comment-lands-after-it)
    sends it to the later side — that half is [#22](#divergence-22). What *this*
    entry is about is the row: one row in, one row out.)

    elm-format splits the list one item per line, and lifts the comment onto a
    line of its own with a blank line above it:

    ```gren
    -- elm-format:
    arr =
        [ 1

        {- one -}
        , 2
        , 3
        ]
    ```

    The same difference shows up in a signature's record type, where elm-format
    additionally pushes every `->` part apart:

    ```gren
    -- gren-format:
    returnRecordComment : Int -> { x : Int, y : Int {- note -} }

    -- elm-format:
    returnRecordComment :
        Int
        ->
            { x : Int, y : Int

            {- note -}
            }
    ```

    This follows from [Your line breaks are your layout](howItWorks.md#why-this-design):
    the author wrote one line and one line still works, so gren-format keeps it.
    A comment that genuinely *can't* share the line does break these open — a
    `--` comment, a `{- … -}` spread over several lines, or one the author put on
    its own row (see [Block comments](formatterRules.md#block-comments-----)). Note this is the one
    place gren-format is *less* aggressive than elm-format about comments: points
    1 and 12 are also about elm-format moving comments away from the code they
    were written beside, and the reasoning is the same.

    When a comment *does* break one of these open, one more difference shows up: a
    blank line you wrote before that comment, inside the brackets, is dropped. This
    is one instance of a single rule gren-format applies everywhere (see
    [Blank lines around comments](formatterRules.md#blank-lines-around-comments)): **a blank line
    separates statements and declarations — top-level units, `let` bindings, `when`
    cases, `if`/`else` branches — and never separates the parts of a single
    expression.** A list, a record, a record type, a binop chain, and a pipeline
    are each one expression, so no blank line ever falls between their parts,
    including above a line-leading comment that belongs to one of them.
    elm-format instead sets such a comment off with a blank line above it inside
    a list or record. So
    given a record type in a signature that you wrote with a blank line before an
    inner `--` comment:

    ```gren
    -- you wrote:
    foo :
        { aa : Int

        -- note
        , bb : Int
        }
    ```

    ```gren
    -- gren-format (the record type drops below the `:` at +4; the blank is gone):
    foo :
        { aa : Int
        -- note
        , bb : Int
        }

    -- elm-format (keeps the blank line above the comment):
    foo :
        { aa : Int

        -- note
        , bb : Int
        }
    ```

19. <a id="divergence-19"></a>**When a pipeline breaks, every `|>` lines up; elm-format keeps the steps
    that still fit up on the seed's line.** This one only shows up under a
    narrow condition, so it helps to see it alongside a lookalike case that
    *doesn't* trigger the divergence.

    **Case A — the whole expression on one physical source line.** The author
    writes the pipeline flat, and the last step holds a `when`, an `if`, or a
    `let` — which always renders multi-line, however compactly it's written:

    ```gren
    -- what the author wrote (one physical line):
    v =
        seed |> fn |> gn (when sel is Just w -> w)
    ```

    Because of the `when`, the whole expression becomes multi-line.
    gren-format puts the seed on its own line and every `|>` under it, all at
    the same indent:

    ```gren
    -- gren-format:
    v =
        seed
            |> fn
            |> gn
                (when sel is
                    Just w ->
                        w
                )
    ```

    elm-format instead fills the first line with as many steps as it can and only
    starts breaking at the step that forced the issue, so `seed` and `fn` share a
    row while the rest do not. And then, the two "|>" misalign:

    ```gren
    -- elm-format (case instead of when):
    v =
        seed |> fn
            |> gn
                (case sel of
                    Just w ->
                        w
                )
    ```

    We keep the aligned form on purpose. A pipeline is a list of steps, and
    reading it means scanning the `|>` column; elm-format doesn't do that.
    Filed upstream as [elm-format#842](https://github.com/avh4/elm-format/issues/842).

    **Case B — same pipeline, but the block is already broken across its own
    lines.** The `|>` chain itself is still written flat; only the `when`/`case`
    inside `gn`'s argument spans multiple rows in the source:

    ```gren
    -- what the author wrote (|> chain flat, block pre-broken):
    v =
        seed |> fn |> gn (when sel is
            Just w ->
                w
        )
    ```

    Both formatters now agree on the fully aligned shape — byte-for-byte the
    same except for the `when`/`case` keyword:

    ```gren
    -- gren-format AND elm-format (case instead of when for the latter):
    v =
        seed
            |> fn
            |> gn
                (when sel is
                    Just w ->
                        w
                )
    ```

20. <a id="divergence-20"></a>**A comment trailing the *last* `let` binding drops below `in`; elm-format
    keeps it with the bindings.** When you write a comment after the value of the
    *last* binding in a `let`, gren-format moves it onto its own line after `in`,
    at the result-expression column. elm-format keeps it at the bindings' indent,
    above `in`:

    ```gren
    -- you wrote:
    x =
        let
            y =
                1 -- a note about y
        in
        y

    -- gren-format (the note drops below in, at the body column):
    x =
        let
            y =
                1
        in
        -- a note about y
        y

    -- elm-format (the note stays with the bindings, above in):
    x =
        let
            y =
                1

            -- a note about y
        in
        y
    ```

    This one is **forced by a missing fact, not a taste preference**, and it
    cannot be matched here. The `in` keyword has no recorded source position —
    the parsed `let` is only `{ defs, body }` (see
    [Comment placement near invisible tokens](knownLimitations.md#comment-placement-near-invisible-tokens)) —
    so a comment written in the gap between the last binding and the result is
    *positionally indistinguishable* between "trailing the last binding" (which
    elm-format renders above `in`) and "leading the result" (which belongs below
    `in`). gren-format resolves the ambiguity one way for all such comments:
    route them below `in`. That is the only choice that is both always stable
    when reformatted **and** always correct for a comment the author genuinely
    wrote below `in` — a comment that really does lead the result must not be
    dragged up into the bindings.

    Matching elm-format for the trailing-binding case specifically would need to
    tell the two intents apart, and the only signal available locally — whether
    the comment shares a row with the binding's value — is destroyed by the very
    act of formatting: once the comment is moved onto its own line above `in`, a
    reformat no longer sees it as same-row and drops it back below `in`, so the
    column oscillates on every pass. Routing below `in` avoids that entirely. The
    output is stable when reformatted.

21. <a id="divergence-21"></a>**A single-item container (record, update, or array) whose contents fit
    collapses to one line; elm-format keeps anything you broke inside the brackets
    expanded.**

    ```gren
    -- you wrote:
    v =
        { x =
            1
        }

    -- gren-format (one field, value fits → collapses):
    v =
        { x = 1 }

    -- elm-format (keeps every newline you wrote inside the braces):
    v =
        { x =
            1
        }
    ```

    **What gren-format is doing.** The signal that decides whether a bracketed
    container renders one-per-line is *a gap between its items* — some item
    starting on a row below where the previous item ended (`itemsSpanRows`). That
    is what "you broke this" means for a list: `[ 1, 2\n, 3 ]` has a gap before
    `3`, so it opens; `[ 1, 2, 3 ]` has none, so it stays flat. A break *inside* a
    single item — dropping a field's value below its `=`, or putting the one field
    on its own row — is not a gap *between* items, so it doesn't open the
    container; the value is then laid out on its own (and if it fits, it fits).
    With only one item there can never be a between-item gap, so a single-item
    container collapses unless its one item is itself unavoidably multi-line.

    **Why this is consistent.** It is one rule, applied to every bracketed
    container. A single-item *array* collapses exactly as a single-field record
    does — `[ 1\n]` formats to `[ 1 ]` — and multi-item containers of either kind
    honour the break. Records and arrays share the same `itemsSpanRows` signal, so
    there is no record-vs-array or one-field-vs-many special case; the behaviour
    falls straight out of "a break between items is your layout; a break inside one
    item is that item's business."

    **Why it differs from elm-format.** elm-format uses a simpler signal — *any*
    newline between the brackets opens the container — so it keeps a single-field
    record or single-item array expanded whenever you put a newline anywhere in it.
    Both formatters still agree once there is real structure to preserve: a second
    item on its own row, or an item that genuinely renders across rows (an
    `if`/`when`/`let`, or one long enough to wrap) opens the container in both.

    **The trade-off (and why this may be revisited).** This is the one place
    gren-format is *less* author-driven than its usual "your line breaks are your
    layout" rule: for a single-item container you cannot force it to stay expanded
    by hand — a lone fitting value always collapses. The argument for the current
    behaviour is that item-per-line breaks carry structure (a list reads down its
    items) while a lone value-break carries none — it is just where a line happened
    to wrap — so collapsing what fits keeps a one-item container in its natural
    compact form, the same way a [binary operator chain](formatterRules.md#binary-operators) that
    fits collapses. The argument against is that it quietly overrides an intent the
    author did express. If that agency wins out, the change is local — make
    `itemsSpanRows` also count a single item that spans rows — but note it would
    then apply to arrays too (`[ 1\n]` would stay open), which is why it is one
    deliberate rule here rather than a record-only tweak. It is stable when
    reformatted either way.

22. <a id="divergence-22"></a>**A comment beside punctuation the parser throws away snaps to one
    canonical side; elm-format keeps the side you wrote it on.** This is the
    single largest family of comment-placement differences between the two
    formatters, and unlike the rest of this catalogue it is **not a preference** —
    it is forced, for the same reason as [#20](#divergence-20), which is one
    instance of it.

    elm-format has its own parser and keeps every comment pinned to the exact
    spot it was written. gren-format is built on the real Gren compiler's parser,
    which records a source position for a binary operator (`+`, `|>`, `++`, …)
    and for the brackets `(`, `)`, `[`, `]`, `{`, `}` — and for nothing else that
    separates two pieces of an expression:

    | punctuation | position in the AST? |
    |---|---|
    | `+` `++` `\|>` `<\|` … | **yes** — `Binops.operator : Located String` |
    | `(` `)` `[` `]` `{` `}` | **yes** — the expression's own `start` / `end` |
    | `=` `:` `\|` `,` `->` | no |
    | `if` `then` `else` `when` `is` `let` `in` | no |
    | an import's `as` and alias name | no |

    For anything in the bottom half of that table, `x {- c -} TOKEN y` and
    `x TOKEN {- c -} y` reach the formatter as *the same three facts* — where `x`
    ends, where the comment is, where `y` starts. The only thing that would tell
    them apart is how wide the whitespace gaps are, and reading that would break
    the property that formatting is insensitive to the spacing you used
    (`x  =  1` and `x = 1` must format alike). So gren-format picks one side for
    all of them and documents it; every case is listed with an example in
    [When the formatter can't tell what you meant](formatterRules.md#when-the-formatter-cant-tell-what-you-meant).

    The side it picks is the **later** one — the comment lands after the
    separator, not before it ([rule C2](commentHandling.md#c2--when-the-parser-doesnt-record-the-punctuation-the-comment-lands-after-it)):

    ```gren
    -- both of these:                     -- gren-format:
    { field {- why -} = compute 1 }       { field = {- why -} compute 1 }
    { field = {- why -} compute 1 }

    [ 1 {- c -}, 2 ]                      [ 1, {- c -} 2 ]
    [ 1, {- c -} 2 ]

    { rec {- c -} | a = 1 }               { rec | {- c -} a = 1 }
    { rec | {- c -} a = 1 }

    when sel {- c -} is                   when sel is
    when sel is {- c -}                       {- c -}
                                              Just w ->
    ```

    elm-format renders each of those two spellings differently, so exactly one of
    the two matches it and the other diverges. There is no version of this that
    matches both.

    **A `--` (or a multi-line `{- … -}`) at a `,`, a `|`, or a broken
    signature's `->` is the exception** and keeps the row it was written on.
    Those separators *lead* their line, so a comment above one strands nothing —
    it sits at the separator's own column — and a comment that ends its row is
    genuinely tellable apart: it is on the previous item's row, or on a row of
    its own. Two spellings, two outputs, both fixed points:

    ```gren
    -- you wrote, and gren-format keeps:
    [ apple -- the red one        { rec -- about the base       = A -- about A
    , banana                          | alpha = 1              | B
    ]                             }

    [ apple                       { rec                         = A
    -- about banana                   -- about alpha            -- about B
    , banana                          | alpha = 1              | B
    ]                             }

    foo :                         foo :
        Int -- about Int              Int
        -> String                     -- about Int
                                      -> String
    ```

    A line-leading comment sits at the **separator's** column in all of them —
    the `,`, the update's `|`, the union's `|`, the signature's `->` — not
    indented
    under the item above it. That is the same column rule
    [#24](#divergence-24) states for a record update, holding at every
    line-leading separator.

    The `->` is the one member of the family where the exception *gains*
    elm-format parity rather than trading it: the comment-free layouts already
    agree byte-for-byte, so keeping the row makes both spellings above match
    elm-format exactly (see [#5](#divergence-5)). At a `,` and a `|` the
    exception matches on the first spelling only, and at a record update on
    neither — as below.

    The third spelling — the comment written *after* the separator, still on the
    previous item's row — is positionally identical to the first (the separator
    has no position, so all gren-format sees is "item ends, comment, next item on
    the following row"), and collapses onto it:

    ```gren
    -- you wrote:                 -- gren-format:
    [ apple, -- the red one       [ apple -- the red one
      banana ]                    , banana
                                  ]

    { rec | -- about the base     { rec -- about the base
        alpha = 1 }                   | alpha = 1
                                  }

    foo : Int -> -- about Int     foo :
        String                        Int -- about Int
                                      -> String
    ```

    **This is where the record update costs parity, and it is worth stating
    plainly.** elm-format has its own parser, does not have to collapse anything,
    and renders all three spellings differently. For a list and a union,
    gren-format's answer matches elm-format on the first spelling and diverges on
    the third. For a record update it matches on *neither*: elm-format renders the
    first spelling in a third way again, floating the comment onto its own line
    at an indent that varies with how deeply the update is nested (the column
    [#24](#divergence-24) is about).

    ```gren
    -- you wrote:            -- gren-format:        -- elm-format:
    { rec -- c               { rec -- c             { rec
        | alpha = 1 }            | alpha = 1          -- c
                             }                          | alpha = 1
                                                    }

    { rec | -- c             { rec -- c             { rec
        alpha = 1 }              | alpha = 1            | -- c
                             }                            alpha = 1
                                                    }
    ```

    Sending both record-update spellings past the `|` instead would match
    elm-format on the second one, at the price of making the record update the
    one line-leading separator that moves a `--` off the row it was written on.
    A rule that holds at `,`, at a union's `|` and at a record update's `|`
    alike is worth more than parity on one spelling of one construct. The choice
    costs 600 cells of the comment axis and nothing in `core/`,
    `compiler-common/`, `compiler-node/` or this repo — the spelling does not
    occur in real code, in any of its three forms.

    Three constructs do not follow C2. Since both spellings collapse into one
    output, the side chosen decides **which of the two authors gets their text
    back unchanged**; C2 serves the one who writes the comment after the
    separator, and these three serve the one who writes it before:

    - an `exposing ( … )` list, whose items are *sorted* and whose comment
      ownership `SortSymbols` models the other way round — the one structural
      reason of the three;
    - a union variant's `|`, where a note beside a variant reads as a note
      about that variant (elm-format breaks the union open around the comment
      on either side, so no side gains parity);
    - an import's `as`, where the comment stays beside the module name it
      annotates (elm-format keeps each side as written, so this matches it on
      the before-`as` spelling and diverges on the other).

    The last two are **stated preferences, not forced choices**. The parser
    records no more at a union's `|` than at a record update's: both spellings
    of each arrive byte-identical, so either construct could be made to serve
    either author. Both are kept — the two `|`s deliberately
    answer the same question in opposite directions, and a single-line `{- -}`
    beside a `|` occurs nowhere in the 592-file package corpus, in either
    position, in either construct.

    Where the token **is** recorded, gren-format keeps the side you wrote it on
    and agrees with elm-format. A comment just inside an opening bracket stays
    inside it, and one just past a closing bracket stays outside:

    ```gren
    -- both formatters:
    [ {- primary -} 1, 2 ]
    { {- the state -} rec | a = 1 }
    fn a { rec | a = 1 } {- c -} last
    ```

    A *run* of them behaves the same way, all-or-nothing: every comment of the
    run rides the first item's line, or — as soon as one of them cannot ride,
    being a `--` or a multi-line `{- … -}` — the whole run stands on its own
    rows.

    ```gren
    -- both formatters:
    [ {- a -} {- b -} 1, 2 ]
    ```

    A record update is where the two halves meet: its `{` and its base name are
    both recorded, so a comment before the base is placed exactly, and only a
    single-line `{- -}` after it — where nothing but the `|` remains, and the two
    spellings really are identical — is canonicalized.

23. <a id="divergence-23"></a>**A comment never breaks the construct around it further open than you
    wrote it; elm-format opens the construct to give the comment room.**
    [#18](#divergence-18) is this rule for lists and records and
    [#16](#divergence-16) is it for a lambda's `->`; it holds everywhere else
    too. gren-format adds no line breaks the code didn't already need — it just
    puts the comment where it goes and leaves the rest alone:

    ```gren
    -- you wrote (and gren-format keeps):    -- elm-format:
    v =                                      v =
        fn                                       fn
            -- note                              -- note
            <| 1                                 <|
                                                     1

    -- (elm also pulls the comment and the `<|` back to the seed's own column;
    --  that half is point 14. What this entry is about is the `1`, which
    --  elm-format gives a row of its own and gren-format leaves on the `<|`.)
    ```

    ```gren
    -- you wrote (and gren-format keeps):    -- elm-format:
    v =                                      v =
        when sel is                              case sel of
            Just -- note                             Just
                w ->                                     -- note
                1                                        w
                                                         ->
                                                             1
    ```

    elm-format's rule is that a comment inside a construct forces every part of
    that construct onto its own line — so a `<|` loses its operand, and a `when`
    pattern is split from its own `->`. gren-format treats a comment as something
    to place, not as a reason to re-lay-out working code.

24. <a id="divergence-24"></a>**A record update's line-leading comment sits at
    the field indent; elm-format hangs it two columns past the `{`.** A comment the author wrote on
    a row of its own in the `|` gap stays on a row of its own
    ([#22](#divergence-22)), and gren-format puts it where the `|`/`,` field lines
    are — 4 past the `{`, the same indent every other part of the update uses (see
    [Record updates](formatterRules.md#record-updates)):

    ```gren
    -- gren-format:            -- elm-format:
    v =                        v =
        { rec                      { rec
            -- note              -- note
            | a = 1                  | a = 1
        }                          }
    ```

    elm-format's column lines up with nothing: not the `{`, not the fields, not
    the closing `}`. gren-format keeps the update's inner columns uniform.

    **The same holds at every `|`, not just a record update's** — the comment
    axis's type contexts reach the other two, and both answer the same way. gren-format puts the comment at the column of the line it leads;
    elm-format hangs it two columns past the enclosing opener, where again
    nothing lines up with it:

    ```gren
    -- an extensible record TYPE's `|`
    -- gren-format:            -- elm-format:
    foo :                      foo :
        { r                        { r
            -- note                  -- note
            | a : Int                    | a : Int
        }                          }
        -> Int                     -> Int

    -- a union's `|`
    -- gren-format:            -- elm-format:
    type U                     type U
        = A Int                    = A Int
        -- note                      -- note
        | B Int                    | B Int
    ```

25. <a id="divergence-25"></a>**gren-format adds and removes no rows around a
    comment; elm-format re-spaces around it, in both directions.**
    [#23](#divergence-23) is about elm-format breaking
    the *code* open to make room for a comment. This is the other half: what
    elm-format does to the rows around it. It will add a row gren-format
    doesn't, and it will take one away — which direction depends on where the
    comment is, and neither is something gren-format does at all.

    **It adds a blank line above a line-leading comment inside a container:**

    ```gren
    -- you wrote (and gren-format keeps):    -- elm-format:
    v =                                      v =
        { a = 1                                  { a = 1
        -- note
        , b = 2                                  -- note
        }                                        , b = 2
                                                 }
    ```

    **And with a multi-line comment it re-lays-out the body too**, putting the
    closing `-}` on a row of its own at the container's indent. gren-format
    leaves the delimiters where you wrote them and only re-indents the
    continuation rows:

    ```gren
    -- you wrote (and gren-format keeps):
    v =
        [ 1
        {- note
           second row -}
        , 2
        ]

    -- elm-format (blank row added above, and `-}` given its own row):
    v =
        [ 1

        {- note
           second row
        -}
        , 2
        ]
    ```

    This is the same rule as the blank line above, not a second one — both are
    "what elm-format does to the comment's own rows". It is listed separately
    because it is **the single largest family in the comment-parity baseline**.

    A single-line `{- -}` in front of an operator is not part of it. That rides
    the operator's row, the same answer `++` gives, and the two formatters agree
    on it byte for byte:

    ```gren
    -- you wrote:            -- gren-format AND elm-format:
    w =                      w =
        fn                       fn {- note -} <|
            {- note -}               one
            <| one
    ```

    A comment that *cannot* ride — a `--`, or a `{- … -}` spread over rows —
    still keeps the row the author gave it, and what elm-format then does to the
    operand is [#23](#divergence-23), not this entry.

    gren-format's rule is the one in [Your line breaks are your
    layout](howItWorks.md#why-this-design), applied to comments as well as code:
    a comment occupies the rows you wrote it on. Nothing is floated to give it
    air ([C5](commentHandling.md#c5--gren-format-adds-nothing-around-a-comment)),
    and nothing is pulled up to close a gap you left.

26. <a id="divergence-26"></a>**A comment trailing a `<|` rides that operator's row;
    elm-format drops it below onto the body's rows.** gren-format's flat `<|`
    layout keeps the operator on the seed's line with the body after it
    (`fn <|` / `····body`). A `--` runs to end of line, so the body cannot follow
    it on that row — but the operator can keep its place and take the comment
    with it, which is the same "a comment sticks to what it trails" rule as
    [#13](#divergence-13) / [#15](#divergence-15). elm-format keeps the operator's
    row too and re-homes the comment onto the body's row instead:

    ```gren
    -- you wrote:                -- gren-format:        -- elm-format:
    [ fn <| -- c                 [ fn <| -- c           [ fn <|
            one ]                    one                    -- c
                                 ]                          one
                                                        ]
    ```

    The trade is which of the two things moves: gren-format preserves the
    comment's attachment and pays for it with the body's row, elm-format
    preserves the body's row and pays for it with the attachment.

    **A multi-line `{- … -}` there answers the same way** — it cannot ride the
    body's line either, so it rides the operator's and the body drops below it:

    ```gren
    -- you wrote:            -- gren-format:          -- elm-format:
    fn <| {- c               fn <| {- c               fn <|
       second row -}                  second row -}       {- c
        one                      one                         second row
                                                          -}
                                                          one
    ```

    A comment's own rows are no reason to move the operator, and every spelling
    of this code — no comment, `--`, ridable single-line `{- -}`, multi-line —
    puts the `<|` on the seed's row. Fixture
    `BinopsAndPipelines/BackwardPipeMultilineOperatorRowComment`.

    What makes gren-format's the more consistent of the two is `|>`. Both
    formatters agree, byte for byte, that a `--` trailing a **forward** pipe stays
    on it (`|> -- c` / `····step`) — so elm-format is treating the two pipeline
    operators differently and gren-format is treating them the same. Every other
    cell in this family agrees: a single-line `{- c -}` after either operator, and
    either operator with no comment at all. This entry is exactly the `<|`-plus-`--`
    corner.


27. <a id="divergence-27"></a>**A parenthesized *function* type is flattened back onto
    one line; elm-format keeps the break.** When you write the type across rows,
    gren-format keeps your break — inside a record type, inside parens, and
    between `->` segments alike (see
    [Type signatures](formatterRules.md#type-signatures)). The one place it
    still flattens is an arrow-joined type inside parens:

    ```gren
    -- you write:            -- gren-format:                 -- elm-format:
    parened : (Int           parened : (Int -> Int) -> Int   parened :
        -> Int) -> Int                                           (Int
                                                                  -> Int
                                                                 )
                                                                 -> Int
    ```

    The break vanishes and the signature stays flat with it. The reason is
    mechanical rather than considered: an arrow-joined type has to break
    **before** each `->` — the per-segment shape `makeSignatureBox` builds at
    the top level — and that shape is not yet rendered inside a `ParenBlock`. A
    generic vertical flow would emit `(Int ->` ⏎ `····Int`, matching neither
    formatter's canonical layout, so the flat form is kept until the segment
    renderer reaches inside parens.

    The signature staying flat is not a second choice; it follows from the
    first. A signature breaks only for a break that *survives* rendering — one
    broken around a break that vanished would read as a one-row type on reparse
    and flip straight back. See `SignatureSegmentBreaks`.

    A parenthesized **application** has no such problem and keeps its break:

    ```gren
    -- gren-format:        -- elm-format (same break, parens stripped, [#10](#divergence-10)):
    parenedApp :           parenedApp :
        (Array                 Array
            Int                    Int
        )                      -> Int
        -> Int
    ```


28. <a id="divergence-28"></a>**A type break gren-format doesn't record is flattened;
    elm-format keeps every one.** When you write a type across rows, gren-format
    keeps the break in three places — between `->` segments, between a record
    type's fields, and inside a parenthesized *application*. Anywhere else in a
    type there is nothing to hold the author's layout, so the break is lost:

    ```gren
    -- you write:              -- gren-format:        -- elm-format:
    type alias T =             type alias T =         type alias T =
        Array                      Array Int              Array
        Int                                                   Int

    foo : { a :                foo : { a : Int }      foo :
        Int }                                             { a :
                                                              Int
                                                          }

    foo : Array (Array         foo :                  foo :
        Int)                       Array (Array           Array
                                           Int                (Array
                                         )                        Int
                                                                )
    ```

    The three cases are one cause. A type is built as a flat run of leaves —
    `InsertTypes.typeWithArgs` splices its argument nodes straight into the
    parent flow — so there is no container to hold "the author broke this".
    Only three things in a type carry an author-layout flag at all: the `->`
    segmentation, `itemsSpanRows` over a record's fields, and a parenthesized
    application. `itemsSpanRows` compares each
    field's *start* against the previous field's *end*, which is why a break
    inside a single field, or between `{` and the first field, is invisible to
    it.

    **The record half is not a type question.** `itemsSpanRows` is shared with
    expression records and arrays, so `v = { a =` ⏎ `1 }` collapses to
    `{ a = 1 }` in exactly the same way. Changing it moves every bracketed
    literal in the corpus, which is why it is catalogued rather than fixed.

    Related: [#27](#divergence-27) is the same phenomenon for a parenthesized
    *function* type, kept separate because its fix is different (the
    per-`->`-segment shape rendered inside a `ParenBlock`, not an author-layout
    flag).

29. <a id="divergence-29"></a>**A `let` binding's annotation keeps a broken type on the
    `name :` line; elm-format lifts it below.** A top-level signature that
    breaks puts the type on its own rows under `foo :`. A `let` binding's
    annotation is not rendered by `makeSignatureBox` at all — it is an ordinary
    token flow (`bnd`, `:`, the type) — so a type that breaks stays glued to the
    `bnd :` line:

    ```gren
    -- you write:                -- gren-format:        -- elm-format:
    let                          let                    let
        bnd : (Array                 bnd : (Array           bnd :
              Int)                           Int                Array
        bnd =                              )                        Int
            one                      bnd =                  bnd =
    in                                   one                    one
    bnd                          in                     in
                                 bnd                    bnd
    ```

    This only shows up for a type whose break gren-format keeps *and* which does
    not drop of its own accord. A multi-line **record** type already drops below
    the `:` here and matches elm-format byte-for-byte, because a dropping record
    is a flow-level rule (`FlowPolicy`'s `DropBlock`) rather than a signature
    one — so the shape above is the parenthesized case specifically, and it
    carries [#10](#divergence-10) with it.

    It is an inconsistency rather than a preference: the same type under a
    top-level `foo :` does lift. Lifting it here means either routing a `let`
    annotation through `makeSignatureBox` or giving a multi-line `ParenBlock`
    the same drop behaviour a record has, and both reach well beyond `let`.


30. <a id="divergence-30"></a>**A comment RUN keeps the rows you wrote it on;
    elm-format re-decides them per context.** Two or more comments in one gap are
    a *run*, and gren-format never moves a member between rows: written on one
    row they stay on one row, written on separate rows they stay apart. That is
    rule [C7](commentHandling.md#c7--comments-written-together-stay-together-comments-written-apart-stay-apart).

    It holds in every context but one: a run written *just inside an opening
    bracket*, or between a pipeline step's operator and its operand, is laid out
    all-or-nothing instead, because the authored row is not recorded for it — see
    [Known limitations](knownLimitations.md#a-comment-run-just-inside-a-bracket-doesnt-keep-its-rows).
    That case happens to agree with elm-format, so it costs nothing here.


    elm-format decides per context instead, and the two answers disagree in both
    directions. Measured, same run and same term, only the context changed:

    ```gren
    -- you write (one row):        -- gren-format:          -- elm-format:
    \q ->                          \q ->                    \q ->
        {- a -} {- b -} { x = 1        {- a -} {- b -}          {- a -}
        , y = 2 }                      { x = 1                  {- b -}
                                       , y = 2                  { x = 1
                                       }                        , y = 2
                                                                }
    ```

    ```gren
    -- you write (separate rows):  -- gren-format:          -- elm-format:
    fn                             fn                       fn
        (\a ->                        (\a ->                   (\a ->
            g a                            g a                      g a
            {- one -}                   {- one -}                {- one -} {- two -}
            {- two -}                   {- two -}               )
        )                              )
    ```

    elm-format stacks a leading run one-per-row after a lambda's `->`, a
    declaration's `=`, a `let` binding's `=` and a `when` branch's `->`, and keeps
    it on one row in an `else` branch, a record field value, a `<|` body and a
    call's argument list — while joining a run the author *did* split, in the
    second shape above. gren-format asks one question in all eight positions:
    what did you write?

    **The trade is deliberate.** The rule is simpler to state
    and to predict than elm-format's, and it is the only one under which the
    formatter never invents or destroys a row break inside a run. It costs
    agreement in the four stacking contexts and gains it in the other four; the
    single-comment axis is unaffected, since a run needs two.


31. <a id="divergence-31"></a>**gren-format accepts a declaration that doesn't
    start in column 1; Elm's parser rejects the whole file — and so does the real
    Gren compiler.** This is the one entry in the catalogue that is **not** a
    deliberate choice. It is an upstream parser bug,
    [compiler-common#37](https://github.com/gren-lang/compiler-common/issues/37),
    and it goes away when that ships. It is catalogued anyway because the
    behaviour is real today and a fixture pins it, and because — like
    [#8](#divergence-8) — it is a syntax-*acceptance* difference rather than a
    layout one: elm-format never gets as far as formatting, so there is no Elm
    rendering of the program to agree or disagree with.

    ```gren
    foo : a
    {- lead -} foo =      -- gren-format: parses, formats, is a fixed point
        one               -- elm-format:  Unable to parse file <STDIN>:5:13
    ```

    Elm requires a top-level declaration to begin in column 1 (and a `let`
    binding to hold its block's column). **Gren has the same rule.** What does
    not have it is `compiler-common`, the parser gren-format is built on: the
    Haskell compiler guards each declaration with
    `Space.checkFreshLine E.DeclStart` (literally `col == 1`) in `chompDecls`,
    and `compiler-common`'s `declarationLoopParser` has no equivalent test. So a
    comment written in front of a declaration's name gets through the parser —
    and because gren-format never moves a comment off the row it was written on
    ([C7](commentHandling.md#c7--comments-written-together-stay-together-comments-written-apart-stay-apart), #25), the
    name stays right of it in the output too. Handed that same file, gren 0.6.6
    answers `EXPECTING DEFINITION` — "Type annotations always appear directly
    above the relevant definition, without anything else in between. (Not even
    doc comments!)".

    **The comment is not what is being permitted.** The same declaration
    indented with no comment anywhere parses just as well, and there gren-format
    normalizes the name back to column 1:

    ```gren
    -- you write:                     -- gren-format:
    deeplyIndentedBody =              deeplyIndentedBody =
                one                       one


            noCommentAnywhere =       noCommentAnywhere =
        two                               two
    ```

    gren 0.6.6 refuses that one as well, and there it names the rule outright:
    `UNEXPECTED EQUALS` … "I need all definitions to be indented exactly the same
    amount, so the problem may be that this new definition has too many spaces in
    front of it."

    So the difference is about what the two *parsers* accept, and the comment is
    only what makes the accepted shape survive into the output. In a `let` the
    comment takes a row of its own, so the binding name normalizes and only the
    input is un-Elm-able.

    Whether an indented declaration is reached at all depends on what precedes
    it rather than on any column rule: a declaration above it absorbs the name as
    an application argument whenever it can, and what ends that declaration is
    its body's own indent scoping, never a module-level column test.
    `foo : Int` ⏎ `{- lead -} foo =` is refused by **compiler-common** too —
    `Int` takes `foo` as a type argument — which is why the example above
    annotates `foo : a`.

    On its own this costs only elm-format parity. Combined with
    [compiler-common#14](https://github.com/gren-lang/compiler-common/issues/14)
    it does real damage: at the top level the stray row of a mis-scoped body is
    read as the head of a declaration nobody wrote, and the file is silently
    rewritten into a different program. See
    [At the top level it rewrites the file instead of refusing it](knownLimitations.md#at-the-top-level-it-rewrites-the-file-instead-of-refusing-it).

    `matrix-syntax.py --comments` generates 73 such cells, skips oracle 4 on them
    and counts them apart as `no-elm-twin` rather than blaming its translator.
    Translating them by moving the comment onto its own row — which both
    languages accept — is deliberately **not** done: that would ask elm-format
    about a different program and manufacture a divergence out of nothing. Those
    73 cells rest on a shape the real Gren compiler rejects as well, so when #37
    ships they become inputs `compiler-common` itself refuses and the generator
    has to stop emitting them — this entry and that bucket retire together.


32. <a id="divergence-32"></a>**A lambda head broken across rows keeps its `->`
    on the row of whatever precedes it; elm-format gives the `->` a row of its
    own.** gren-format treats the arrow as trailing punctuation on the head's
    last row, the same way it treats the `->` of a one-row head. elm-format
    breaks a broken head into one part per row, and the arrow is a part.

    ```gren
    -- you write:
    subtotal items =
        items
            |> Array.map (\{ quantity -- always >= 1, validated at the door
                , unitPriceCents } -> quantity * unitPriceCents)
            |> Array.foldl (+) 0
    ```

    ```gren
    -- gren-format:                           -- elm-format:
    subtotal items =                          subtotal items =
        items                                     items
            |> Array.map                              |> Array.map
                (\{ quantity -- always >= 1               (\{ quantity
                  , unitPriceCents                            -- always >= 1
                  } -> quantity * unitPriceCents            , unitPriceCents
                )                                           }
            |> Array.foldl (+) 0                           ->
                                                              quantity * unitPriceCents
                                                        )
                                                    |> Array.foldl (+) 0
    ```

    **Only a comment can reach this shape.** Layout here is author-driven with no
    page-width fitter, so nothing else breaks a lambda's parameter list — a
    pattern the author wrote on one row stays on one row however long it is. The
    divergence is therefore confined to a head with a comment inside it, and it
    is the same rule gren applies to a *one-row* head, where both formatters
    agree on `\q -> body`.

    Keeping the arrow buys two things. The comment stays on the row of the field
    it annotates (C7), and the head costs fewer rows — six against eight above.
    What it costs is a uniform body indent: gren's body starts after `} -> `, so
    a wide closing row pushes it right, where elm's body always sits at a fixed
    offset under the `\`. The trade is deliberate, and gren's side is kept.

    Not to be confused with [#16](#divergence-16), which is about a comment
    written *after* the arrow of a **one-row** head, and where the two formatters
    agree as soon as the body wraps. This entry is about the arrow's own row when
    the **head** breaks; the two are independent and can appear together.

    `KitchenComments` has pinned this shape since long before it was catalogued
    (`\{ basisPoints }` ⏎ `-- comment` ⏎ `accumulatingFactor ->`); it became
    *visible* only when `matrix-syntax.py` gained lambdas whose pattern can
    break, every lambda in it having been `\q ->` until then.

33. <a id="divergence-33"></a>**A lambda after `<|` keeps its head on the
    operator's row, and a chain of them sits at one column; elm-format builds a
    staircase.** Gren-format uses 3 rules here. **R1**: when the right-hand
    operand of `<|` is a lambda, the lambda's *head* — the `\`, its parameters
    and the `->` — stays on the `<|`'s row, and only the body moves. **R2**: a
    body that is itself another `… <| \… ->` starts at the *same* column as the
    row above it; any other body starts at +4. **R3**: the body that finishes a
    chain always takes a row of its own, even where you wrote it on the `->` row.
    Together they turn a chain of *n* continuations into *n* rows at one column,
    closed by a body at +4.

    ```gren
    -- you write (and this is also what gren-format gives back):
    init env =
        Init.await FileSystem.initialize <| \fsPermission ->
        Init.await ChildProcess.initialize <| \cpPermission ->
        Init.await HttpClient.initialize <| \httpPermission ->
            done fsPermission cpPermission httpPermission
    ```

    ```gren
    -- elm-format, from that same input:
    init env =
        Init.await FileSystem.initialize <|
            \fsPermission ->
                Init.await ChildProcess.initialize <|
                    \cpPermission ->
                        Init.await HttpClient.initialize <|
                            \httpPermission ->
                                done fsPermission cpPermission httpPermission
    ```

    The staircase form shows the nesting the code has, `<|`
    being right-associative with a lambda swallowing everything to its right. It
    is that every step costs eight columns and the depth is unbounded:
    `compiler/src/Main.gren`'s six-step opener would put its`let` at column 52.
    Gren chooses the aligned form instead.


    **Normalization, not preference.** Both spellings come back as the aligned
    form; where the author put the `\` decides nothing. Everywhere else in
    gren-format a row choice decides whether *one* construct is inline or broken,
    but here it would decide the indentation of everything below it.

    There are 5 special situations:

    - **A multi-line left-hand side turns R1 off** — a call whose arguments you
      broke, a multi-row record, array or paren keeps the staircase. Leaving the
      head up there would start it well right of the base column, while R2 pulls
      its body back to the base. The same `<|` twice, differing only in whether
      the left-hand side is one row:

      ```gren
      -- you write:
      init env =
          Init.await
              FileSystem.initialize
              env <| \fsPermission ->
          done fsPermission


      initShort env =
          Init.await FileSystem.initialize env <| \fsPermission ->
          done fsPermission

      -- gren-format gives back:
      init env =
          Init.await
              FileSystem.initialize
              env <|
              \fsPermission ->
                  done fsPermission


      initShort env =
          Init.await FileSystem.initialize env <| \fsPermission ->
              done fsPermission
      ```
    - **A one-row lambda is untouched if it is not a step in a chain.**
      `await one <| \a -> done a` is one row because the author wrote it that
      way, and stays one row. It is not a chain of binops; it is just oen
      binop. Written as the last step of a chain, R3 moves its
      body down: the +4 row is the chain's closing mark, not a record of a body
      that happened to break. The boundary is how many `<|`s there are, not
      how many rows you used — a whole chain written on one row normalizes:

      ```gren
      -- you write:
      init env =
          Init.await FileSystem.initialize <| \fsPermission ->
          Init.await Terminal.initialize <| \terminalPermission -> done fsPermission

      -- gren-format gives back:
      init env =
          Init.await FileSystem.initialize <| \fsPermission ->
          Init.await Terminal.initialize <| \terminalPermission ->
              done fsPermission
      ```
    - **A head that spans several rows stays on the `<|`'s row too.** R1 does not
      ask how long the head is: the `\` sits after the `<|` as always, and the
      head's remaining rows continue underneath it instead of the whole head
      dropping to a row of its own. Only a comment can break a head at all — that
      is [#32](#divergence-32) — and this is the same trade #32 already took.

      ```gren
      -- you write:
      init env =
          Init.await FileSystem.initialize <| \{ readPermission -- only reads
              , writePermission } ->
          done readPermission writePermission

      -- gren-format gives back:
      init env =
          Init.await FileSystem.initialize <| \{ readPermission -- only reads
                                               , writePermission
                                               } ->
              done readPermission writePermission
      ```
    - **A comment between the `<|` and the `\` keeps R1 only if the comment
      itself fits on the operator's row**: a single-line `{- … -}` the author
      wrote there does, and a `--`, a multi-row `{- … -}`, or one the author gave
      a row of its own ([#30](#divergence-30)) sends that step back to the
      staircase.

      ```gren
      -- you write:
      rides env =
          Init.await FileSystem.initialize <| {- fs -} \fsPermission ->
          done fsPermission


      stairs env =
          Init.await FileSystem.initialize <| -- fs
          \fsPermission ->
          done fsPermission

      -- gren-format gives back:
      rides env =
          Init.await FileSystem.initialize <| {- fs -} \fsPermission ->
              done fsPermission


      stairs env =
          Init.await FileSystem.initialize <|
              -- fs
              \fsPermission ->
                  done fsPermission
      ```
    - **A comment beside the `->` leads the body, and the head does not move.**
      The arrow carries no position, so writing the comment before it or after
      it gives the formatter the same three facts ([#22](#divergence-22)) and it
      snaps to the body's side ([#16](#divergence-16)).

      ```gren
      -- you write, either of these:
      run env =
          Init.await FileSystem.initialize <| \fsPermission {- go -} ->
          done fsPermission

      run env =
          Init.await FileSystem.initialize <| \fsPermission -> {- go -}
          done fsPermission

      -- gren-format gives back, from both:
      run env =
          Init.await FileSystem.initialize <| \fsPermission ->
              {- go -}
              done fsPermission
      ```

    Which bodies count as continuations for R2 is three further questions. All
    three are decided, and here is each as its own small chain — the formatter's
    actual output, pinned by
    `tests/testfiles/BinopsAndPipelines/BackPipeContinuationAlignment`:

    ```gren
    -- Case 1 — a multi-step run ending in a lambda is NOT a continuation: +4.
    --          The lambda is handed to `Task.mapError toReport` rather than to
    --          the leftmost expression `withRetries 3`, so this is not the plain
    --          `seed <| lambda` shape R2 was written for.
    case1 =
        Init.await one <| \a ->
            withRetries 3 <|
                Task.mapError toReport <| \attempt ->
                    done attempt


    -- Case 2 — a continuation whose own body fits on one row IS one: +0, the
    --          same `seed <| lambda` shape as every other row of the chain. R3
    --          then moves that body to the row below, so `done a b` lands there
    --          whether or not you wrote it on the `->` row.
    case2 =
        Init.await one <| \a ->
        Init.await two <| \b ->
            done a b


    -- Case 3 — a *parenthesized* continuation is NOT one: +4. The parens are
    --          the author's mark that the inner expression is a value rather
    --          than the next step of the chain. R3 does not reach inside them
    --          either, so a one-row lambda in there keeps its row.
    case3 =
        Init.await one <| \a ->
            (Init.await two <| \b ->
                done a b
            )
    ```

    Why each went the way it did — and one sub-case, what happens *inside* a
    multi-step run — is in
    [`settledDecisions.md`](settledDecisions.md#which-bodies-are-continuations).

    Note what R2 emits: a token at exactly the column of the row above it, which
    is the shape family of
    [compiler-common#14](https://github.com/gren-lang/compiler-common/issues/14).
    The emitted shape is safe — a continuation body opens a new scope at the base
    column, so the row above is already closed — but the aligned form reads like
    a statement list, so a second statement written at that column is a parse
    error rather than the reading the author intended. It fails loudly.

    ```gren
    -- you write, taking the aligned rows for a list of statements and adding
    -- one more:
    init env =
        let
            started =
                Init.await FileSystem.initialize <| \fsPermission ->
                Init.await Terminal.initialize <| \terminalPermission ->
                    done fsPermission terminalPermission
                cleanup env
        in
        started
    ```

    `cleanup env` sits at the chain's column, and nothing reads it as the next
    statement. gren-format refuses the file — `FAILED TO PARSE`, with the caret
    on the `in` — because the binding's body scoped its indent to the column of
    the first `Init.await` and a token back at exactly that column ends the body
    early. That refusal is #14 itself. The real compiler does parse the text, but
    it reads `cleanup env` as *more arguments* to the row above and stops on the
    type error that gives. So the two disagree about which error you get, and
    agree that the row is not a statement — contrast
    [#34](#divergence-34)'s swallowed `|>` step, which compiles.

    One arrangement is not so obvious, and it is #14's, not R2's: at the **top level**
    a module can take the stray row for a new declaration, so the file formats
    with no complaint and comes back meaning something else. That one is written
    up in
    [`knownLimitations.md`](knownLimitations.md#at-the-top-level-it-rewrites-the-file-instead-of-refusing-it).

34. <a id="divergence-34"></a>**A bare operand after `|>` keeps its head on the
    operator's row; elm-format parenthesizes it instead.** A lambda, an `if`, a
    `when` and a `let` are all legal right operands of `|>` with no parentheses
    around them. gren-format keeps such an operand's head on the `|>`'s row and
    lets the rest fall below it, the way it treats every other operand that
    breaks. elm-format wraps the operand in parentheses **it adds itself**, and
    then puts the `(` in exactly that spot.

    ```gren
    -- gren-format:
    bareIf rows =
        rows
            |> if isEmpty rows then
                none

               else
                some
    ```

    ```gren
    -- elm-format, from the same input:
    bareIf rows =
        rows
            |> (if isEmpty rows then
                    none

                else
                    some
               )
    ```

    **Why gren parts company.** gren-format never introduces a parenthesis. That
    is the same rule as [#10](#divergence-10) seen from the other side: parens
    the author wrote are never stripped, and parens the author did not write are
    never added — punctuation is the author's, not the formatter's. Once that is
    fixed, keeping the head up on the operator's row is the only remaining
    choice that does not strand `|>` alone on a row of its own, a shape
    gren-format produces nowhere else. It also makes `|>` agree with its
    neighbours: `>>`, `++` and user-defined operators already keep a bare lambda
    or a bare block head on their row, and so does `<|`
    ([#33](#divergence-33)).

    The two formatters agree exactly where the author *did* write the parens —
    `|> (if …)` comes back identically from both — so the divergence is over the
    parens alone, never over where a row breaks.

    The columns under a bare operand are emergent, not chosen: the body advances
    to the next tab stop past the keyword's own column, which is the same rule
    the parenthesized form follows one column further right. That is why the
    `else` above sits under the `if` and its branches one column past it.

    **What the parens the author left out are actually doing.** A bare block has
    no closing token, so it extends as far as the indentation allows — and a
    `|>` step written *after* one is swallowed into it, becoming part of the
    block's last branch or body rather than the next step of the pipeline:

    ```gren
    -- you write, meaning two steps:
    attempt rows =
        rows
            |> when rows is
                [] ->
                    none

                _ ->
                    some
            |> always 1
    ```

    ```gren
    -- gren-format shows you what that parsed as:
    attempt rows =
        rows
            |> when rows is
                [] ->
                    none

                _ ->
                    some
                        |> always 1
    ```

    That is the parser's reading, not a relocation — the formatter's AST check
    passes, and writing the parens (`|> (when … )`) gives the two steps back.
    The layout discloses it: a swallowed operator renders inside the block's own
    body, which starts at a tab stop past the keyword's column and so always
    lands strictly right of the column a genuine step sits at. But note this is
    *quieter* than [#33](#divergence-33)'s neighbouring hazard, where a second
    statement at the chain's column is a parse error and fails loudly. This one
    compiles, and can typecheck; the four-column indent is the only signal.


## Out of scope for comparison


Some fixtures use Gren syntax with no valid Elm equivalent, so they can't be
mechanically translated and run through `elm-format` at all:

- A record-update base that's a parenthesized call or a dotted field-access
  chain (`{ (someTransform base) | ... }`, `{ model.sub | ... }`) — Elm's
  grammar only allows a bare variable there. gren-format renders both forms
  exactly as written, same as any other record update:

  ```gren
  update base =
      { (someTransform base) | count = 0 }

  updateSub model =
      { model.sub | count = 0 }
  ```
- Gren's record-pattern field-renaming syntax, `{ field = alias }` in pattern
  position (e.g. `Just { endpoint = sinkEndpoint } ->`) — Elm patterns only
  support bare `{ field }`. `elm-format` hard-errors on this construct (or, if
  the renamed identifier looks like a wildcard such as `_x`, silently
  mis-parses it into two separate patterns instead of erroring) — so this
  whole class of fixtures is fundamentally outside the scope of an
  elm-format comparison. gren-format renders it like any other pattern field:

  ```gren
  handle msg =
      when msg is
          Just { endpoint = sinkEndpoint } ->
              sinkEndpoint

          Nothing ->
              ""
  ```
