# How the formatter handles comments

A developer's guide to comment placement in `gren-format`. If you are adding a
construct, read the checklist in `DEVELOPER.md` first; this document is the
architecture behind step 5 of that checklist. For *why* the code is shaped this
way (the bug history that motivated it), see `comment-arch.md`.

## The problem, in one paragraph

Comments are not part of the AST. The parser hands them back as a separate,
source-ordered stream of `Located` strings (`Compiler.Parse.Context.comments`),
and the formatter re-attaches each one to the tree *by position* before
rendering. The hard part is that placement must be a **reparse fixed point**:
after we format, someone can re-parse our output and format again, and the
comment must land in the same place — same line, same indent. A rule that
decides "does this comment trail the previous token?" by comparing *source rows*
is fragile, because the moment formatting moves anything, the rows change. Get
it wrong and a comment's indentation oscillates (`+4 ↔ column 0` across
reformats) or a code path crashes on a shape a predicate mispromised. The whole
design exists to make placement stable by construction.

## The rules, in English

Six rules decide every comment. Everything below this section is the machinery
that implements them stably; when a bug report and the machinery disagree, these
are what the formatter is *trying* to do. They were written down on 2026-08-01,
reading back the verdicts from `tests/triage-comment-parity.py --interview` over
the comment axis's parity divergences, and the code was changed where it did not
already obey them (see [What changed](#what-changed-2026-08-01)).

**C1 / C2 are about attachment; C3–C6 are about layout.** The two never trade
against each other: attachment is settled first, in `Comments.gren`, and the
renderer lays out whatever it is handed.

### C1 — A comment belongs to the code you wrote it next to

Placement comes from the comment's own source position relative to the tokens the
parser recorded, and nothing else. Layout never re-homes a comment to a different
owner to make a line fit.

### C2 — Where the separator has no source position, the comment leads what follows it

`=` `:` `|` `,` `->` and every keyword are parsed and thrown away (see
[What the parser records](#what-the-parser-records-and-what-it-doesnt)), so
`x {- c -} SEP y` and `x SEP {- c -} y` reach the formatter as the same three
facts. One of the two spellings must therefore differ from elm-format whichever
side is picked. gren-format picks the **later** side — the comment leads `y`:

```gren
-- you wrote (either spelling)          -- gren-format
{ fld {- c -} = 1 }                     { fld = {- c -} 1 }
{ rec {- c -} | a = 1 }                 { rec | {- c -} a = 1 }
[ 1 {- c -}, 2 ]                        [ 1, {- c -} 2 ]
f : Int {- c -} -> Int                  f : Int -> {- c -} Int
when sel {- c -} is …                   when sel is ⏎ {- c -} ⏎ …
let a = 1 {- c -} in b                  let … in ⏎ {- c -} ⏎ b
```

Picking the later side is what makes a `--` spelling readable, which is the
clearest way to see why the side matters at all — the choice forces a layout:

```gren
-- gren-format, for both spellings:     -- the earlier side would give:
{ fld =                                 { fld
    -- why                                -- why
    compute 1 }                             =
                                            compute 1 }
```

**The one exception is a `--` between two items of a list**, which stays with the
item above it:

```gren
[ apple -- the red one
, banana
]
```

A `--` ends its row, so it reads as a note about the row it is on — and there
that row is a complete list item. Sending it to the later side would move the
note onto `banana`. The idiom is unanimous in real code (every instance across
`core/` and `compiler-common/` is spelled this way, none the other), and it is
also what elm-format produces for that spelling. A single-line `{- -}` in the
same gap has no such pull and follows C2 (`[ 1, {- c -} 2 ]` — which does not
appear in real code at all, in either spelling).

**Two places C2 is not yet applied**, both deliberate and both out of the scope
that was reviewed:

- an **`exposing ( … )` list**, the one bracket list whose items get *reordered*.
  `SortSymbols` owns comment ownership there on the opposite model — a comment
  after a name is that name's and travels with it through the sort, which is what
  makes `(a {- c -}, b)` and `(b, a {- c -})` land on the same bytes
  (`unfoldLastTrailing`). Flipping the side means reshaping clustering and the
  closing-`)` pinning with it; until that is designed and put under the
  `sort-order` oracle, an exposing list keeps the earlier side.
- a **union variant's `|`** (`= A {- c -} | B`). elm-format breaks the union open
  around that comment whichever side it is on, so gren diverges from it either
  way and flipping buys no parity.

### C3 — A comment never forces a break

A construct the author wrote on one line stays on one line whenever every comment
inside it can share that line. A single-line `{- -}` can; a `--` cannot (it
swallows the rest of the row), and a multi-line `{- … -}` brings its own newlines.

```gren
{ rec {- c -} | a = 1 }      stays flat
[ 1, {- c -} 2 ]             stays flat
[ 1, -- c                    breaks — the `--` ends the row
  2 ]
```

### C4 — A comment never causes the code around it to be re-laid-out

Delete every comment from gren-format's output and you get the layout it would
have produced for the same input with no comments in it. elm-format's opposite
rule — a comment inside a construct forces every part of that construct onto its
own line — is [#23](elmFormatComparison.md#divergence-23); [#14](elmFormatComparison.md#divergence-14),
[#16](elmFormatComparison.md#divergence-16) and [#17](elmFormatComparison.md#divergence-17)
are instances of the same disagreement.

### C5 — gren-format adds nothing around a comment

No blank line above or below that the author did not write; no floating a comment
out onto a row of its own to give it air.
([#12](elmFormatComparison.md#divergence-12), [#18](elmFormatComparison.md#divergence-18))

### C6 — An own-line comment is indented to the code it leads

Not to a column of its own. ([#24](elmFormatComparison.md#divergence-24))

### What changed (2026-08-01)

Two of the six did not hold when they were written down, and the code was changed
to match rather than the rules weakened to fit:

- **C2 at the record update's `|`.** The comment used to be canonicalized to its
  own line between the base and the fields, on the argument that both sides were
  claims the formatter could not support. That was a third answer to a two-sided
  question, and it cost C3 as well — a record update carrying such a comment
  could never stay on one line. It now leads the first field.
- **C2 at a list's `,`, for a single-line `{- -}`.** It used to trail the item
  above it, like a `--`.

Both are one new `CommentRole`, `LeadsNext`, plus the renderer gluing such a
comment onto the front of the following item's box *before* the `| `/`, ` prefix
goes on — so whatever drops below lands in the column the prefix would have
occupied, which is elm-format's rule and the only placement that survives a
reparse.

`LeadsNext` keys on **either** adjacent item's row, and that is load-bearing: the
comment renders on the *next* item's row, so a rule that recognised only "same row
as the previous item" would read its own output back as an own-row comment and
drop it to its own line. `[ 1 {- c -}` ⏎ `, 2 ]` oscillated exactly that way
before the second row was added.

One item shape refuses a front glue: a `"""…"""`. Gluing shifts the item's first
row by the comment's width while the string's own content rows move by a
different amount, and Gren requires every row of a multi-line string to be
indented equally — the output stops parsing. Such a comment stays own-line
(`subtreeHasMultilineString`). The same guard covers the **opener** slot
(`[ {- c -} """…"""`), which had the bug already and was crashing on it; 19 of
`gen-random.py`'s first 1,500 seeds hit it, all of them that shape.

## The one-line pipeline

```
parse → Src.Module + Ctx.Context
      → LPT (Logical Printing Tree)          Formatter.Logical.MakeLogical
      → comments classified + attached        Formatter.Logical.Comments   ← the key stage
      → sorted / blank-lined                   SortSymbols, VerticalSpace
      → Box                                    Formatter.Render.*
      → String
```

The invariant that keeps comments stable:

> **After `Formatter.Logical.Comments` runs, no code in `Render/*` reads a source
> row or position to make a comment-placement or layout decision.** Placement is
> the comment's stored `CommentRole`; verticality is author-intent flags plus the
> *rendered box* shape. This is enforced by `tests/check-render-invariant.py`.

## `CommentRole` — decide once, store it

A comment leaf carries a `CommentRole`, decided **once** in `Comments.gren` from
the pristine parse rows and read verbatim by the renderer. There are four:

| Role | Meaning | Renders as |
|---|---|---|
| `TrailsPrevious` | glues onto the end of the previous sibling's last line | `<prev last line> <comment>` |
| `LeadsOwnLine` | stands on its own line at the flow/body indent, before the next sibling | own line |
| `LeadsNext` | belongs to the sibling that *follows*, across a separator with no position — rule **C2** | glued to the front of that sibling's box, inside the `,`/`\|` prefix |
| `RidesInline` | a single-line `{- -}` riding mid-flow without breaking (`f {- k -} x`) | mid-line, inline |
| `Standalone` | a top-level detached comment at column 1 | its own `OriginalRows` |

`RidesInline` vs `TrailsPrevious` matters to exactly **one** consumer
(`literalCommentsRideFlatLine`, which keeps a bracket/union flat only when every
comment can ride the flat line). Everywhere else the two glue identically, so the
distinction never depends on the *next* neighbour. `DocComment` (`{-| … -}`) is
top-level-only and carries no role.

The constructors live in `Formatter.Logical.LogicalPrintingTree`:

```gren
| SingleLineComment { loc : Located String, role : CommentRole }
| BlockComment       { loc : Located String, role : CommentRole }
```

## Where classification happens

All in `Formatter.Logical.Comments`, at the **single point** where a comment leaf
is spliced into a child array (`insertAmongChildren` → `classifyCommentKind`):

- **`Standalone`** — `findOrCreateOrigRow` detaches an own-line comment below a
  top-level declaration to a fresh column-1 `OriginalRows` (matching elm-format;
  column 1 cannot drift). Its `created` flag sets the role.
- **everything else** — `classifyCommentKind` reads the comment's kind, its
  immediate neighbours (`before` / `after` after the synthesized-token skip), and
  its row, and returns `TrailsPrevious` / `LeadsOwnLine` / `RidesInline`.

This is the *one* place source-row arithmetic is legitimate — interpreting the
original source is the whole job of `Comments.gren`, and the rows are still
pristine here.

### The body redirect

Before classifying, `insertAmongChildren` checks whether the node the comment
would be spliced *before* is a body wrapper — an `IndentedBlock` (a `let`
binding's value), a `PipelineStep`, or a `BodyBlock` (a declaration's value).
If so the comment is pushed inside that node as its **first child**, with the
role forced to `LeadsOwnLine`.

`IndentedBlock` and `BodyBlock` start their content on a fresh line
unconditionally, so a comment sitting between the head and the body has no flat
line to ride: it leads the body wherever the author wrote it, and the row it was
written on cannot change that. Leaving it outside instead makes the flow's soft
separator glue it onto the head — `x =` ⏎ `{- c -}` ⏎ `42` rendered as
`x = {- c -}` ⏎ `42`, which contradicted both the stored role and elm-format. The
`BodyBlock` arm is what makes a top-level declaration behave like the `let`
binding one level down.

A `PipelineStep` is the exception among the three, and its arm is conditional: a
pipeline can stay on one row (`seed |> f |> g`), so a `RidesInline` comment
written between the seed and the operator *does* have a line to ride and stays
outside, classified normally (`seed {- c -} |> f`). Redirecting it made it the
step's first child, where nothing precedes it — so it classified `LeadsOwnLine`
and forced the whole chain vertical, a comment creating breaks the code never
needed. A `--` or a multi-line `{- … -}` breaks the row regardless and still
leads the step body. `SoftIndentedBlock` (a lambda body the author started on the
`->` row) has its own arm applying the same "can it ride?" test.

## The rule: coarse generic flow vs permissive list-like contexts

The recurring discovery (see `comment-arch.md`) is that different render paths
glue comments with different permissiveness, so the classifier keys on the
**container**:

- **Generic flow** (call arguments, `let` bindings, `when`-branch bodies, …) —
  the *coarse* rule. A same-row `--` glues onto the previous item's last token,
  but a same-row `{- -}` glues only after a bare token or a *multi-line* bracket's
  close — **not** after a plain-token call or a single-line bracket (there a
  block comment stays own-line, matching elm-format). The two are the
  `prevLineGlueRow` (line, liberal) and `prevBlockGlueRow` (block, coarse) helpers,
  a faithful port of the renderer's old per-box-kind tables.
- **List-like contexts** — *permissive*: a same-row comment trails **any** item.
  These have their own branches because their comments never reach the generic
  path:
  - **binop** (`Binop` / `OpAndRhs`) — glue relative to the last real *operand*
    row, mirroring `BinopLayout`'s `contentRow`.
  - **bracket lists** (`AllAcrossOrAllVertical` / `AlwaysVertical` / …) — glue if
    on the previous item's row, any item kind.
  - **union variants** (`= Ctor` / `| Ctor`) and **`when` branches** — handled
    inside `prevLineGlueRow` / `prevBlockGlueRow` by treating a union-variant
    `AcrossOrVertical` or a `WhenBranch` predecessor permissively.

`classifyCommentKind`'s doc comment pins each branch to a fixture; the reparse
fixed-point argument for each role is spelled out in `comment-arch.md` §5.4.

### Two subtleties worth knowing

- **Elided `->`** — `nodeIsElided` is scoped to the *zero-width synthesized `->`*
  only (an `UnbreakableText "->"` with `start == end`), not every synthesized
  token. A comment trailing that arrow glues regardless of row (the arrow renders
  where its content wraps, not where it is anchored). A comment after a
  position-less `in` / `=` does **not** glue — the `let … in` trailing comment
  stays own-line, a deliberate divergence (see below).
- **Bracket "renders multi-line"** — `prevBlockGlueRow` needs to know whether a
  preceding bracket renders multi-line so a following block comment can glue onto
  its close. This is approximated from source structure (`bracketRendersMultiline`:
  an `AlwaysVertical`, a multi-row span, or a contained comment). It is the one
  spot where the classifier peeks at "will this render vertical" from rows; it is
  a sound approximation for author-preserved layouts.

## The renderer side — consuming the role

Every render site reads the role; none re-derive it. Each is a small predicate
over `commentRole (lpnBox node)`:

| Render site | Reads role via | For |
|---|---|---|
| `FlowPolicy.decide` / `commentPlacement` | `roleGlues` | generic flow (call args, let, when bodies) |
| `FlowAssembly.assembleBrokenWithComments` | `commentGlues` (+ `pending` pairing) | forced-vertical binop / broken call / pipeline-step suffix |
| `MakeRenderBox.commentBracketListBox` | `commentTrailsRole` | comment-bearing bracket lists |
| `MakeRenderBox.makeUnionBodyVerticalBox` | `commentTrailsRole` | broken union bodies |
| `MakeRenderBox.renderWhenBranchesBox` | `commentTrailsRole` (+ `pending` guard) | `when` branches |
| `CommentBox.makeCommentLineBox` | `commentTrailsRole` | top-level comment runs |
| `NodeClassify.literalCommentsRideFlatLine` | `role == RidesInline` | flat-vs-open gate for literals/unions |
| `BinopLayout.splitTrailingOwnLineComments` | `role == LeadsOwnLine` | own-line vs inline trailing binop comments |
| `MakeRenderBox.binopChainIndentedLines` | the `LeadsOwnLine` split above | forced-vertical binop chains (both renderers) |
| `MakeRenderBox.operatorPrefixedOperandBox` | `runStartsOnOperatorRow` (`role /= LeadsOwnLine`) | the comment run between a `\|>`/`<\|` and its operand |

### A comment between a function and its first argument

`assembleBrokenCall` keeps a broken call's first argument on the function's line
(`String.join " "` / args below). A comment written between the two rides that
line with them, provided it *can* share a line — a single-line `{- -}` the
classifier tagged as gluing. `spanRidingComments` peels that run; a `--` or a
multi-line `{- … -}` still stands the function up alone.

The run used to block the glue outright, justified as matching elm-format's
`ElmStructure.application` gates. It does not: elm attaches such a comment to the
argument, so its arg0 is still an argument and still glues. It also contradicted
gren's own rule — the glue is gated on `nodesShareStartRow fn arg0`, and writing a
comment between them moves neither token's row.

### An operator is a prefix, not a flow item

`makeOpAndRhsBox` (binops), `stepBodyBox` (`|>`) and `backwardStepBodyBox`
(`<|`) all render the operator with `prefixOperator`, i.e. `B.prefix`. That pads
the operand's continuation rows by the operator's own width, so whatever drops
below the operator lands in the column it *would* have occupied on the
operator's line — `+ ` → 2, `++ ` → 3, `|> ` → 3 — which is elm-format's rule
and the only one that stays put under a comment. A `Tab` inside the operand box
still snaps to an absolute multiple of 4 after that padding (`B.prefix` does not
freeze tabs), so a broken call's arguments keep their +4.

Treating the operator as the flow's first item instead gave every continuation a
flat `flowIndent` of 4: one column past the operand column for `|>`, and level
with the operator itself for a `<|` step body, where an argument stopped looking
like an argument.

The comment run between the operator and its operand is peeled out and handled by
`glueLeadingCommentRun` rather than left to the flow, because a flow's
`flowIndent` is a single nest over every continuation row — it must be +4 for a
broken call's arguments, which would also push an operand that merely dropped
below a `--`. One exception, which `runStartsOnOperatorRow` tests: a run the
author wrote on a row *below* the operator must not be hoisted onto it, or the
reparse reads it as `RidesInline` there and pulls the operand up with it.

`renderWhenBranchesBox` guards its glue on `pending` being empty so a same-row
comment *run* leading a branch stays together instead of the second comment
gluing back onto the previous branch. `assembleBrokenWithComments` carries the
same kind of `pending` state for the opposite direction: a leading single-line
block comment (any position in the stack, not just first) waits and rides the
next term's line — `{- c -} arg` — matching elm-format's broken-call layout, and
it is the only reparse fixed point once the comment sits on the term's row. A
`--`, a multi-line comment, or a comment whose next term renders multi-line
stands on its own row instead. These are the two places the role alone is not
enough and a small amount of accumulation state is.

`binopChainIndentedLines` applies that same pairing across a forced-vertical
binop chain's group boundaries: the own-line comment run peeled off a group
rides the FOLLOWING group's operator line (`{- c -} + b`), which is where
elm-format puts it. The chain has its own function rather than reusing
`assembleBrokenWithComments` because its unit is a precedence *group*, not a
flow item — but the two agree on which comments pair, both via the
`FlowItem.comment` record, and both chain renderers
(`verticalBinopChainBoxFromItems` and the legacy `verticalBinopChainBox`
fallback) route through this one function so they cannot drift.

Pairing is **all-or-nothing over a comment run** in both: a `--` or a multi-line
`{- -}` anywhere in the run keeps every comment of that run on its own row,
rather than letting the block comments behind it jump ahead onto the term. That
is elm-format's rule, verified directly — `-- first` / `{- c -}` / `+ b` leaves
both comments own-row there.

## Verticality — observe the box, don't predict it

Comment placement is one half; the other is "will this construct render
multi-line?" The same discipline applies: **decide it from the rendered box, not
a source-row predicate.**

- bracket literals — `ElmStructure.groupBox` breaks vertical when
  `not (B.allSingles itemBoxes)`.
- record updates — `contentVertical = Array.any (not << isSingleLine) fieldBoxes`.
- binop chains — `anyOperandRendersMultiline` renders each operand and checks
  `isSingleLine`.

`checkContentVertical` is **not** a shape predictor to be removed — it is the
author-vs-synthesized flag that *gates* whether a paren consults its rendered
content shape. A genuine `Src.Parens` opts in (`(x)` breaks with its content); a
formatter-synthesized wrap opts out. There are no remaining source-row shape
predicates: the old `subtreeHasVerticalBox` / `bracketOpenGate` /
`nodeSpansRows` mirror-predicates are gone (retired by rendering the box and
reusing it).

**Render once, reuse.** When you decide layout from a child's box, render the
child *once*, keep the `(node, box)` pair, and both decide and assemble from that
box. Rendering to test and again to use reintroduces an `O(2^depth)` blow-up over
the tree (the same class as the `Box.gren` `renderRowState` self-format hang).
`FlowItem` carries `{ node, box, … }` for exactly this reason.

## The enforcement gate

`tests/check-render-invariant.py` greps `src/Formatter/Render/*.gren` (comment-
and string-aware) for source-row/position accessors and fails on any it finds
outside a small allowlist of genuinely-structural, non-decision functions
(`nodeStartRow`, `nodesShareStartRow`, the signature-segment `seg*` helpers,
`isElidedArrow`'s zero-width check, one `lastRow >= 0` "has content" guard, and
the union flat-vs-vertical author-layout check). Add a row-read in a decision and
the build fails; if a use is truly structural, allowlist its function there with
a reason.

## What the parser records, and what it doesn't

Placement can only be as good as the positions the parser hands over. Of
everything that separates two pieces of an expression, exactly two things carry
a source position:

- a **binary operator** — `Binops.operator : Located String`
- a **bracket** — an expression's own `start` / `end` span the `(`/`[`/`{` and
  its closer

Everything else — `=` `:` `|` `,` `->`, the keywords `if`/`then`/`else`/
`when`/`is`/`let`/`in`, an import's `as` — is discarded. For those, `x {- c -} TOK y`
and `x TOK {- c -} y` arrive as the *same* three facts (where `x` ends, where the
comment is, where `y` starts), so the side the author chose is unrecoverable.
[Rule C2](#c2--where-the-separator-has-no-source-position-the-comment-leads-what-follows-it)
is the answer — the later side, with the exceptions listed there. It is documented
([divergence #22](elmFormatComparison.md#divergence-22), with the full list in
[formatterRules.md](formatterRules.md#when-the-formatter-cant-tell-what-you-meant)).
Do not try to recover the side from how wide the whitespace gaps are —
`fuzz-whitespace.py` exists to keep that unobservable.

Where the position **is** recorded, use it. Two guards depend on it:

- `RecordUpdate.name.start` — a record update's base name has a real position,
  which is what separates its *opener* slot (`{ {- c -} rec | a = 1 }`, placed
  exactly) from the gap after the base, where only the unrecorded `|` remains
  and C2 sends the comment to the first field. `Comments.gren` makes that call
  once — the `|`-slot comment is the only `LeadsNext` an update ever carries — so
  `role` alone means "opener" and `splitOpenerComments` needs no rows.
- `lpnBracketStart` (set by `authoredBracketList` / the record-type builders) —
  a comment written past the `{`/`[` belongs *inside* the container
  (`[ {- c -} 1, 2 ]`), even though the container's first *leaf* is the first
  item and makes the comment look like it precedes the whole thing. The
  opening position is folded into the node's cached `firstPos`/`minRow` so
  every ancestor's bounds agree.
- `boxKeepsTrailingCommentOutside` + `commentInsideTrailingBracket` — a comment
  written past the `}`/`]` belongs *outside*.

## Deliberate divergences and dead ends (don't "fix" these)

- **`let … in` trailing comment routes *below* `in`.** `in` has no source
  position, so before-`in` and after-`in` are indistinguishable; routing below
  `in` is the only stable-and-correct choice. Both alternatives oscillate.
  (`LetInTrailingComment`.)
- **A `where`-block `--` escape is unfixable.** The parser hands byte-identical
  AST + Context for both layouts, so there is nothing to distinguish.
- **Own-line comment below a top-level decl detaches to column 1.** Not attached
  as the construct's trailing comment — column 1 is the only drift-free anchor
  (elm-format does the same).

## Where to look

- Model + classifier: `src/Formatter/Logical/Comments.gren`
  (`classifyCommentKind`, `prevLineGlueRow`, `prevBlockGlueRow`).
- Role type: `src/Formatter/Logical/LogicalPrintingTree.gren` (`CommentRole`).
- Consumers: grep `commentRole` / `roleGlues` / `commentGlues` /
  `commentTrailsRole` across `src/Formatter/Render/`.
- Debug: `node ../gren-format/app --lpt File.gren` prints each comment's role.
- Rationale + bug history: `comment-arch.md`.
- Fixtures: `tests/testfiles/Formatter/` (`BinopChainMixedComments`,
  `LiteralInlineComment`, `BetweenWhenBranchesComment`, `LetInTrailingComment`,
  `MultilineBlockComments`, `AdjacentTopLevelComments`, …).
