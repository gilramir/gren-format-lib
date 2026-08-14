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
4 real bugs found and fixed, 0 UNREVIEWED, 0 known BUGs remaining.

Updated again 2026-07-18 (second session): **the real-corpus sweep (avenue #1)
was run** against 10 published packages and found **9 real bugs in 5 classes**
(A–E, see `scan.md`) — the single most productive method to date, more than the
matrix and both fuzzers combined. All 5 are now fixed with fixtures, and the
sweep was turned into a repeatable gate (`tests/corpus-check.py`). The bugs it
found were *conjunctions of features* that every single-axis synthetic tool
missed by construction; see "Why the synthetic gates missed these" below.

Updated again 2026-07-18 (third session): **the property-based random AST
generator (avenue #2) was built** — `tests/gen-random.py`, spec in
`tests/GENERATOR.md`. It samples the feature-co-occurrence axis directly, without
depending on which real packages exist, and it paid off immediately: on the
freshly-built app it found **two real bug classes** — a soft-glue crash on a
record-*update* field holding a multi-line binop chain, and the trailing-`--`-at-
declaration-end col-4→0 non-idempotency (the latent bug flagged unfixed in the
coverage-fixture work). Both are now fixed with promoted fixtures. It is now the
only gate that varies *structure* (not just comments/whitespace over a fixed
corpus), so it is a per-change gate for the co-occurrence axis.

Updated again 2026-07-30: **avenue #1 (boundary/pathological inputs) is now
fully done, both halves.** Nesting depth was closed 2026-07-29
(`tests/pathological-nesting.py`); this session closed the non-depth half
(`tests/pathological-other.py`) — long identifiers/strings/comments, wide
lists/records/modules/comment-runs, empty modules, all-comment files, CRLF,
unicode — finding a real CRLF doc-comment bug and 6 `O(n^2)` performance bugs
(all fixed; see "Already built and run" below and the README's "Performance"
section). Avenue #3 (complexity-guided review) is now the only genuinely
untried avenue left in this file.

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
- **`tests/gen-random.py` — the property-based random AST generator** (avenue #2,
  built 2026-07-18; spec in `tests/GENERATOR.md`). Builds random-but-legal Gren
  modules with bounded depth — structure *and* comments — and checks four
  oracles per module: parses (`--pre-ast`; a failure is a generator bug, walled
  off in `quarantine/`, never a formatter find), no-crash + AST-equiv +
  idempotent + reparses (`--show`, one call), and comment preservation (multiset
  of `(type, normText)` from `--pre-context` on input vs. formatted, positions
  discarded — catches drop/dup/invent/kind-change, which AST-compare is blind to
  and idempotency only catches on a *shift*). Layout decisions are baked into the
  node tree so emission is pure: `--seed` replays exactly and the shrinker
  (tree-surgery + deterministic re-emit) minimizes every failure. Artifacts land
  in gitignored `tests/gen-out/run-NNNNNN/` (failures-only, bucketed,
  self-contained `report.txt`); `--promote <seed> --name Foo` turns a fixed find
  into a fixture. This is the ONLY gate that varies structure rather than
  perturbing comments/whitespace over a fixed corpus, so it reaches
  co-occurrences no corpus happens to contain. Rebuild the app first; it shells
  out to `../gren-format/app`. Found two real bugs on its first run (both fixed).
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
- **`tests/fuzzrun.py` — long unattended sweeps of `gen-random.py`.** Three lanes
  (`dense-comments` depth 6 / comments 0.70, `deep-structure` depth 8 / no
  comments, `default-mix` depth 5 / comments 0.25), run in ~10-minute chunks
  across many sessions from 2026-07-25 through 2026-07-29. On the current
  grammar generation (`ae6869c`, unchanged since 2026-07-26) this covered
  **1,416,241 seeds over 33h26m of CPU-wall** with **zero real bugs found** —
  the earlier grammar generations before that (also fully clean; their one
  recorded finding was fixed same-generation, see below) add another ~503,000
  seeds, for **~1.92 million seeds total** since the harness was built, spanning
  more than 3 calendar days of intermittent sweeping. One non-idempotency was
  found and fixed during generation 2 (`d2caabf`); the only other recorded
  failure is `stale-grammar` (its seed no longer generates the module that
  failed it once the grammar changed) and was never re-confirmed as live. This
  is the deepest single soak this codebase has had: the co-occurrence axis
  (avenue #2) is now sampled at a scale an interactive session can't reach, and
  it came back clean. Re-run with `./fuzzrun.py run --for <duration>`; check
  `./fuzzrun.py status` for lane coverage and `./fuzzrun.py failures -v` for
  anything open. A find here is worth more than a find anywhere else in this
  list, precisely because everything cheaper has already had its shot.
- **`tests/pathological-nesting.py` — boundary/pathological inputs (avenue #1,
  see "Untried avenues" below).** For 8 nesting shapes (parens, list, record,
  lambda, if-chain, unary-minus, binop-chain, pipeline-chain) it geometrically
  grows + bisects to find the exact depth where `--show` breaks, then
  independently bisects `--pre-ast`'s own boundary to tell a parser-level
  ceiling (out of scope — `compiler-common` is frozen) from a
  formatter-introduced one (in scope). First run (2026-07-29) found 3 real
  bugs, all since triaged:
  - **Record-literal nesting, `O(2^depth)` render hang — FIXED** (`15da4a8`).
    `renderRecordFieldBox` rendered a field's value once to decide whether it
    drops onto its own line, then again for real; each nesting level doubled
    the cost of the level below (depth 22 took ~21s). Restructured to render
    the value once and thread the box through both the decision and whichever
    layout it resolves to. Depth 400 now formats in ~0.25s; the shape's
    ceiling is now the parser's own limit (~480), same as every other shape
    below. Same failure class as the historical `Box.gren` `renderRowState`
    hang, a different site.
  - **Lambda nesting, stack overflow ~depth 400-404 — accepted, not fixed.**
    Plain single-descent recursion (`assembleFlowImpl → factsFor →
    softBlockChildForcesVerticalBox`), not a duplication bug — confirmed
    `softBlockChildForcesVerticalBox` reads the already-rendered box, it does
    not re-render. The render pipeline's call chain is ~10-15 JS frames per
    nesting level (vs. the parser's shorter chain, hence its higher ~700+
    tolerance), so Node's default stack runs out around depth 400. A real fix
    means trampolining the recursive-descent renderer's mutually-recursive
    core — large and invasive for a depth no real code has ever approached.
    Fails safely (a clean crash, not a hang or silent corruption).
  - **Unary-minus nesting, stack overflow ~depth 306 — accepted, not fixed.**
    Same root cause and same call: `makePBox → renderFlowItem →
    makeParenBlockBox → parenGenericFallbackBox → parenLambdaMultiline`.
  Re-run: `./pathological-nesting.py` (all 8 shapes) or `--shape record` /
  `--shape lambda` / `--shape unaryminus` for one.
- **`tests/pathological-other.py` — the non-depth half of avenue #1** (long
  identifiers/strings/comments, wide lists/records/modules/comment-runs,
  empty modules, all-comment files, CRLF, unicode identifiers/strings — the
  shapes `pathological-nesting.py`'s depth-only bisection can't reach). Two
  probe kinds: geometric-growth + bisection size sweeps (`long-identifier`,
  `long-string`, `long-comment`, `wide-list`, `wide-record`, `wide-module`,
  `wide-comments-only`) and one-shot scenarios (`empty-module`,
  `all-comment-file`, `crlf-corpus`, `unicode-identifiers`,
  `unicode-strings`). First run (2026-07-30) found two real bug classes, both
  fixed:
  - **CRLF doc-comment leak.** A `{-| … -}` doc comment's body is emitted as
    one opaque literal (unlike a plain block comment, which reconstructs
    line-by-line and incidentally strips a stray `\r`), so a CRLF-encoded
    file's embedded `\r`s survived verbatim into formatted output. Fixed at
    the source-read boundary (`gren-format/src/Format.gren`'s `readSource`
    normalizes `\r\n`→`\n` right after UTF-8 decode) plus defense-in-depth in
    the doc-comment renderer.
  - **Six `O(n^2)` perf bugs**, found via the `wide-module` /
    `wide-comments-only` size sweeps timing out well short of any realistic
    file size. All shared the same shape — rebuilding or rescanning the
    *entire* array of already-processed declarations/nodes/comments once per
    new one, instead of accumulating with `Array.Builder` or a monotonic
    cursor — across `MakeLogical.gren` (`addRootChild`), `VerticalSpace.gren`
    (6 functions), `SortSymbols.gren` (`walkTop` and its helpers, in two
    rounds — the accumulator, then a deeper fix so a long chain of
    non-import comments is bulk-skipped instead of rescanned from every
    position inside it), and `Comments.gren` (`lptAddComments`, rewritten
    around a settled/target/rest cursor). All fixed and verified against the
    full gate suite plus a 3000-seed `gen-random.py` sweep (the comment-
    placement-specific oracle) for the `Comments.gren` change specifically.
    Representative before/after numbers are in the README's "Performance"
    section. No open items remain in this avenue.
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

1. ~~**Boundary/pathological inputs.**~~ **Done, both halves** — built as
   `tests/pathological-nesting.py` (nesting depth) and
   `tests/pathological-other.py` (everything else) (see "Already built and
   run" above). Nesting depth: 8 shapes, geometric-growth + bisection,
   parser-ceiling vs. formatter-ceiling split, found 3 bugs (1 fixed — the
   record-literal `O(2^depth)` hang was exactly the failure class this avenue
   was chasing; 2 accepted as documented stack-depth limits, unreachable at
   realistic depth). Non-depth shapes (long identifiers/strings/comments,
   wide lists/records/modules/comment-runs, empty modules, all-comment
   files, CRLF, unicode) — done 2026-07-30: found a real CRLF doc-comment bug
   (fixed) and, via the `wide-module`/`wide-comments-only` size sweeps, 6
   distinct `O(n^2)` perf bugs (all fixed; see "Already built and run"
   above and the README's "Performance" section). No open items remain in
   this avenue.

2. ~~**Random AST generation (property-based).**~~ **Done** — built as
   `tests/gen-random.py` (see "Already built and run" above). Remaining work is
   grammar expansion (type aliases/unions/ports, author-broken types,
   multiline-string content mutation, doc comments, richer patterns), tracked in
   `tests/GENERATOR.md`.

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

Updated 2026-07-29: the multi-day `fuzzrun.py` soak (~1.92 million seeds, 3+
calendar days, see "Already built and run" above) came back clean on the
current grammar generation. That doesn't mean the well is dry — it means the
co-occurrence axis at the current grammar's depth/comment-density settings is
now well-covered, and the *marginal* next seed is unlikely to find anything the
last million didn't. The leverage has shifted:

Updated 2026-07-30: avenue #1's non-depth cases are **done** too — see
"Already built and run" above and "Untried avenues" item 1. Both halves of
avenue #1 are now closed. That leaves avenue #3 (complexity-guided review) as
the only genuinely untried avenue in this file; everything else is either
done or an ongoing-maintenance gate to re-run.

1. ~~**Grow `gen-random.py`'s grammar.**~~ **Stale as of this writing — already
   done.** The priority list this item used to carry (type aliases/unions/ports,
   author-broken types & signatures, multiline-string literal-content mutation,
   doc comments, richer patterns) was closed by v1.1–v1.5 (2026-07-19), and the
   grammar kept growing well past it — qualified refs, extensible records,
   infix decls, effect modules, as-patterns, hex/scientific literals, import/
   exposing comment edge cases, named wildcards, mismatched `port module`
   headers — through **v1.31** (2026-07-26), each addition verified at 0
   quarantine. See `tests/GENERATOR.md` for the full version history. There is
   no known open grammar gap right now; the next grammar addition is whatever
   new Gren syntax lands, not a backlog item.
2. ~~**Avenue #1 (boundary/pathological inputs), non-depth cases.**~~ **Done**
   as of 2026-07-30 (see "Already built and run" above and "Untried avenues"
   item 1). Both halves of this avenue found *performance* bugs rather than
   correctness ones — the historical `Box.gren` hang, the 2026-07-30
   record-literal `O(2^depth)` hang, and 6 more `O(n^2)` sites from the
   non-depth sweep — suggesting this avenue probes a different failure mode
   than the co-occurrence-sampling tools (matrix, both fuzzers, the
   generator). Worth remembering if a future grammar addition or large
   refactor reopens this axis; nothing open right now.
3. **Avenue #3 (complexity-guided review) — the only untried avenue left.**
   A targeted close-read of `assembleFlowImpl`, `MakeRenderBox.gren`, and the
   paren-block tab-stop machinery — the densest, most-patched code in the
   repo — as a human/agent-driven audit rather than a generated-input sweep.
4. Keep the corpus sweep in rotation as new packages publish; keep
   `gen-random.py` (including `fuzzrun.py` for unattended depth),
   the author-broken matrix, and both fuzzers as the fast per-change gate —
   re-running `fuzzrun.py` after a change like this session's is exactly this:
   not chasing a known gap, just re-confirming the co-occurrence axis is
   still clean.
