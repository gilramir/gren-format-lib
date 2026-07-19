# Extending the formatter for new Gren syntax

This is the orientation guide for a developer who needs to teach `gren format`
about a new piece of Gren syntax — a new AST node, a new declaration kind, a new
expression form. Gren will keep growing, so the formatter has to be easy to
extend without breaking the invariants that keep formatting correct, stable, and
comment-faithful.

Read this once for the mental model, then keep `README.md` (the authoritative,
example-driven description of *what every rule does*) open while you work.

One decision explains most of what follows: **gren-format reuses the
production Gren compiler's parser, and that parser throws comments away.**
Comments come back as a separate list of positions, re-attached to the
formatter's own tree after the fact — and nearly everything below (the
Logical Printing Tree, its position caches, the `Comments` pass, the
idempotency fuzzer, the `forceVertical`-stability rule) exists because
re-attaching something by position, after the fact, is harder to get right
than never losing it in the first place. [Why the architecture is
comment-driven](#why-the-architecture-is-comment-driven--contrasted-with-elm-format)
makes that comparison explicit against elm-format, which took the opposite
approach — it's dense but worth reading in full once, even placed near the
end here.

The sections below go roughly in this order: how source text becomes a tree
(the pipeline and the modules that build it), what that tree looks like and
the rules for building it correctly, how the tree turns back into text, a
practical checklist for adding new syntax, and finally a list of the mistakes
that are easy to make and expensive to find.

## Table of contents

- [The pipeline in one line](#the-pipeline-in-one-line)
- [The modules](#the-modules)
- [What the formatter consumes](#what-the-formatter-consumes)
  - [The AST — `Compiler.Ast.Source.Module`](#the-ast--compilerastsourcemodule)
  - [The comments — `Compiler.Parse.Context`](#the-comments--compilerparsecontext)
- [The LPT — `Formatter.Logical.LogicalPrintingTree`](#the-lpt--formatterlogicallogicalprintingtree)
  - [Boxes you will reach for](#boxes-you-will-reach-for)
  - [`OriginalRows` and `SyntaxType` — the top level only](#originalrows-and-syntaxtype--the-top-level-only)
  - [The cached bounds (why `lpnNode` matters)](#the-cached-bounds-why-lpnnode-matters)
- [Author layout — the `forceVertical` flag](#author-layout--the-forcevertical-flag)
- [`Formatter.Render.Box` — the backend](#formatterrenderbox--the-backend)
  - [Why Box replaced Doc (and PrettyExpressive before it)](#why-box-replaced-doc-and-prettyexpressive-before-it)
- [Adding a new construct — the checklist](#adding-a-new-construct--the-checklist)
  - [1. Find the AST node](#1-find-the-ast-node)
  - [2. Convert AST → LPT](#2-convert-ast--lpt)
  - [3. Get positions right (the difficult part)](#3-get-positions-right-the-difficult-part)
  - [4. Detect author layout intent](#4-detect-author-layout-intent)
  - [5. Comments — usually nothing to do](#5-comments--usually-nothing-to-do)
  - [6. Render it — `MakeRenderBox.makePrettyLineBox`](#6-render-it--makerenderboxmakeprettylinebox)
  - [7. Blank lines (top-level only)](#7-blank-lines-top-level-only)
- [Things to worry about](#things-to-worry-about)
- [How to test](#how-to-test)
- [Why the architecture is comment-driven — contrasted with elm-format](#why-the-architecture-is-comment-driven--contrasted-with-elm-format)
  - [elm-format: comments live inside the AST](#elm-format-comments-live-inside-the-ast)
  - [gren-format: comments are re-attached by position](#gren-format-comments-are-re-attached-by-position)
  - [The tradeoff in one line](#the-tradeoff-in-one-line)
- [Where to read more](#where-to-read-more)

---

## The pipeline in one line

```
Src.Module + Ctx.Context  ──►  LPT  ──►  Box  ──►  String
                          MakeLogical   MakeRenderBox
```

- **Input** is two things from the parser: the AST (`Src.Module`) *and* a
  separate stream of comments (`Ctx.Context`). Comments are **not** in the AST.
- **LPT** (Logical Printing Tree) is our own intermediate tree. It says *what
  should be grouped with what* and *how each group may break across lines* — but
  not the exact spaces. It also carries enough source-position information to put
  comments back where the author wrote them.
- **`Formatter.Render.Box`** turns the LPT into a concrete string. It is a
  faithful port of elm-format's own `Box.hs` render IR: every node is already
  either `SingleLine` or `Stack` (2+ actual output lines) — there is no
  intermediate "could become a newline" node and nothing to resolve at render
  time. The flat-vs-vertical choice is made once, at LPT-build time, via
  `forceVertical` flags on certain boxes (see below); rendering just executes
  whichever shape the tree already committed to. See [Why Box replaced Doc (and
  PrettyExpressive before it)](#why-box-replaced-doc-and-prettyexpressive-before-it)
  for how this backend got here and why it looks nothing like a typical
  Wadler-style pretty-printer.

Entry point: `Formatter.prettyPrint : Src.Module -> Ctx.Context ->
Result String String`. It calls `MakeLogical.makeLogicalPrintingTree` (build the
LPT) then `MakeRender.makePrettyResult` (render it). Every stage returns
`Result String _`; there are no silent fallbacks — an unhandled case is an
`Err`, not a guess.

---

## The modules

All formatter source lives in `src/Formatter/`:

```
Formatter.gren                  entry: prettyPrint
Formatter/Strings.gren          tiny string helpers (countNewlines)
Formatter/Logical/              AST + comments → LPT
  MakeLogical.gren                orchestrator: one process* per top-level decl kind
  InsertExpressions.gren          expression → LPT (one insert* per expression form)
  InsertPatterns.gren             pattern → LPT
  InsertTypes.gren                type → LPT (typeWithArgs shared by TType/TTypeQual)
  LiteralFormat.gren              string / char / hex literal escaping
  LPTHelpers.gren                 LPT construction helpers: mkText*/plainAcross/
                                    syntheticParens/authoredBracketList/resultFoldl/…
  BinopPrecedence.gren            operator fixity table for binop-chain layout
  LogicalPrintingTree.gren        LPBox / LPNode types, smart constructors, bounds cache
  LPTJson.gren                    --lpt debug serialiser
  Comments.gren                   re-attach parse-context comments by position
  SortSymbols.gren                sort exposing lists + import groups
  VerticalSpace.gren              insert blank lines
Formatter/Render/               LPT → String
  MakeRender.gren                 thin orchestrator: maps RootBox children through MakeRenderBox, joins with "\n"
  MakeRenderBox.gren               LPT → Box, one builder per LPBox constructor
  Box.gren                         elm-format's Box IR (Line/Box, Tab tab-stops, prefix) + renderer
  FlowPolicy.gren                  shared inline/break decision layer for flow sequences
  ElmStructure.gren                faithful port of elm-format's ElmStructure.hs layout combinators
```

`LogicalPrintingTree.gren` is the hub every module depends on; its module doc
opens with a categorised map of all ~30 `LPBox` constructors. `BinopPrecedence`
is imported by both `InsertExpressions` (to decide the author's break tier) and
`MakeRender` (to render it) — they must agree, so the fixity table has one home.

---

## What the formatter consumes

### The AST — `Compiler.Ast.Source.Module`

(defined in `compiler-common`, shared with the compiler). Top-level shape:

```gren
type alias Module =
    { name    : Located String
    , exports : Exposing                       -- Open | Explicit (Array Exposed)
    , docs    : Maybe (Located String)         -- module doc comment
    , imports : Array (Located Import)
    , values  : Array (ModuleDeclaration Value)  -- functions / constants
    , unions  : Array (ModuleDeclaration Union)  -- custom types
    , aliases : Array (ModuleDeclaration Alias)
    , binops  : Array (Located Infix)
    , effects : Effects                          -- NoEffects | Ports … | Manager …
    }
```

Everything is wrapped in `Located` (`{ start : Position, end : Position, value }`)
where `Position = { row : Int, col : Int }`, 1-based. Expressions, patterns and
types are their own recursive `Src.Expr` / `Src.Pattern` / `Src.Type_` trees,
each node `Located`. When you add syntax, the parser team will have added a
constructor here; your job starts from that constructor.

**Positions are the load-bearing part.** The formatter leans on `start`/`end` of
every token — not to reproduce them, but to (a) decide source order, (b)
re-attach comments, and (c) detect author layout intent. If a new AST node
carries a token the parser *doesn't* record a position for (a synthesized
keyword, a closing bracket), you will have to synthesize a faithful position for
it (see below).

### The comments — `Compiler.Parse.Context`

```gren
type alias Context = { indent : Int, lineStart : Int
                     , comments : Builder (Located Comment) }
type Comment = Line String | Block String
```

Comments ride alongside the AST as a flat, source-ordered list of located
`Line` (`--`) or `Block` (`{- -}`) strings. They are re-attached to the LPT
*after* it is built, purely by position (`Formatter.Logical.Comments`). This is why
positions on your LPT nodes must be honest: a comment is placed next to whatever
token its `(row, col)` falls between.

---

## The LPT — `Formatter.Logical.LogicalPrintingTree`

An `LPNode` is a `box` (the layout shape) plus `children`, plus a handful of
**cached subtree bounds**. Build nodes only with the smart constructors —
`lpnLeaf box`, `lpnNode box children`, `lpnBracketNode box closePos children` —
never with raw record syntax: the constructors compute the caches bottom-up, and
skipping them yields wrong positions and mis-placed comments.

### Boxes you will reach for

Every example below is real, formatted Gren — run through the actual CLI
(`--show`) and checked for idempotency, not hand-typed. Where two snippets
appear together, they're the same construct rendered two ways, to show what
flips the box's shape.

Leaves (carry text/position, no children):

- `UnbreakableText (Located String)` — a real source token. Prints as-is, never
  breaks. **This is the common case** — most of your tokens are this.

  ```gren
  foo x y =
      bar x y
  ```

  `foo`, `x`, `y`, and `bar` are each their own `UnbreakableText` leaf,
  carrying the exact position the parser recorded for that token.

- `SynthesizedText String` — punctuation/keywords the AST doesn't position
  (`=`, `->`, `in`, `(..)`). **Excluded from all row-range and comment math.**

  ```gren
  foo =
      1
  ```

  The parser consumes `=` without giving it a position, so it becomes
  `SynthesizedText "="` rather than `UnbreakableText`. That also means no
  comment can ever attach to it — there's no position for `Comments` to
  compare against.

- `SingleLineComment` / `BlockComment` / `DocComment` (`Located String`) —
  inserted by `Formatter.Logical.Comments`, you rarely emit these yourself.

  ```gren
  module Comments exposing (foo)

  {-| A doc comment. -}


  -- a single-line comment
  foo = {- a block comment -}
      1
  ```

  All three come from `Compiler.Parse.Context`'s comment list and are spliced
  into the tree by position after it's built (see [The comments —
  `Compiler.Parse.Context`](#the-comments--compilerparsecontext)) — you build
  the surrounding boxes correctly and these attach on their own.

- `MultilineString (Located (Array String))`, `EmptyLine`, `RootBox`.

  ```gren
  foo =
      """
      line 1
      line 2
      """
  ```

  `MultilineString` carries one array element per content line and renders
  each on its own hard-newline-separated line between the `"""` delimiters.
  `EmptyLine` is the blank-line leaf `VerticalSpace` inserts between
  declarations — you won't construct one directly. `RootBox` only ever
  appears once, as the tree's own root.

Layout boxes (have children):

- `AcrossOrVertical { forceVertical : Bool }` — bare (unbracketed) token
  sequence, all on one line *or* one child per line with continuations
  indented +4 — same author-driven choice as `AllAcrossOrAllVertical`, just
  without delimiters. The default for "a thing and its parts" (a function
  call, a variant + payload). When `forceVertical` is `True`, continuations
  always break — no flat option.

  ```gren
  foo x y =
      bar x y
  ```
  ```gren
  foo x y =
      bar
          x
          y
  ```

  Same call, `forceVertical = False` then `True` — nothing but the author's
  own row choice for `bar`'s arguments flips the flag.

- `AllAcrossOrAllVertical ListBrackets` — bracketed list, all on one line *or*
  one item per line (`ListParen`/`ListCurly`/`ListSquare`). Vertical when any
  item boundary spans rows.

  ```gren
  foo =
      [ 1, 2, 3 ]
  ```

  Written flat, it stays flat regardless of width — there's no page-width
  logic anywhere that would wrap this onto multiple lines on its own.

- `AlwaysVertical ListBrackets` — bracketed list that never collapses.

  ```gren
  foo =
      [ 1
      , 2
      , 3
      ]
  ```

  The author wrote this array across three rows, so it's built as
  `AlwaysVertical` rather than `AllAcrossOrAllVertical` — once a list commits
  to vertical, it stays one item per line even though it would easily fit on
  one.

- `IndentedBlock` / `BodyBlock` — a body on its own (indented) line, hard
  break. `SoftIndentedBlock` is the soft variant that may stay inline (lambda
  bodies, port payloads).

  ```gren
  foo x =
      if x then
          1

      else
          2
  ```

  The `if`/`else` bodies (`1` and `2`) are each an `IndentedBlock`: a hard
  newline, indented +4 from `if`/`else`. The function body itself —
  everything after `foo x =` — is a `BodyBlock`: a hard newline at the
  *current* indent, no extra nesting, since the enclosing declaration already
  supplies it.

  ```gren
  foo =
      List.map (\x -> x + 1) list
  ```

  Here the lambda body `x + 1` is a `SoftIndentedBlock`: written on the same
  row as `->`, it stays glued inline. Move the body to its own row and the
  same lambda gets `IndentedBlock` instead — the same row-span check from
  [Author layout](#author-layout--the-forcevertical-flag) decides which one.

- `WhenBranch`, `IfCondition { forceVertical }`, `WhenFlow { forceVertical }`,
  `PipelineStep`, `ParenBlock`, `OpAndRhs`, `AlignedFlow`, `PrefixGlue`,
  `RecordUpdate { forceVertical }`, `EmptyBracketed` — specialised shapes;
  read their doc comments in `LogicalPrintingTree.gren` before reusing. One
  concrete example each:

  - **`WhenFlow` / `WhenBranch`** — a whole `when … is` expression, and one of
    its `pattern -> body` arms:

    ```gren
    foo x =
        when x is
            Just y ->
                y

            Nothing ->
                0
    ```

  - **`IfCondition { forceVertical }`** — an `if`/`else if` condition.
    Continuations indent +8 (double `IndentedBlock`'s +4), so a wrapped
    condition is visually distinct from the branch body under it:

    ```gren
    foo a b =
        if
            a
                && b
        then
            1

        else
            2
    ```

  - **`PipelineStep`** — one step of a `|>`/`<|` chain, indented +4 from the
    seed, operator-led:

    ```gren
    foo xs =
        xs
            |> List.map inc
            |> List.filter isEven
    ```

  - **`ParenBlock`** — a parenthesized expression; children render glued
    directly onto `(`/`)` with no inner space. The lambda in the
    `SoftIndentedBlock` example above is itself a `ParenBlock`:
    `List.map (\x -> x + 1) list` — everything between `(` and `)` is its
    children.

  - **`OpAndRhs`** — one `op rhs` link of a non-pipeline binop chain:

    ```gren
    foo =
        1 + 2 * 3
    ```

    This builds as `Binop [1, OpAndRhs(+, 2, OpAndRhs(*, 3))]` — the `+`'s
    `OpAndRhs` nests a further `OpAndRhs` for `* 3` inside its own right-hand
    side. That nesting is precedence, not left-to-right flattening: `2 * 3`
    stays grouped together because `*` binds tighter than `+`.

  - **`RecordUpdate { forceVertical }`** — `{ base | … }`, inline or exploded
    the same way a record literal is:

    ```gren
    foo pt =
        { pt | x = 1, y = 2 }
    ```
    ```gren
    foo pt =
        { pt
            | x = 1
            , y = 2
        }
    ```

  - **`EmptyBracketed`** — an empty `{}` / `[]` / `()`, carrying its full span
    so a comment written between the brackets (`[ {- c -} ]`) still has a
    place in the tree to attach instead of falling out to a sibling:

    ```gren
    foo =
        []
    ```

  - **`PrefixGlue`** — a prefix glued with no space to what follows (`-expr`,
    `\pat -> …`). The lambda in the earlier examples is one: `\x -> x + 1`
    glues `\` directly onto the pattern `x` via `PrefixGlue "\\"` — the same
    box handles unary negation, as `PrefixGlue "-"`.

  - **`AlignedFlow`** — worth naming honestly rather than faking an example
    for: as of this writing, nothing in `InsertExpressions`/`MakeLogical`
    actually constructs one, and `MakeRenderBox`'s dispatch for it is a stub
    (`Err "box: construct not ported [AlignedFlow]"`). It's a real
    constructor with some render-side scaffolding already in place, but it
    isn't reachable from any current source — don't go looking for a live
    example, and if you find yourself wanting this exact shape, expect to
    build it out rather than reuse it.

See `README.md` for the rendered example of each rule these boxes implement,
in user-facing terms rather than internal ones.

### `OriginalRows` and `SyntaxType` — the top level only

Each **top-level declaration** becomes exactly one `OriginalRows { first, last,
stype }` node directly under `RootBox`, where `stype : SyntaxType` tags the kind
(`StModule`, `StImport`, `StFunctionSignature`, `StTypeUnion`, …) and
`first`/`last` are its source-row range. Comments and blank lines are then added
as *sibling* `OriginalRows` nodes. The row range drives two things: source
ordering (`MakeLogical.sortOriginalRows`) and blank-line decisions
(`Formatter.Logical.VerticalSpace`). Get `first`/`last` right or comments/blanks land in
the wrong place — `first` should be the declaration's **leading keyword** row.

### The cached bounds (why `lpnNode` matters)

Every node caches `firstPos`, `lastPos`, `minRow`, `maxRow`, `lastBracketEnd`,
and `bracketEndExact`. `Formatter.Logical.Comments` uses these to answer "what's the
first/last positioned token here?" and "where does the rightmost bracket close?"
in O(1). `lpnNode` fills them from `selfBoxBounds box` merged with the children;
`lpnBracketNode` additionally records an *exact* closing-bracket position.
`SynthesizedText` contributes nothing to these caches — that is deliberate, so a
generated `->` never attracts a comment.

---

## Author layout — the `forceVertical` flag

The formatter has **no page width**. Whether a construct stays on one line or
breaks across lines is determined at LPT-build time from the author's source
positions, not at render time from a column budget.

The mechanism: some boxes carry `{ forceVertical : Bool }`. Set it `True` when
the author's source has a line break inside that construct; set it `False` for
flat intent. `MakeRender` then picks between a flat flow (`buildFlowDoc`) and a
hard-breaking flow (`buildFlowDocBroken`) based on that flag.

**Where to detect multiline intent** (in `InsertExpressions.gren`):

- **Function call** — `forceVertical = itemsSpanRows (fn :: args)`: true when
  any argument starts on a different row than the preceding item.
- **`if` condition** — `forceVertical = firstBranch.test.start.row > locExpr.start.row`:
  true when the condition is on a different row than `if`.
- **`when` expression** — `forceVertical = expression.start.row > locExpr.start.row`:
  true when the scrutinee is on a different row than `when`.
- **Lambda body** — uses `IndentedBlock` (always-break) when the body is on a
  different row than `->`, `SoftIndentedBlock` otherwise.
- **Pipeline / binop / record update** — already carried `forceVertical` from
  before the author-layout rewrite; the same row-span logic applies.

**For new constructs**: check if any structural item is on a different row than
its predecessor. If yes → `forceVertical = True`; the renderer does the rest.

---

## `Formatter.Render.Box` — the backend

`Formatter.Render.Box` (`Box.gren`) is a faithful port of elm-format's own
`Box.hs`. Two types, both closed (no page-width machinery anywhere in them):

```gren
type Line = Text String | Row (Array Line) | Space | Tab
type Box  = SingleLine Line
          | Stack { first : Line, second : Line, rest : Array Line }  -- 2+ lines
          | MustBreakBox Line
```

A `Box` is never "maybe one line, maybe more" — it already *is* one or the
other, decided by whoever built it. `Tab` isn't "+4 spaces"; it's a real tab
stop (advance to the next multiple of 4), and `prefix` glues a string onto
line 1 while padding the other lines by its exact character width — the same
two primitives elm-format uses to make e.g. a `Stack`-shaped record update
line up correctly no matter what column it starts rendering at. `freezeTabs`
rewrites a box's `Tab`s to literal spaces so it can be `prefix`-glued
somewhere the tab-stop arithmetic would otherwise re-snap incorrectly.

Key functions, mirroring `Box.hs`. Each one is small enough to show what it
does directly — build the left side, and `B.render` turns it into the string
on the right:

- `B.line l` — wrap one `Line` as a `SingleLine` box.

  ```gren
  B.line (B.row [ B.keyword "let", B.space, B.identifier "x" ])
  ```
  ```
  let x
  ```

- `B.mustBreak l` — wrap one `Line` as a `MustBreakBox` (a `--` comment: it
  must own its line, no matter what glues around it). It renders identically
  to `B.line`; the difference only matters one layer up, in `FlowPolicy`,
  which refuses to glue anything after a `MustBreakBox` onto the same line.

  ```gren
  B.mustBreak (B.literal "-- keep on one line")
  ```
  ```
  -- keep on one line
  ```

- `B.stack1 boxes` — stack 2+ boxes into one multi-line `Box`.

  ```gren
  B.stack1
      [ B.line (B.identifier "one")
      , B.line (B.identifier "two")
      , B.line (B.identifier "three")
      ]
  ```
  ```
  one
  two
  three
  ```

- `B.indent box` — prepend a `Tab` to every line. A `Tab` advances to the next
  multiple of 4, so from column 0 that's a plain 4-space indent:

  ```gren
  B.indent
      (B.stack1
          [ B.line (B.identifier "one")
          , B.line (B.identifier "two")
          ]
      )
  ```
  ```
      one
      two
  ```

- `B.prefix pref box` — glue `pref` onto line 1, pad the rest by `pref`'s
  exact character width. This is the primitive that makes a broken binop's
  continuation line land under the *value*, not under the `=`:

  ```gren
  B.prefix (B.literal "x = ")
      (B.stack1
          [ B.line (B.identifier "1")
          , B.line (B.row [ B.punc "+", B.space, B.identifier "2" ])
          ]
      )
  ```
  ```
  x = 1
      + 2
  ```

- `B.addSuffix suffix box` — append to the *last* line only, however many
  lines the box has:

  ```gren
  B.addSuffix (B.literal ",")
      (B.stack1
          [ B.line (B.identifier "1")
          , B.line (B.row [ B.punc "+", B.space, B.identifier "2" ])
          ]
      )
  ```
  ```
  1
  + 2,
  ```

- `B.freezeTabs box` — bake every `Tab` in `box` into the literal spaces it
  would render to *right now*, at its current column-0-anchored position. The
  rendered string doesn't change yet, but a `Tab` that used to recompute its
  width from wherever it lands is now a fixed number of spaces — which only
  matters once you `prefix` the box onto something else. Compare the same
  indented box glued to a 2-character label, with and without freezing first:

  ```gren
  B.prefix (B.literal "x:") (B.indent (B.line (B.identifier "a")))
  ```
  ```
  x:  a
  ```

  ```gren
  B.prefix (B.literal "x:") (B.freezeTabs (B.indent (B.line (B.identifier "a"))))
  ```
  ```
  x:    a
  ```

  Without freezing, the `Tab` re-snaps to the next multiple of 4 measured
  *from column 2* (the end of `"x:"`) — only 2 spaces, visibly squeezing a gap
  that was 4 spaces wide when the box rendered on its own. Freezing first
  locks that 4-space gap in as literal spaces before the prefix ever touches
  it.

- `B.render box` — the final `String`, right-trimmed line by line. Trailing
  whitespace inside a `Box` never survives to the output:

  ```gren
  B.render (B.line (B.row [ B.identifier "x", B.space ]))
  ```
  ```
  x
  ```

There is no `Group`, no `nl`/`breakDoc`, and nothing to "render flat and see
if it fits." The flat-vs-vertical decision is made once, upstream of this
module, when an LPT box is built with `forceVertical = True`/`False`; the two
layers above `Box.gren` just materialize that decision:

- **`Formatter.Render.FlowPolicy`** (`decide`) — given the running flow state
  and the next item's facts (its row position, its rendered box shape), says
  *how* the item joins: glued with a space, dropped to its own line, wrapped
  as an indented block, … This is the one place every join decision lives —
  the module doc calls out explicitly that the renderer must not carry any
  layout policy of its own, because a second copy of a join decision is a
  divergence generator, not extra precision.
- **`Formatter.Render.ElmStructure`** — a faithful port of the `ElmStructure.hs`
  combinators (`groupBox`, `extensionGroup`, …) for shapes like bracketed
  literals: single line when every child is a `SingleLine` and the caller
  didn't force multiline, otherwise the fully-expanded vertical form.
- **`Formatter.Render.MakeRenderBox`** (`makePrettyLineBox`) — the actual
  dispatch: one builder per `LPBox` constructor, calling into `FlowPolicy` and
  `ElmStructure` and assembling the result with `Box.gren`'s primitives.

When you add a new box type, add an arm to `makePrettyLineBox`'s `when box is
…` dispatch returning `Result String Box`. Reuse an existing box shape if one
fits — a new `LPBox` constructor requires new arms in *every* `when box is` in
`MakeRenderBox` plus `selfBoxBounds` in `LogicalPrintingTree`.

### Why Box replaced Doc (and PrettyExpressive before it)

This backend has had three incarnations; each replacement removed a layer of
machinery that turned out to be solving a problem gren-format doesn't have.

**1. PrettyExpressive → a custom `Doc` (June 2026).** The formatter originally
rendered through `gilramir/gren-pretty-expressive` (kept for reference at
`../gren-pretty-expressive/`) — a Wadler/Prettier-style pretty-printer (the
family of formatters, including JavaScript's Prettier, that lay out code by
searching for the best line-break points within a page-width budget) with a
genuine cost-based layout *optimizer*: given a page width, it searched for the
best place to break each group. But gren-format's actual rule is "your line
breaks are your layout decisions" — there is no 80-column target to optimize
for, and every construct's flat-vs-vertical choice is already decided from the
author's source positions (`forceVertical`) before rendering starts. Running
an optimizer over a decision that was already made is dead weight at best and,
at worst, a second source of truth that can disagree with the first. It was
replaced with a small custom `Doc` type (`Group`/`Nest`/`Nl`/`HardNl`) whose
`Group` *always* rendered flat — no search, no page width, just a fixed
choice — while keeping the general Wadler-style vocabulary.

**2. Custom `Doc` → `Box` (June–July 2026, the "Change-1" strangler).** With
the optimizer gone, `Doc`'s `Group`/`Nest`/`Nl` combinators were still a
*reinvented* layout vocabulary — our own abstraction, not elm-format's —
and matching elm-format's exact output through it meant hand-tuning column
arithmetic construct by construct (bespoke `fieldLine`/`R.align` code for
record updates, for instance) and still landing on documented divergences the
arithmetic couldn't reach — e.g. a lambda field value's `\arg ->` head
dropping to its own line, which elm-format does unconditionally but the `Doc`
renderer couldn't reproduce.

A proof-of-concept (commit `7f3a536`) tried something more direct: port
elm-format's actual `Box.hs`/`ElmStructure.hs` combinators verbatim, instead
of re-deriving their behavior through a different abstraction. The result was
byte-identical to real `elm-format` output on every case tried, including the
lambda-field case, **with zero hand-tuned column arithmetic** — because it
uses elm-format's own primitives (`Tab` tab-stops, `prefix` padding) rather
than approximating them. That result justified a full rewrite: a "strangler
fig" migration — replace the old implementation piece by piece behind a
safety net, rather than in one big-bang rewrite — ported one construct at a
time behind a self-verifying guard that compared `Box` output against the
live `Doc` output and only trusted `Box` where they agreed (see the many
`Change-1 strangler tranche N` commits).
Once every construct crossed over with 0 `Box` `Err`s across the whole corpus,
`fa25ba0` deleted `Doc.gren` and the guard — `Box` has been the sole backend
since.

The throughline: each step removed a layer that was re-deciding something
already decided elsewhere. PrettyExpressive re-decided layout via cost search
when the author already decided it. `Doc` re-derived elm-format's rendering
behavior through a different vocabulary when the shortest path was to just
port elm-format's actual code. `Box` doesn't re-decide or re-derive
anything — it's the same IR elm-format itself renders through, which is also
why the [comments section below](#why-the-architecture-is-comment-driven--contrasted-with-elm-format)
can now describe our render-time behavior and elm-format's in the same terms.

---

## Adding a new construct — the checklist

Most new syntax is "build some boxes in a flow," and the existing comment and
blank-line machinery just works. Before the general checklist, here's what
that looks like end to end for one example — hypothetical and simplified for
teaching, but shaped exactly like real work you'd do. Imagine Gren grows an
`unless` expression, `unless cond then body`, formatted like a single-branch
`if` with no `else`:

- **The AST node.** The parser hands you a new `Src.Expr` constructor,
  `Src.Unless { cond : Src.Expr, body : Src.Expr }`. It records real positions
  for `cond` and `body` (they're full sub-expressions) but not for the
  `unless` or `then` keywords — those are tokens the parser matched and threw
  away, same as `if`/`then` today.
- **AST → LPT.** This is an expression, so it goes through
  `InsertExpressions.insertExpression`: match the new constructor, recurse into
  `cond` and `body` with `insertExpression` itself, and assemble `unless`, the
  condition, `then`, and the body into a flow — the same shape the real
  `if`/`then` handling already builds.
- **Positions.** `cond` and `body` come with honest positions from the AST, so
  emit them and trust those. `unless` has no parser position, but no comment
  could ever legitimately precede it either — it's the first token of the
  expression — so it's a plain `SynthesizedText`. `then` is the interesting
  one: a comment *could* appear between the condition and `then`, or between
  `then` and the body — exactly the hazard the real `ThenElseBoundaryComment`
  fixture exists for on `if`. So `then` needs `mkZeroWidthText`, anchored at
  the end of `cond` (a real, stable position) rather than at some column of
  its own.
- **Author layout.** Does `unless`'s body ever start on a row after `unless`
  itself, in real source? If so, mirror `if`: `forceVertical = True` when the
  body's row differs from `unless`'s row.
- **Comments.** Nothing extra to write. Emitting `cond`/`then`/`body` as
  ordinary flow items means `Formatter.Logical.Comments` re-attaches boundary
  comments correctly on its own — *provided* the `then` position above is
  honest.
- **Render.** Add an arm to `MakeRenderBox.makePrettyLineBox` for the new
  shape, or — more likely — reuse whichever box already renders `if`'s
  condition/body pairing, since `unless` is structurally identical minus a
  branch.
- **Blank lines.** Not applicable here: `unless` is an expression, not a
  top-level declaration, so `VerticalSpace` never sees it.

The checklist below generalizes each of those steps into the general case; use
the worked example above to see what each step concretely produces, then come
back to the checklist itself as the reference for your next addition. Go in
this order.

### 1. Find the AST node
Locate the new constructor in `compiler-common`'s `Compiler.Ast.Source` and
note every token it holds and, crucially, every token it *doesn't* (keywords and
brackets the parser consumes without recording a position).

### 2. Convert AST → LPT
Add/extend the right converter:

- **Top-level declaration kind** → a `process*` function in
  `Formatter.Logical.MakeLogical` (mirror `processUnionDecl` / `processPorts`). Wrap the
  result in `makeOrigRows firstRow stype children` with a new or existing
  `SyntaxType`. `firstRow` = the **keyword** row.
- **Expression** → `Formatter.Logical.InsertExpressions.insertExpression`.
- **Pattern** → `Formatter.Logical.InsertPatterns`.
- **Type** → `Formatter.Logical.InsertTypes`.

Use the shared helpers in `Formatter.Logical.LPTHelpers`: `mkTextFromLocString` (a real
token at its `Located` position), `mkText pos str` (text at an explicit
position), `mkZeroWidthText pos str` (a synthesized token anchored at a real
position but contributing zero width — see below), `resultFoldl`. For the two
most common container shapes there are smart constructors that fill in the
default flags for you: `plainAcross children` (an `AcrossOrVertical` flow — a
head-and-its-parts) and `syntheticParens children` (a formatter-synthesized
`ParenBlock` with no author position). Prefer them over spelling out the box
record; reach for the raw box only when you need a non-default flag
(`forceVertical = True`, `isCallArgument = True`, …).

### 3. Get positions right (the difficult part)
- A real token from the AST → `mkTextFromLocString` / `UnbreakableText`. Its own
  `Located` position is correct, use it.
- A keyword/punctuation the parser discards (`=`, `->`, `then`) that has no
  bearing on comment placement → `SynthesizedText`. It is invisible to ranges, so
  it can never split a comment to the wrong side.
- A discarded token that a comment *could* sit beside (e.g. `exposing`, an
  `as` alias, a `where` label) → `mkZeroWidthText pos kw`, anchoring `pos` at a
  **real, stable** position (usually the end of the preceding real token). The
  anchoring rule of thumb, learned the hard way: anchor so any comment written in
  that gap sorts to **one** deterministic side in both the source and our
  re-parsed output. `MakeLogical.processModuleLine` / `processImport` have worked
  examples and explain the failure mode (a comment that flips sides across
  reformats — non-idempotent and sometimes unparseable).
- A closing bracket/delimiter the parser discards (`}`, `]`, `)`, the `)` of an
  `exposing` list) → build the container with **`lpnBracketNode closePos`** so
  the comment logic can tell "inside the brackets" from "past them."

### 4. Detect author layout intent
If the new construct is one where the user might write it flat on one line or
broken across rows, detect which they chose and set `forceVertical` accordingly.
Check whether the construct's items (arguments, conditions, fields) span more
than one row, using the positions from the AST. See the [Author layout
section](#author-layout--the-forcevertical-flag) for the pattern.

### 5. Comments — usually nothing to do
`Formatter.Logical.Comments` re-attaches every comment by position **and
classifies its `CommentRole`** (`TrailsPrevious` / `LeadsOwnLine` /
`RidesInline` / `Standalone`) once, from the pristine parse rows; the renderer
reads that role and never re-derives placement from rows. See
`docs/commentHandling.md` for the whole model and `Comments.gren`, "Adding
support for a new construct", for the required reading. The short version:

- Emit your tokens as ordinary boxes in a flow and boundary comments place
  correctly on their own.
- If your construct has a **closing delimiter the parser discards**, you must use
  `lpnBracketNode` (step 3) or a comment written just before the close
  (`{ … {- c -} }`) will escape outside the container.
- The one recurring hazard is a comment that **trails a node's last token**: it
  must attach at the enclosing flow level (rendered at the outer indent) on
  *every* format, or its indentation oscillates across reformats. This is already
  enforced generically by position-only tests (`nextSiblingIsBoundary`,
  `boxKeepsTrailingCommentOutside`); do **not** add a construct-specific comment
  branch — if `fuzz-idempotency.py` flags a trailing-comment gap, fix it in those
  shared places.
- **Never read a source row or position in `Render/*` to decide comment
  placement or verticality.** Placement comes from the stored `CommentRole`;
  verticality comes from author-intent flags plus the *rendered box* shape
  (`isSingleLine` / `B.allSingles`), never a source-row predicate. If your
  construct needs a new glue rule, add a *classifier* arm in
  `Comments.gren` (pin it with a fixture — see `classifyCommentKind`'s doc), not
  a render-side row test. `tests/check-render-invariant.py` fails the build on a
  new render-side row-read; if a use is genuinely structural, allowlist its
  function there with a reason.

### 6. Render it — `MakeRenderBox.makePrettyLineBox`
Add an arm to the `makePrettyLineBox` `when box is …` dispatch (and to the
parallel flow dispatches in `FlowPolicy`/`ElmStructure` if your box appears
there) returning a `Result String Box` built from `Formatter.Render.Box`
primitives. Reuse an existing box shape if one fits — prefer
`AcrossOrVertical`, `AllAcrossOrAllVertical`, `IndentedBlock` etc. over
inventing a new box. Only add a new `LPBox` constructor when no existing shape
expresses the breaking behaviour you need; a new constructor means new arms in
*every* `when box is` in `MakeRenderBox` plus `selfBoxBounds` in
`LogicalPrintingTree`.

### 7. Blank lines (top-level only)
If you added a top-level `SyntaxType`, check `Formatter.Logical.VerticalSpace`: is your
declaration a "function group" start (2 blank lines before) or an ordinary
declaration (1)? Adjust `tagGroupStarts` if needed.

---

## Things to worry about

These mistakes are easy to make and expensive to find, because most of them
pass a first read of the diff cleanly. They surface later — as a
`fuzz-idempotency.py` gap, as a reformat that quietly reindents someone's
comment, or as a bug report that a file changed on the *second* run of
`gren format`, not the first.

**Construction and positions**

- **Always construct nodes via `lpnLeaf` / `lpnNode` / `lpnBracketNode`, never
  raw record syntax.** These are the smart constructors that compute the
  position caches (`firstPos`/`lastPos`/`minRow`/`maxRow`) bottom-up from a
  node's children — the same caches `Comments` uses to decide what's near what
  in the LPT (Logical Printing Tree, the intermediate structure between the
  AST and the rendered text). Build a node by hand instead and those caches
  come back wrong silently: no type error, just a comment that lands next to
  the wrong token, or a declaration reporting the wrong source-row range.
- **Use `SynthesizedText` for anything position-less that a comment must never
  attach to** — a generated `=`, `->`, `in`. If a comment *could* legitimately
  sit beside that token in real source (`exposing`, an `as` alias), reach for
  `mkZeroWidthText` instead, anchored carefully (see step 3 of the checklist
  above for how to pick the anchor). Get this wrong and a comment either
  attaches to a token that was never really there, or fails to attach at all.

**Idempotency and canonicalization**

- **Idempotency is a hard requirement, not an aspiration:**
  `format (format x) == format x`, down to every comment position and blank
  line. The two usual ways to break it are a trailing comment that renders at
  a different indent on the second pass, and a discarded closing bracket that
  lets a comment escape its container. `fuzz-idempotency.py` exists
  specifically to catch this class before a user does.
- **Whitespace-canonicalization is the stronger sibling goal:** output should
  depend on the code's *meaning*, not on incidental whitespace in the input —
  the one deliberate exception being the author's own flat-vs-vertical choice,
  which the formatter does preserve. Two inputs differing only in extra spaces
  or blank lines, but making the same structural choice, must format
  identically. `fuzz-whitespace.py` is the check for this.
- **`forceVertical` must be stable across a reformat.** It's computed from
  source row positions, and formatting itself changes those positions
  (indentation shifts, blank lines get inserted). If the *re-parsed* positions
  would compute a different `forceVertical` than the original pass did,
  format→reparse→format changes the layout — non-idempotent by definition.
  The idempotency fuzzer catches this too, but it's worth checking by hand
  whenever you add a new multiline-detection rule.

**Debugging mindset**

- **Think in two passes: attachment, then rendering.** A comment's position
  decides *where it attaches* before anything decides how it's drawn. A bug
  that looks like "this renders in the wrong place" is very often "this
  attached to the wrong node," one layer up. Reach for `--lpt` before you go
  looking in the renderer — it shows exactly which node a comment ended up
  under.
- **Watch for doubled hard breaks.** Some comment renderers already append a
  trailing `hardNl` internally; wrapping their result in another can wedge the
  layout. Check what the helper you're calling already does before adding a
  newline around its result.

---

## How to test

**Inspect what the formatter is doing** (run from `gren-format-lib/`):

```bash
node ../gren-format/app --show   src/F.gren   # formatted output to stdout
node ../gren-format/app --pre-ast  src/F.gren # parsed AST + comment context as JSON
node ../gren-format/app --lpt    src/F.gren   # the Logical Printing Tree as JSON
node ../gren-format/app --post-ast src/F.gren # format, verify ASTs match, print formatted AST
```

`--lpt` is your best friend for a placement bug: it shows exactly where a comment
attached and what each node's row range is.

**The effectful suite** is the main gate. Each `assertPretty` runs three checks:

```bash
cd gren-format-lib/tests && ./run-tests.sh
```

1. **formatting** — `format(<name>.dirty.gren)` is byte-equal to
   `<name>.formatted.gren`.
2. **AST equivalence** — re-parsing the output yields a semantically equal
   `Module` (catches formatting that changes meaning).
3. **idempotency** — re-formatting the `.formatted` file changes neither the
   `Module` nor the comment/blank-line `Context` (formatting is a fixed point).

Add a test by writing both `testfiles/Formatter/<Name>.dirty.gren` (deliberately
messy input) and `<Name>.formatted.gren` (the canonical output), then an
`assertPretty fsPerm "description" "<Name>"` line in
`tests/src/Test/Formatter/Format.gren`. Generate the `.formatted` with:

```bash
node ../../gren-format/app --show <Name>.dirty.gren > testfiles/Formatter/<Name>.formatted.gren
```

**Read it** to confirm it is actually canonical before trusting it.

**Two fuzzers** guard the cross-cutting properties; both are run by hand (not in
`run-tests.sh`) and need a fresh build of `gren-format/app`:

```bash
cd gren-format-lib/tests

python3 fuzz-idempotency.py -j 12
# Inserts a {- ¤ -} marker in every inter-token gap, formats twice,
# requires byte-identical output. The safety net for comment-shift bugs.

python3 fuzz-whitespace.py -j 12 --mode indent   # modes: stretch (default) | indent
# Perturbs incoming whitespace and requires byte-identical output
# (canonicalization — same meaning, same output, regardless of incoming spaces).
```

Run **both** after any change that touches comments, positions, or vertical
space — especially after adding a comment-bearing fixture, which can itself
surface a latent gap. A new construct that holds comments should get at least one
comment-bearing fixture so the fuzzers exercise it.

---

## Why the architecture is comment-driven — contrasted with elm-format

Almost everything above (position caches, the separate `Comments` pass, the
idempotency fuzzer, the `forceVertical`-stability rule) exists because of one
upstream decision: **gren-format reuses the production Gren compiler's parser
(`compiler-common`), and that parser discards comments.** elm-format made the
opposite choice, and comparing the two is the fastest way to understand why this
codebase looks the way it does.

Note this is a *parser*-level divergence, not a render-level one: since the
[Box cutover](#why-box-replaced-doc-and-prettyexpressive-before-it), our
render IR literally *is* elm-format's `Box`/`Line` types, ported rather than
reinvented. So everything below about how elm-format's `Box` renders a
comment once it's in the tree (`MustBreak` for `--`, `SingleLine` for an
inline `{- -}`, the `Tab`/`prefix` indentation mechanism) describes our
renderer too. What's still genuinely different — and what the rest of this
section is really about — is how a comment *gets into* that tree in the
first place: elm-format's parser puts it there directly, in a typed slot;
ours puts it there afterward, by matching source positions.

### elm-format: comments live inside the AST

elm-format ships its own parser, purpose-built for formatting, whose AST is
*comment-carrying*. Comments are first-class nodes in typed structural slots:

```haskell
data Comment = BlockComment [String] | LineComment String | ...

Commented c a                       -- a value 'a' plus its comments (the "C" ctor)

data CommentType = BeforeTerm | AfterTerm | Inside | BeforeSeparator | AfterSeparator
type C2 l1 l2 = Commented (Comments, Comments)      -- e.g. pre + post slots
type C1Eol l  = Commented (Comments, Maybe String)  -- + an end-of-line comment
```

The list types (`Sequence`, `OpenCommentedList`, `ExposedCommentedList`) carry
comment slots on **every element and every separator**. The parser fills these
slots as it consumes source, so a comment's attachment is decided
*grammatically* — `{- x -}` is `AfterTerm` on `a` because the parser read it in
that grammatical position. There is no position arithmetic anywhere.

Rendering then treats a slotted comment as just another `Box` (elm-format's
render IR — a bottom-up `SingleLine | Stack | MustBreak` of lines) and runs it
through the *same* `spaceSepOrStack` combinators as everything else:
`formatComment (LineComment …)` is a `MustBreak` box (a `--` inherently ends its
line); a one-line `BlockComment` is a `SingleLine` box (so `{- x -}` can stay
inline); an end-of-line comment on a single-line box becomes `MustBreak`, which
is exactly how a trailing comment forces its enclosing structure open — straight
from the slot, no inference. **There is no separate comment pass.** Comments ride
the tree from parse to render.

That same bottom-up `Box` model is also where elm-format's join-vs-stack
decisions live: `allSingles children` asks "is every part still one line?", and
author newlines enter as parser flags (`FASplitFirst`/`FAJoinFirst`,
`ForceMultiline`, `Multiline`). Indentation is a tab-stop (`Tab` rounds to the
next multiple of 4) plus `prefix` (pads continuation lines by the exact character
width of the prefix) — the mechanism behind the fixed-4-vs-round-to-4 divergence
noted in the README.

### gren-format: comments are re-attached by position

Our AST (`Compiler.Ast.Source`) has no comment slots. Comments arrive as a flat,
source-ordered side list (`Compiler.Parse.Context`, `Located (Line | Block)`) and
are re-attached to the already-built LPT **geometrically** by
`Formatter.Logical.Comments`: a comment at `(row, col)` is placed next to
whichever token its position falls between. Everything that looks like incidental
bookkeeping in this codebase is forced by that one fact:

- **The LPT carries source positions on every node** (`firstPos`/`lastPos`/
  `minRow`/`maxRow`, computed by the smart constructors) — `Box` itself still
  carries none, on either side; the positions live one layer up, in the LPT,
  precisely because *something* upstream of `Box` has to be able to *locate*
  a comment before rendering, and elm-format's AST slots make that
  unnecessary.
- **Attachment must survive a reformat.** We format, our output is re-parsed, and
  comments are re-attached from their *new* positions. If a comment lands in a
  different relative gap the second time, the output shifts — the "comment moved
  on reparse" bug class. `fuzz-idempotency.py` (a `{- ¤ -}` in every inter-token
  gap, format twice, demand byte-equality) is the safety net specifically for
  this. elm-format gets comment-idempotency for free: the comment never leaves
  its slot.
- **`forceVertical` stability** (the rule in *Things to worry about*) is the same
  hazard one level up — our author-layout signal is recomputed from positions
  each pass, so it must be invariant under reformatting; elm-format's equivalent
  is a parser flag baked into the tree once.
- **Render-time comment logic re-derives elm's typed slots from geometry.**
  `buildFlowDocImpl`'s `SingleLineComment`/`BlockComment` handling,
  `peelTrailingComments`, `splitLeadingComments`, `boxKeepsTrailingCommentOutside`,
  and the `prevElided` zero-width-token hazard all exist to recover
  "trailing-same-line vs standalone-own-line vs end-of-line" — the distinctions
  elm-format reads directly off `BeforeTerm` / `AfterTerm` / `C0Eol`. Our version
  keys off `loc.start.row == acc.prevRow` and friends.

### The tradeoff in one line

elm-format **owns its parser**, so comments are grammatical and idempotency is
automatic — but it must track the Elm language itself. gren-format **borrows the
compiler's parser**, so it can never diverge from what Gren actually accepts — but
it pays for that by reconstructing comment attachment from positions, which is
what the LPT, the `Comments` pass, and the fuzzers are all in service of. When you
add a construct that can hold comments, you are extending *our* half of that
tradeoff: get the positions right (step 3 of the checklist above) and give it a
comment-bearing fixture so the fuzzers exercise the reconstruction.

---

## Where to read more

- `README.md` — authoritative, example-by-example description of every rule.
- `Logical/LogicalPrintingTree.gren` — the module doc's categorised box table,
  then every box's own doc comment and the caching invariants.
- `Logical/Comments.gren` module doc — the comment-attachment algorithm and its
  "Adding support for a new construct" section; the body is banner-sectioned into
  phase 1 (top-level slot) and phase 2 (inner descent).
- `Logical/BinopPrecedence.gren` — the operator fixity table and why its
  `binopMinPrecedence` seam is shared by `InsertExpressions` and `MakeRender`.
- `Formatter.Render.Box` (`Render/Box.gren`) — the `Line`/`Box` types and
  renderer; small enough to read in full.
- `Formatter.Render.MakeRenderBox` (`Render/MakeRenderBox.gren`) — the
  per-`LPBox` dispatch that builds `Box` values.
- `Formatter.Render.FlowPolicy` — the flow-item join decision layer
  (`decide`); read its module doc before adding a new kind of flow item.
