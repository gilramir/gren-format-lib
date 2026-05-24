# The bracket-path trailing-comment idempotency problem

This documents a residual class of `gren format` idempotency failures — comments
that *trail the last token of a bracketed item* (a record field value, an array
element, a record-update field) and flip between two layouts across reformats.
It is the hard remainder after the systemic and other boundary clusters were
fixed (see `project_idempotency_burndown` in memory and the `fix(formatter): …`
commit series).

## Symptom

Formatting an already-formatted file must reproduce it byte-for-byte. For these
cases it does not: `format(x)` and `format(format(x))` differ, oscillating
between "comment glued inline after the item's last token" and "comment on its
own line at the container indent".

### Example A — array value as a record field (KitchenSink ~line 274)

A `{- C -}` inserted after the `]` of an array that is a record field value:

```
format¹:
    , annotations =
        [ { source = "processConfiguration", severity = 0, message = "ok" } ] {- C -}
    }

format²:
    , annotations = [ { source = "processConfiguration", severity = 0, message = "ok" } ]
    {- C -}
    }
```

format¹ keeps `annotations =` on its own line and glues the comment after `]`;
format² collapses `annotations = [ … ]` onto one line and drops the comment onto
its own line.

### Example C — record-update field value (KitchenSink ~line 291)

A `{- C -}` after the value of a record-update field:

```
format¹:
    { startingContainer | generatedAtMillis = startingContainer.generatedAtMillis
        + 1 {- C -}

format²:
    { startingContainer | generatedAtMillis = startingContainer.generatedAtMillis + 1
    {- C -}
```

Same shape: format¹ glues the comment inline (which makes the value wrap), and
format² puts the comment on its own line (so the value fits on one line).

## Why it oscillates: a placement ↔ line-break feedback loop

The two outputs are *both* "valid" renderings; the formatter just doesn't pick
the same one twice. The loop is:

1. The comment is glued **inline** after the item's last token (`+ 1 {- C -}`).
2. Item-value + comment now overflows the page width, so the value **wraps**.
3. On reparse, the comment sits on the *wrapped* continuation row.
4. From that row the formatter decides the value fits without the comment, so it
   **un-wraps** the value and the comment moves to **its own line**.
5. Reparse again ⇒ value short ⇒ comment glues inline ⇒ back to step 1.

Comment placement changes line-breaking, and line-breaking changes where the
comment lands — the two never reach a fixed point.

## Root cause: render-time placement vs. attachment-time placement

There are two independent subsystems that each decide "where does this comment
go", and they can disagree after a reparse:

- **Attachment (`Formatter.Comments`)** weaves each comment into the LPT as a
  child of some node, based purely on source rows/columns
  (`findOrCreateOrigRow`, `commentInsideTrailingBracket`, the descent guard).
- **Rendering (`Formatter.MakePretty`)** then decides inline-vs-own-line by
  comparing the comment's source row against the *preceding item's* row
  (`listBoxesWithBrackets` → `handleCommentNode`: `commentRow == prevRow`).

When rendering glues a comment inline, the next reparse sees it on a *different*
row (because the value wrapped), so attachment + the row comparison resolve it
differently. format¹ ≠ format².

## Why the sibling boundary fixes worked but this one resists

Two structurally-identical boundary bugs *were* fixed cleanly this session:

- **When-branches** (`WhenBranch` case in `buildFlowDocImpl`): peel a trailing
  single-line block comment off the branch and glue it inline — mirrors the
  existing `PipelineStep` peel.
- **Type-union variants** (`makeUnionBodyDoc`): compare a trailing comment
  against the variant's last *token* row via `lpnLastPos` (not its first row,
  and not its subtree max row, which a multi-line comment inside the variant
  inflates past the last token).

Those builders **own both attachment and rendering for their narrow construct**,
so making the two agree was local. The bracket path is shared by records,
arrays, record-updates, and `exposing` lists, all funnelling through
`listBoxesWithBrackets`, so a single render-time row tweak has a wide blast
radius and cannot distinguish the cases.

## Render-time fixes that were tried and reverted (do not retry)

| Attempt | Outcome |
| --- | --- |
| Global `prevRow = lastRowInSubtree` in `buildFlowDocImpl` | 266 new non-idempotent gaps |
| `nodeLastRow` for every `listBoxesWithBrackets` item | glued a trailing **line** comment on a binop operand; suite 188/1 |
| Narrow block-comment-only `prevLastRow` in `listBoxesWithBrackets` | cleared 3 KitchenSink gaps but introduced 2 new RecordFieldValue regressions (comment after a nested record `}`) |

Each regressed because it tweaked **rendering** while leaving **attachment**
unchanged, so the two still diverged on reparse — just for a different set of
inputs.

## Direction for a real fix (attachment-level)

The stable layout is format²: such a trailing comment lives **on its own line**
and the bracketed value is laid out **without it influencing the line break**.
To reach a fixed point, attachment and rendering must agree on that:

- Decide the canonical at **attachment time** (`Formatter.Comments`): a comment
  that trails the last token of a bracketed item — i.e. sits between that item's
  last token and the container's closing bracket / next item — is normalised to
  a stable slot (its own line) rather than glued to the item.
- Then rendering must honour that slot **independently of width** (a comment in
  that slot always renders on its own line), removing the feedback loop.

The detailed exploration of where in `Formatter.Comments` to intervene follows
below.

## Exploration of the fix

### Confirmed root cause: attachment diverges between the two input forms

Dumping the LPT (`gren format --lpt`) for the *inline* vs *own-line* form of the
same comment in a single-field record update shows attachment is **not stable**:

Inline `{ start | f = … + 1 {- C -} }` →

```
AllAcrossOrAllVertical            (the record update)
  AcrossThenIndent  …  + 1        (the field)
  BlockComment ' C '              ← attached INSIDE, as the update's last child
```

Own-line `{ start | f = … + 1` ⏎ `{- C -}` ⏎ `}` →

```
root children:
  OriginalRows funcDecl           (the update has NO comment child)
  OriginalRows blockComment ' C ' ← ESCAPED to a top-level comment
```

So the same comment lands **inside the update** in one form and **at the file
top level** in the other. Rendering then faithfully reproduces two different
layouts — there is no fixed point to converge to. The render-time tweaks failed
because they tried to paper over this at the wrong layer.

### Why it escapes — a missing close position (a real, located bug)

`commentInsideTrailingBracket` / `findOrCreateOrigRow` keep an own-line trailing
comment inside a container only if the container's row range reaches the closing
bracket. The container's range comes from `lpnNode`'s cached `maxRow`, which by
default stops at the last positioned child. Containers that should reach their
`}`/`]`/`)` opt in via `lpnBracketNode closeEnd …` (sets `lastBracketEnd` and
extends `maxRow` to `closeEnd.row`).

Audit of `InsertExpressions.gren`:
- Array literal (`:202`), record literal (`:216`, `:219`) — use `lpnBracketNode`. ✅
- 2+ field record **update** (`:265`) — `RecordUpdate recLoc` with `recLoc.end =
  locExpr.end` (covers `}`). ✅
- **Single-field record update (`:262`) — plain `lpnNode (AllAcrossOrAllVertical
  ListCurly) [updatedField]`, NO close position.** ❌ This is exactly Example C's
  container: an own-line comment after its one field escapes.

### Attempted fix and the two reasons it isn't enough

Changing `:262` to `lpnBracketNode (AllAcrossOrAllVertical ListCurly)
locExpr.end [updatedField]` (matching the literal/array pattern) made the
minimal inline/own-line forms agree (both attach inside, both idempotent). But:

1. **It doesn't fix Example C itself.** KitchenSink 291's field value is long
   enough to *wrap*, so the placement ↔ line-break feedback loop (above) persists
   even with consistent attachment. Attachment-consistency is necessary but not
   sufficient; the loop is a separate problem.

2. **It ripples.** Extending the update's `maxRow` to the `}` row pulls trailing
   comments that previously sat elsewhere *into* the update, which changed an
   unrelated when-branch in `KitchenComments` and inserted a spurious blank line
   between branches (suite 188/1, a new idempotency failure). `maxRow` is
   overloaded: besides comment attachment it feeds when-branch multi-line / blank
   decisions (`folderInsertWhenBranch` `isBodyMultiLine`, `VerticalSpace`).
   Moving it for one consumer perturbs the others.

Both changes were reverted; suite is back to 189/0 at 13 gaps.

### What a real fix has to do (three coordinated parts)

1. **Attachment consistency (Comments.gren / InsertExpressions).** Give *every*
   bracketed container a close position so an own-line trailing comment attaches
   inside, the same as the inline form. The single-field update is the concrete
   missing case; an audit may find more (calls/parens — Examples in KitchenSink
   198/214/374/375 are call- and paren-arg trailing comments, a different
   container again).

2. **Decouple the close-bracket extent from `maxRow`'s other consumers.** So that
   extending a container's range to cover `}` for *comment attachment* does not
   change when-branch multi-line detection or `VerticalSpace` blank-line
   insertion. Options: a dedicated "close row" field separate from `maxRow`, or
   make those consumers read the last *token* row (`lpnLastPos`) rather than the
   bracket-extended `maxRow`.

3. **Break the placement ↔ line-break feedback loop (MakePretty).** A trailing
   comment in a bracket container must render in a width-independent slot (its
   own line) so the bracketed value's line-break decision does not depend on the
   comment, and the comment's resulting row does not depend on whether the value
   wrapped. The structural signal "comment is the container's last child" is
   stable across reparse (verified: both input forms attach it as the last child
   *once part 1 is in place*), so a "last-child comment → own line" render rule
   keyed on structure (not source row) is the candidate — but it must be applied
   to `listBoxesWithBrackets` / `makeRecordUpdateDoc` consistently and may change
   baselines for `{ a = 1 {- c -} }`-style single-item containers (regenerate and
   confirm idempotent).

Only with all three landing together does each bracket case reach a fixed point;
each in isolation regressed (see table above). This is a larger, cross-cutting
change than the per-construct fixes (when-branch, union) that succeeded, and
should be done behind heavy fuzzing of every record/array/update/call/paren
fixture plus the full `fuzz-idempotency.py` sweep.

### Update — implementation attempt and a fourth entanglement

Implementing part 1 (give the single-field update `:262` its close position)
exposed a problem the plan above understates: **correct comment attachment is
itself what changes the blank-line decision**, so they cannot be fixed
independently.

Concretely, in `KitchenComments` a single-field update is a `when` branch body
with two block comments after its `}`:

```
0 -> … { r | label = … "zero"
    } {-c88-} {-c89-}
_ -> … r
```

- **Without** the close position, the update's range stops at `"zero"`, so
  `{-c88-} {-c89-}` fall *outside* the branch and attach as comment items
  *between* the two branches. A comment between branches leaves the next branch's
  separator `AlreadyTerminated`, which **suppresses the inter-branch blank**.
- **With** the close position, the branch's range now reaches the `}` row, so
  `{-c88-} {-c89-}` (genuinely on the branch's last row) attach *inside* the
  branch. The branches are now adjacent, the separator is `FlowSep`, and the
  multi-line `0 ->` branch inserts a **spurious blank** before `_ ->` (suite
  188/1).

This is not the `maxRow`-overloading of part 2 (the blank here is driven by
`WhenBranch`'s `prevBranchMultiLine`/`AlreadyTerminated` separator logic, not by
a row range). It is a *fourth* coupling: the inter-branch blank rule keys off
"is there a comment item between the branches", and moving the comment to its
correct home (inside the branch) removes that signal. A real fix therefore also
needs the inter-branch blank rule to be **insensitive to where a trailing
comment attaches** — e.g. suppress the blank when the previous branch's rendered
output already ends with a comment line, regardless of whether that comment is a
sibling between branches or a trailing child of the branch.

Worse, the branch-blank fix this implies **conflicts with an existing fix**. The
LPT after part 1 shows `{-c88-} {-c89-}` as direct children of the `0 ->`
`WhenBranch`, after the branch's `AcrossThenIndent`:

```
WhenBranch
  AcrossThenIndent   0  ->  …  { r | label = … "zero" }
  BlockComment c88
  BlockComment c89
```

To suppress the inter-branch blank, the branch must treat trailing comments as
*terminating* it (own-line, set the next separator to `AlreadyTerminated`). But
the committed `WhenBranch` peel (`fix(formatter): glue trailing block comment
inside when-branch`, for CtorAsPattern) does the **opposite** — it glues a
trailing block comment *inline* so it can't escape. The same construct now wants
inline-glue in one case and own-line-terminate in another, distinguished only by
whether the branch body is multi-line and whether a following branch exists.
Reconciling these is its own sub-design.

**Net assessment:** the bracket-path fix is a coordinated change across at least
five coupled mechanisms — comment attachment extent (LogicalPrintingTree /
Comments), the placement↔break feedback loop (MakePretty bracket renderers), the
overloaded `maxRow` (multi-line/blank consumers), the when-branch blank separator
rule, and the existing when-branch trailing-comment peel. Every single-piece
attempt has regressed the suite or the fuzzer. It must be designed and landed as
one refactor with the full fixture fuzz sweep as the gate, not incrementally.
Until then the 13 residual gaps are left as documented known limitations; the
formatter stays at suite 189/0.
