# Extending the formatter for new Gren syntax

This is the orientation guide for a developer who needs to teach `gren format`
about a new piece of Gren syntax — a new AST node, a new declaration kind, a new
expression form. Gren will keep growing, so the formatter has to be easy to
extend without breaking the invariants that keep formatting correct, stable, and
comment-faithful.

Read this once for the mental model, then keep `README.md` (the authoritative,
example-driven description of *what every rule does*) open while you work.

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

Leaves (carry text/position, no children):

- `UnbreakableText (Located String)` — a real source token. Prints as-is, never
  breaks. **This is the common case** — most of your tokens are this.
- `SynthesizedText String` — punctuation/keywords the AST doesn't position
  (`=`, `->`, `in`, `(..)`). **Excluded from all row-range and comment math.**
- `SingleLineComment` / `BlockComment` / `DocComment` (`Located String`) —
  inserted by `Formatter.Logical.Comments`, you rarely emit these yourself.
- `MultilineString (Located (Array String))`, `EmptyLine`, `RootBox`.

Layout boxes (have children):

- `AcrossOrVertical { forceVertical : Bool }` — bare (unbracketed) token
  sequence, all on one line *or* one child per line with continuations
  indented +4 — same author-driven choice as `AllAcrossOrAllVertical`, just
  without delimiters. The default for "a thing and its parts" (a function
  call, a variant + payload). When `forceVertical` is `True`, continuations
  always break — no flat option.
- `AllAcrossOrAllVertical ListBrackets` — bracketed list, all on one line *or*
  one item per line (`ListParen`/`ListCurly`/`ListSquare`). Vertical when any
  item boundary spans rows.
- `AlwaysVertical ListBrackets` — bracketed list that never collapses.
- `IndentedBlock` / `BodyBlock` — a body on its own (indented) line, hard break.
  `SoftIndentedBlock` is the soft variant that may stay inline (lambda bodies,
  port payloads).
- `WhenBranch`, `IfCondition { forceVertical }`, `WhenFlow { forceVertical }`,
  `PipelineStep`, `ParenBlock`, `OpAndRhs`, `AlignedFlow`, `PrefixGlue`,
  `RecordUpdate { forceVertical }`, `EmptyBracketed` — specialised shapes; read
  their doc comments in `LogicalPrintingTree.gren` before reusing.

See `README.md` for the rendered example of each.

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

Key functions, mirroring `Box.hs`:

- `B.line l` — wrap one `Line` as a `SingleLine` box
- `B.mustBreak l` — wrap one `Line` as a `MustBreakBox` (a `--` comment: it
  must own its line, no matter what glues around it)
- `B.stack1 boxes` — stack 2+ boxes into one multi-line `Box`
- `B.indent box` — prepend a `Tab` to every line
- `B.prefix pref box` — glue `pref` onto line 1, pad the rest
- `B.addSuffix suffix box` — append to the *last* line only
- `B.freezeTabs box` — see above
- `B.render box` — the final `String`, right-trimmed line by line

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
`../gren-pretty-expressive/`), a Wadler/Prettier-style pretty-printer with a
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
fig" migration ported one construct at a time behind a self-verifying guard
that compared `Box` output against the live `Doc` output and only trusted
`Box` where they agreed (see the many `Change-1 strangler tranche N` commits).
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
blank-line machinery just works. Go in this order.

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
`Formatter.Logical.Comments` re-attaches every comment by position; it is largely
construct-agnostic. Its module doc (`Comments.gren`, "Adding support for a new
construct") is required reading, but the short version:

- Emit your tokens as ordinary boxes in a flow and boundary comments place
  correctly on their own.
- If your construct has a **closing delimiter the parser discards**, you must use
  `lpnBracketNode` (step 3) or a comment written just before the close
  (`{ … {- c -} }`) will escape outside the container.
- The one recurring hazard is a comment that **trails a node's last token**: it
  must attach at the enclosing flow level (rendered at the outer indent) on
  *every* format, or its indentation oscillates across reformats. This is already
  enforced generically by position-only tests (`columnClaim`,
  `nextSiblingIsBoundary`); do **not** add a construct-specific comment branch —
  if `fuzz-idempotency.py` flags a trailing-comment gap, fix it in those shared
  places.

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

- **Always construct via `lpnLeaf`/`lpnNode`/`lpnBracketNode`.** Raw record
  syntax skips the position caches.
- **`SynthesizedText` for anything position-less that a comment must not cling
  to.** If a comment *could* legitimately sit beside it, use `mkZeroWidthText`
  with a carefully chosen anchor instead.
- **Idempotency is a hard requirement.** `format (format x) == format x`,
  including every comment position and blank line. Trailing comments and
  discarded closing brackets are the usual ways to break it.
- **Whitespace-canonicalization is the stronger goal.** Output should depend on
  the code's *meaning*, not its incoming whitespace — with the one deliberate
  exception that the author's flat-vs-vertical choice is preserved. Two programs
  that differ only in incidental whitespace (extra spaces, blank lines) but make
  the same flat-vs-vertical structural choice must format identically.
- **Beware doubled hard breaks.** Some comment renderers already append a
  `hardNl`; wrapping their result in another can wedge the layout. Check the
  helper before adding a newline around it.
- **Two-pass thinking.** Positions affect *attachment* (which comment goes where)
  *before* they affect *rendering*. A bug that looks like "renders wrong" is
  often "attached wrong" upstream — inspect the LPT first (`--lpt`).
- **`forceVertical` must be stable.** The flag is computed from source positions.
  After formatting, those positions change. Make sure the re-parsed positions
  produce the same `forceVertical` value — otherwise format→reparse→format
  changes the layout (non-idempotent). The idempotency fuzzer will catch this.

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
