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
Src.Module + Ctx.Context  ──►  LPT  ──►  R.Doc  ──►  String
                          MakeLogical   MakeRender
```

- **Input** is two things from the parser: the AST (`Src.Module`) *and* a
  separate stream of comments (`Ctx.Context`). Comments are **not** in the AST.
- **LPT** (Logical Printing Tree) is our own intermediate tree. It says *what
  should be grouped with what* and *how each group may break across lines* — but
  not the exact spaces. It also carries enough source-position information to put
  comments back where the author wrote them.
- **`Formatter.Render.Doc`** turns the LPT into a concrete string. It is a simple
  greedy renderer: `Group` always renders flat (no optimizer), `HardNl` always
  breaks, `Nl`/`BreakDoc` are soft (space in flat context, newline otherwise).
  There is no page width. Author layout is encoded at LPT-build time via
  `forceVertical` flags on certain boxes (see below).

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
  MakeRender.gren                 LPT → R.Doc → String
  Doc.gren                        the custom Doc IR + renderer
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

## `Formatter.Render.Doc` — the backend

`Formatter.Render.Doc` is a small custom Doc renderer. Key combinators:

- `R.text s` — literal text, width = `String.count s`
- `R.concat a b` — sequence
- `R.concats docs` — sequence a whole list (right-leaning, so it matches a
  hand-nested `R.concat a (R.concat b …)`); prefer it over deep `R.concat` nests
- `R.hardNl` — unconditional newline + indent padding
- `R.nl` — soft break: space in flat context, newline+indent otherwise
- `R.breakDoc` — soft break: nothing in flat context, newline+indent otherwise
- `R.nest n doc` — increase indent by `n` inside `doc`
- `R.align doc` — set indent to current column inside `doc`
- `R.reset doc` — set indent to 0 inside `doc`
- `R.group doc` — render `doc` in flat mode (all `Nl`/`BreakDoc` become spaces or nothing)
- `R.vcat docs` — join with `hardNl`
- `R.foldDoc f docs` — fold with a combining function

**`Group` always renders flat.** There is no optimizer and no page-width test.
A `group` simply forces flat mode on for its subtree. This replaces the old
`P.group`/`P.choice` pattern: where you used to write `P.group (a |> P.nl |> b)`
to get "try flat, break if needed", now you write `R.group (R.concat a (R.concat R.nl b))`,
which just always stays flat.

When you add a new box type to `makePDoc`, return `Result String Doc` built from
these combinators. Reuse an existing box shape if one fits — a new `LPBox`
constructor requires new arms in *every* `when box is` in `MakeRender` plus
`selfBoxBounds` in `LogicalPrintingTree`.

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
  enforced generically by position-only tests (`trailsClaimedConstruct`,
  `nextSiblingIsBoundary`); do **not** add a construct-specific comment branch —
  if `fuzz-idempotency.py` flags a trailing-comment gap, fix it in those shared
  places.

### 6. Render it — `MakeRender.makePDoc`
Add an arm to the `makePDoc` `when box is …` dispatch (and to the parallel
flow/aligned dispatches if your box appears there) returning a `Result String Doc`
built from `Formatter.Render.Doc` combinators. Reuse an existing box shape if one
fits — prefer `AcrossOrVertical`, `AllAcrossOrAllVertical`, `IndentedBlock` etc.
over inventing a new box. Only add a new `LPBox` constructor when no existing
shape expresses the breaking behaviour you need; a new constructor means new arms
in *every* `when box is` in `MakeRender` plus `selfBoxBounds` in
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

## Where to read more

- `README.md` — authoritative, example-by-example description of every rule.
- `Logical/LogicalPrintingTree.gren` — the module doc's categorised box table,
  then every box's own doc comment and the caching invariants.
- `Logical/Comments.gren` module doc — the comment-attachment algorithm and its
  "Adding support for a new construct" section; the body is banner-sectioned into
  phase 1 (top-level slot) and phase 2 (inner descent).
- `Logical/BinopPrecedence.gren` — the operator fixity table and why its
  `binopMinPrecedence` seam is shared by `InsertExpressions` and `MakeRender`.
- `Formatter.Render.Doc` (`Render/Doc.gren`) — the Doc type and renderer; small enough to read in full.
