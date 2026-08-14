# Triage of the 16,141 UNREVIEWED comment-parity divergences

> **Reviewed and acted on 2026-07-31.** Verdicts, per family, are at the bottom
> of this file under [Outcome](#outcome). Two families were real bugs and are
> **fixed** (A1, A5); one more was a real bug found while reading Group B and is
> also fixed (the `|>`/`<|` operand indent, plus the `<|` over-approximation it
> shared a cause with). The rest are documented divergences — the Group A
> remainder as a single *forced* one, catalogue #22. The rest of this file is
> the original triage, unedited, because the per-family evidence is what the
> verdicts rest on.

Answers `tbd.md` item 1. The baseline records a reason per cell and nothing
else, so the output pairs were regenerated (`--show` + `elm-format` on every
UNREVIEWED cell, plus each cell's **uncommented** form from both formatters for
reference) and every divergence sorted into one family by three mechanical
features. Tool: `tests/triage-comment-parity.py`.

```bash
cd gren-format-lib/tests
./triage-comment-parity.py --collect -j 12   # regenerate the pairs (~1 min at 3.5k UNREVIEWED)
./triage-comment-parity.py                   # the table below
./triage-comment-parity.py --show A1 -n 5    # examples, spread over distinct cells
./triage-comment-parity.py --spread A1       # its construct x context coverage
./triage-comment-parity.py --keys A1         # its baseline keys, one per line
```

**Nothing here is registered.** A family is a unit of *review*, not a verdict —
the 2026-07-15 lesson ("Reclassifying is not a formality") was that a plausible
blanket test can clear 40 of 46 cells and still be wrong. Accept or reject a
family as a whole, with the evidence attached.

Registration then happens one of two ways, and the choice matters. A rule that
generalises — one that will be *right* for a construct nobody has written yet —
belongs in `comment_family` in `matrix-syntax.py`, where every future cell gets
it automatically; that is how #13, #22 and #23 are registered. A verdict about a
*particular* disagreement belongs on the verdict itself, as a `reason` naming a
catalogue entry, applied by `triage-comment-parity.py --register` to exactly the
cells in that group. Reach for the second unless the rule is genuinely
mechanical: a `comment_family` clause that over-reaches sweeps in cells nobody
looked at, which is the failure the 2026-07-15 lesson is about.

## The three features

| feature | what it asks |
|---|---|
| **parens** | did elm delete a redundant paren gren keeps (#10)? Detected, then factored **out** of everything else — otherwise a #10 cell that also moves its comment gets a bucket per paren shape and the real families never surface. |
| **owner** | the comment's index in the paren-free code-token stream. Both formatters emit the same code tokens, so the comment is the only thing that can move. Same index ⇒ they agree what it's attached to. Different ⇒ the family is named by **the tokens the comment crossed**. |
| **layout** | each side's comment-deleted output vs *that same formatter's* output with no comment at all. gren unchanged + elm changed = elm re-flowed because of the comment and gren did not (the documented #12/#18 direction). The reverse is the suspicious one and gets its own family. |

## Result: 13 families, 0 unclassified

**Group A — the two formatters disagree about what the comment is attached to.**
gren renders it at a different token boundary than the author wrote it at.
**10,300 cells, 63.8%.**

| id | n | % | family |
|---|---:|---:|---|
| A1 | 3,714 | 23.0 | gren **hoists a comment out of the container** it was written inside |
| A4 | 2,600 | 16.1 | gren moves a comment written **before a field's `=`** to after it |
| A2 | 1,612 | 10.0 | gren re-homes a comment written **after a `,`** back onto the previous item |
| A3 | 1,312 | 8.1 | gren moves a comment written **after a record update's `\|`** to before it |
| A6 | 940 | 5.8 | comment at a **keyword boundary** (`in` / `else` / `then` / `of` / `->`) |
| A5 | 122 | 0.8 | gren pulls a comment written **after a container's closer** back inside it |

**Group B — attachment agrees; the layout around it differs. 5,841 cells, 36.2%.**

| id | n | % | family |
|---|---:|---:|---|
| B1 | 1,901 | 11.8 | elm **floats the comment out** onto its own line, set off by a blank line |
| B7 | 1,306 | 8.1 | elm breaks the surrounding code further, **without** floating the comment |
| B2 | 1,048 | 6.5 | gren **breaks a construct open** that elm keeps flat |
| B4 | 752 | 4.7 | only the **comment line's own indentation** differs |
| B3 | 554 | 3.4 | both re-flow, and the comment's line lands at a different indent |
| B5 | 144 | 0.9 | gren **strands the comment alone** on a line where elm keeps it beside code |
| B6 | 136 | 0.8 | both re-flow into the same line count, arranged differently |

---

## Group A — attachment

Every family here is **gren moving the comment off the boundary the author wrote
it at**, which is the direction that contradicts gren's stated philosophy ("a
comment sticks to what it trails / stays where you wrote it"). That makes Group A
one policy question with five sub-cases rather than 10,300 separate decisions —
and the answer decides ~64% of the pile.

Verified by hand on ordinary source, outside the matrix harness, so these are not
artifacts of the injection: A1, A3 and A4 all reproduce on hand-written idiomatic
code (`{ -- the timeout in ms` / `[ -- primary` / `{ base | -- bumped` /
`{ field {- why -} = …`).

### A1 — gren hoists a comment out of the container it was written inside — 3,714

The comment was written just after an opening `{` or `[`. gren lifts it onto its
own line **above the whole container**; elm keeps it inside, on the first item's
line. This is the family `tbd.md`'s suggested approach names third. An attachment
decision in `Comments.gren`, not a layout one.

```gren
-- you wrote            -- gren                    -- elm
config =                config =                   config =
    { -- timeout ms         -- timeout ms              { -- timeout ms
      timeout = 500         { timeout = 500              timeout = 500
    , retries = 3           , retries = 3              , retries = 3
    }                       }                          }
```

35 constructs × 25 contexts; block and line comments alike (1,790 / 1,924).
Sub-shapes by what was crossed: `{` (1,834), `[` (1,320), `[ {` (464), `[ [` (64),
`[ [ {` (32).

### A4 — comment before a field's `=` moves after it — 2,600

```gren
-- you wrote                    -- gren                          -- elm
{ field {- why -} = compute 1 } { field = {- why -} compute 1 }  { field {- why -} = compute 1 }
```

gren moves it off the **name** it was written beside and onto the **value**.
Exactly 1,300 block / 1,300 line, 11 constructs × 25 contexts — a completely
uniform family, so one decision settles all of it.

### A2 — comment after a `,` re-homed onto the previous item — 1,612

The author wrote it leading item N (just after the comma); gren attaches it
trailing item N−1.

```gren
-- you wrote                        -- gren                             -- elm
{ fld = 1, {- c -} other = 2 }      { fld = 1 {- c -}, other = 2 }      { fld = 1, {- c -} other = 2 }
```

Same trailing-vs-leading axis as **#13**, but across a comma instead of an
operator — and note the direction is reversed from #13: there elm re-homes, here
**gren** does. Worth deciding together with #13 rather than separately.
Crossed-token sub-shapes: `,` (1,112), `, {` (300), `} ,` (100), `} , {` (100).

### A3 — comment after a record update's `|` moves before it — 1,312

```gren
-- you wrote          -- gren              -- elm
{ base | -- bumped    { base               { base
    count = 1             -- bumped            | -- bumped
}                         | count = 1            count = 1
                      }                    }
```

Same shape as A2 for the update bar. 656 block / 656 line, 6 constructs × 25
contexts.

### A6 — comment at a keyword boundary — 940

The comment sits in the gap around a keyword and the two formatters put it on
opposite sides: `else` (294), `in` (290), `then` (130), `of` (126), `->` (96),
`if` (4).

**`in` is already documented as #20** and explained as forced by a missing fact
(the `in` keyword has no source position, so "trailing the last binding" and
"leading the result" are positionally identical). The review question is whether
that same argument covers the other five keywords, or whether only `in` has the
missing-position excuse:

```gren
-- you wrote                          -- gren                  -- elm
when sel {- c -} is Just w -> 1       case sel of              case sel {- c -} of
                                          {- c -}                  Just w ->
                                          Just w ->                    1
                                              1
```

### A5 — comment after a container's closer pulled back inside — 122

```gren
-- you wrote                          -- gren                            -- elm
fn a { rec | a = 1 } {- c -} last     fn a { rec | a = 1 {- c -} } last  fn a { rec | a = 1 } {- c -} last
```

Smallest family but semantically the loudest: the comment was written *about the
record* and gren renders it *inside* the record, so it reads as a comment on the
last field. The inverse direction of A1. Review this one first — it is small
enough to read every cell (`--keys A5`).

---

## Group B — layout

### B1 — elm floats the comment out with a blank line — 1,901

```gren
-- you wrote / gren             -- elm
{ fld = 1 {- c -} }             { fld = 1

                                {- c -}
                                }
```

This is the **documented #12 / #18 direction** — "your line breaks are your
layout", gren deliberately keeps the comment beside the code. 100% block
comments (a `--` already forces a break, so it cannot appear here). The most
likely candidate for a single widened-`comment_family` rule, and `tbd.md`'s
predicted "one large genuine family" — but note it is only 12%, not the majority
the note expected.

### B7 — elm breaks the code further without floating the comment — 1,306

elm ends up with more lines, but the comment itself stays put; elm splits the
code around it (typically dropping a `<|` / `|>` operand onto its own row). This
is about **elm's operator layout under a comment**, not comment placement — check
it against #14 / #17 / #19 rather than the comment catalogue entries.

### B2 — gren breaks a construct open that elm keeps flat — 1,048

The suspicious direction of B1: a block comment makes **gren** re-flow where elm
renders inline.

```gren
-- you wrote / elm              -- gren
fn (gn {- c -} <| 1) last       fn
                                    (gn
                                        {- c -}
                                        <| 1
                                    )
                                    last
```

76% block comments. A comment forcing verticality the code alone would not is
the over-approximation class, so this is the Group B family most likely to
contain real bugs.

### B4 — only the comment line's own indentation differs — 752

Every code line byte-identical; the comment sits on its own row at a different
column (gren's +4 step vs elm aligning under the operand). Cheapest family to
decide wholesale.

### B3 — both re-flow, comment line at a different indent — 554

Same lines, same words, different leading whitespace. Same question as B4 with a
re-flow underneath it.

### B5 — gren strands the comment alone where elm keeps it beside code — 144

```gren
-- you wrote            -- gren            -- elm
{ rec {- c -}           { rec              { rec {- c -}
| a = 1 }                   {- c -}            | a = 1
                            | a = 1        }
                        }
```

**This is the shape the auto-classifier is deliberately blind to** — both pairing
bugs (`7c20e15`, `cd774f5`) looked exactly like this. 144 cells over 3 constructs;
read them individually, do not batch.

### B6 — same line count, different arrangement — 136

Neither side is "more broken"; the words land on different rows. Heterogeneous —
read these.

---

## Suggested review order

1. **B5** (144) and **A5** (122) — small enough to read whole, and both are shapes
   where gren changes what the comment appears to describe. B5 is the known
   bug shape.
2. **Group A as one policy question** (10,300). A1/A2/A3/A4 are the same
   statement at four delimiters: *may gren re-home a comment across a delimiter
   it was written next to?* Answer that once and 58% of the pile resolves. A4 is
   the most uniform (2,600 cells, one shape) and the cheapest place to test the
   answer.
3. **B2** (1,048) — the over-approximation direction; most likely real bugs in
   Group B.
4. **B1** (1,901) — most likely a clean widening of #12/#18 into a
   `comment_family` rule, with the evidence being this family's own cells.
5. **A6** (940) — decide whether #20's missing-position argument extends past `in`.
6. **B7 / B3 / B4 / B6** (2,748) — layout-only; decide against #14/#17/#19 and the
   indent rules, not the comment catalogue.

## Caveats

- The axis injects **one** comment per cell. A comment *run* has its own
  all-or-nothing pairing rules and no cell generates one (`tbd.md`'s stated gap).
  Nothing here changes that.
- Families are named by mechanism, not by verdict. "gren moves it" is an
  observation; whether that is right is the reviewer's call.
- The `parens` feature is recorded per cell but not used to split families — a
  #10 paren difference riding along on a comment divergence is already
  `INHERITED` in the baseline where it is the *only* difference.

---

## Outcome

Reviewed 2026-07-31 with the repo owner. The decisions, the evidence each rests
on, and what changed.

### The fact that decided Group A

`compiler-common/src/Compiler/Ast/Source.gren` was read directly. Of everything
that can separate two pieces of an expression, **only two carry a source
position**: a binary operator (`Binops.operator : Located String`) and a bracket
(the expression's own `start`/`end`). `=` `:` `|` `,` `->` and every keyword are
parsed and discarded — `RecordField` is `{ field, value }`, `ArrayLiteral` is a
bare `Array Expression`, `Lambda` is `{ patterns, body }`.

So Group A splits cleanly in two, and the split is a fact rather than a
judgement:

| | delimiter | verdict |
|---|---|---|
| **A1** (3,714) | `{` / `[` — **recorded** | **BUG. Fixed.** |
| **A5** (122) | `}` / `]` — **recorded** | **BUG. Fixed.** |
| A4 (2,600) | `=` — not recorded | forced; documented (#22) |
| A3 (1,312) | `\|` — not recorded | forced; documented (#22) |
| A2 (1,612) | `,` — not recorded | forced; documented (#22) |
| A6 (940) | keyword / `->` — not recorded | forced; documented (#22) |

For the bottom four, `x {- c -} TOK y` and `x TOK {- c -} y` reach the formatter
as *the same three positions*. Verified by round-tripping both authorings of
each: gren collapses them to one output, elm-format keeps them apart, so exactly
one of the two must differ from elm whatever side is chosen. The only remaining
signal is how wide the whitespace gaps are, and `fuzz-whitespace.py` exists to
keep that unobservable.

**Gren's existing side is also the better one**, which is easiest to see with a
`--`, where the choice forces a layout:

```gren
-- gren, for both authorings:     -- elm, for the authoring gren does NOT match:
{ field =                         { field
    -- why                          -- why
    compute 1 }                       =
                                      compute 1 }

[ 1 -- c                          [ 1
, 2 ]                             , -- c
                                    2 ]
```

Written up as [divergence #22](../elmFormatComparison.md#divergence-22), with
every case worked in
[formatterRules.md](../formatterRules.md#when-the-formatter-cant-tell-what-you-meant),
and taught to `comment_family` in `matrix-syntax.py` as
`crossed_only_unrecorded_tokens`. That rule is *sound rather than plausible*: it
fires only when the tokens the comment crossed are all position-less, so a move
across a bracket or an operator — a boundary gren can see — still books debt.

### A1 and A5 — the two real bugs, fixed

A1: the container's opening position was known (`locExpr.start`) and thrown
away. `authoredBracketList` now records it (`lpnWithBracketStart`), and
`lpnWithBracketStart` folds it into the node's cached `firstPos`/`minRow` so
every ancestor's bounds agree — without that the comment still reads as
preceding the whole container at the wrapper above it. `[ {- c -} 1, 2 ]`,
`{ {- c -} a = 1 }`, `[ -- c` ⏎ `  1`, and the nested `[ { {- c -} a = 1 } ]`
are now byte-identical to elm-format. Fixture: `ContainerOpenerComment`.

A5: `RecordUpdate` was excluded from `boxKeepsTrailingCommentOutside`. It did
not need to be — its close is EXACT, so `commentInsideTrailingBracket` already
keeps a genuinely-inside comment inside. `fn a { r | a = 1 } {- c -} last` no
longer reads as a note on the last field.

### Group B

- **B1 (1,901) / B7 (1,306) / B4 (752)** — gren is correct; elm's shape is worse.
  B1 is the documented #12/#18 direction. B7 is elm opening the construct around
  a comment (a `<|` losing its operand, a `when` pattern split from its own
  `->`) — now stated as its own entry,
  [#23](../elmFormatComparison.md#divergence-23). B4 is elm hanging a record
  update's own-line comment 2 past the `{`, a column that lines up with nothing —
  [#24](../elmFormatComparison.md#divergence-24).
- **B5 (144)** — *not* a pairing bug, despite the shape. It is A3 seen from the
  other side: all four authorings around a record update's `|` collapse to one
  output, and the own-line placement is the only one that does not claim the
  comment is about the base name or about the first field. Kept, documented
  under #22.
- **B2 (1,048)** — three mechanisms, separated by re-reading the cells:
  - 506 are the A3/B5 record-update comment forcing the update open. Follows
    from #22; not independently fixable.
  - 366 were a **real over-approximation**, now **fixed**: `fn {- c -} <| one`
    broke the chain where `|>`, `+` and `<| {- c -}` all stayed flat. Cause: the
    comment was redirected *into* the `PipelineStep`, where nothing precedes it,
    so it classified `LeadsOwnLine` and forced the chain vertical. The redirect
    is now conditional on the comment not being able to ride the row — the same
    test the `SoftIndentedBlock` arm already applied.
  - the rest are elm keeping a broken call's first argument on the `fn` line
    under a comment. Left as debt; not reviewed.
- **B3 (554) / B6 (136)** — 164 of B3 and the `<|` half of B6 were **one real
  bug**, now **fixed**. gren rendered a pipeline operator as a *flow item* with a
  flat `+4` continuation, where `+`/`++`/`&&`/`==` all went through
  `prefixOperator` and landed at the operator's own width. So a comment pushed
  `|>`'s operand one column past where it belonged, and `<|`'s operand back to
  the `<|` column itself — which is what made `two` stop looking like an argument
  in `fn <| fn one -- c` ⏎ `two`. Both now use `prefixOperator`, matching
  elm-format. That change also **eliminated catalogue #22-as-it-was** (a record
  update as a direct multi-line `|>` operand now matches elm byte-for-byte), so
  the entry was removed and the three new ones took #22–#24.

---

## Re-cluster, after the fixes (2026-07-31, later)

`./triage-comment-parity.py --collect` was re-run against the fixed build and the
remaining 10,267 re-clustered. Three things came out of it.

### A1 was not fully fixed — 972 cells, now fixed too

Every remaining A1 cell was the **record update's** `{`, the one container that
had not been given a bracket position. The reasoning for skipping it (a comment
landing "inside" would sit before the first field, i.e. in the ambiguous `|`
slot) was right but the conclusion was lazy: the base name has a recorded
position of its own (`RecordUpdate.name.start`), so the opener slot and the `|`
slot ARE distinguishable. `{ {- c -} rec | a = 1 }` now matches elm-format, and
the split is made once in `Comments.gren` — which is what makes
`role /= LeadsOwnLine` mean exactly "opener" for the renderer, with no row-read.

### B4 — two mechanisms, both already decided, nothing unaccounted for

Measured on the comment's own line (elm's blank lines were shifting a naive
row-by-row diff):

| n | comment column | blank above | verdict |
|---:|---|---|---|
| 356 | gren +2 | neither | #24 |
| 56 | gren ±1 | neither | #24, with the `{` at a non-multiple-of-4 column |
| 350 | identical | elm only | #18's blank-line half |

### B1 — uniform; B7 — six mechanisms, no new divergences

B1: 100% block comments, 100% `inner`→`alone`, 100% elm-adds-a-blank-line. One
mechanism.

B7 split by role move, and every part maps to an entry that was already decided:

| role move | n | what it is |
|---|---:|---|
| `inner`→`alone` | 682 | lambda `->` body drop — **#16** |
| `inner`→`trailing` | 228 | gren collapses a single-item record — **#21** |
| `inner`→`leading` | 204 | same — **#21** |
| `trailing`→`alone` | 161 | 106 elm splits at every operator (**#17**), 46 `when` pattern split (**#23**) |
| `alone`→`alone` | 111 | `<\|` operand dropped — **#23** + **#14** |
| `trailing`→`trailing` | 108 | **#14** |

So the verdict "gren is correct" held — but it reaches those cells through seven
catalogue numbers, not one. Registering the three families as a single "gren is
correct" reason would have relabelled #14, #16, #17 and #21 as something they
are not. That is the concrete answer to "why do these still need review".

### What was registered, and why it is sound rather than plausible

- **#22**, `crossed_only_unrecorded_tokens` — 2,882 cells.
- **#23**, `only_elm_reflowed` — 2,911 cells: gren emitted *exactly* its
  comment-free rendering and elm did not, so the extra structure is elm's alone.

The asymmetry in #23 is the whole point. The first rule tried was "the comment
moved role, and the base cell already diverges, so call it INHERITED" — it
captured 1,385 cells and was **wrong**: a `{- c -}` in a broken call defeats
gren's own fn/arg0 glue *on top of* the base cell's #10 paren, and that second,
never-reviewed difference would have ridden into the baseline behind a #10 label.
Requiring gren's own side to be untouched is what keeps such a cell out. A `--`
cell where gren legitimately had to break also fails it, and stays UNREVIEWED —
correctly, because there the claim rests on which re-flow is right rather than on
gren having done nothing.

**16,141 → 5,633 UNREVIEWED.**

---

## Interview round 1 (2026-08-01) — 30 verdicts, read back as six rules

`--interview` was run over the 5,485 remaining UNREVIEWED, and 30 review groups
(2,948 cells) got a verdict. Read together rather than one at a time, they are
**six English rules**, now the normative statement of comment behaviour in
[docs/commentHandling.md](../commentHandling.md#the-rules-in-english) — C1/C2
attachment, C3–C6 layout.

The verdicts were *internally consistent under one rule*: at a separator the
parser does not record, the comment goes to the **later** side. A4 `keep`
(gren already does), A3 `bug` and A2 `bug` (gren did not). And a probe of the
live formatter showed the later side was already what `=`, `:`, `in`, `is`,
`then` and `->` did — `,` and `|` were the two outliers, not the rule.

Two things had to go back to the repo owner before any code moved.

### One contradiction

Decisions **1 / 15** (`bug`) and decision **3** (`keep`) are the two spellings of
one gap, and gren emits a byte-identical string for both — the `|` has no
position, so `{ rec -- c` ⏎ `| a = 1 }` and `{ rec | -- c` ⏎ `a = 1 }` arrive as
the same three facts. One verdict called that output right and the other called
it wrong. Resolved in favour of the later side, so decision 3's cell became a
[#22](../elmFormatComparison.md#divergence-22) divergence instead of a
[#24](../elmFormatComparison.md#divergence-24) one.

### One hidden cost

The six A2 `bug` verdicts mean flipping the comma's canonical side, and that is
**not free**: the mirror spelling currently matches elm-format byte-for-byte and
is the only one that appears in real code (7 sites across `core/` and
`compiler-common/`, none the other way):

```gren
, calculate restLeft right -- character deleted
, calculate restLeft restRight
```

It splits by comment kind, though, and that is the useful part. For a single-line
`{- -}` the flip is a **pure win** — `[ 1, 2, {- c -} 3 ]` starts matching
elm-format and the mirror `[ 1, 2 {- c -}, 3 ]` diverges either way (elm floats it
out with a blank line, [#18](../elmFormatComparison.md#divergence-18)). Only the
`--` case is a trade, and there the trade goes the wrong way. So: **block flips,
`--` does not**, which is C2's one documented exception.

### What was implemented

One new `CommentRole`, `LeadsNext`, plus the renderer gluing such a comment onto
the front of the following item's box *before* the `| `/`, ` prefix goes on.

| axis | before | after |
|---|---|---|
| record-update `\|` | own line between base and fields | leads the first field |
| list `,`, single-line `{- -}` | trails the item above | leads the item below |
| list `,`, `--` | trails the item above | unchanged (the exception) |
| exposing list `,` | trails the name | unchanged — see below |
| union variant `\|` | trails the variant | unchanged — see below |

Two constructs were deliberately left out, both stated in the code:

- **exposing lists** are the one bracket list whose items are *reordered*, and
  `SortSymbols` owns comment ownership there on the opposite model
  (`unfoldLastTrailing`'s whole argument is "a comment after a name is that
  name's"). Flipping means reshaping clustering and the closing-`)` pinning, under
  the `sort-order` oracle. Not triaged, not attempted.
- a **union variant's `|`**, where elm-format breaks the union open around the
  comment on either side, so no side gains parity.

### Measured effect

`./matrix-syntax.py --comments` over 38,560 cells, **0 hard failures** — every
cell still formats, preserves its comment exactly once, is idempotent and
AST-equivalent, and tells no predicate lies.

| baseline | before | after |
|---|---:|---:|
| diverging from elm-format | 21,911 | **20,389** |
| UNREVIEWED | 5,485 | **3,561** |
| #22 | 2,882 | 3,132 |
| #23 | 2,001 | 2,001 |
| #13 | 1,766 | 1,766 |
| INHERITED | 9,777 | 9,929 |

**1,522 more cells are byte-identical to elm-format**, and the UNREVIEWED debt
falls by 1,924. Also green: the 294 fixtures, both whitespace-fuzzer modes, the
idempotency fuzzer, the predicate audit, the render-invariant gate, the corpus
check, and the syntax matrix (1738/1738, parity baseline untouched — the change
is comment-only).

### A pre-existing crash found on the way, and fixed

`gen-random.py` turned up 36 `ast-mismatch` seeds in its first 1,500. 17 were
this change widening a latent bug; 19 were already there. All 36 are one thing:
a comment glued to the **front** of an item holding a `"""…"""` shifts that
item's first row by the comment's width while the string's own content rows move
by a different amount, and Gren requires them indented equally — so the formatted
output no longer parses. Both the new `LeadsNext` glue and the pre-existing
opener glue (`[ {- c -} """…"""`) now give way to an own-line comment there
(`subtreeHasMultilineString`). All 36 seeds pass.

Worth knowing: neither matrix covers multi-line strings (stated in
`matrix-syntax.py` — a `"""x"""` cannot be a one-line atom), so `gen-random.py`
is the only gate that sees this class.

## Interview round 2 (2026-08-02) — 10 verdicts, and one rule generalised

Ten more review groups. Two fixes came out of them; the second is the interesting
one, because it **revises round 1's reading of C2** rather than extending it.

### The `--` exception is about line-leading separators, not about lists

Round 1 wrote the exception as a fact about lists: "a `--` between two items stays
with the item above." Round 2's verdicts on the record-update `|` (groups 2, 5 and
23 — 430 cells, given `bug` / `split` / `split`, all one question) do not fit that
shape, and probing the live formatter showed why. `,`, a union's `|` and a record
update's `|` all **lead their line**. A comment above one strands nothing — it
sits at the separator's own column — which is exactly what makes the earlier side
unreadable at `=` `:` `->` and harmless here. Two of the three already behaved
that way; the record update was the outlier, sending *both* same-row spellings
past the `|`.

It now keeps the row the author wrote on:

```gren
{ rec -- c          stays          { rec               stays, at the `|` column
    | alpha = 1                        -- c
}                                      | alpha = 1
                                   }
```

The new role is `TrailsHead`: a record update's base is carried on the
`RecordUpdate` box, not among its children, so `TrailsPrevious` has nothing to
attach to. A single-line `{- -}` in that gap is untouched and still leads the
first field (C2), keeping the update flat (C3). The opener slot, separated by the
base name's recorded position, is untouched.

### The cost, measured before it was accepted

This one **loses** elm-format parity, which is worth recording because it is the
opposite of every other change in this file. elm-format has its own parser and
renders all three spellings of the gap differently; gren-format must collapse the
two same-row ones. The old answer collapsed them onto `| -- c`, which matched
elm-format on `{ rec | -- c` ⏎ `alpha = 1 }`. The new answer collapses them onto
`{ rec -- c`, which matches elm-format on **neither** — elm renders that spelling
in a third way again, floating the comment to the hanging column of
[#24](../elmFormatComparison.md#divergence-24).

**600 comment-axis cells went from byte-identical to divergent; zero gained.**
150 auto-classified (#22, INHERITED:#21+#22); **450 became fresh UNREVIEWED debt**
and have to be given a `keep` verdict as they come up. The headline count fell
3,561 → 3,534, but that is 450 arriving and 477 leaving (the old record-update
family reclassifying to #13), not the 600 being absorbed — do not read the net as
"no new debt". It was taken deliberately, on the grounds
that one rule holding at all three separators is worth more than parity on one
spelling of one construct — and that the spelling does not occur in `core/`,
`compiler-common/`, `compiler-node/` or this repo, in any of its three forms.
The revert, if it is ever wanted, is the record-update arm of
`classifyCommentKind` plus the `TrailsHead` plumbing; nothing else depends on it.

### The other fix: a comment-forced binop break was not indented

Separable from the attachment question, and found inside a group given `keep`.
A `--` forces a break the chain's precedence split would not have made, and the
continuation was landing at the seed's own column instead of `grenIndent`:

```gren
one + two -- c              one + two -- c
* three            →            * three
```

`makeOpAndRhsBox`'s nested-`OpAndRhs` arm used `buildFlowBoxInline 0`. The
precedence-split behaviour the `keep` was protecting ([#17](../elmFormatComparison.md#divergence-17))
is unchanged; only the indent of the forced break moved.

### One group closed as `keep` after probing

Group 30 (`{ a =` ⏎ `1 {- ¤ -} }` collapsing to one line) is not a comment
finding. A 1-field record literal is unconditionally flat, comment or no comment,
so C4 holds exactly — delete the comment and the layout is identical — and the
comment already trails the `1`. Honouring the author's break there would be a
record-literal *layout* change, to be decided on uncommented code. elm-format's
output also inserts a blank line, violating C5.

### Gates

All green: 296 fixtures (3 regenerated, 1 added — `RecordUpdatePipeComment`),
both whitespace-fuzzer modes, the idempotency fuzzer, the render-invariant gate,
the syntax matrix (1738/1738, parity baseline untouched — both changes are
comment-only), 1,500 `gen-random.py` seeds, and the comment axis at 0 hard
failures across all 38,560 cells.

## Interview round 3 (2026-08-01) — 18 verdicts, one fix, and one contradiction caught

Eighteen more review groups. Eleven of them were answered by decisions already on
record; four were the round-2 change's own debt arriving; one was a real bug that
the `keep` verdict's own stated reason argued against.

### The contradiction: four `bug` verdicts asking to undo round 2

Groups 45–48 (`intLit/updateField@flat#g8`, `recordUpdate1/arrayItem@flat#g9`,
`arrayUpdates/top@flat#g16`, `updateNested/top@flat#g8` — 388 cells) were all one
shape, a `--` written **after** the record-update `|` on the base row, with the
note *"keep the comment to the right of the pipe"*. That is round 2's question
with the opposite answer, and the two cannot both be had:

```gren
{ rec -- c            {  rec | -- c         {  rec |    -- c
    | fld = 1 }              fld = 1 }             fld = 1 }
```

All three format to **byte-identical** output. The `|` carries no source position,
and the only thing that distinguishes the spellings is the width of a whitespace
gap, which `fuzz-whitespace.py` exists to keep unobservable. Round 2 already
recorded that ("only **two** shapes are distinguishable, not three"); what round 3
adds is that the *other* spelling comes back as a separate-looking group once the
fix reshapes the disagreement, and reads as a fresh bug report.

These four, plus group 49 (the own-row spelling, 20 cells), are precisely the
"450 fresh UNREVIEWED" round 2 predicted and said to answer with `keep`. All five
were superseded to `keep` in `comment-review.jsonl`, each carrying the reproduction
that shows the collapse.

For the record, elm-format distinguishes all **four** authorings of that gap and
gren-format renders **two**:

| authored | elm-format | gren-format |
|---|---|---|
| `{ rec -- c` ⏎ `\| f = 1` | comment at the hanging column | `{ rec -- c` ⏎ `\| f = 1` |
| `{ rec \| -- c` ⏎ `f = 1` | `\| -- c` ⏎ `  f = 1` | *(same as above)* |
| `{ rec` ⏎ `-- c` ⏎ `\| f = 1` | comment at the hanging column | own row, at the `\|` column |
| `{ rec` ⏎ `\| -- c` ⏎ `f = 1` | `\| -- c` ⏎ `  f = 1` | *(same as above)* |

Group 49's divergence is not about a side at all: both formatters keep the comment
on its own row and agree which fields follow it, and differ only in the column.

### The fix: a comment-forced break has to respect precedence

Groups 50–51 were given `keep` with the reason *"gren has the precedence-aware
formatting of binops; elm doesn't"* — which is right for the family and is exactly
what these two cells were violating:

```gren
-- what the author wrote:      -- gren-format was emitting:
one + two -- c                 one + two -- c
          * three                  * three
```

The break lands at the **tighter** `*` and glues across the **looser** `+`, so the
first row reads as `(one + two) * three`. Comment-free, gren-format never does
that — an author break at `*` collapses back to `one + two * three`, and a forced
break goes at the `+`. This is an internal inconsistency, checkable without
elm-format at all, which is why it outranks the parity question in the same cell.

Cause: `makeBinopBox` asked `NodeClassify.commentBreaksFlowRow` of **each operand
separately**. That function's rule is "a line-ending comment breaks the row only
when a real item actually follows it" — correct, and deliberately so, since
`foo bar -- c` breaks nothing. But a comment at the end of a **non-last** operand
has nothing after it *within that operand*, so the chain read as unbroken, missed
the forced-vertical (precedence-aware) renderer, and fell through to a generic
flow that breaks wherever the comment happens to sit.

`BinopLayout.commentBreaksBinopChain` asks the same question of the whole chain,
interleaving the operator leaves back between the operands, so the follower is
found. It is a strict superset of the old per-operand test — every case that used
to force vertical still does — and it is reparse-stable: the comment is still
mid-chain in the output, so the second format makes the same decision.

```gren
one                    one * two -- c          one
    + two -- c             + three                 + two
      * three                                      + three -- c
                                                     * four
```

Round 2 fixed the *indent* of this same forced break (`buildFlowBoxInline 0` →
`grenIndent` in `makeOpAndRhsBox`); round 3 fixes *where it breaks*. Both halves
now match the claim divergence
[#17](../elmFormatComparison.md#divergence-17) was already making: a comment
changes where the rows fall, never how the operators group.

New fixture `BinopCommentPrecedenceBreak` (all four shapes);
`BinopMultilineCommentRhs` regenerated — same class, block comment, and its
description updated, since it was pinning the old break point as expected output.

### Eleven answered by decisions already on record

- **Groups 53–54** (`{ rec` ⏎ `| a = 1 {- c -} }` flattening) — asked *"why do we
  flatten it?"*. Not a comment finding: a **single-field** record update is
  unconditionally flat, comment or not. `{ rec` ⏎ `| a = 1 }` with no comment
  anywhere also formats to `{ rec | a = 1 }`, while the two-field form honours the
  break. C4 holds exactly. Same answer as the record-*literal* form of the rule
  (round 2's group 30, and group 52 in this round).
- **Groups 59–62** (a `--` between a record field's name and its `=`) — C2
  verbatim: `RecordField` is `{ field, value }`, the `=` carries no position, so
  the comment goes to the later side. Already `keep` at round 1's groups 1, 3, 6,
  8 and 9; these are the same disagreement in nested contexts (paren binop arg,
  pipeline operand, binop rhs), where the surrounding indentation is layout the
  comment did not cause.
- **Groups 55–58** (a `{- -}` on its own row before a `|>` / `<|`) — the author's
  own row break is the layout; elm-format re-flows it inline. `keep`, consistent
  with round 1's groups 12 and 13.

### Gates

All green: 297 fixtures (1 regenerated, 1 added — `BinopCommentPrecedenceBreak`),
both whitespace-fuzzer modes, the idempotency fuzzer, the render-invariant gate,
the predicate audit (287 files, 0 findings), `corpus-check.py`, the syntax matrix
(1738/1738, parity baseline untouched — the change is comment-only), 700
`gen-random.py` seeds, and the comment axis at **0 hard failures across all
38,560 cells**, 17,373 byte-identical to elm-format and **UNREVIEWED unchanged at
3,534** — the cells the fix touches were already registered
[#17](../elmFormatComparison.md#divergence-17) divergences and still are, since
elm-format splits at every operator either way.

### Registering the verdicts (same day)

Three rounds of verdicts had moved the baseline by **zero**. `comment-review.jsonl`
is the review record; `matrix-comment-baseline.json` only changes when a cell is
given a *reason*, and nothing had been connecting the two — so 40 decided groups
covering **2,631 cells, 74% of the debt**, still read `UNREVIEWED`. That is why
the count read 3,534 before and after rounds 2 and 3 alike.

`--register` closes it. A verdict now carries a `reason` — a divergence-catalogue
number — and `--register` writes that into the baseline, overwriting only
`UNREVIEWED` and reporting any reviewed group still without a reason instead of
inventing one. **UNREVIEWED 3,534 → 903.**

**It keys on the group as it is now, not on the `keys` the verdict recorded.**
That distinction is not theoretical: those key lists drift as fixes reshape
groups, and registering from them would have given a reviewed reason to **103
cells whose current group has no verdict at all** — a stale approval arriving
wearing a reviewed label, which is the single failure this baseline exists to
prevent. `group_sig` is the same function `--interview` skips on, so a group
registers exactly when it would not be re-asked.

Assigning the reasons needed **one new catalogue entry**,
[#25](../elmFormatComparison.md#divergence-25). Six groups (269 cells) had
nothing to point at: elm-format *adds* a blank line above an own-row comment
inside a container, and *removes* the row break below one that leads an operator.
Opposite directions, one rule — gren-format neither adds nor removes rows around
a comment. [#23](../elmFormatComparison.md#divergence-23) covers elm re-flowing
the *code* around a comment; #25 is what it does to the comment's own rows. The
other 34 groups are #22 / #23 / #17 / #14 / #12 / #21 / #24 combinations, the
largest being 1,252 cells of `#22+#23` (a `--` between a field's name and its
`=`: unrecorded separator, plus elm putting the `=` on its own row).

`--decisions` was also made to print the **current** verdict per group rather than
the whole append log. Re-answering a group leaves both records in the file and
`--interview` reads the later one; the read-back was showing a superseded `bug`
beside the `keep` that replaced it with nothing to mark which was live — which is
the confusion this record exists to prevent, and it is what made round 3's
contradiction take a reproduction to find rather than a glance.

## Interview round 4 (2026-08-01) — 10 verdicts, one bug, and the `=`/`|` question again

Ten more review groups: four `keep`, six `unsure`. Five of the six `unsure` notes
asked the same thing in different words — *"does the `=` get tracked in the AST?"*,
*"what's our choice about `|` and comments?"* — which is worth recording, because
it is the third round in a row where the reviewer met [#22](../elmFormatComparison.md#divergence-22)
without recognising it. The answer both times is that **neither token is tracked**:
only a binary operator and a bracket carry a position in `Compiler.Ast.Source`, so
both authorings around an `=` or a `|` arrive identically and one of them must
differ from elm-format whichever side gren picks.

- Groups 42–44 (`arrayRecords/*`, 24 cells) — a `--` in a record field's `=` gap.
  Straight [C2](../commentHandling.md#c2--where-the-separator-has-no-source-position-the-comment-leads-what-follows-it):
  gren puts the comment after the `=`, elm strands the `=` on a row of its own
  above it. Registered `#22`.
- Groups 45–47 (`arrayUpdates/*`, 24 cells) — a `--` at a record update's `|`.
  #22's line-leading exception, i.e. the debt `fab9370` was predicted to create
  and said to answer with `keep`. Registered `#22`.

### The bug: a glued-lambda record field indented its dropped body 2, not 4

Group 49 (`lambda/recordField@bareFlat#g8` and its `lambdaLiteral` /
`lambdaRecord` twins, 24 cells) was **not** that question, and the `unsure` note
that caught it — *"what does this look like without a comment?"* — is exactly the
right one to ask. It doesn't:

```gren
{ fld =              { fld =              { fld =
    someLongThing      -- c                 -- c
        + other        f arg1 arg2          \q -> q + one   <- 2, not 4
}                    }                    }
```

Every commented field value drops 4 past the `{`, matching elm-format *and*
gren's own comment-free rendering of the same field — except a lambda body, which
landed at 2, aligned under the field name. `renderGluedLambdaField`'s
single-line-body branch assembled the field flow with `assembleFlow False 0`,
copied from `makePBox`'s `IndentedBlock` arm where the `0` is right because the
*parent* applies the indent. A record field is rendered as a bracket-list **item**,
so its own box has to carry the +4 — which is what `renderFieldFlowWithValueBox`,
the path every other field value takes, already did.

The two only ever disagreed when the field's flow broke, and a comment in the head
is the only thing that breaks it — so this was invisible to every gate that does
not cross syntax with comments, which is the hole the comment axis was built to
close. Now `assembleFlow False grenIndent`; byte-identical to elm-format in record
literals, multi-field literals and record updates alike (verified against
`elm-format --stdin`, not inferred from the triage pair). Fixture
`RecordLambdaFieldCommentIndent`, which pins the three broken shapes plus three
controls: the non-lambda value with the same comment, the comment-free glued
lambda that must stay on the `= ` line, and the `{- -}` case that legitimately
stays flat.

**Revising a verdict means appending a row with the same `sig`** — `load_decisions`
keeps insertion order and `--register` builds `{d["sig"]: d}` over it, so the last
row wins. That is how all seven of these were changed from `unsure`; `--redo`
re-asks interactively, but there is no in-place edit and none is wanted, since the
append log is the record of what was thought before.

**Measured**: UNREVIEWED 855 → 831 (the 24 fixed cells left the baseline as
byte-identical; `--update-baseline` removed exactly those 24 entries and touched
nothing else). 0 hard failures across all 38,560 comment cells; 17,397 byte-identical
to elm-format. Every other gate green — 298 effectful tests, the syntax matrix
(1738/1738, parity baseline unchanged), `corpus-check.py`, `audit-predicates.py`,
and both fuzzers over the whole corpus.

Four groups still carry a verdict with no `reason` and so are still UNREVIEWED in
the baseline (74 cells): three `keep`s — `binopMixedPrec/top@bareFlat#g8` (50
cells), `recordLit2/arrayItem@flat#g11` (8), `backPipe/arrayItem@bareFlat#g8` (8) —
and group 49 itself, whose cells the fix has already removed.
