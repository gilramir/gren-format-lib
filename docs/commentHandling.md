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
already obey them. Each later interview round has revised the *reading* of a rule
or found code not yet obeying one; the "What changed" sections below are that
history in order, most recent last —
[2026-08-01](#what-changed-2026-08-01), [2026-08-02](#what-changed-2026-08-02),
[C4 in a binop chain](#what-changed-next-c4-in-a-binop-chain),
[rounds 4 and 5](#what-changed-interview-rounds-4-and-5),
[the `<|` a comment moved](#what-changed-the--a-comment-moved-off-its-seed),
[the same `<|`, one step further in](#what-changed-next-the--a-comment-moved-off-a-later-seed),
[the `{- -}` that opened a container](#what-changed-next-the----that-opened-a-container-for-a-lambda),
[the `{- -}` that floated off an operator](#what-changed-next-the----that-floated-off-an-operators-row),
[the `--` in front of a later `<|`](#what-changed-last-the----in-front-of-a-later-).

**C1 / C2 are about attachment; C3–C6 are about layout.** The two never trade
against each other: attachment is settled first, in `Comments.gren`, and the
renderer lays out whatever it is handed.

**C1 and C2 are ordered, not parallel.** C1 decides wherever the parse carries
enough information to see what the author wrote; C2 is the tie-break for the gaps
where it does not. C2 never overrides C1. Reading the two as competing rules is
the mistake this ordering exists to prevent — it is why C2's "exception" below is
not really an exception, but C1 still having evidence.

### C1 — A comment belongs to the code you wrote it next to

Placement comes from the comment's own source position relative to the tokens the
parser recorded, and nothing else. Layout never re-homes a comment to a different
owner to make a line fit.

```gren
-- you wrote                          -- gren-format
someFunction                          someFunction
    argOne -- about argOne                argOne -- about argOne
    argTwo                                argTwo
```

The comment annotates `argOne`, and it is still on `argOne`'s row after the call
is laid out. Nothing about `argTwo` needing its own row, or the call being wide,
can move it.

One convergence to know about, because it looks like a counter-example: where a
comment *cannot* share the row it was written on, "trails the previous item" and
"leads the next one" render identically, so the stored role can differ between two
formats even though the bytes do not. `someFunction argOne -- about argOne` ⏎
`argTwo` renders the comment on its own row (a `--` ends its row, and an item
follows — see C3), so the second parse reads that row as `LeadsOwnLine` where the
first read `TrailsPrevious`. In that shape both roles render to the same bytes, so
the output is still a fixed point; what moved is the stored role, not the
placement C1 is about.

### C2 — Where the separator has no source position, the comment leads what follows it

`=` `:` `|` `,` `->` and every keyword are parsed and thrown away (see
[What the parser records](#what-the-parser-records-and-what-it-doesnt)), so
`x {- c -} SEP y` and `x SEP {- c -} y` reach the formatter as the same three
facts — where `x` ends, where the comment is, where `y` starts. One of the two
spellings must therefore differ from elm-format whichever side is picked.
gren-format picks the **later** side — the comment leads `y`:

```gren
-- you wrote (either spelling)          -- gren-format
{ fld {- c -} = 1 }                     { fld = {- c -} 1 }
{ rec {- c -} | a = 1 }                 { rec | {- c -} a = 1 }
[ 1 {- c -}, 2 ]                        [ 1, {- c -} 2 ]
f : Int {- c -} -> Int                  f : Int -> {- c -} Int
when sel {- c -} is …                   when sel is ⏎ {- c -} ⏎ …
let a = 1 {- c -} in b                  let … in ⏎ {- c -} ⏎ b
```

**The exception is a `--`, or a multi-line `{- … -}`, at a *line-leading*
separator** — a `,` or a `|`. Those keep the row the author wrote them on.

It is an exception to C2, not to C1 — it is the case where C1 can still see the
answer. The parser records where each *item* ends, so the one thing C1 has to go
on at a separator is **which row the comment is on relative to the items around
it**. Two spellings are distinguishable exactly when they put it on different
rows:

| The two spellings around a separator | Different rows? | Decided by |
|---|---|---|
| `--` / multi-line `{- … -}` at a **line-leading** separator (`,`, `\|`) — one ends the previous item's row, the other owns a row above the next item | **yes** | **C1** |
| `--` / multi-line `{- … -}` at a **line-trailing** separator (`=`, `:`, `->`) — both end the same row | no | C2 |
| single-line `{- -}`, at any separator — ends no row either way | no | C2 |

So at a `,` or a `|`, both authorings survive:

```gren
[ apple -- the red one     { rec -- about the base        = A -- about A
, banana                       | alpha = 1                | B
]                          }

[ apple                    { rec                          = A
-- about banana                -- about alpha             -- about B
, banana                       | alpha = 1                | B
]                          }
```

(The own-line comment sits at the separator's own column, not the item's — see
C6.)

Two spellings, two outputs — and both are fixed points, because a comment that
ends its row is on the previous item's row or on a row of its own, and re-parsing
the output puts it back where it was. A third spelling, written *after* the
separator but still on the item's row (`[ 1, -- c` ⏎ `2 ]`, `{ rec | -- c` ⏎
`a = 1 }`), puts the comment on the same row as the first spelling, so C1 cannot
separate them and it collapses onto the first:

```gren
-- you wrote               -- gren-format
[ 1, -- c                  [ 1 -- c
  2 ]                      , 2
                           ]
```

So there are three ways to type it and only ever **two** outcomes — the
before-separator and after-separator spellings on the item's row are one and the
same as far as the parse is concerned.

That is the whole difference from `=` `:` `->`: those *trail* their line, so the
earlier side strands them —

```gren
-- gren-format, for both spellings:     -- the earlier side would give:
{ fld =                                 { fld
    -- why                                -- why
    compute 1 }                             =
                                            compute 1 }
```

— whereas `,` and `|` *lead* their line, so a comment above one strands nothing;
it simply sits at the separator's own column. Sending it to the later side
anyway would move the note onto `banana` **while the parse still says it was
written after `apple`** — C1 overruled by a layout choice, which the ordering
above forbids. The idiom is unanimous in real code for a list (every instance
across `core/` and `compiler-common/` is spelled that way, none the other); the
record update's `|` has no instances at all either way, and follows because it is
the same shape.

A single-line `{- -}` has no such pull — it does not end its row, so both
spellings really are indistinguishable — and follows C2 at every separator
(`[ 1, {- c -} 2 ]`, `{ rec | {- c -} a = 1 }`, both flat by C3). Neither
appears in real code, in either spelling.

A record update's base is not one of its children — the fields are — so a comment
trailing it needs its own role, `TrailsHead`; everywhere else the item above is a
sibling and `TrailsPrevious` reaches it.

**Two places C2 is not yet applied** — the only two spots where the rule set is
genuinely not uniform. Both are deliberate, and both were out of the scope that
was reviewed:

- an **`exposing ( … )` list**, the one bracket list whose items get *reordered*.
  `SortSymbols` owns comment ownership there on the opposite model — a comment
  after a name is that name's and travels with it through the sort, so the same
  module written in either author order lands on the same bytes. Flipping the
  side means reshaping clustering and the closing-`)` pinning with it; until that
  is designed and put under the `sort-order` oracle, an exposing list keeps the
  earlier side.

  ```gren
  module M exposing (alpha {- c -}, bravo)      -- unchanged; C2 would give
                                                -- (alpha, {- c -} bravo)

  module M exposing (charlie, alpha {- c -}, bravo)     -- you wrote
  module M exposing (alpha {- c -}, bravo, charlie)     -- gren-format: travels
  ```

  Travelling is what a comment on an **earlier** name does. A comment trailing
  the *last* name of a **flat** list is a different case, and deliberately so: on
  one row, "inside the brackets" and "past the `)`" are written in the same place
  and the `)` has no position to separate them, so such a comment is read as the
  *list's* and pinned above the `)` (`unfoldLastTrailing`). It therefore does not
  travel, and the two orders below are not the same module as far as ownership
  goes:

  ```gren
  module M exposing (alpha {- c -}, bravo)     -- alpha's; stays as written
  module M exposing (bravo, alpha {- c -})     -- the list's; becomes:
  --  module M exposing
  --      ( alpha
  --      , bravo
  --      {- c -}
  --      )
  ```

  That is [A comment past a flat
  list](knownLimitations.md#a-comment-past-a-flat-list), and `gen-random.py`'s
  `sort-order` oracle exempts exactly this shape (`_reverse_header_exposing`) for
  the same reason. Write the list vertically and the `)` has a row of its own,
  which is enough to tell the two apart.
- a **union variant's `|`**. elm-format breaks the union open around that comment
  whichever side it is on, so gren diverges from it either way and flipping buys
  no parity.

  ```gren
  type V                                        -- unchanged; C2 would give
      = A {- c -} | B                           -- = A | {- c -} B
  ```

  Note this is the *single-line `{- -}`* case only. A `--` or a multi-line
  `{- … -}` at a union's `|` is a line-leading separator like any other, and C1
  keeps the row it was written on (`= A -- about A` ⏎ `| B`).

### C3 — A comment never forces a break

A construct the author wrote on one line stays on one line whenever every comment
inside it can share that line. A single-line `{- -}` can; a `--` cannot (it
swallows the rest of the row), and a multi-line `{- … -}` brings its own newlines.

```gren
-- you wrote                 -- gren-format
{ rec {- c -} | a = 1 }      { rec | {- c -} a = 1 }     flat: the `{- -}` rides
[ 1 {- c -}, 2 ]             [ 1, {- c -} 2 ]            flat: same
f {- k -} x                  f {- k -} x                 flat: same

[ 1 -- c                     [ 1 -- c                    open: the `--` ends
, 2 ]                        , 2                         the row, so item 2
                             ]                           cannot follow on it
```

The break in the last case is the comment's own row-ending, not a decision: the
container has to open because there is no line left to put `2` on. That is the
only kind of break a comment ever causes, and C4 is what says so.

### C4 — A comment changes where the rows fall, and nothing else

A comment can force row breaks — that is C3, and it is unavoidable. Everything
past that must be what gren-format would have produced with the comment deleted:
the same indentation, the same grouping, the same owner for every piece.

The earlier statement of this rule — "delete every comment from the output and you
get the comment-free layout" — is too strong, and the reason is worth keeping in
view. The row break a `--` forces is still there once you delete it, so the two
outputs differ by construction; the claim only holds of everything *other* than
the row structure.

**Grouping.** A `--` mid-chain has to break the chain, but at the operator
precedence chooses, never at whichever operator the comment happens to sit next
to:

```gren
-- you wrote            -- was: echoed back        -- is: broken at the `+`,
                        --      as written         --     which is looser
one + two -- c          one + two -- c             one
    * three                 * three                    + two -- c
                                                         * three
```

The middle form reads as `(one + two) * three` — a grouping gren-format never
produces without a comment. Delete the comment from it and you get `one + two` ⏎
`* three`, which is not what gren-format renders for that expression either way.

**Indentation.** From `PipeCommentOperandIndent`, where the comment-free twin sits
in the fixture directly beneath each commented case for exactly this comparison:

```gren
backPipeRecord =            backPipeRecordNoComment =
    gn <|                       gn <|
        {- c -}                     { x = 1
        { x = 1                     , y = 2
        , y = 2                     }
        }
```

The record is at the same column with and without the comment. It used to be one
level deeper, because a comment-led `<|` body was handed to the broken-*call*
assembler, which puts item 0 on the opening row and the rest at +4 — right for a
function and its arguments, wrong for a comment and the operand it annotates.

**The test, operationally.** Render the same construct again with the comment
deleted and compare. gren disagreeing with its own comment-free rendering is a
bug; gren agreeing with itself while elm-format differs is a divergence. This
settles a layout question without consulting elm-format at all, and it found four
bugs across interview rounds 3–5 (see [What changed](#what-changed-interview-rounds-4-and-5)).

elm-format's opposite rule — a comment inside a construct forces every part of
that construct onto its own line — is [#23](elmFormatComparison.md#divergence-23);
[#14](elmFormatComparison.md#divergence-14),
[#16](elmFormatComparison.md#divergence-16) and [#17](elmFormatComparison.md#divergence-17)
are instances of the same disagreement.

### C5 — gren-format adds nothing around a comment

No blank line above or below **because of** the comment; no floating a comment out
onto a row of its own to give it air.
([#12](elmFormatComparison.md#divergence-12), [#18](elmFormatComparison.md#divergence-18),
[#25](elmFormatComparison.md#divergence-25))

```gren
-- you wrote, and gren-format keeps:     -- elm-format:
[ 1                                      [ 1
-- about two
, 2                                      -- about two
]                                        , 2
                                         ]
```

Two things that look like counter-examples and are not:

- **A blank line gren would have added anyway.** `let` bindings are separated by
  one forced blank line, and a comment leading a binding travels with it, so the
  blank lands above the comment. Delete the comment and the blank line is still
  there — it is the binding's, not the comment's, which is exactly C4's test.

  ```gren
  -- you wrote            -- gren-format
  let                     let
      x =                     x =
          1                       1
      -- about y
      y =                     -- about y
          2                   y =
  in                              2
                          in
  ```

  The blank line is between the two bindings, where it always is; the comment
  leads `y` and so ends up under it.
- **The `"""…"""` guard.** A comment that would glue onto the front of an item
  holding a multi-line string is put on its own row instead. Gluing shifts that
  item's first row by the comment's width while the string's content rows move by
  a different amount, and Gren requires every row of a multi-line string to be
  indented equally — so the alternative is output that does not parse. This is an
  output-validity carve-out, not air. (`subtreeHasMultilineString`, fixture
  `MultilineStringItemComment`.)

  ```gren
  afterComma =
      [ "first"
      {- leads the second item -}
      , """
        bravo
        """
      ]
  ```

### C6 — An own-line comment is indented to the code it leads

Not to a column of its own. ([#24](elmFormatComparison.md#divergence-24))

Precisely: to the column where the line it leads **begins**, which in a bracket
list, a union body or a record update is the `,`/`|` prefix's column, not the
item text's. In a flow — call arguments, `let` bindings, `when` bodies — the two
are the same thing:

```gren
someFunction              [ 1
    argOne                , [ 2
    -- about argTwo         -- about three
    argTwo                  , 3
                            ]
                          ]

{ rec                     type U
    -- about alpha            = A
    | alpha = 1               -- about B
}                             | B
```

Note the bracket and record cases: the comment is at the `,`/`|` column, two
columns left of `3` and of `alpha`. This is normalizing, not preserving — write
the comment over the item text and gren-format pulls it back to the prefix
column:

```gren
-- you wrote          -- gren-format
[ apple               [ apple
  -- about banana     -- about banana
, banana              , banana
]                     ]
```

A `Standalone` comment — one below a top-level declaration — detaching to column 1
is the same rule, not an exception: the declaration it leads begins at column 1.
It is spelled out separately under
[deliberate divergences](#deliberate-divergences-and-dead-ends-dont-fix-these)
because the *choice* being made there is to attach it to the declaration below
rather than to the one above.

### What changed (2026-08-01)

Two of the six did not hold when they were written down, and the code was changed
to match rather than the rules weakened to fit:

- **C2 at the record update's `|`.** The comment used to be canonicalized to its
  own line between the base and the fields, on the argument that both sides were
  claims the formatter could not support. That was a third answer to a two-sided
  question, and it cost C3 as well — a record update carrying such a comment
  could never stay on one line. A single-line `{- -}` now leads the first field.
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

### What changed (2026-08-02)

The `--` half of the same gap. C2's exception was written as a fact about lists;
reading the next round of verdicts it is a fact about **line-leading separators**,
and the record update's `|` was the only one not obeying it — it sent *both*
spellings to the field after the `|`, where `,` and a union's `|` had always kept
each spelling where it was written. It now keeps them too: a `--` or multi-line
`{- … -}` on the base's row trails the base (the new `TrailsHead` role), one on
its own row stays on its own row, rendered at the `|`'s column by the
`LeadsOwnLine` path that already served the fields.

This is not a return to the pre-2026-08-01 behaviour, which canonicalized *both*
spellings onto their own line. Two authorings, two outputs, each a fixed point.

**It was chosen knowing what it costs.** The old answer sent both same-row
spellings past the `|`, which happened to match elm-format on `{ rec | -- c` ⏎
`a = 1 }`; the new one matches elm-format on neither same-row spelling, because
elm-format renders `{ rec -- c` in a third way again (its own hanging column,
[#24](elmFormatComparison.md#divergence-24)). That is **600 comment-axis cells of
parity given up for zero gained** — measured, not estimated. 450 of them land as
new `UNREVIEWED` baseline debt (150 auto-classify); the net UNREVIEWED count barely
moved only because 475 unrelated cells left the bucket the same day. The trade was made
because one rule holding at `,`, at a union's `|` and at a record update's `|` is
worth more than parity on one spelling of one construct, and because the spelling
in question occurs nowhere in `core/`, `compiler-common/`, `compiler-node/` or
this repo, in any of its forms. If that judgement is ever revisited, the revert is
the record-update arm of `classifyCommentKind` plus the `TrailsHead` plumbing —
nothing else depends on it.

Also that day, unrelated to attachment: a comment that forces a binop chain to
break at an operator its precedence split would have kept inline now indents the
continuation `grenIndent`, like every other broken chain, instead of landing
flush under the seed (`one + two -- c` ⏎ `····* three`).

### What changed next: C4 in a binop chain

That indent fix left the *break point* wrong, which is the C4 half of the same
shape. A `--` mid-chain has to break the chain — but it was breaking it wherever
the comment sat, including at an operator **tighter** than one it then glued
across:

```gren
-- was:                        -- is:
one + two -- c                 one
    * three                        + two -- c
                                     * three
```

Delete the comment from the left-hand form and you get `one + two` ⏎ `* three`,
which is not gren-format's comment-free layout for that expression (`one + two *
three`, or a break at the `+` if one is forced) — a plain C4 violation, and one
that misreads as `(one + two) * three` besides.

`makeBinopBox` asked `NodeClassify.commentBreaksFlowRow` of **each operand on its
own**. That function's rule — a line-ending comment breaks the row only when a
real item actually follows — is right, and is why `foo bar -- c` breaks nothing.
But a comment at the end of a non-last operand has nothing after it *within that
operand*, so the chain looked unbroken, skipped the forced-vertical
(precedence-aware) renderer and fell through to a generic flow, which breaks at
the comment. `BinopLayout.commentBreaksBinopChain` asks the same question of the
whole chain, interleaving the operator leaves back between the operands. It is a
strict superset of the per-operand test, and reparse-stable: the comment is still
mid-chain in the output, so the second format decides the same way.

### What changed: interview rounds 4 and 5

Three more C4 violations, all the same shape as the binop one above — **gren's
comment path contradicting gren's own comment-free path**, with elm-format merely
agreeing with the rendering gren already produces when the comment is removed.
None of them needed elm-format to settle; each was decided by rendering the
construct twice.

- **A record field holding a lambda dropped its body 2 past the `{`, not 4**
  (`4c23e3d`). `renderGluedLambdaField` assembled the field flow with
  `assembleFlow False 0`, copied from `makePBox`'s `IndentedBlock` arm — where the
  0 is right because *there* the parent applies the indent. A field is a
  bracket-list **item** and must carry its own +4, as `renderFieldFlowWithValueBox`
  already did. The two only disagree when the field's flow breaks, and a comment in
  the head is the only thing that breaks it. Fixture
  `RecordLambdaFieldCommentIndent`.
- **A comment-led `<|` body landed a level too deep** (`0df8347`).
  `buildBackwardBodyBox` handed it to `assembleBrokenCall`, which puts item 0 on
  the opening row and everything after it at +4 — right for a function and its
  arguments, wrong for a comment and the operand it annotates. Leading comments are
  now peeled and stacked above the body at the body's own column.
- **A paren-wrapped lambda stopped lining up with its own `)`** (`0df8347`). The
  direct-operand lambda arm sizes its offsets (body +8, `)` +3) *from the
  operator*, so they hold only when the prefix **is** the operator — `|> ` / `<| `,
  three columns. A `{- c -}` on the running line widened the prefix, moving the `(`
  without moving the body or the `)`. Now gated on that width; a wider prefix falls
  through to the generic paren arm, which anchors every row at the paren's `(`
  column — already what a paren-wrapped `if`/`when`/binop does there, so the lambda
  joins them rather than gaining a rule. Fixture `PipeCommentOperandIndent`
  (quoted under C4); `LambdaCommentBuriedMixedPipeline` had **frozen** this bug,
  expecting `)` two columns left of its own `(`, and was regenerated.

Round 5 also found two `bug` verdicts that were **not** formatter bugs: both
reproduce with no comment present and are
[compiler-common#14](knownLimitations.md) (a continuation line at the same column
as the body above it ends that body early). The `--` only forces the body onto its
own row, which is what changes the parse. They are registered
`PENDING-UPSTREAM:<issue>`, a reason class that is printed on every matrix run like
`BUG:` but listed separately, since the work-list belongs to somebody else. It
needs no follow-up bookkeeping: when the upstream fix ships and the dependency is
bumped, the cells stop diverging and `parity-baseline-stale` fails until the entry
is removed.

The rounds 4–5 method is the same one C4 states, and it is the one that keeps
paying: **for any layout question, render the construct without the comment.**
Round 5 also produced one bookkeeping rule worth repeating — *do not invent a
reason string to close out a group*. A fully fixed group's cells leave the
baseline on `--update-baseline` and need no reason at all; a partly fixed group
still diverges and needs the real catalogue number. Registering either as `FIXED`
wrote a meaningless reason over six cells that were plain
[#25](elmFormatComparison.md#divergence-25).

### What changed: the `<|` a comment moved off its seed

The largest single family in the comment axis's remaining debt — 78 review
groups, 173 cells — was one C4 violation, and it was the *reverse* of a decision
already in the catalogue.

```gren
-- you wrote          -- was                  -- is, and is what the same
fn <| -- c            fn                      --     expression renders without
    one                   <| -- c             --     the comment, plus its row
                             one              fn <| -- c
                                                  one
```

`<|` is the one operator that does **not** lead its row: it stays glued to the
seed's last line, which is
[divergence #14](elmFormatComparison.md#divergence-14) — elm-format drops it
below a multi-line seed and gren-format deliberately does not. A comment,
though, sent the whole chain to the operator-LEADING layout, producing exactly
the shape #14 exists to reject, and one gren-format renders for no comment-free
input: both author spellings (`fn <|` ⏎ `body` and `fn` ⏎ `<| body`)
canonicalize to `fn <|` ⏎ `+4 body`.

The trigger was `stepNeedsCommentedLayout`, which routed a step carrying *any*
`--` or multi-line `{- … -}` to that layout. Two of its cases are real and stay:

- a comment written **before** the `<|` (`fn` ⏎ `-- note` ⏎ `<| 1`) — it has to
  sit above the operator, so the operator cannot be on the seed's row. This is
  the shape [#23](elmFormatComparison.md#divergence-23) and
  [#25](elmFormatComparison.md#divergence-25) illustrate, and it is byte-for-byte
  unchanged;
- a comment in a **non-last** step's body, whose `<|` for the *next* step is
  glued onto that body's last line by `backwardMultiStep` and would be swallowed.
  (This one turned out to be the same violation one step further in, and was
  narrowed again the next day — see
  [the `<|` a comment moved off a *later* seed](#what-changed-next-the--a-comment-moved-off-a-later-seed).)

Everything else keeps the trailing-operator layout. A comment the author wrote on
the operator's own row is peeled by `spanOperatorRowComments` and glued after the
`<|`, so `fn <| -- c` and `fn <|` ⏎ `-- c` stay two spellings with two outputs,
each a fixed point — the same shape C2's line-leading exception has at a `,`.

Two consequences worth knowing:

- **This does not buy parity.** elm-format floats such a comment down below
  `fn <|` regardless ([#12](elmFormatComparison.md#divergence-12) /
  [#25](elmFormatComparison.md#divergence-25)), so the cells still diverge — they
  are now correctly-shaped divergences instead of a bug frozen in the baseline.
- **It exposed a second, smaller C4 violation underneath.** With the chain no
  longer diverted, a comment-broken call reached `buildBackwardBodyBox`'s
  plain-flow arm, which passed a flow indent of `0` — so an argument sat flush
  under its own function (`gn -- c` ⏎ `arg`) where the identical call broken by
  the identical comment indents +4 anywhere else. The arm now passes `grenIndent`
  when `commentBreaksFlowRow` says the flow breaks.

Five fixtures had frozen the old layout and were regenerated
(`PipelineOperatorCommentIndent`, `ParenBlockTrailingComment`,
`BackwardPipeMultilineTrailingComment`, `MultilineStringCommentBinopPrecedence`,
`LambdaLeadingCommentDroppedBody`). Each new output was checked against the C4
test before being accepted, not merely against "it round-trips": three of them
now render byte-identically to the comment-free twin of the same expression, and
`LambdaLeadingCommentDroppedBody`'s first three declarations went **flat**,
because the author wrote them on one line and the only comment that could break
them (`-- trailing`) sits at the end of the flow, where C3 says nothing follows
it to push down.

### What changed next: the `<|` a comment moved off a *later* seed

The second of the two cases kept above was too wide by half, and the half it got
wrong was the same C4 violation one step further into the chain. It survived the
round that found the first because it looked like a *reason* rather than a
symptom: the next step's ` <|` really is glued onto this body's last line, and a
`--` ending that body really would swallow it. What did not follow is the remedy.
Diverting the whole chain to the operator-leading layout to protect one operator
moves **every** operator, including the seed's, over a comment nowhere near it:

```gren
-- you wrote          -- was                  -- is
fn <| fn -- c         fn                      fn <|
    <| one                <| fn -- c              fn -- c
                              <| one              <|
                                                      one
```

Read the right-hand column against `fn <| fn <| one` with the comment deleted
(`fn <|` ⏎ `fn <|` ⏎ `one`): every operand is at the column it has there. The
middle column moves all three. That is C4's test, and the middle column fails it
for the same reason and in the same direction as the version before it.

`backwardMultiStep` already had the move this wants. A body that renders as a
relocated broken call cannot take a glued ` <|` either, and that case drops the
operator onto a row of its own at the *body's* indent (`isBrokenCall`), leaving
everything below at its normal depth. Ending in a `--` is the same problem, so it
now takes the same exit, keyed on `subtreeEndsWithLineComment` of the body's last
node — the recursive query, which also closes a hazard the old shallow test had
noted and left open: a `--` buried deeper than a step's direct children was
invisible to it.

Only a `--` qualifies. `{- … -}` and `{-| … -}` self-terminate, so ` <|` glued
after `-}` is safe, and `alpha <| beta {- c -} <| gamma` keeps the plain chain
(fixture `midChainTrailingComment` / `midChainBlockComment` in
`BackwardPipeCommentNesting`).

The other half of that case — a comment **leading** a step's body rather than
ending it (`alpha <|` ⏎ `-- c` ⏎ `beta <| gamma`) — still routes to the
operator-leading layout, and has to for now: the trailing path lays a body out
with `assembleFlow`, which reads a row-breaking leading comment as a call's head
and puts the body +4 past it. That is the `leadingComment` fixture, and removing
the routing without fixing the assembler renders `beta` one level too deep.

**Since closed.** The `{- c -}` spelling was the C3 violation fixed in
[the `{- -}` that floated off an operator's row](#what-changed-next-the----that-floated-off-an-operators-row);
the `--` spelling was the last of this family, and it went the same way as the
body-ending `--` above — see
[the `--` in front of a later `<|`](#what-changed-last-the----in-front-of-a-later-).

### What changed next: the `{- -}` that opened a container for a lambda

A single-line `{- -}` never forces a break (C3), and one written before an array
item rode that item's row — before `one`, before `{ a = 1 }`, before `fn one`.
Before a **lambda** it broke the container open:

```gren
-- you wrote                       -- was                -- is
[ {- c -} \q -> q + one ]          [ {- c -}             [ {- c -} \q -> q + one ]
                                     \q -> q + one
                                   ]
```

The cause is a wrapper, not a lambda rule. A value made of several LPT nodes is
wrapped in a `BodyBlock`, and `Comments.gren` redirected every comment landing
before a `BodyBlock` *inside* it, as an own-line comment. That is right for the
one `BodyBlock` the arm was written for — a declaration's value, which always
starts its own row — and false for every other use, where `BodyBlock` is a plain
grouping wrapper renderered through `buildFlowBox` with no hard newline at all.
A lambda is simply the multi-node value common enough to notice; `one` and
`{ a = 1 }` stay bare children and never met the arm. The redirect is now gated
on the container actually being a declaration's (`isDeclValueContainer`), and on
the same `RidesInline` test the neighbouring `PipelineStep` arm already used.

Letting the comment out of the wrapper then exposed two render sites that had
never seen one there, and both are about the same thing: **where a comment may
ride depends on the rendered box, so the code that decides must be the code that
knows the box.**

- **`<|`.** `buildBackwardBodyBox` stacked leading comments above the body
  unconditionally. It cannot decide: the step renders `fn <| {- c -} \q -> …`
  when it stays inline and `fn <|` ⏎ `{- c -}` ⏎ `\q -> …` when it does not,
  and the second is not optional — with the `<|` on an earlier row the comment
  reparses as `LeadsOwnLine`, so gluing there oscillates. It now hands riding
  comments back as `ridingLeading` and the caller, which makes the
  inline/vertical call, places them.

- **Bracket items.** The prefix lands on the item's first row and deliberately
  does not shift its continuation rows, which is right when there are none and
  wrong when there are — it slides the first row out from under the rest of its
  own construct, so `[ {- c -} if cond then` leaves `else` back at the item
  column rather than under its `if`. Riding is now limited to a single-line item.

The bracket half took two corrections, both found by `fuzz-idempotency.py` and
both the same mistake — narrowing a rule past the case it was about:

- **Both bracket paths had to change together.** A comment in the opener slot
  classifies `RidesInline` on any row (it glues to the `[`, and nothing precedes
  it to compare rows with), so the flat path renders format 1 of a file and
  `commentBracketListBox` renders format 2, with no role to tell them apart. One
  riding while the other stacked was an oscillation with nothing to blame it on.

- **Only an OPENER run may stack.** A comment held for the item after a `,`
  (rule C2's `LeadsNext`) is rendered inside the caller's `, ` prefix, so
  stacking it emits `, {- c -}` ⏎ `  item` — which reparses as a comment written
  *before* the separator, a different comment with a different role. It must glue
  whatever the item looks like. The same is true of a record field, a record
  update's field and a union variant, which all render as `head` ⏎ indented tail:
  the prefix shifts the head row and nothing that has to stay under it. Both
  bracket folds now carry the opener-vs-separator distinction, and
  `pairInlineComments` takes the ride test from its caller rather than assuming
  one.

Fixture `LambdaLeadingBlockCommentRides`, each shape beside its comment-free twin.

### What changed next: the `{- -}` that floated off an operator's row

A single-line `{- -}` written in front of an operator rides that operator's row.
At a `++` it always had. At a `|>` and a `<|` it floated onto a row of its own —
rule C5, which says gren-format adds nothing around a comment and never gives one
a row to itself for air:

```gren
-- you wrote          -- `++` gave, and now all three do    -- `|>` and `<|` gave
head                  head                                  head
{- c -} ++ rest           {- c -} ++ rest                       {- c -}
                                                                ++ rest
```

The C4 test settles it without consulting elm-format: `head` ⏎ `{- c -} ++ rest`
and `items` ⏎ `{- c -} |> fn` are the same authoring of the same thing, and gren
already answered the first by gluing. (Both spellings — the comment on the
operator's row and on a row above it — collapse to the glued form, at all three
operators. They are not distinguishable in the output, exactly as at a
[line-leading separator](#c2--where-the-separator-has-no-source-position-the-comment-leads-what-follows-it).)

Three sites, one rule each:

- `|>` — `renderPipelineStepChildren` stacked leading comments and only the
  one-line pipeline form gilded them. It now uses the same test in both.
- `<|` — a leading comment used to route the whole chain to the operator-leading
  layout. A ridable one no longer does: `stepLeadBoxes` glues it onto the end of
  the line the operator trails, so `fn` ⏎ `{- c -} <| one` renders `fn {- c -} <|`
  ⏎ `one` — the comment-free layout with the comment on the row it was written on,
  and byte-identical to elm-format. A `--` or a multi-line `{- … -}` still routes
  as before, because neither can share the row.
- The all-or-nothing ride test is `commentTextCanRide`, not `joinInline`.
  **`joinInline` cannot decide it**: a `--`'s box is an ordinary single `Line`
  here, so `B.allSingles` accepts it and the join emits `-- c |> fn`, with the
  operator swallowed inside the comment — output that no longer parses to the
  same AST.

**This reverses a divergence that had been registered as intentional.**
`BackwardPipelineSeedComment` (`b934a74`, 2026-07-17) froze the floating layout
and its fixture title called it author-driven. It predates C1–C6; under C5 it is
the bug this entry fixes, and `++` had been contradicting it in the same repo the
whole time. Two other fixtures asserted the same shape in their own prose and
were corrected with it.

### What changed last: the `--` in front of a later `<|`

The final cell of the comment axis, and the last of the `<|` family: a comment
that cannot ride, written in front of a **later** step's `<|`, still sent the
whole chain operator-leading.

```gren
-- you wrote      -- was            -- is, = elm-format   -- with the comment
fn <| fn          fn                fn <|                 fn <|
-- c                  <| fn             fn                    fn <|
<| one                    -- c          -- c                      one
                          <| one        <|
                                            one
```

Which step the comment leads decides it, and the reason is where that step's
operator sits:

- **The first step's** operator is on the **seed's own row**. A `--` above it
  means the seed cannot keep it, and the whole chain really is operator-leading
  from the top — `fn` ⏎ `-- c` ⏎ `<| one`, which is what the author wrote and
  what [#23](elmFormatComparison.md#divergence-23) records. Unchanged.
- **A later step's** operator is on the previous *body's* row. Dropping that one
  operator to a row of its own is enough, and it is the move `backwardMultiStep`
  already makes for a relocated broken call and for a body ending in a `--`. The
  comment takes the rows above it, at the previous body's indent, and every
  operand keeps the column the comment-free chain gives it — read the right-hand
  column above against the far right one.

Same shape as [the mid-chain `--`](#what-changed-next-the--a-comment-moved-off-a-later-seed)
and the same mistake: a real local hazard answered by moving every operator in
the chain instead of the one that was blocked. Fixtures
`lineCommentBeforeSecondOperator` / `leadingCommentFirstStep` in
`BackwardPipeCommentNesting`.

**The comment axis has no UNREVIEWED cells left.**

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
the pristine parse rows and read verbatim by the renderer. All seven, which is
the complete set — `CommentRole`'s docstring in
`Formatter.Logical.LogicalPrintingTree` is the authority if this table ever
drifts from it:

| Role | Meaning | Renders as |
|---|---|---|
| `TrailsPrevious` | glues onto the end of the previous sibling's last line | `<prev last line> <comment>` |
| `LeadsOwnLine` | stands on its own line at the flow/body indent, before the next sibling | own line |
| `LeadsNext` | belongs to the sibling that *follows*, across a separator with no position — rule **C2** | glued to the front of that sibling's box, inside the `,`/`\|` prefix |
| `TrailsHead` | glues onto the container's **head**, which is not one of its children — today only a record update's base (`{ rec -- c`) | `<head's line> <comment>` |
| `RidesInline` | a single-line `{- -}` riding mid-flow without breaking (`f {- k -} x`) | mid-line, inline |
| `LeadsInline` | the front-of-line mirror of `TrailsPrevious`: a block comment glued before a declaration's first token (`{- c -} import Qux`) — it travels with the declaration, so it never breaks an import run | glued to the front of the declaration's first line |
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
- Fixtures: spread across `tests/testfiles/` (one directory per suite) —
  `BinopChainMixedComments`, `LiteralInlineComment`, `BetweenWhenBranchesComment`,
  `LetInTrailingComment`, `MultilineBlockComments`, `AdjacentTopLevelComments`, …).
