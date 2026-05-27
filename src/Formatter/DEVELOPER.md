# Extending the formatter for new Gren syntax

This is the orientation guide for a developer who needs to teach `gren format`
about a new piece of Gren syntax — a new AST node, a new declaration kind, a new
expression form. Gren will keep growing, so the formatter has to be easy to
extend without breaking the invariants that keep formatting correct, stable, and
comment-faithful.

Read this once for the mental model, then keep `README.md` (the authoritative,
example-driven description of *what every rule does*) open while you work. The
architecture summary in the repo `CLAUDE.md` is the short version of this file.

---

## The pipeline in one line

```
Src.Module + Ctx.Context  ──►  LPT  ──►  PrettyExpressive Doc  ──►  String
                          MakeLogical   MakePretty
```

- **Input** is two things from the parser: the AST (`Src.Module`) *and* a
  separate stream of comments (`Ctx.Context`). Comments are **not** in the AST.
- **LPT** (Logical Printing Tree) is our own intermediate tree. It says *what
  should be grouped with what* and *how each group may break across lines* — but
  not the exact spaces. It also carries enough source-position information to put
  comments back where the author wrote them.
- **PrettyExpressive** turns the LPT into a concrete string, choosing line
  breaks optimally for the 100-column page width.

Entry point: `Formatter.PrettyPrinter.prettyPrint : Src.Module -> Ctx.Context ->
Result String String`. It calls `MakeLogical.makeLogicalPrintingTree` (build the
LPT) then `MakePretty` (render it). Every stage returns `Result String _`; there
are no silent fallbacks — an unhandled case is an `Err`, not a guess.

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
every token — not to reproduce them, but to (a) decide source order and (b)
re-attach comments. If a new AST node carries a token the parser *doesn't* record
a position for (a synthesized keyword, a closing bracket), you will have to
synthesize a faithful position for it (see below).

### The comments — `Compiler.Parse.Context`

```gren
type alias Context = { indent : Int, lineStart : Int
                     , comments : Builder (Located Comment) }
type Comment = Line String | Block String
```

Comments ride alongside the AST as a flat, source-ordered list of located
`Line` (`--`) or `Block` (`{- -}`) strings. They are re-attached to the LPT
*after* it is built, purely by position (`Formatter.Comments`). This is why
positions on your LPT nodes must be honest: a comment is placed next to whatever
token its `(row, col)` falls between.

---

## The LPT — `Formatter.LogicalPrintingTree`

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
  inserted by `Formatter.Comments`, you rarely emit these yourself.
- `MultilineString (Located (Array String))`, `EmptyLine`, `RootBox`.

Layout boxes (have children):

- `AcrossThenIndent` — flow tokens across; wrap continuations +4. The default for
  "a thing and its parts" (a function call, a variant + payload).
- `AllAcrossOrAllVertical ListBrackets` — bracketed list, all on one line *or* one
  item per line (`ListParen`/`ListCurly`/`ListSquare`).
- `AlwaysVertical ListBrackets` — bracketed list that never collapses (2+ field
  record literal).
- `IndentedBlock` / `BodyBlock` and their soft variants `SoftIndentedBlock` /
  `SoftBodyBlock` — a body on its own indented line (hard break) vs. one that may
  stay inline when it fits.
- `WhenBranch`, `IfCondition`, `PipelineStep`, `ParenBlock`, `OpAndRhs`,
  `AlignedFlow`, `PrefixGlue`, `RecordUpdate`, `EmptyBracketed` — specialised
  shapes; read their doc comments in `LogicalPrintingTree.gren` before reusing.

See `README.md` for the rendered example of each.

### `OriginalRows` and `SyntaxType` — the top level only

Each **top-level declaration** becomes exactly one `OriginalRows { first, last,
stype }` node directly under `RootBox`, where `stype : SyntaxType` tags the kind
(`StModule`, `StImport`, `StFunctionSignature`, `StTypeUnion`, …) and
`first`/`last` are its source-row range. Comments and blank lines are then added
as *sibling* `OriginalRows` nodes. The row range drives two things: source
ordering (`MakeLogical.sortOriginalRows`) and blank-line decisions
(`Formatter.VerticalSpace`). Get `first`/`last` right or comments/blanks land in
the wrong place — `first` should be the declaration's **leading keyword** row.

### The cached bounds (why `lpnNode` matters)

Every node caches `firstPos`, `lastPos`, `minRow`, `maxRow`, `lastBracketEnd`,
and `bracketEndExact`. `Formatter.Comments` uses these to answer "what's the
first/last positioned token here?" and "where does the rightmost bracket close?"
in O(1). `lpnNode` fills them from `selfBoxBounds box` merged with the children;
`lpnBracketNode` additionally records an *exact* closing-bracket position.
`SynthesizedText` contributes nothing to these caches — that is deliberate, so a
generated `->` never attracts a comment.

---

## PrettyExpressive (the backend)

`gilramir/gren-pretty-expressive` is a Wadler/Lindig-style optimal pretty
printer: you build a `Doc` from combinators (`text`, `concat`, `nl`, `hardNl`,
`group`, `nest`, `align`, `vcat`) and it picks the cheapest legal line-breaking
for the page width. `MakePretty` is the only module that imports it; you only
need its docs if you add a genuinely new layout shape. Page width is 100 and one
indent step is 4 (`grenIndent`), both in `MakePretty`.

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
  `Formatter.MakeLogical` (mirror `processUnionDecl` / `processPorts`). Wrap the
  result in `makeOrigRows firstRow stype children` with a new or existing
  `SyntaxType`. `firstRow` = the **keyword** row.
- **Expression** → `Formatter.InsertExpressions.insertExpression`.
- **Pattern** → `Formatter.InsertPatterns`.
- **Type** → `Formatter.InsertTypes`.

Use the shared helpers in `Formatter.LPTHelpers`: `mkTextFromLocString` (a real
token at its `Located` position), `mkText pos str` (text at an explicit
position), `mkZeroWidthText pos str` (a synthesized token anchored at a real
position but contributing zero width — see below), `resultFoldl`.

### 3. Get positions right (the part that bites)
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

### 4. Comments — usually nothing to do
`Formatter.Comments` re-attaches every comment by position; it is largely
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

### 5. Render it — `MakePretty.makePDoc`
Add an arm to the `makePDoc` `when box is …` dispatch (and to the parallel
flow/aligned dispatches if your box appears there) returning a `Result String
(P.Doc cost)`. Reuse an existing box shape if one fits — prefer
`AcrossThenIndent`, `AllAcrossOrAllVertical`, `IndentedBlock` etc. over inventing
a new box. Only add a new `LPBox` constructor when no existing shape expresses
the breaking behavior you need; a new constructor means new arms in *every*
`when box is` in `MakePretty` plus `selfBoxBounds` in `LogicalPrintingTree`.

### 6. Blank lines (top-level only)
If you added a top-level `SyntaxType`, check `Formatter.VerticalSpace`: is your
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
  the code's *meaning*, not its incoming whitespace. Two known principles:
  multi-line block-comment bodies are re-indented from the comment's *own*
  structure, not the input `{-` column (`blockCommentDoc`; see README,
  "Multi-line block comments"); and a comment's inline-vs-own-line placement is
  meaningful and preserved (see README, "Comment placement is meaning-bearing and
  preserved"). Don't reintroduce input-column dependence.
- **Beware doubled hard breaks.** Some comment renderers already append a
  `hardNl`; wrapping their result in another can wedge the layout. Check the
  helper before adding a newline around it.
- **Two-pass thinking.** Positions affect *attachment* (which comment goes where)
  *before* they affect *rendering*. A bug that looks like "renders wrong" is
  often "attached wrong" upstream — inspect the LPT first.

---

## How to test

**Always rebuild first.** The test harness does *not* rebuild the formatter.
After editing `src/Formatter/`:

```bash
cd compiler && ./build.sh          # builds the formatter into ../app
```
If `build.sh` fails with an opaque "package constraints too wide", compile the
module standalone for a real error message (use the module name, not a path):
`cd compiler-node && ../gren.sh make Formatter.PrettyPrinter`.

**Inspect what the formatter is doing** (run from `compiler-node/`):

```bash
../gren.sh format --show   src/F.gren   # formatted output to stdout
../gren.sh format --pre-ast  src/F.gren # parsed AST + comment context as JSON
../gren.sh format --lpt    src/F.gren   # the Logical Printing Tree as JSON
../gren.sh format --pex    src/F.gren   # the PrettyExpressive Doc as JSON
../gren.sh format --check  src/F.gren   # format, then verify ASTs still match
```
`--lpt` is your best friend for a placement bug: it shows exactly where a comment
attached and what each node's row range is.

**The effectful suite** is the main gate. Each `assertPretty` runs three checks:

```bash
cd compiler-node/effectful-tests && ./run-tests.sh
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
`src/Test/Formatter/Format.gren`. Generate the `.formatted` with
`../gren.sh format --show ….dirty.gren > …formatted.gren`, but **read it** to
confirm it is actually canonical before trusting it.

**Two fuzzers** guard the cross-cutting properties; both are run by hand (not in
`run-tests.sh`) and need a fresh `build.sh`:

```bash
cd compiler-node/effectful-tests
python3 fuzz-idempotency.py        # inserts a {- ¤ -} marker in every token gap,
                                   # formats twice, requires byte-identical output.
                                   # The safety net for comment-shift bugs.
python3 fuzz-whitespace.py --mode newline   # perturbs incoming whitespace and
                                   # requires byte-identical output (canonical-
                                   # ization); modes: stretch | indent | newline.
```
Run **both** after any change that touches comments, positions, or vertical
space — especially after adding a comment-bearing fixture, which can itself
surface a latent gap. A new construct that holds comments should get at least one
comment-bearing fixture so the fuzzers exercise it.

---

## Where to read more

- `README.md` — authoritative, example-by-example description of every rule.
- `LogicalPrintingTree.gren` — every box's doc comment and the caching invariants.
- `Comments.gren` module doc — the comment-attachment algorithm and its
  "Adding support for a new construct" section.
- `CLAUDE.md` (repo root) — condensed architecture map and build/run commands.
