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
| `FlowAssembly.assembleBrokenWithComments` | `commentGlues` | forced-vertical binop / broken call |
| `MakeRenderBox.commentBracketListBox` | `commentTrailsRole` | comment-bearing bracket lists |
| `MakeRenderBox.makeUnionBodyVerticalBox` | `commentTrailsRole` | broken union bodies |
| `MakeRenderBox.renderWhenBranchesBox` | `commentTrailsRole` (+ `pending` guard) | `when` branches |
| `CommentBox.makeCommentLineBox` | `commentTrailsRole` | top-level comment runs |
| `NodeClassify.literalCommentsRideFlatLine` | `role == RidesInline` | flat-vs-open gate for literals/unions |
| `BinopLayout.splitTrailingOwnLineComments` | `role == LeadsOwnLine` | own-line vs inline trailing binop comments |

`renderWhenBranchesBox` guards its glue on `pending` being empty so a same-row
comment *run* leading a branch stays together instead of the second comment
gluing back onto the previous branch — the one place the role alone is not enough
and a small amount of accumulation state is.

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
