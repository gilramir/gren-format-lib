# Trailing-comment drift below a declaration

Status (2026-07-16, re-confirmed 2026-07-17): **narrow bracket case fixed;
general fix is an open follow-up.** This note captures the problem, worked
examples, the design decision (**detach to column 1**), the *mechanism* of the
subtle drift/attach boundary (§4.1), the cases that must NOT change, and why the
first general attempt was reverted. Pick this up next session.

The `--decl-ends` fuzzer still reports **80 findings across 36 fixtures** on the
2026-07-17 build — unchanged since the class was first measured.

---

## 1. The bug in one sentence

An **own-line, indented** comment written **below a top-level declaration** is
non-idempotent for many body shapes: it drifts one column to the left on every
reformat until it reaches column 1.

```gren
-- you write this:
listVal =
    [ 1
    , 2
    ]
        -- trailing below a list

-- format once  -> comment at column 5
-- format again -> comment at column 1   (it moved!)  ← NOT idempotent
```

This is a genuine formatter bug: `format(format(x)) /= format(x)`. It is only
triggered by a comment on its **own line, indented** below the declaration. A
comment on the **same line** as the last token, or at **column 1**, is already
stable.

---

## 2. Root cause

Two pieces of `src/Formatter/Logical/Comments.gren` disagree about which column
a trailing comment belongs at:

- **`columnClaim`** (line ~333) decides a comment is a construct's trailing
  comment when its column is `>= lastLeafRowMinCol(node).col` — the column of
  the construct's **deepest positioned leaf**.
- **`appendTrailingComment`** (line ~1088) then renders it own-line at the
  **append-target node's content indent** — where the descent (`shouldDescendInto`)
  actually stops.

When those two columns differ, the rendered comment lands at a column that no
longer re-triggers the same claim on reparse, so it re-attaches one level
shallower — and repeats, drifting left until column 1.

The design *intends* these to coincide (a reparse fixed point); they coincide
for some shapes and not others.

**Concrete numbers for the list example above:** the closing `]` is not a
positioned leaf, so `lastLeafRowMinCol` returns the deepest list *item* (`2` at
col 7) as the claim threshold. But `appendTrailingComment` will not descend into
a bracket container, so it renders the comment at the list's *outer* block indent
(col 5). 5 ≠ 7 → drift.

---

## 3. What is already fixed

**Commit `22daf23` — the bracket-container case only.** `columnClaim` now refuses
the claim (via `trailingAppendHitsBracket`, line ~1059) when the append would land
immediately after a bracket container (`[…]`, `{…}`, record update, paren-block,
…). Those comments **detach to a top-level column-1 comment in a single pass**,
matching elm-format. Fixture: `TrailingCommentAfterContainer` (list / record /
update). All gates green.

This fixed the reported bug but **only** for bracket-terminated bodies.

---

## 4. The broad class (still open)

The drift is not bracket-specific. A new pass in the idempotency fuzzer
(`tests/fuzz-idempotency.py --decl-ends`, commit `46c4c70`) injects an own-line
trailing comment — **both** the block (`{- ¤ -}`) and line (`-- ¤`) form — one
indent level below every top-level declaration, and requires each to be
idempotent. Against the current build it reports **80 findings across 36
fixtures**.

Reproduce:

```bash
cd gren-format && ./build.sh                      # rebuild the CLI the fuzzer shells out to
cd ../gren-format-lib/tests
python3 fuzz-idempotency.py --decl-ends -j 12     # 80 findings today
python3 fuzz-idempotency.py --gaps      -j 12     # per-gap pass: 0 (clean)
```

### Which body shapes drift vs. stay stable (single indented `--` comment below)

| body                                  | result  | note |
|---------------------------------------|---------|------|
| `1`                                   | stable  | leaf; attaches at col 5 |
| `foo bar baz` (single-line call)      | stable  | |
| `1 + 2` (2-operand binop)             | stable  | one `OpAndRhs` level |
| **`1 + 2 + 3` (3-operand binop)**     | **DRIFT** | nested `OpAndRhs` gives the descent two levels to leapfrog between |
| `-x`, `x.field`, `Just 5`             | stable  | |
| `foo <| bar`, `foo |> bar`            | stable  | (single-line) |
| `{ x = 1 }`, `{ x | y = 1 }` (inline) | stable  | |
| `[ 1, 2 ]` (inline list)              | stable  | fixed by `22daf23` |
| `(1 + 2)` (paren group)               | stable  | detaches to col 1 in one pass — a `ParenBlock` is a bracket container, so `22daf23` already covers it. (`( 1, 2 )` is not valid Gren — no tuples.) |
| **`"""x"""` (multiline string body)** | **DRIFT** | |
| `alpha ++ beta ++ gamma` (multi-line chain) | stable | **must stay — documented, see §7** |
| `x |> f |> g` (multi-line pipeline)   | stable  | |
| **`foo`/`bar`/`baz` (multi-line call)** | **DRIFT** | descent enters the call; render indent shallower than deepest leaf. Marches to col 1. |
| **`Int -> Int -> Bool` (multi-line function type)** | **DRIFT** | signature / type-alias body broken across `->` rows (`TypeSignature`, `TypeAliasFunctionType`). Marches to col 1. |

The boundary is subtle (2-operand binop stable, 3-operand drifts), which is why
hand-enumerating box types is the wrong approach — the fuzzer is the oracle.
§4.1 explains *why* the boundary sits exactly there.

### 4.1 Why the boundary sits where it does (the mechanism)

The drift/attach split is not about the top-level construct *kind* — two nodes
of the same kind land on opposite sides. §2 names the two columns that must
coincide; this section shows how they move relative to each other.

**Two columns, one of them input-dependent.**

- **Claim threshold** = `lastLeafRowMinCol(node)`: the *smallest* column among the
  positioned leaves on the construct's **last source row**. `columnClaim` keeps
  the comment attached only while `commentCol >= threshold`; below it the comment
  falls through to a fresh column-1 row (detaches).
- **Render column** = the level where `appendTrailingComment`'s descent stops. The
  descent (`shouldDescendInto`) walks the rightmost positioned spine, entering the
  last positioned child **while `commentCol >= that child's last-line indent`**.
  So the render column is **a function of the comment's own column**, not a
  constant.

Idempotency needs `render(commentCol) == commentCol` — a genuine fixed point.
That holds when the spine offers **at most one** descendable level (the descent
can only stop in one place, so `render` is constant and equals it). It fails when
the spine offers **two or more** levels: `render` then maps a shallow input
column to a deep one and vice-versa, and there is no fixed point.

The 2-vs-3-operand binop is the sharpest illustration; the LPT shows why the
operand count changes the level count:

```
1 + 2        Binop [ "1", OpAndRhs [ "+", "2" ] ]                        -- ONE descendable level
1 + 2 + 3    Binop [ "1", OpAndRhs [ "+", "2", OpAndRhs [ "+", "3" ] ] ] -- TWO nested levels
```

`1 + 2` has a single `OpAndRhs`, so the comment renders at one fixed column
(col 7) regardless of input — stable. `1 + 2 + 3` nests a second `OpAndRhs`, so
the descent has two stop points and leapfrogs between them. Same construct kind,
opposite side of the boundary, decided purely by nesting depth — which is why the
boundary is *empirical*: it tracks the shape of the rightmost spine, not the name
of the construct.

#### Two drift trajectories (both are the same bug)

A drifting shape does not always march to column 1. Which of the two behaviours
you get is decided by the **depth of the claim threshold**, and this is why the
fuzzer count is the honest oracle — a single `format∘format` check from the wrong
starting column misses half of them:

- **Period-2 oscillation** — when the threshold is *shallow*. A single-row body
  (`1 + 2 + 3`) has all its leaves on one row, so `lastLeafRowMinCol` is the
  body's own left column (col 5). A shallow re-render (col 5) still satisfies
  `commentCol >= threshold`, so it stays claimed and re-renders deep again:
  `format(col 5) = col 9`, `format(col 9) = col 5` — it bounces forever and never
  settles. `--show` flags it from *any* starting column. (A comment placed
  *shallower* than col 5 — e.g. col 3 — is below the threshold, so it detaches to
  col 1 and is stable; verified.)
- **Monotone march to col 1** — when the threshold is *deep*. A multi-row body
  (multi-line call, multi-line function-type signature, bracket containers
  pre-`22daf23`) has its last-row leaves at a deep column (col 9), so the
  threshold is deep. The first render lands shallow (col 5 < col 9); on the next
  reparse the comment now fails `commentCol >= threshold`, so it detaches:
  `col 9 → col 5 → col 1 → col 1` (verified). It reaches col 1 and stops there.
  `--show` only flags it while it is still moving, so a check starting from the
  already-col-5 state sees `col 5 → col 1` once and then quiesces.

The design fix (§5) collapses both trajectories to the same one-pass detach to
column 1 — the monotone shapes reach their existing endpoint immediately instead
of over several passes, and the oscillating shapes finally get an endpoint at
all.

---

## 5. Design decision — **DETACH to column 1**

For the un-attachable cases, the user chose (2026-07-16): the comment should
**detach to a top-level column-1 comment** — which is also exactly what
elm-format emits. NOT attach-at-body-indent.

```gren
-- DECISION: this
x =
    [ 1
    , 2
    ]
-- comment          ← detached to column 1

-- NOT this
x =
    [ 1
    , 2
    ]
    -- comment      ← attached at body indent (rejected)
```

So the general fix should make every **drifting** shape detach to column 1,
while leaving the **stable-attach** shapes untouched (see §7).

---

## 6. Worked examples (gren-format vs elm-format)

These are the "comment at the end of something" family (README §"A comment at the
*end* of something" and divergence-catalogue #12/#13).

### CASE A — comment on the SAME line as `]` (catalogue #12, working as designed)

```gren
x =
    [ 1
    , 2
    ] {- same line -}
```
gren-format keeps it glued there. elm-format floats it to column 1. Idempotent,
deliberate divergence. **No change wanted.**

### CASE B — own-line, indented, below `]` (the bug; fixed for brackets)

```gren
x =
    [ 1
    , 2
    ]
        {- own line, indented, below ] -}
```
Used to drift col 8→4→0. Now (commit `22daf23`) detaches to col 1 in one pass —
matches elm-format.

### CASE C — indented to a MULTI-LINE binop chain (STABLE, must keep)

```gren
total =
    alpha
        ++ beta
        ++ gamma
        {- indented to the chain -}
```
gren-format **attaches** at the chain indent (col 9), stable/idempotent — a
deliberate divergence from elm-format (which floats to col 1). **Must keep.**

### Single-line 3-operand binop (DRIFT — oscillates, should detach)

```gren
binopBody =
    1 + 2 + 3
        -- comment
```
`format(col 5) = col 9` and `format(col 9) = col 5` — a period-2 oscillation
that never settles (see §4.1). A 2-operand `1 + 2` is stable at col 7; the
extra operand nests a second `OpAndRhs` and tips it over the boundary. Should
detach to col 1.

### Multi-line call (DRIFT — marches to col 1, should detach)

```gren
callBody =
    foo
        bar
        baz
        -- comment
```
The descent enters the call but stops shallower than the deepest argument, so
the comment marches `col 9 → col 5 → col 1` over successive formats, one level
per pass. Should detach to col 1 in a single pass. elm-format floats it to
col 1.

### Multi-line function-type signature (DRIFT — marches to col 1, should detach)

```gren
type alias F =
    Int
    -> Int
    -> Bool
        -- comment
```
`format¹ → col 5, format² → col 1`. The same shape appears on a real function
signature (`f : Int -> Int -> Bool` broken across `->` rows). Fixtures
`TypeSignature` and `TypeAliasFunctionType` carry this today. Should detach to
col 1.

---

## 7. Cases that MUST keep attaching (do NOT blanket-detach)

A naive "detach everything" is wrong — several trailing-below comments attach
**by design**, are documented, and are fixture-tested:

- **Union variants** — `UnionTrailingOwnLineComment` fixture: an own-line comment
  below the last variant attaches at the variant (`|`) indent. Must stay attached.
- **Multi-line binop chain / pipeline** — CASE C above; README "A comment at the
  *end* of something".
- **Divergence catalogue #12 and #13** (README) — "a comment written after code
  stays by the code"; gren-format deliberately keeps trailing comments next to
  their code where elm-format floats them away.

The general fix must detach **only the shapes that would otherwise drift**, i.e.
where render-column ≠ claim-threshold.

---

## 8. The general fix attempt that was REVERTED (learn from it)

Attempted: replace `trailingAppendHitsBracket` with a general
`trailingClaimIsStable col node` = "claim only if the comment's render column
equals the claim threshold, else detach." Render column was approximated as
`firstPositionedChild(appendTargetNode(col, node)).col`.

**Why it failed:** `firstPositionedChild.col` is NOT the render column for
list-like structures. A union's variant list renders the comment at the `|`
indent, but its first positioned child is the first *variant* at a deeper column
— so the approximation said "mismatch → detach" and **regressed
`UnionTrailingOwnLineComment`** (plus one other), and *raised* total drift from
80 to 111. Reverted to the committed narrow bracket fix.

**Lesson:** the render column is the append-target's **content indent**, which is
node-type-specific (a block's indent, an operator-chain's continuation column, a
variant list's `|` column, …). Approximating it from child positions is not
reliable. Computing it correctly ≈ reimplementing part of the renderer's indent
logic inside `Comments.gren`.

---

## 9. Where the code is

`src/Formatter/Logical/Comments.gren`:

- `columnClaim` (line ~333) — the top-level trailing-comment claim; claim
  condition at line ~410. Add the "is this a stable attach?" gate here.
- `appendTrailingComment` (line ~1088) — renders a claimed comment by walking the
  spine and stopping via `shouldDescendInto`.
- `shouldDescendInto` (line ~1027), `lastPositionedChild`, `isIndentingFlowBox` —
  hoisted spine-walk helpers (shared, so a stability check can reuse them).
- `lastLeafRowMinCol` (line ~882) — the claim threshold (deepest positioned leaf).
- `isBracketContainerBox` (line ~954), `trailingAppendHitsBracket` (line ~1059) —
  the current narrow (bracket-only) detach.

Design docs: the module header comment in `Comments.gren` ("The trailing-comment
boundary rule (idempotency)"); README §"A comment at the *end* of something" and
divergence-catalogue entries #12/#13.

---

## 10. Ideas for the general fix (for tomorrow)

Options, roughly in increasing effort / decreasing risk of regression:

1. **Compute the true render column.** Give each append-target node type a
   correct "content indent" (block indent, chain continuation column, variant
   `|` column, …) and detach when it ≠ `lastLeafRowMinCol`. Most correct;
   requires modeling renderer indents in `Comments.gren`.
2. **Attach only into MULTI-ROW continuations.** Keep the claim only when the
   append target genuinely spans multiple rows AND the comment column matches its
   continuation indent; detach single-row / inline nested bodies. Needs care:
   multi-line *call* drifts even though multi-row, so "multi-row" alone is not
   sufficient.
3. **Round-trip check.** After building the attached tree, verify the comment's
   rendered column would re-claim identically; if not, detach. Hard to do without
   rendering inside the logical stage.
4. **Move column *ownership* to the render stage (preferred).** The root cause is
   a stage-ordering mistake: `Comments.gren` commits an *absolute column* for the
   comment by *predicting* the renderer, before the renderer has run — so drift is
   just "prediction ≠ reality" (which is why options 1–3 keep re-deriving renderer
   indents inside the logical stage). Instead:
     1. In the logical stage decide **only attach-vs-detach**, from stable,
        layout-independent facts — never compute a column.
     2. When attaching, store a **structural target** ("trailing child of node
        N"), not a number.
     3. Let the **renderer** place the comment at N's own continuation indent,
        whatever that turns out to be.
   Because the renderer owns every other indent in the file, a comment the
   renderer places cannot disagree with the renderer — **both** drift trajectories
   (oscillation *and* monotone march, §4.1) die by construction rather than by
   better guessing. This is option 3 done at the right layer.

   For the attach-vs-detach call in step 1, the useful axis is **"does the body
   render flat or vertical"** (a flat construct has no interior column to attach
   to, so col 1 is its only fixed point — which alone removes the entire
   *oscillation* subclass, since every oscillator is a flat body). **Caveat — do
   NOT read the raw `forceVertical` field:** `LogicalPrintingTree.gren:298-300`
   documents that it is *not* reparse-stable (a crammed-onto-one-row construct
   reads `False` on pass 1 and `True` after reflow), so keying off it would trade
   the column oscillation for a *verticality* oscillation. Use *effective*
   verticality — `subtreeHasVerticalBox` in `Render/NodeClassify.gren`, the
   fixed-point "does it actually render with a hard newline" predicate that
   `audit-predicates.py` already gates against the renderer. (It lives in the
   Render stage; `Comments.gren` runs earlier, so it needs hoisting to a shared
   module, not a backward import.) Verticality does **not** separate the
   *vertical* drifters (multi-line call, multi-line function type) from the
   deliberate *vertical* attachers (chain / pipeline / union, §7) — that split
   still needs an explicit construct-kind allow-list, or falls out for free once
   the renderer owns placement.

   Before adopting flat→detach, confirm no fixture froze the accidental stable:
   a flat `1 + 2` currently attaches at col 7 (not in the §7 must-keep list), and
   this moves it to col 1 — an improvement toward elm-format.

   **Corpus grep done (2026-07-17): no fixture freezes it.** Scanning every
   `.formatted.gren` for a flat binop that is the last line of its declaration
   with an indented own-line comment trailing it, the decisive fact is that **no
   trailing comment in the corpus sits *deeper* than a flat binop body** — the
   accidental col-7 attach (comment at the operand column, deeper than the block)
   appears *only* in the synthetic `--decl-ends` fuzzer inputs, never as frozen
   expected output. Every real match has the comment *shallower* than the binop:
   14 pairs, all **let-binding bodies** (binop at the let-body indent, comment at
   the let-*binding* indent where the next binding also sits) —
   `LetBindingComments`, `LetBindingSignature`, `LetBlankLines`,
   `LetCommentedSignatureVariants`, `BetweenLetBindingsComment`, and the
   `let`/`when`-interior ones in `KitchenComments` / `KitchenSink`.

   **Scoping constraint that grep surfaced:** those 14 let comments are
   legitimately attached at the binding indent via the **let-flow comment path**
   (`VerticalSpace.insertBlanksInLetFlow`), *not* the top-level `columnClaim`
   trailing claim. So flat→detach must be scoped to `columnClaim`'s top-level
   trailing path **and must not touch the let-flow path** — otherwise those 14
   would wrongly march to col 1. This is sharper than "detach flat bodies": the
   rule is "detach flat bodies *claimed as a top-level construct's trailing
   comment*", and the let flow is a different mechanism entirely.

Gate every attempt with **all** of:
`run-tests.sh` (esp. `UnionTrailingOwnLineComment`, `TrailingCommentAfterContainer`),
`fuzz-idempotency.py --decl-ends` (target: 0), `fuzz-idempotency.py --gaps`
(stay 0), `matrix-syntax.py`, and spot-check against `elm-format`.

---

## 11. Commits so far (branch `elm2`)

- `e0e83a5` — coverage fixtures + unreachable-code comments (unrelated).
- `22daf23` — **narrow bracket drift fix** + `TrailingCommentAfterContainer`.
- `46c4c70` — **fuzzer `--decl-ends` pass** (the tool that found the broad class).

The general detach fix for the remaining ~80 findings is **not yet written**
(one attempt reverted).

---

## 12. Implementation plan for option 4 (SUPERSEDED by §13)

**Superseded 2026-07-17 by §13 (Option C).** Kept for context. The elm-format
check in §13 showed elm-format detaches *every* trailing-below comment — including
union variants and binop chains — so the attach/allow-list machinery this plan
builds (hoisting `subtreeHasVerticalBox`, the `forceVertical` binop signal,
`isMustKeepAttachTrailing`) is unnecessary. §13 detaches everything instead.

Grounded in the code (2026-07-17): `columnClaim` returning `Nothing` is already
the detach lever — it falls through to the fresh col-1 `OriginalRows` path in
`findOrCreateOrigRow`. So the whole fix is about **when to refuse the claim**.
`subtreeHasVerticalBox` is LPT-only (no `Box`/`FlowPolicy` deps) and
`Comments.gren` imports nothing from Render, so the stable-verticality predicate
is hoistable with no import cycle. The 14 let-flow comments route through the
**`IndentedBlock`-redirection** path (Comments.gren ~588–646), *not* `columnClaim`
— so scoping the change to `columnClaim`/`appendTrailingComment` leaves them
untouched.

**Approach:** decide attach-vs-detach in the logical stage from reparse-stable
facts; detach = `columnClaim → Nothing`; attach only the §7 must-keep set via the
existing (already stable) `appendTrailingComment`. Stage as **4a** (change the
*decision* only — low risk, captures the bulk of the 80) then **4b** (make the
attach path column-free) *only* where the fuzzer still flags an attached shape.

### Phase 0 — Spec the boundary empirically (read-only)
- Detach set = the 80 `--decl-ends` findings.
- Keep set = grep **all** fixtures for *currently-attached* trailing-below
  comments (every body kind, not just binops); classify keep vs incidental.
  Expected: {vertical binop chain, vertical pipeline, union variant list}.
- Confirm the 14 let-cases route through `IndentedBlock`-redirection, not
  `columnClaim` (spot-check `--lpt`).
- Output: a `construct-kind × effective-verticality → attach|detach` table.

### Phase 1 — Hoist the stable verticality predicate
Move `subtreeHasVerticalBox` + LPT-only deps (`bracketOpenGate`,
`literalCommentsRideFlatLine`, `nodeIsComment`) into a shared module both stages
import; `NodeClassify` re-exports so Render callers don't churn. Keep the DAG
acyclic.

### Phase 2 — Structural decision replaces column descent (core, 4a)
In `columnClaim` (~line 410) replace
`col >= lastLeafRowMinCol && col > nextItemCol && not trailingAppendHitsBracket`
with `claim iff isMustKeepAttachTrailing candidate.node` (else `Nothing` →
detach). Keep the `adjacentPrev` row-adjacency logic. Retire
`trailingAppendHitsBracket` (brackets are simply not an attach-kind now — this
subsumes `22daf23`).

**Crux / highest risk:** `isMustKeepAttachTrailing` = union variant list OR a
binop/pipeline that *renders multi-line*. Bare `subtreeHasVerticalBox` does NOT
capture an author-broken binop chain (`Binop{forceVertical}` recurses into
children rather than self-reporting vertical), so this needs a binop-specific
"renders multi-line" signal — and it must be **verified reparse-stable in
Phase 0** before Phase 2 leans on it, or it reintroduces an oscillation.

### Phase 3 — Keep the attach path; prove it stays a fixed point
Must-keep set continues through `appendTrailingComment` unchanged. **4b (only if
residual drift):** attach as the construct node's trailing child and let the
renderer place it; delete the column-guided `shouldDescendInto` descent.

### Phase 4 — Simplify & document
Remove dead code (`trailingAppendHitsBracket`; if 4b, the descent helpers).
Rewrite the `Comments.gren` module-header rule as *decision-not-prediction*.
Update this note (§3/§5) + README catalogue; add fixtures
`TrailingCommentFlatBinop`, `TrailingCommentMultilineCall`,
`TrailingCommentMultilineType` (detach); keep the union/chain/pipeline attach
fixtures.

### Phase 5 — Gate
`--decl-ends` → 0; `--gaps` stays 0; `run-tests.sh` (esp.
`UnionTrailingOwnLineComment`, `TrailingCommentAfterContainer`, the 14 let
fixtures); `matrix-syntax.py`; `audit-predicates.py`; spot-check `elm-format`.

---

## 13. Option C — CHOSEN approach (detach everything, full elm-format parity)

Decided 2026-07-17. **Supersedes the §5 "keep the §7 must-keep set attached"
decision:** there are now **no attach exceptions** for the trailing-below-a-
declaration case. (The *interior* "a comment stays by the code" divergences —
README catalogue #12/#13, let-flow comments, mid-expression comments — are a
different mechanism and are **unchanged**.)

### Why: elm-format detaches *all* of them, including the union

The pivotal check (2026-07-17): run every shape through `elm-format --stdin`.
elm-format floats **every** trailing-below comment to column 1 — union variants,
binop chains, multi-line calls, everything — separating it from its neighbours
with blank lines. Worked results:

```
type T                          total =
    = A                             alpha
    | B                                 ++ beta
    | C                                 ++ gamma
                                                       -- both →
-- comment  (col 1)             -- comment  (col 1)
```

So gren-format's two attach behaviours — union at the `|` indent, chain at the
continuation indent — were **both divergences from elm-format**, not matches.
The union attach was the only one frozen in a fixture
(`UnionTrailingOwnLineComment`), which turns out to freeze a divergence, not a
feature. Detaching everything is therefore the *simplest* rule **and** full
parity — the rare case where the two align.

### The change is tiny

`columnClaim` (Comments.gren ~333) is the only thing that ever claims an
own-line-below comment as a construct's trailing comment. Making it **always
refuse** routes every such comment through the existing fresh col-1
`OriginalRows` path in `findOrCreateOrigRow`. That single change *is* Option C;
the rest is cleanup, the blank-line rule, fixtures, docs, and gating.

### Steps

1. **`columnClaim` always detaches.** In `findOrCreateOrigRow` drop the
   `columnClaim` branch so the top-level trailing-below path always creates a
   fresh (col-1) row; `trailingClaim` becomes always-`False`. Verify on the union
   .dirty via `--show` that it lands at col 1 before proceeding.
2. **Delete the dead attach machinery.** `columnClaim`, `appendTrailingComment`,
   `trailingAppendHitsBracket`, `shouldDescendInto`, `isIndentingFlowBox`,
   `isBracketContainerBox`, `lastLeafRowMinCol`, `lastPositionedChild`. Grep each
   for other callers first — some may be shared; keep those. Compile after each
   deletion (`devbox run -- gren make Formatter`). This subsumes the `22daf23`
   narrow bracket fix.
3. **Blank-line separation (new — required).** A detached col-1 comment sitting
   immediately above the next declaration would re-read as that declaration's
   *leading* comment (it was authored trailing the *previous* one). So a detached
   trailing comment must be separated from a following declaration by **one blank
   line if none already exists** — this is what elm-format does (it floats the
   comment free of both neighbours). Lives in `VerticalSpace`; needs a way to tell
   a *detached-trailing* comment (belongs to the decl above, by original
   adjacency) from a genuine *leading* comment (belongs to the decl below) — a
   provenance flag set at detach time, or an original-row adjacency check in
   `VerticalSpace`. Settle the mechanism during implementation.
4. **Fixtures.** Rewrite `UnionTrailingOwnLineComment.formatted.gren` to col-1
   detach — regenerate then **read** it to confirm it is genuinely canonical.
   `TrailingCommentAfterContainer` already expects col-1 and should be unchanged.
   Add detach fixtures `TrailingCommentFlatBinop`, `TrailingCommentMultilineCall`,
   `TrailingCommentMultilineType` (chain / call / type-sig → col 1), each a
   `.dirty` + `.formatted` pair with an `assertPretty` line, to lock in the fix.
5. **Docs.** Rewrite the Comments.gren module-header "trailing-comment boundary
   rule" as: *own-line comments below a top-level declaration always detach to
   col 1 (matches elm-format)* — decision, not prediction. In the README
   divergence catalogue, re-scope #12/#13 to the *interior* cases they still
   cover (the trailing-below case is now a match, not a divergence). Mark this
   note RESOLVED.
6. **Gate.** `fuzz-idempotency.py --decl-ends -j 12` → **0** (was 80);
   `--gaps -j 12` stays 0; `fuzz-whitespace.py` both modes; `run-tests.sh` —
   review every fixture diff (the diff is the oracle for any trailing-below case
   the corpus grep missed); `matrix-syntax.py`; `audit-predicates.py`; elm-format
   spot-check.

### Risks
- **Blank-line provenance** (step 3) is the one non-mechanical piece — getting
  "detached-trailing vs leading" wrong would either glue the comment to the wrong
  neighbour or over-insert blanks. Gate with fixtures that place a decl right
  after the detached comment.
- **Hidden shared helpers** (step 2) — compile-driven deletion; don't remove a
  helper `VerticalSpace`/`SortSymbols` still call.
- **Fixtures the grep missed** — `run-tests.sh` diff catches them; review, don't
  auto-accept.
