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

Updated again 2026-07-18 (second session): **the real-corpus sweep (avenue #1)
was run** against 10 published packages and found **9 real bugs in 5 classes**
(A–E, see `scan.md`) — the single most productive method to date, more than the
matrix and both fuzzers combined. All 5 are now fixed with fixtures, and the
sweep was turned into a repeatable gate (`tests/corpus-check.py`). The bugs it
found were *conjunctions of features* that every single-axis synthetic tool
missed by construction; see "Why the synthetic gates missed these" below.

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
- **`tests/corpus-check.py` — the real-corpus sweep.** Runs `--show` over every
  `.gren` file in a tree of real published packages and buckets each failure
  (crash / AST-mismatch / non-idempotent / out-of-scope parse). `--show`
  internally does parse → format → reparse → AST-compare → format-again →
  idempotency-compare, so one clean exit per file buys no-crash + meaning-
  preserved + idempotent + reparses. Run 2026-07-18 against 10 published
  packages (`~/prj/gren-format-preview/pkgs`, list in
  `pkgs/format_failed.txt`); the 10 failures minimized to **5 fix classes**
  (`scan.md`): A multi-line-string content corruption (trailing-whitespace
  strip + quote over-escape), B a signature record-type crash, C/D two
  non-idempotencies, E a soft-glue-after-block crash. This found more real bugs
  in one run than any other tool. **Scope it to package `src/`+`tests/`** — the
  `examples/` dirs in that corpus are old-Gren-version syntax the current parser
  rejects (out-of-scope `FAILED TO PARSE`, not formatter bugs). Rebuild the app
  first; it shells out to `../gren-format/app`.
- **Manual elm-format diffing** (documented in the root `CLAUDE.md`) —
  mechanically translate a body of real Gren source to Elm syntax, diff
  `elm-format` output against `gren format --show` output. Only ever run
  once, on `compiler-common/src`. (The `corpus-check.py` sweep is the
  crash/AST/idempotency half of this made repeatable; the elm-format *layout*
  diff still needs the manual translation, and `matrix-syntax.py`'s oracle 4 is
  the automated version for generated syntax.)
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

## Why the synthetic gates missed these (2026-07-18 scan)

Every A–E bug was a **conjunction of features**, and each synthetic gate varies
exactly ONE axis over a fixed base — so none of them could reach the combination:

- `matrix-syntax.py` embeds ONE construct in ONE context (now in flat/broken
  variants), but it never *repeats* a multi-line child (class E needed a call
  with **three** multi-line block args), never reaches a type/signature
  author-broken variant (class B needed a broken record type at an arrow
  boundary), and explicitly excludes multi-line strings (class A).
- `fuzz-idempotency.py` perturbs *comments* over the fixed corpus; it never
  creates new nesting depth or new construct combinations, so it couldn't build
  "pipe-to-record-arg then else-if" (D) or "binop with a commented bracket
  operand" (C) unless a fixture already had that shape.
- `fuzz-whitespace.py` perturbs whitespace, not structure or literal content.
- `audit-predicates.py` checks predicate/renderer agreement — it can't see a bug
  where the predicate and renderer *agree on a wrong answer* (D's row math was
  wrong in both the LPT flag and the layout).

Real source varies many axes at once, which is why one corpus sweep out-earned
all four. The lesson isn't "the synthetic gates are weak" — each is exhaustive on
its axis — it's that **feature co-occurrence is its own axis**, and only real
code (or random generation, #3) samples it. Concretely, the cheap follow-ups
that would have caught specific classes ahead of the sweep:

- **Literal-content preservation fuzzer** (would catch A). Mutate string / char /
  number literal *content* — trailing whitespace, embedded quotes, `"""` runs,
  control chars, unicode — and assert the reparsed AST *value* is unchanged.
  Nothing today mutates inside a literal; the AST-compare gate only fires if a
  fixture already carries the tricky content.
- **Repetition / arity in the matrix** (would catch E). Generate each
  multi-line construct as a call's 2nd and 3rd argument, and as the 2nd/3rd
  item of a container, so "block after block" is exercised.
- **Author-broken types & signatures in the matrix** (would catch B). The
  broken variant only covers expression atoms; extend it to record *types* in
  argument, return, and mid-arrow positions.

## Untried avenues

1. **Boundary/pathological inputs.** Deeply nested parens/records, very long
   identifiers, files that are all comments, empty modules, CRLF line
   endings, unicode in strings/identifiers, huge single-line inputs. The one
   performance bug found historically (an `O(2^depth)` render hang in
   `Box.gren`'s `renderRowState`) came from exactly this category — nothing
   else in the toolbox stress-tests structural depth.

2. **Random AST generation (property-based).** Everything above walks a
   fixed/enumerated space. A generator that builds random-but-valid small
   ASTs (bounded depth) and checks the three standing invariants (parses,
   idempotent, AST-equivalent to the original) would explore combinations
   nobody thought to write by hand — a step up from the matrix's fixed grid.
   This is the avenue that most directly targets the **feature-co-occurrence**
   axis the 2026-07-18 scan proved matters (see above) without depending on
   which real packages happen to exist.

3. **Complexity-guided review.** `assembleFlowImpl`, `MakeRenderBox.gren`
   generally, and the paren-block tab-stop machinery are the densest,
   most-patched code in the repo — most historical bugs came from there. A
   targeted close-read of the remaining unaudited dense functions (as
   opposed to a comment-driven pass) is a different lens on the same
   territory.

## Recommendation

The real-corpus sweep (formerly untried #1) is **done and paid off biggest** —
9 bugs in 5 classes, all fixed, and now a repeatable gate (`corpus-check.py`).
Re-run it on any fresh batch of published packages: it is the cheapest way to
find real bugs, because someone else already wrote the tricky code. Scope it to
`src/`+`tests/` (examples are old-Gren parser gaps).

Two next steps, in order of leverage:

1. **Property-based random AST generation** (untried #2) — now the top priority.
   The scan proved the productive axis is *feature co-occurrence*, and this is
   the only avenue that samples it independent of which packages exist. The
   three cheap targeted fuzzers listed under "Why the synthetic gates missed
   these" (literal-content preservation, matrix arity/repetition, matrix broken
   types/signatures) are the low-effort down-payments — each closes exactly one
   of the classes A/E/B against future regressions.

2. Keep the corpus sweep in rotation as new packages publish; keep the
   author-broken matrix and both fuzzers as the fast per-change gate.
