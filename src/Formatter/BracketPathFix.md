# Bracket-path trailing-comment fix — concrete refactor design

Companion to `BracketPath.md` (which states the problem, the root cause, and why
every incremental attempt regressed). This is the implementation design for the
single coordinated refactor. Read `BracketPath.md` first.

## 0. Invariant we are buying

For any input `x` that parses, `format(format(x)) == format(x)` byte-for-byte,
for comments that trail the last token of a bracketed item (record field value,
array element, record-update field) — without regressing the 189-test suite or
re-introducing fuzzer gaps elsewhere.

The fixed point is reached only if, for such a comment, **(i) attachment is the
same tree whether the source wrote it inline or on its own line, and (ii)
rendering is a function of that tree alone, not of the page-width outcome of the
value it trails.** Today both (i) and (ii) fail; the five sub-parts below
establish them.

## 1. The canonical rule (the load-bearing decision)

Observed today (all idempotent, keep them):

```
{ a = 1 {- c -}        -- comment BETWEEN items: glued inline to the item it
, b = 2 {- d -}        --   follows, in the forced-vertical layout. STABLE.
}
```

Broken today (Examples A/C in `BracketPath.md`): a comment trailing an item
whose **value is itself a breakable container** (array / record / record-update
/ call / paren) rides that value's line-break and oscillates.

**Rule.** A trailing comment renders **inline** (glued with one space, current
behaviour) **iff the token it follows is atomic** — a leaf that can never become
multi-line (var, literal, operator, `}`/`]`/`)` of a *single-line* container).
If the item it trails **ends in a breakable container that is (or may become)
multi-line**, the comment renders on **its own line**, at the item's indent,
*after* the item — and the item is laid out **as if the comment were absent**.

Why this rule and not "always own-line" or "always inline":
- *Always inline* cannot be width-independent — a glued comment shares the
  value's line, so it always feeds back into that value's break decision.
- *Always own-line* needlessly churns the thousands of idempotent
  `, b = 2 {- d -}` cases.
- The rule keys on a **structural** property (does the trailed item end in a
  breakable container?), not on width, so it is width-independent yet narrow.

Decision the implementer must ratify (it sets baseline churn): treat a
*single-line* breakable container (`notes = [ x ] {- c -}`, where `[ x ]` fits)
as "breakable" (→ own-line, more churn, simplest rule) **or** as "atomic for
this purpose" (→ inline while it fits; needs a width probe and risks the
boundary oscillation). **Recommend: breakable ⇒ own-line, unconditionally.** It
is the only choice that is provably width-independent. Accept the baseline churn
and regenerate.

## 2. Part A — attachment consistency (close positions everywhere)

**Problem.** `--lpt` proves the inline form attaches the comment *inside* the
container while the own-line form lets it *escape* (to a sibling / top-level
comment), because the container's row range stops at its last item, not its
closing bracket. Concrete gap: `InsertExpressions.gren:262`, the **single-field
record update**, builds with plain `lpnNode` (no close position) unlike the
array (`:202`), record literal (`:216`/`:219`), and 2+-field update (`:265`).

**Change.**
- Audit every container constructor in `InsertExpressions.gren`,
  `InsertPatterns.gren`, `InsertTypes.gren`, `MakeLogical.gren` and ensure each
  passes its real closing-bracket position to `lpnBracketNode` (or carries an
  equivalent close for `RecordUpdate`/where-block/exposing). Known missing:
  `:262`. Suspected missing: call/paren argument groups (KitchenSink 198/214/
  374/375 are call-/paren-arg trailing comments).
- The module `exposing` `)` and effect `where {}` `}` have no AST position;
  they keep their existing synthesized handling (`MakeLogical` bumps) — those
  are already idempotent and out of scope here.

**Result of Part A alone:** attachment converges (both input forms → comment is
the container's last child). But it ripples (Parts B and D) and does not yet fix
the feedback loop (Part C). Hence the coordinated landing.

## 3. Part B — separate "comment-acceptance extent" from layout `maxRow`

**Problem.** `lpnBracketNode` extends the node's cached `maxRow` to the close
row. `maxRow` is read by *two unrelated* concerns: comment attachment
(`makeOrigRows` `last`, the `Comments` descent via `subtreeContainsRow`) **and**
layout (`VerticalSpace` blank lines; indirectly the row a sibling sees). Moving
it for attachment perturbs layout.

**Change.** Add a dedicated field to the `LPNode` bounds
(`LogicalPrintingTree.gren`, the record under `type LPNode`):

```
, commentMaxRow : Int   -- furthest row that may still belong to this subtree
                        -- for COMMENT ATTACHMENT (covers the closing bracket);
                        -- layout uses maxRow (last real token) unchanged.
```

- `lpnNode`: `commentMaxRow = max maxRow (max over children .commentMaxRow)`;
  `maxRow` stays "last positioned token" (do **not** let `lpnBracketNode` push
  `maxRow`).
- `lpnBracketNode closeEnd`: set `commentMaxRow = max commentMaxRow closeEnd.row`
  and `lastBracketEnd = Just closeEnd`; **stop** extending `maxRow`.
- `lpnReplaceChildren`: preserve both, recomputing from children.
- Attachment readers switch to `commentMaxRow`: `subtreeContainsRow`,
  `subtreeEndsBefore`, and `makeOrigRows`'s `last` (so a declaration's
  `OriginalRows` covers an own-line comment before its closing bracket).
- Layout readers keep reading `maxRow`/`lastRowInSubtree`: `VerticalSpace`,
  any multi-line heuristics.

This removes the cross-talk: extending a container to accept an own-line
trailing comment no longer changes blank-line or sibling-row decisions.

Note: the existing `MakeLogical` module-line / where-block bumps deliberately
extend the *layout* `last` — leave those as explicit `last` overrides; they are
a separate, working mechanism.

**Caveat — confirm Part B is actually needed.** The ripples *observed* in the
failed attempts were Parts C (feedback loop) and D (inter-branch blank), not a
demonstrated layout-`maxRow` leak. `subtreeRowRange`'s only readers are
`subtreeContainsRow`/`subtreeEndsBefore` (attachment) and `lastRowInSubtree`
(→ `makeOrigRows` `:101` and signature `:650`). `makeOrigRows`'s `last` feeds
**both** attachment (`findOrCreateOrigRow`) and layout (`VerticalSpace`) through
one box field — but extending a top-level declaration's `last` to its real
closing bracket is *correct* for `VerticalSpace` too (the decl genuinely ends
there), so that sharing is benign. So Part B may reduce to "do nothing" once the
reader audit confirms no purely-layout consumer is hurt; keep the
`commentMaxRow` split in reserve and introduce it only if the audit finds a
layout reader that the close extension perturbs. Doing the audit **first** is the
non-optional part.

## 4. Part C — width-independent render of the trailing comment

**Problem.** Even with consistent attachment, the renderers
(`listBoxesWithBrackets`, `makeRecordUpdateDoc`, `makeListItemDoc`) glue a
same-row trailing comment with a hard space, so it joins the value's line and
feeds its break decision.

**Change.** Implement the Part-1 canonical rule in the bracket renderers:
- When emitting an item followed by a trailing comment, decide inline-vs-own-line
  by the **structural** predicate "does this item end in a breakable container?"
  (a small helper over the item's last leaf's box: breakable = `AllAcrossOrAllVertical`
  / `AlwaysVertical` / `RecordUpdate` / `ParenBlock` / call group; atomic =
  everything else). Do **not** use the source-row comparison
  (`commentRow == prevRow`) for this decision — that comparison is exactly what
  diverges on reparse.
- Inline branch: unchanged (`P.concat itemDoc (P.text " " <> commentDoc)`).
- Own-line branch: render the item's doc, then `P.hardNl`, then the comment at
  the list's item indent, then continue to the close. Because the comment is
  past a `hardNl`, the item's group fit-check no longer includes it — the value
  lays out as if the comment were absent (breaks the feedback loop).
- Apply the identical predicate in `makeUnionBodyDoc` (replace the `lpnLastPos`
  row comparison landed earlier — keep it as the *fallback* for the
  already-fixed multi-line-variant case, but the breakable predicate subsumes
  it) and in the `WhenBranch` path (Part D).

Single source of truth: factor the predicate + the inline/own-line emission into
one helper (e.g. `emitItemWithTrailingComment : LPNode -> Maybe LPNode -> ...`)
and call it from every container renderer, so the rule cannot drift between
records, arrays, updates, unions, and branches.

## 5. Part D — unify the when-branch trailing-comment rule

**Problem.** The committed `WhenBranch` peel (`fix(formatter): glue trailing
block comment inside when-branch`) glues a trailing comment **inline** to keep
it from escaping; but the bracket fix needs a multi-line branch body's trailing
comment **own-line**, and a comment that used to sit *between* branches
(suppressing the inter-branch blank via `AlreadyTerminated`) now lands *inside*
the branch and a spurious blank appears. The same construct currently wants
opposite things.

**Change.** Replace the peel with the Part-C helper, so a branch-trailing
comment follows the same breakable-vs-atomic rule (CtorAsPattern's body ends in
a call → atomic-ish → inline, which is what the peel achieved; a body ending in
a breakable container → own-line). Then make the inter-branch blank rule
**insensitive to where the trailing comment attached**:
- In the `WhenBranch` case of `buildFlowDocImpl` (`MakePretty.gren:656`), when
  the branch's rendered doc ends with a comment line (its last child subtree
  ends in a comment), set the result `separator = AlreadyTerminated` (today it
  is always `FlowSep`). The next branch's existing `AlreadyTerminated -> P.concat
  acc.doc childrenDoc` arm then attaches it directly with no inserted blank —
  matching the pre-existing "comment between branches" behaviour, regardless of
  whether the comment is now a child of the branch.
- Add a small `subtreeEndsWithComment : LPNode -> Bool` (mirror of the existing
  `nodeIsComment` / row helpers) to drive that.

This makes "comment between branches" and "comment trailing the branch" produce
the same inter-branch spacing — the divergence that caused suite 188/1.

## 6. Order of work and the test gate

Land as one branch, built in this order, but **commit only after the whole set
is green** (intermediate states regress, as documented):

1. Part B (data-model split) — pure refactor, no behaviour change; verify suite
   189/0 unchanged and full `fuzz-idempotency.py` count unchanged (still 13).
2. Part A (close positions) — now safe because Part B prevents the layout ripple;
   expect attachment to converge.
3. Part C (render helper + breakable predicate) — breaks the feedback loop.
4. Part D (when-branch unification) — removes the inter-branch blank divergence.

Gate after the full set:
- `effectful-tests/run-tests.sh` → 189/0 (regenerate the *.formatted.gren
  baselines that change due to the own-line canonical; manually inspect each
  diff to confirm it is the intended own-line layout, then re-run to confirm the
  regenerated baselines are self-idempotent).
- `python3 effectful-tests/fuzz-idempotency.py` over **all** fixtures → 0
  non-idempotent gaps (down from 13). Pay special attention to `RecordFieldValue`,
  `RecordUpdateComplexBase`, `ExtensibleRecords`, `NestedContainersInList`,
  `KitchenSink`, `KitchenComments`, `WhenExpression`, `WhenPatterns`,
  `MultilineBlockComments` — the fixtures that regressed in prior single-piece
  attempts.

## 7. Risks and fallback

- **Baseline churn (expected).** The breakable⇒own-line rule will move some
  currently-inline trailing comments onto their own line (`notes = [ x ] {- c -}`
  → two lines). This is intended and must be reviewed as a deliberate style
  change, not a bug. If the churn is judged unacceptable, the only
  width-independent alternative is to additionally force every breakable
  container that carries a trailing comment to vertical layout and glue the
  comment to its closing-bracket line — more invasive, not recommended.
- **Hidden coupling.** `maxRow` may have readers beyond `VerticalSpace`
  (grep `lpnMaxRow`, `lastRowInSubtree`, `subtreeRowRange`); each must be
  classified attachment-vs-layout before Part B flips it. Misclassifying one is
  the most likely source of a new regression.
- **Fallback.** If the full set cannot reach fuzzer-0 without regressions, the
  fixes are independent enough to ship **Parts B + D** alone (a pure
  decoupling + a blank-rule robustness improvement that should be net-neutral or
  positive) and leave Parts A + C — i.e. ship the plumbing that makes the future
  attempt safe, even if the user-visible canonical change is deferred.

## 8. Why this is worth doing as a unit

The per-construct fixes that took the session from 83→13 gaps each owned both
attachment and rendering for one narrow construct (module line, exposing list,
union variant, when-branch). The bracket path is the shared spine
(`listBoxesWithBrackets`) under records, arrays, updates, exposing, and call
args; the only durable fix is to make attachment and rendering agree *there*,
once, behind the single canonical rule above — which is exactly what Parts A–D
do.
