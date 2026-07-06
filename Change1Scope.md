# Change 1 — replace `Render.Doc` with elm-format's Box model: scope

## Why

Bucket A of the elm-format audit (the `)`-anchor after `|>`/`+`, the `&&`/`==`
branch offsets, `-(` negation) all have one cause: **elm-format aligns a
group's continuation/closing lines to the *exact character width* of its
opening delimiter (`prefix`), gren aligns to a *uniform multiple of 4*
(`nest`).** Change 2 showed we can hand-match individual cases (record update),
but each fix is bespoke arithmetic and the nested compositions keep producing
off-by-a-column bugs (B3 was exactly this). Change 1 removes the whole class by
running elm's actual two-primitive layout instead of approximating it.

**This requires reversing the earlier "uniform-4 over width-dependent
indentation" preference** (see the paren-block decision). That is the gating
product decision, not a technical one.

## The model mismatch (why there's no cheap wrapper)

`Formatter.Render.Doc` is a free-form IR: `Text`, `Concat` (163 call sites),
`Nest`, `Align`, `Reset`, `Group`, soft breaks. Horizontal composition is an
unrestricted `concat` of any two docs, including multi-line ones.

elm-format's `Box` (`elm-format/elm-format-lib/src/Box.hs`, ~310 lines) is a
**line-stack**: a box is `SingleLine`, `Stack` (2+ lines), or `MustBreak`.
There is **no general horizontal concat of two multi-line boxes** — horizontal
joins go through `prefix` (prepend to line 1, pad the rest by the prefix's
exact width) and `addSuffix` (append to the last line). Indentation is two
primitives: `Tab` (advance to the next multiple of 4 — a real tab stop, *not*
fixed +4) and the width-padding inside `prefix`.

Because gren's pervasive `concat` has no Box equivalent, `Doc` cannot be
reimplemented as a thin shim over `Box`. The producers must be rewritten to
think in Box's join discipline (`prefix`/`addSuffix`/`stack1`), not free
concat. That rewrite *is* Change 1.

## Surface to change

- **`src/Formatter/Render/Doc.gren`** (~309 lines) → replaced by `Box.gren`
  (`Line` = Text/Row/Space/Tab; `Box` = SingleLine/Stack/MustBreak; combinators
  `indent`, `prefix`, `addSuffix`, `stack1`, `render`). Direct port of `Box.hs`.
- **`src/Formatter/Render/MakeRender.gren`** (4160 lines, ~50 functions
  returning `Result String Doc`, ~500 `R.*` call sites) → rewrite every producer
  to build Box. This is the bulk of the work.
- **The ElmStructure combinator layer** (the Change-2 direction): build ~8
  combinators (`group'`, `extensionGroup`, `equalsPair`, `application`,
  `spaceSepOrPrefix`, `prefixOrIndented`, …) on Box, and map each construct to
  one. Change 1 and the rest of Change 2 converge here.

## Primitive mapping

| Doc (today) | Box (target) | Notes |
|---|---|---|
| `Text s` | `line (literal s)` | |
| `concat a b` (both 1-line) | `row [a, b]` | trivial |
| `concat a b` (b multi-line) | `prefix`/`addSuffix` | **the hard rewrite** — no free concat |
| `nest 4 d` | `indent` (`Tab`) | fixed +4 → tab-stop (next mult of 4) |
| `align d` | `prefix` anchor | already ≈ prefix; becomes structural |
| `reset d` (1 site) | n/a | re-express or drop |
| `group d` (16 sites) | structural | "flat" = keep as `SingleLine`; no flat-forcing op needed since we decide vertical up front |
| `hasHardBreak d` (5 sites) | `isMultiline` (`isLine`/`allSingles`) | **free simplification** — verticality is structural in Box |
| soft `nl`/`breakDoc` | delete | dead page-width machinery |
| `docToJson` (`--render-doc`) | new Box encoder or drop the flag | debug-only |

## Hard parts / risks

1. **`concat`-of-multiline → prefix/addSuffix.** The 163 `concat` sites must be
   triaged: single-line joins are trivial `row`; any join where the right side
   can be multi-line must become `prefix`/`addSuffix`. This is the main effort
   and the main risk.
2. **Author-driven, not fit-driven.** Box's shape is structural (SingleLine vs
   Stack). We must decide flat-vs-vertical *up front* from `forceVertical` +
   the render-time content check (exactly the `anyChildForcesVertical` pattern
   from Change 2) and emit the right Box shape. Do **not** import elm's
   width-based fit search.
3. **Comments.** elm uses `MustBreak` for `--` comments; gren's comment
   placement is hard-won (see the comment-attachment memories). Map carefully;
   fuzz heavily.
4. **`MultilineString`, `EmptyLine`, `SynthesizedText`** — small dedicated ports.
5. **Corpus re-baseline.** Indentation shifts across ~140 fixtures (tab-stop +
   prefix-width). Mechanical but every `.formatted` needs regen + human review;
   AST-equivalence and idempotency gates must stay green.

## Recommended migration: strangler, not big-bang

A Doc↔Box bridge at nesting boundaries would reintroduce the arithmetic
mismatch, so incremental *within one renderer* is not clean. Instead run **two
renderers side by side**:

1. Port `Box.gren` (small, faithful to `Box.hs`).
2. Add `MakeRenderBox.gren` behind the same `makePrettyLineDoc`/`makePrettyLine`
   boundary, selected by a flag (e.g. `--render-doc`-style or an env toggle).
   `Doc` stays the shipping default.
3. Migrate construct-by-construct in the Box renderer, **starting with record
   update** — directly compare the Box output against the Change-2 hand-tuned
   version to prove the Box version needs no bespoke arithmetic. Then binops
   (`spaceSepOrPrefix`), parens (the `)` anchor), lists (`group'`), negation.
4. When the Box renderer matches on the full gate suite **and** the elm-format
   audit, cut over and delete `Doc.gren` + the Doc path.

Each slice is gated by: effectful suite (AST-equivalence + idempotency),
idempotency fuzzer, whitespace fuzzer (both modes), and the
`scratchpad/audit/` elm-format diff for that construct.

## First slice (proposed)

Record update in Box, compared against the just-landed Change-2 output. Success
= byte-identical to elm-format for `if`/`let`/multi-field **with no hand-tuned
`fieldLine`/`R.align` arithmetic** — the Box `extensionGroup` + `Tab`/`prefix`
should produce it structurally. That single comparison is the go/no-go signal
for whether Change 1 pays for itself.

## Rough sizing

- `Box.gren` port: small (mirror ~310-line `Box.hs`).
- `MakeRenderBox.gren`: large — the 4160-line producer rewrite is 80% of the
  effort, dominated by the concat→prefix/addSuffix triage.
- Fixture re-baseline: medium, mechanical, needs review.
- De-risked by the strangler approach and the existing gate suite.
