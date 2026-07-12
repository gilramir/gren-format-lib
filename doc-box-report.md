# Report: the 15 remaining Box `Err` constructs

*A snapshot analysis (originally HEAD `16deb41`) of the declarations the Box
renderer still cannot render, why, and what fixing each would cost. All ship
correct output via the Doc fallback — this is cutover-completion analysis, not a
bug list.*

> **Update (`a08e85b`): Class A landed.** No-trigger multi-line pipeline steps
> render via Box (15 → 14).
>
> **Update (`e8fd6c9`): Class B landed.** Re-inspection showed the premise was
> wrong — elm-format DROPS a soft multi-line field value below `= ` (its
> `equalsPair`); our Doc *glued* it (an unintentional divergence) and Box
> disagreed on the indent. Both renderers now drop, matching elm-format
> byte-for-byte. All 4 record-update-field Errs cleared: **corpus now 10 `Err`s,
> 0 mismatches.** Sections A and B are kept below, marked LANDED.
>
> **Update (`aafdf01`): Class D landed.** The opener-alone "verbatim" block
> comment turned out to be a divergence from elm-format (which reindents the
> body, it doesn't keep absolute columns). Removed the verbatim special-case in
> both renderers; every multi-line block comment now reindents. **Corpus now 9
> `Err`s, 0 mismatches.** Sections A, B, D marked LANDED below.
>
> **Update (`43d6c3c`): Class G landed.** The "comment inside a multi-node
> signature type" Err was *not* an idempotency hazard (that was a self-
> contradictory analysis — idempotency depends only on output text, and Box's
> output here equals Doc's). It was a conservative Err; routing the multi-node
> case through the generic comment-flow clears it. **Corpus now 8 `Err`s, 0
> mismatches.**

---

## 0. Background you need first

`gren format` runs a **self-verifying hybrid renderer**. Every top-level
declaration is rendered twice — once by the newer **Box** renderer, once by the
legacy **Doc** renderer — and the guard in `MakeRender.makePrettyLine` is:

```
if Box output == Doc output  →  ship Box
else                         →  ship Doc   (fallback)
```

A construct is an **`Err`** when the Box renderer *refuses to render it* (returns
`Err`, so the guard falls back to Doc). Today there are **15 such `Err`
declarations across the fixture corpus, and 0 mismatches** — i.e. everywhere Box
*does* render, it is byte-identical to Doc. **All 15 ship correct output via the
Doc fallback; none is a user-visible defect.** They only matter for a future
"delete the Doc renderer" cutover, which requires driving `Err` → 0.

### How this report classifies them

For each `Err` I temporarily **relaxed the guard to "trust Box always" and
removed the `Err`** (let Box render its best effort), then re-ran the corpus
census. That splits every `Err` into one of three buckets:

| bucket | meaning | census signal when relaxed |
|---|---|---|
| **CONSERVATIVE** | Box *already* produces the correct bytes; the `Err` is over-cautious | flips to `ok` |
| **GENUINE** | Box renders a *different* (wrong) layout | flips to `MISMATCH` |
| **UNIMPLEMENTED** | Box has no code path at all to attempt | stays `Err` |

**Result of the probe:** relaxing the record-update + soft-glue + no-trigger-step
`Err`s produced **1 flip to `ok`, 8 flips to `MISMATCH`, 6 still `Err`.**

---

## 1. Executive summary / decision table

| # | Class | Count | Bucket | Fix cost | Ships correct today? |
|---|---|---|---|---|---|
| A | No-trigger multi-line pipeline step | 1 | **CONSERVATIVE — ✅ LANDED (`a08e85b`)** | done | yes |
| B | Record-update field w/ soft multi-line value | 4 | **✅ LANDED (`e8fd6c9`)** — was a Doc divergence from elm | done | yes |
| C1 | Direct-operand lambda glue `\|> (\x -> …)` | 2 | GENUINE | hard — **attack after Box cutover** | yes |
| C2 | Soft-glue of RecordUpdate/AlignedFlow item | 3 | GENUINE | hard — **attack after Box cutover** | yes |
| D | Verbatim (opener-alone) block comment | 1 | **✅ LANDED (`aafdf01`)** — was a Doc divergence from elm | done | yes |
| E | mlbc as nest-carrying first item (`#13`) | 1 | UNIMPLEMENTED — **cutover target = Box `+4`** (verified idempotent) | at cutover | yes |
| F | Leading mlbc in inline-start flow (`#37`) | 1 | UNIMPLEMENTED — **cutover target = Box** (same as E; verified idempotent) | at cutover | yes |
| G | Comment inside a multi-node signature type (`t61`) | 1 | **✅ LANDED (`43d6c3c`)** — was a conservative Err, not idempotency | done | yes |
| H | Multi-line item in comment-bearing bracket list | 1 | UNIMPLEMENTED — **product decision made: adopt elm form (= Box's col 9) at cutover** | at cutover | yes |

*Total 15. \*Class H may be re-scopable — see §H, gren currently **diverges from
elm-format** there.*

**The one cheap win:** Class A. Everything else is genuine exact-space /
comment-idempotency work with no shortcut.

---

## A. No-trigger multi-line pipeline step — 1 — **CONSERVATIVE — ✅ LANDED (`a08e85b`)**

**Fixture:** `MultilineBlockComments` @215 (`pipeline`); dedicated
`PipelineNoTriggerStep` fixture added. **Err site (removed):**
`MakeRenderBox.stepBodyBox`, no-trigger arm.

**Shipped output (Doc):**
```gren
pipeline xs =
    xs
        {- 33 a
           33 b
           33 c -}
        |> Array.map identity
        |> {- 34 a
              34 b
              34 c -}
            Array.foldl (+) 0
```

**Doc vs Box:** **identical.** A pipeline step is only "multi-line" here because
of the block comment; Box's `buildFlowBox` renders it correctly, but the code
defensively returns `Err "…without a trigger (direct-operand paren glue)…"`
instead of the box it already computed.

**Probe result:** changing that `Err` to `Ok box` drops the corpus from **15 →
14 `Err`s with 0 new mismatches**. `@215` flips to `ok`.

**Work to fix:** ✅ done in `a08e85b` — the no-trigger arm collapsed to
`buildFlowBox grenIndent children`. Full trust-Box drill green (effectful
142/142, fuzzers 0); corpus 15 → 14 `Err`s, 0 mismatches. A dedicated
`PipelineNoTriggerStep` fixture pins it (its decls now census `ok` via Box).

---

## B. Record-update field with a soft multi-line value — 4 — **✅ LANDED (`e8fd6c9`)**

> **Correction / resolution.** This section originally claimed "gren matches
> elm-format for record updates" and that Box diverged — both wrong. Re-inspection
> (feeding elm-format the value *already broken across rows*, since elm is
> author-driven, not width-fitting) showed elm-format **DROPS** a soft multi-line
> field value below `= ` via its `equalsPair` combinator, and our **Doc** was the
> one *gluing* (`| f = Array.map` / args) — an unintentional divergence from elm.
> Fix: both renderers now drop soft values below `name =` (`dropFieldValueBox` /
> `fieldInnerDoc`, mirroring `equalsPair`), verified byte-for-byte against
> elm-format. Deleted the Box gate; the KitchenSink nested updates (previously
> hanging-indented to ~column 40) are now clean. The head-comment sub-case
> (`f = {- c -} …`) keeps the existing path. **All 4 cleared, 0 mismatches.**

_Original analysis, for the record:_

**Fixtures:** `KitchenSink` @148, @237; `KitchenComments` @193;
`LambdaBracketBodyNestedInCall` @4 (plus a nested comment-bearing variant that
surfaces as a deeper `Err` in KitchenComments). **Err site:**
`makeRecordUpdateBox`, line ~1707 (`recordFieldValueDrops` gate).

**Shipped output (Doc) — `LambdaBracketBodyNestedInCall` @4:**
```gren
updateHeaders sinkConfig =
    { sinkConfig
        | headers = Array.map
                (\headerEntry ->
                    { headerEntry
                        | headerName = String.toLower headerEntry.headerName
                        {- lowercase header names -}
                    }
                )
                sinkConfig.headers
```
The field value (`Array.map (\…) …`) is a **soft** multi-line node (a broken
call). Doc keeps it glued to `= ` and lets it break by its own internal layout.

**gren vs elm-format:** **gren matches elm-format** for record updates (verified
against elm-format 0.8.8). So this `Err` is purely an *internal* Box-vs-Doc gap,
not a divergence from elm.

**Doc vs Box:** GENUINE divergence (confirmed `MISMATCH` when relaxed). Per the
in-code comment: *"Box's flat stacker drops the whole value onto its own line,
diverging."* Doc glues via a soft `R.group` (flattens to a space); Box drops it.

**Work to fix:** medium–hard. Box must render a soft multi-line field value
**glued to `= `**, breaking only by the value's own align/nest — the align-vs-nest
distinction the flat Box stacker currently can't express. Shared
`makeRecordUpdateBox` path.

---

## C. Soft-glue of a multi-line item — 5 — GENUINE (two sub-types)

**Err site:** `assembleFlowImpl`, `softGlueAlignment … UnclassifiedCarrying`
arm, line ~4153.

### C1. Direct-operand lambda glue — 2 (`PrefixAnchorDivergence` @23, `CallArgBlockRelocation` @71)

**Shipped output (Doc) — `PrefixAnchorDivergence`:**
```gren
directOperandPipe =
    values
        |> (\n ->
                if n then
                    aaaaa

                else
                    bbbbb
            )
```

**Doc vs Box (captured — this is the real Box output when un-Err'd):**
```
  BOX (wrong)                     DOC (shipped, correct)
  |> (\n ->                       |> (\n ->
      if n then                           if n then       ← Box 4 cols too shallow
          aaaaa                               aaaaa
      else                                else
          bbbbb                               bbbbb
  )                                       )               ← Box 4 cols too shallow
```
Box renders the lambda body (and the closing `)`) **4 columns too shallow** —
wrong vs both Doc and elm-format. This is why the `Err` exists; it is **not**
false caution.

**The closing `)` — a Doc divergence from elm-format, to be fixed in Box.**
On the closing `)`, elm-format aligns it *under* the `(` (col 12); the Doc puts
it **one column to the right** (col 13); Box (un-Err'd) puts it under the `|>`
(col 9). This was previously documented (README point 11) as an intentional
"keep as is"; the decision is now to **bring it into line with elm-format** —
`)` aligned under `(` — but **in the Box renderer, not the Doc** (the Doc is
slated for deletion at cutover, so it isn't worth fixing there).

**Why it can't be fixed cheaply in the Doc (investigated 2026-07-11).** The
obvious one-line fix — add `R.align` to `makeParenBlockDoc`'s `isLambdaMultiline`
arm — does *not* work: `R.align` re-anchors the whole paren body at the `(`
column, so it fixes the `)` (col 13 → 12) but simultaneously pulls the lambda
**body** left by one (col 17 → 16), and the body was already at elm's column 17.

| | body (`if n then`) | `)` |
|---|---|---|
| elm-format | col 17 | col 12 |
| gren today | col 17 ✓ | col 13 ✗ |
| gren + `R.align` | col 16 ✗ | col 12 ✓ |

elm anchors the body and the `)` at **two different references** — body at a
fixed +4 from the lambda content, `)` at the *width-dependent* `(` column
(`step-base + width("|> ")`). gren's fixed-multiple-of-4 model has one anchor
per `align`, so it can't place the `)` one column left of the body's nest
without a **token-width-dependent** offset. That's the exact-space class (same
wall as C2 and the other hard Errs) — so this is **not a contained change**; it
belongs with the Box exact-space work, placing the `)` at the width-derived `(`
column (with the body kept at its fixed +4). A correct Box port must therefore
get **both** right: the body indent (Box currently 4 cols too shallow) *and* the
`)` aligned under `(`.

### C2. Soft-glue of a RecordUpdate / AlignedFlow item — 3 (`KitchenSink` @311, `KitchenComments` @303, `MultilineBlockComments` @72 `#12`)

**Doc vs Box:** GENUINE (`MISMATCH` when relaxed). These items' continuation
lines carry `Tab`s. Box's `B.prefix` glue pads by *exact character width*, but
`Tab` **snaps to the next multiple of 4**, so the continuation lands a few
columns off (documented example: the `#12` extension record lands **3 columns
short**). Doc reaches the right column by explicitly compensating prefix widths;
Box's Tab arithmetic can't match it under a prefix glue.

**Work to fix (all of C):** hard. This is the core **exact-space / Tab-vs-prefix
problem**. A global "Tab → exact 4 spaces" swap was tried and **falsified** (it
broke 9 other constructs whose Doc side compensates prefix widths). It can only
fall **per-construct**, with a targeted spaces-indent or per-line anchor.

---

## D. Verbatim (opener-alone) multi-line block comment — 1 — **✅ LANDED (`aafdf01`)**

> **Resolution.** The premise ("verbatim col-0 preservation is needed for ASCII
> art") was wrong twice over. First, dropping the `R.reset` naively breaks
> *nested* opener-alone comments (the body grows by the ambient indent on each
> reformat — `hardNl` compounds the nest). Second, and decisively: **elm-format
> reindents** an opener-alone body (smallest content line → offset 3, everything
> relative) — it does **not** preserve absolute columns. So gren-format's
> verbatim mode was a divergence. Fix: route every multi-line block comment
> through the existing `R.align`-based reindent path (Box already had it) — ASCII
> art keeps its *relative* shape, it's idempotent for top-level and nested, and
> Box can render it. Verified byte-identical to elm-format. Deleted the verbatim
> branch in both renderers plus the dead `R.reset`/`Reset` machinery.

_Original analysis, for the record:_

**Fixture:** `BlockCommentBodyIndent` @31. **Err site:** line ~648.

**Shipped output (Doc):**
```gren
{-
      An opener ALONE on its first line is the author's signal for verbatim
  mode: every body line keeps its absolute column, untouched.

         /\
        /  \    hand-aligned art must not be re-anchored
       /____\
-}
```
When a block comment's first line is bare (just `{-`), the body is reproduced
**verbatim at absolute column 0** — hand-aligned ASCII art, etc., must not be
re-indented.

**Doc vs Box:** Box has no path — every Box block sits inside the ambient
indent; there is no way to force body lines back to **column 0** regardless of
nesting.

**Work to fix:** medium. Needs a new Box primitive: a `reset`-to-column-0 wrapper
for verbatim comment bodies. Self-contained (doesn't touch the flow fold), so
lower-risk than the exact-space classes, but it is genuinely new machinery.

---

## E. mlbc as nest-carrying first item in an indented flow (`#13`) — 1 — UNIMPLEMENTED (cutover target decided)

> **Decision (2026-07-11): adopt Box's `+4` form at cutover; keep it an `Err`
> (Doc `+8` ships) until then — no Doc work.** For `decl {- mlbc -} x =`, the
> three renderers all differ:
> ```
>   Doc (shipped)         Box (bare push)       elm-format
>   decl {- 13 a          decl {- 13 a          decl
>           13 b                  13 b              {- 13 a
>           13 c -}               13 c -}              13 b
>           x =    (+8)       x =     (+4)             13 c
>                                                   -}
>                                                   x
>                                                   =
> ```
> elm-format **explodes the whole head** (name / comment / each arg / `=` each on
> its own line) — verbose and inconsistent with gren's glue-not-explode style, so
> we're **not** matching elm here. The Doc puts the argument at `+8` (aligned with
> the comment's interior); the Box bare-push puts it at `+4` (the normal
> continuation indent), which reads better. **Box's `+4` form is a verified
> reparse fixed point** (byte-idempotent on double-format, AST-preserved, and
> idempotent for the alternate author layout too) — so the exact-space label was
> about the Box-vs-Doc *disagreement*, not the `+4` form itself.
>
> We deliberately do **not** make the Doc produce `+4` (that's the hard
> exact-space work on a to-be-deleted renderer). So `#13` stays a fallback `Err`
> (shipping Doc's `+8`) for now; **at cutover, drop the Err, let Box render `+4`,
> and regenerate the fixture.** (Doing it now would create a Box-vs-Doc mismatch,
> breaking the 0-mismatch / trust-Box-green invariant for zero shipped benefit,
> since `+8` ships either way until cutover.)

_Original analysis, for the record:_

**Fixture:** `MultilineBlockComments` @92 (`decl`). **Err site:** line ~4095.

**Shipped output (Doc):**
```gren
decl {- 13 a
        13 b
        13 c -}
        x =
    {- 14 a
       14 b
       14 c -}
    x
```
The header `decl {- mlbc -} x =` is the first item of the indented declaration
flow. Inside its box the mlbc body lines are **align-anchored** (padded by the
prefix width) while the follower's fresh row is **ambient-anchored**. The Doc
gives the ambient lines the outer flow's nest on top; **no single uniform
per-box shift satisfies both anchors** — the same t42/t43 exact-space class.

**Reparse angle:** this is fenced narrowly. A broad fence cost 8 `Err`s
(including ~6 previously-green module-header/union nodes). The current fence
catches exactly this one node.

**Work to fix:** hard (exact-space, mixed anchors).

---

## F. Leading mlbc in an inline-start flow (`#37`) — 1 — UNIMPLEMENTED (same decision as E)

> **Decision (2026-07-11): same as `#13` — adopt Box's form at cutover, keep the
> `Err` (Doc ships) until then.** For `\{- mlbc -} x -> …`:
> ```
>   Doc (shipped)     Box (un-Err'd)     elm-format
>   \{- 37 a          \{- 37 a           \{- 37 a
>       37 b              37 b               37 b
>       37 c -}           37 c -}            37 c
>       x ->   (col 9)  x ->    (col 6)     -}
>                                          x
>                                          ->
> ```
> elm-format again **explodes the head** (`-}`, `x`, `->` each on their own line)
> — verbose, not matched. Box puts `x ->` at col 6 (aligned under where `x` would
> sit in `\x`, arguably cleaner than Doc's col 9); **verified idempotent**
> (byte + AST). Same structure as `#13`.
>
> **Nuance:** the `#37` "leading-mlbc-inline-start" Err is itself *conservative*
> (Box's `ownLine` matches Doc for the leading comment) — clearing it just
> exposes the same `#13` nest-carrying divergence underneath (`x ->` at col 6 vs
> col 9). So `#37` and `#13` are the **same underlying issue**; the lambdas decl
> needs both sites un-Err'd to render. Deferred to cutover with `#13`.

_Original analysis, for the record:_


**Fixture:** `MultilineBlockComments` @237 (`lambdas`). **Err site:** line ~3968.

**Shipped output (Doc):**
```gren
lambdas =
    \{- 37 a
        37 b
        37 c -}
        x ->
        {- 38 a
           38 b
           38 c -}
        {- 39 a
           39 b
           39 c -}
        x
```
A multi-line comment **leads** an inline-start flow (`\{- … -} x ->`). The
first-row box's continuation lines fall **outside the enclosing flow's indent
reach** — the Doc gets that re-indent from its outer `nest`, which Box's
first-row glue can't reproduce.

**Reparse angle:** discovered as a `#37` regression during a drill and
deliberately re-fenced — a naive widening shifted the comment on reparse.

**Work to fix:** hard (exact-space + inline-start anchoring).

---

## G. Comment inside a multi-node signature type (`t61`) — 1 — **✅ LANDED (`43d6c3c`)**

> **Correction + resolution.** The analysis below was wrong, and self-
> contradictory: it claimed "Box renders this correctly but breaks idempotency."
> Idempotency is a property of the **output text** (reparse it, reformat,
> compare) — it cannot depend on *which renderer* produced identical bytes. In
> fact Box's rendering of this construct is **byte-identical to Doc's** (the
> committed, idempotency-tested form), so it is trivially a fixed point. The old
> "breaks idempotency" note referred to a *different, reverted layout* (the
> historical `t61` Doc-era attempt), which this report mis-attributed to the
> current Box. The Err was simply **conservative** (like Class A): the multi-node
> type case (`{ … } -> …`) hadn't been routed to the generic comment-flow path.
> Fix: route it through `buildFlowBox grenIndent children` (the same path the
> sibling `hasComment` arm uses). Census 9 → 8, 0 mismatches, shipped output
> unchanged, full trust-Box drill green. Lesson: "Box renders X but it's
> non-idempotent" is a contradiction whenever Box's X equals the shipped Doc
> output — always check whether Box's output actually *differs* first.

_Original (incorrect) analysis, for the record:_

**Fixture:** `TrickyComments` @92 (`extensibleSig`). **Err site:** line ~2273.

**Shipped output (Doc):**
```gren
extensibleSig : { a
                    {- between extension var and pipe -}
                    | foo : Int
                    , bar : String
                } -> Int
extensibleSig _ =
    0
```

**This is the reparse/idempotency case.** Box *can* render this correctly on
this target — but the fix was **reverted (t61)** because it **breaks
idempotency**: the comment's reconstructed source position, on reparse, resolves
to a slightly different spot, so re-formatting the output **shifts the comment**
— i.e. formatting is no longer a fixed point. (Recall: gren-format rebuilds
comment positions from source coordinates after parsing, so any layout that
moves a comment must be a *reparse fixed point*, or the idempotency fuzzer trips.
Doc's placement here is a proven fixed point; the Box attempt was not, and it
also perturbed two *other* flows.)

**Work to fix:** hard. Not a rendering problem — a **fixed-point** problem.
"Renders right on the target, breaks idempotency elsewhere; do not retry
naively."

---

## H. Multi-line item in a comment-bearing bracket list — 1 — UNIMPLEMENTED (product decision made)

> **Decision (2026-07-11): adopt elm-format's form (branches at col 9, +4 from
> `[`) — and Box *already produces it*.** Box uses `Tab`-based indent (like
> elm-format), which anchors the `when`-branches to the array-element base →
> col 9, **byte-identical to elm-format** (verified). The Doc uses fixed +4 from
> the `[ `-pushed `when` column → col 11 (2 deeper), which is the divergence.
> ```
>   Doc (shipped)      Box = elm-format
>   [ when x is        [ when x is
>         True -> (11)     True -> (9)
>             1                1
> ```
> So this "hard exact-space" Err is actually **Box already matching elm** — the
> Err just flags Box-vs-Doc disagreement. Shipping col 9 **now** would need the
> Doc to reproduce the Tab-snapped col 9 (the hard exact-space work, on a
> to-be-deleted renderer) — not worth it. **At cutover: drop the Err, Box's col 9
> (= elm) ships, regenerate the fixture.** No interim mismatch introduced now.

_Original analysis, for the record:_

**Fixture:** `WhenInCommentedArray` @4. **Err site:** line ~3159.

**Shipped output (Doc) — note the branch indent:**
```gren
foo x y =
    -- first item is a when expression
    [ when x is
          True ->
              1

          False ->
              2
    -- second item is also a when expression
    , when y is
          True ->
              3
    ...
```

**gren vs elm-format — ⚠️ these DIVERGE:**
```
  gren (shipped, Doc)             elm-format
  [ when x is                     [ case x of
        True ->      (col 11)         True ->     (col 9)
            1                             1
```
gren indents the `when`-branches **two columns deeper** than elm-format (branches
under `when`+4 vs under `[`-item+4). This is the "Tab-vs-prefix bracket item"
exact-space case — **but** unlike classes B/C (where gren matches elm and the
gap is internal), here **gren's shipped output already differs from elm-format.**

**Open question for you:** is gren's deeper branch indent intentional, or a
latent Doc quirk? If we'd rather match elm-format's shallower form, the Box
renderer (which is elm-aligned) may render *that* naturally — which could turn
this from "hard exact-space work" into "adopt Box's output + regenerate the
fixture." Worth a dedicated look before classifying the effort.

**Work to fix:** hard as-is (exact-space), OR possibly a product decision to
adopt elm-format's indent (then likely cheap).

---

## 2. Recommendations

1. **Do Class A now** — one-line change, 15 → 14 `Err`s, zero risk, zero output
   change. The only genuinely free reduction.
2. **Leave Classes B, C, E, F as-is** — genuine exact-space / Tab-vs-prefix /
   mixed-anchor work. Multi-session, revert-prone (the global Tab→spaces attempt
   is already falsified), and buys **zero user-facing benefit** (all ship correct
   via Doc). These are the reason the full cutover was parked.
3. **Class D (verbatim col-0 reset)** is the most *self-contained* of the hard
   set — a new primitive, not entangled with the flow fold. If you ever want one
   more `Err` off the board with bounded risk, this is the candidate.
4. **Class G (`t61`)** — do not retry without solving the comment fixed-point
   first; it's an idempotency problem, not a layout one.
5. **Class H — decide the product question first** (match elm-format's branch
   indent or keep gren's deeper form). That decision may make it cheap or
   confirm it's hard.

### Bottom line
Of the original 15: **A (1), B (4), D (1), and G (1) are now landed.** B and D
were *unintentional divergences from elm-format*; A and G were *conservative
Errs* (Box already matched Doc, just not wired up). **8 remain, all with a clear
disposition — nothing is undecided:**
- **C (4)** — genuine exact-space divergences; real work, **attack after the Box
  cutover**.
- **E, F (2) + `#13`** — nest-carrying mlbc; **Box's form ships at cutover**
  (verified idempotent; elm explodes the head, not matched).
- **H (1)** — **product decision made: adopt elm's form, which Box already
  produces** (col 9); ships at cutover.

The self-verify guard ships correct output for every one of them today. The
pattern worth noting: **most of the "hard" frontier was misdiagnosed** — the real
issue was the Doc renderer quietly diverging from elm-format (B, D, H) or a
conservative Err (A, G), which the elm-aligned Box either already handles or will
at cutover. The genuinely hard residual is just **C** (4 items) — the
`Tab`-vs-prefix exact-space cases where the Doc compensates prefix widths by
different arithmetic than Box.
