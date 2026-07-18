# Methods for finding bugs in gren-format-lib

A survey of techniques for finding formatter bugs, split by what's already
built and run versus what's genuinely untried. Written 2026-07-15, after a
session that found two real bugs (backward-`<|`-pipeline cascading indent,
type-alias multi-line function type) by chasing stale/misleading source
comments rather than through any of the tools below — comment auditing is
itself a method, just not a repeatable script. Updated 2026-07-18: dogfooding
and coverage-gap analysis moved to "already run" (dogfooding found a real
crash; coverage-gap analysis became the `gren-coverage-node` repo), and the
author-broken syntax matrix extension is now **done** — 850 → 1738 cells,
4 real bugs found and fixed, 0 UNREVIEWED, 0 known BUGs remaining (see
`gren-format-lib/CLAUDE.md` for the full writeup).

## Already built and run

- **`tests/matrix-syntax.py`** — a construct×context grid, in up to four
  layout variants (`flat`/`broken`/`bareFlat`/`bareBroken`, 850 → 1738 cells),
  plus an elm-format parity oracle gated on `tests/matrix-parity-baseline.json`.
  Exhaustive over *known* syntax shapes, and — since the 2026-07-18
  author-broken extension — over both flat and pre-broken layouts, including
  bare (un-parenthesized) value positions. That extension found and fixed 4
  real bugs (lambda-body over-indent in array/nested-lambda positions, `let`
  as a `<|` body over-indenting its `in`, a multi-line container operand
  dropping below a dangling `|>`, bare `if`/`let` as an array item
  over-indenting) — all the same class, an extra `AcrossOrVertical`
  item-wrapper stacking its own +4 on a block's own +4. Current state:
  1738/1738 pass oracles 1–3, 0 UNREVIEWED, 0 known BUGs in the parity
  baseline.
- **`tests/fuzz-idempotency.py`** — inserts a block comment into every
  inter-token gap of every fixture, formats twice, requires byte-identical
  output. Catches "comment shifts on reparse."
- **`tests/fuzz-whitespace.py`** — perturbs inter-token whitespace (stretch /
  indent modes) and requires `format(perturbed) == format(original)`.
- **`tests/audit-predicates.py`** — checks every layout predicate in
  `Render/NodeClassify.gren` against the actual renderer output. The only
  gate that catches a predicate answering "forces vertical" when the real
  Box output doesn't, or vice versa.
- **Manual elm-format diffing** (documented in the root `CLAUDE.md`) —
  mechanically translate a body of real Gren source to Elm syntax, diff
  `elm-format` output against `gren format --show` output. Only ever run
  once, on `compiler-common/src`.
- **Comment auditing** — grep source comments for stale terminology
  (deleted modules/flags, old architecture references), TODO-style markers,
  hedging language, and "fall back"/"not ported" claims, then empirically
  verify each one against the current build. Slow, manual, but it's what
  found the 2026-07-15 session's two real bugs — the mechanisms it caught
  were never wrong enough to fail a fixture, only wrong enough to mislead a
  future reader into re-deriving the same mistake.
- **Self-hosting / dogfooding** — run `gren-format` over its own source
  (`gren-format-lib/src/**/*.gren`, the no-argument in-place run) and check
  no crashes + idempotency. Run 2026-07-18 and it *immediately* found a real
  crash: a record **literal** field whose value is a multi-line binop chain
  (`NodeClassify.gren`'s `signatureForceVertical`, `{ broken = acc.broken ||
  (...) }` across rows) hit an "unreachable" flow-assembler arm. The record
  **update** path already dropped such a value below `name =` (elm-format's
  `equalsPair` rule); the literal path rendered each field through bare
  `makePBox` and had no drop rule, so the two `name = value` paths had
  drifted. Fixed by sharing one field renderer between them. This is exactly
  the dogfooding payoff — real, large, organically-varied code is a different
  distribution than hand-written fixtures, and it exercised a three-way
  conjunction (record-literal + bare value + author-broken) that no fixture
  and no flat matrix cell had.
- **Coverage-gap analysis** — enumerate the reachable arms nothing currently
  tests. Built as the sibling `gren-coverage-node` repo: Gren line/region
  coverage from V8 coverage + sourcemaps, joined against the AST; run via the
  effectful suite's `run-tests.sh --coverage`, outputs `out/` (json + lcov).
  This drove the Tier 1/2/3 coverage-fixture work (added
  `HexLiteralDigits` / `RecordUpdateQualifiedBase` / `MultilineStringControlChars`,
  92.40 → 92.69%), which in turn flagged dead code (an `InsertExpressions`
  flatten branch, a `FlowPolicy` `WhenBranchItem` arm) and surfaced a still-
  unfixed latent non-idempotency (an indented `--` trailing a container at
  declaration end oscillates col 4 → 0). This is the complement of the
  `box-err.md` audit, which proved certain `Err` arms *unreachable*; this
  asks which reachable arms are untested.

## Untried avenues

1. **Broader real-corpus sweep.** The elm-format comparison audit has only
   ever been run on `compiler-common/src`. Running it over `core/src`,
   `compiler-node/src`, and `compiler/src` (translate each to Elm, diff)
   would surface divergences on code nobody wrote as a test case.

2. **Boundary/pathological inputs.** Deeply nested parens/records, very long
   identifiers, files that are all comments, empty modules, CRLF line
   endings, unicode in strings/identifiers, huge single-line inputs. The one
   performance bug found historically (an `O(2^depth)` render hang in
   `Box.gren`'s `renderRowState`) came from exactly this category — nothing
   else in the toolbox stress-tests structural depth.

3. **Random AST generation (property-based).** Everything above walks a
   fixed/enumerated space. A generator that builds random-but-valid small
   ASTs (bounded depth) and checks the three standing invariants (parses,
   idempotent, AST-equivalent to the original) would explore combinations
   nobody thought to write by hand — a step up from the matrix's fixed grid.

4. **Complexity-guided review.** `assembleFlowImpl`, `MakeRenderBox.gren`
   generally, and the paren-block tab-stop machinery are the densest,
   most-patched code in the repo — most historical bugs came from there. A
   targeted close-read of the remaining unaudited dense functions (as
   opposed to a comment-driven pass) is a different lens on the same
   territory.

## Recommendation

The author-broken syntax matrix (formerly #1 here) is **done** — it directly
targeted the exact bug class found three times (twice on 2026-07-15, once by
dogfooding on 2026-07-18), found 4 more real instances of it, and closed out
at 0 UNREVIEWED / 0 known BUGs.

The cheapest next win is **#1, the broader real-corpus sweep** (the
elm-format method already exists; only the translation is manual), and the
bigger step up is **#3, property-based random AST generation**, which is the
only avenue that leaves the fixed/enumerated space entirely.
