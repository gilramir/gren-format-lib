# Testing gates

The formatter is guarded by several independent checks, each aimed at a
different failure class. This page describes what each gate does, what it can
and cannot catch, and how to run it. The gates are complementary on purpose: a
bug that slips one is usually meant to be caught by another, so a change to core
render or comment code should clear the whole suite, not just the gate nearest
the edit.

The gates fall into two kinds, and the distinction matters:

- **Self-consistency checks** verify that the formatter agrees with *itself* —
  that its output is stable, meaning-preserving, and reproducible. They cannot
  tell you the output is *correct*, only that it is not contradictory. Wrongly
  laid out but deterministic output passes all of them.
- **Oracle checks** compare the formatter against an *external* source of truth —
  another formatter, the renderer itself, or a hand-specified expectation — and
  so can catch output that is wrong even though it is perfectly self-consistent.

Most of the suite is the first kind. Keep that in mind when a change "passes
everything": passing the self-consistency gates is necessary, not sufficient.

### Two things that apply to every gate below

**Rebuild the `gren-format` app first.** Every gate except `run-tests.sh` and
`check-render-invariant.py` shells out to the built CLI as a subprocess, so it
exercises whatever formatter source was last compiled — not your working tree.
Never rebuild while a fuzzer is running.

```bash
cd ../../gren-format && ./build.sh
```

**Pass `-j`.** The drivers default to `-j 2`; this machine has 16 cores, so
`-j 12` is the difference between a coffee and an afternoon on a whole-corpus
sweep.

---

## Effectful test suite (`run-tests.sh`)

### What it guards against

The baseline gate: for a fixed set of hand-picked source files, does the
formatter produce exactly the expected output, does it preserve meaning, and
is its output a fixed point? Every other gate in this suite supplements this
one rather than replacing it.

### The three checks

Each fixture runs through `assertPrettyIn fsPerm "<dir>" "description" "FileBaseName"`,
which performs three independent checks on one dirty/formatted pair:

1. **Formatting** — format `testfiles/<dir>/<FileBaseName>.dirty.gren` and
   diff the bytes against `testfiles/<dir>/<FileBaseName>.formatted.gren`.
   This is the suite's one genuine oracle check: the `.formatted.gren` file is
   a hand-verified expected output, not something derived from the formatter.
2. **AST equivalence** (self-consistency) — re-parse the formatted output and
   check with `Compiler.Ast.Compare` that it is semantically equal to the
   original AST. Catches formatting that silently changes meaning.
3. **Idempotency** (self-consistency) — re-format the `.formatted.gren` file
   and require both the `Module` AST *and* the parse `Context` (every comment
   position, every blank line) to come back unchanged. This is stronger than
   the fuzzer's byte-diff below — it fails on `Context` drift even when the
   re-formatted bytes still happen to match.

### How to run it

```bash
cd gren-format-lib/tests
./run-tests.sh   # builds tests/app via devbox, then runs it
```

`run-tests.sh` runs `check-render-invariant.py` first (see below), then
recompiles the test harness against the formatter source in `src/` directly —
the `tests/` app depends on the package locally — so editing formatter source
and re-running `run-tests.sh` is enough; there's no separate library build
step.

### Where the fixtures live

One directory per suite under `tests/testfiles/`, each named for the
`Format.gren` suite function that reads it — e.g. `BracketComments/`,
`KitchenSink/`, `ImportStatements/`. `Divergence/` is the one suite with no
source-tree twin: one fixture per entry in the
[divergence catalogue](elmFormatComparison.md#divergence-catalogue), named for
its entry (`D17PrecedenceSplit` is #17) and built from that entry's own worked
example. This suite tests the **documentation**: the `.dirty.gren` is what the
entry says you wrote, the `.formatted.gren` is what it says gren-format
produces, so a divergence that gets fixed — or reshaped by an unrelated fix —
breaks its own catalogue entry instead of leaving a false claim behind.
Writing it found six such claims, three of them one day old. Nothing else goes
in this directory; `check-divergence-index.py` (run by `run-tests.sh`) fails if
the mapping stops being 1:1 in either direction.

Every fixture, in any directory, is asserted with `assertPrettyIn fsPerm "<dir>"`.
Every check is identical regardless of which suite directory it lives in.

Note that a `.dirty.gren` byte-identical to its `.formatted.gren` is normal and
sometimes the whole point — "gren-format keeps what you wrote" is a claim about
a fixed point. `find-identical-fixtures.py` lists them; it is an inventory, not
a gate.

### Adding a fixture

Add both `<FileBaseName>.dirty.gren` and `<FileBaseName>.formatted.gren` under
the suite's directory (`testfiles/<SuiteDir>/`), then add an `assertPrettyIn`
line in `tests/src/Test/Formatter/Format.gren`. Generate the candidate
`.formatted.gren` with:

```bash
node ../../gren-format/app --show <FileBaseName>.dirty.gren > testfiles/<SuiteDir>/<FileBaseName>.formatted.gren
```

then read it before trusting it — nothing checks that the generated output is
actually canonical except your own review, since from that point on it *is*
the oracle for check 1. For a `Divergence/` fixture, "read it" means read it
against the catalogue entry it belongs to: if the two disagree, one of them is
wrong and it is not always the fixture.

### Where the code lives

- **`tests/src/Test/Formatter/Format.gren`** — the fixture list, one
  `assertPrettyIn` call per case.
- **`tests/testfiles/*/*.dirty.gren` / `*.formatted.gren`** — the fixture pairs;
  the `.formatted.gren` half also doubles as the corpus every other gate
  (matrix, both fuzzers, the audit) walks. `tests/corpus.py` is where those
  gates ask which directories exist, so a new suite directory is swept the day
  it is added rather than the day somebody remembers to widen four globs.
- **`tests/run-tests.sh`** — builds and runs the harness.

## Idempotency fuzzer (`fuzz-idempotency.py`)

### What it guards against

Self-consistency, specifically the "comment shifts on reparse" bug class: a
comment that lands in a slightly different place — or glues to the wrong
token — the second time the same file is formatted. The effectful suite's
idempotency check only ever exercises comment placements a fixture author
happened to write; this fuzzer places one in *every* possible gap, catching
placement bugs no hand-written fixture thought to cover.

### What it checks

For each `*.formatted.gren` fixture, it inserts a `{- ¤ -}` block comment into
every inter-token gap in turn, formats the perturbed file twice, and requires
the two outputs to be byte-identical. A gap where the two formattings diverge
is a finding — the comment (or the surrounding layout) moved between the first
and second format.

### How to run it

```bash
cd gren-format-lib/tests
python3 fuzz-idempotency.py -j 12                                      # whole corpus
python3 fuzz-idempotency.py -v testfiles/<SuiteDir>/Foo.formatted.gren  # one file, with the format¹/format² diff per gap
python3 fuzz-idempotency.py --pairs -j 12                              # the PAIR axis (slow; see below)
python3 fuzz-idempotency.py --update-known-baseline -j 12              # re-register the upstream findings
```

### What the exit status means

**Non-zero means an UNLABELLED finding, not any finding.** A finding whose
cause the gate can diagnose as an upstream parser bug is printed with a
`[known: …]` mark, counted, and registered in
`idempotency-known-baseline.json` — keyed by the label `repro.py` takes, so a
registered finding can be replayed from its key alone. Those do not fail the
run.

Two more things do fail it, and they are why the baseline is a *set* rather
than a count:

- an upstream-classifying finding that is **not registered** — a regression is
  not allowed to hide behind an automatic classification;
- a registered entry that **no longer reproduces** — a fix must not leave a
  stale exemption behind.

Only the default full sweep is gated this way. `--kind`, `--run`, `--mix*`,
`--pairs` and a file argument each probe a different set of gaps, so their
findings are reported but not held against the baseline.

This distinction is not decoration. A gate that exits non-zero on *any* finding
runs permanently red, and then "27 findings, 19 of them known" reads exactly
like "19 findings, all known" — which is how eight findings of a real bug (an
`if`/`when` header that could not see a comment nested in its condition) once
sat in that summary line looking like the upstream ones.

Run a full sweep after any change to comment handling, and especially after
adding a comment-bearing fixture — a new comment shape can surface a latent
gap no existing fixture exercised.

### The pair axis (`--pairs`)

Every other multi-comment mode puts its comments in **one gap**: `--run N`
varies a run's length there, `--mix*` varies its composition, and the matrix's
comment axis injects one comment per cell. `--pairs` is the only mode that
places comments at **two different gaps**.

That distinction is the reason it exists. The `if`/`when` header bug needed a
riding comment in the header *and* a row-breaking one nested inside the
condition — two gaps, not one. The gate found it only because a hand-written
fixture already had the first half, so the single-gap pass supplied the second
by accident. Nothing was sweeping for the shape.

Pairs are scoped to **one declaration**, for two reasons: the whole corpus
all-pairs is not a tractable sweep (20,874 gaps for one comment kind is ~2×10⁸
pairs), and the bug class is local anyway — an outer construct whose row is
broken by something nested inside it. Two comments in different declarations
cannot interact.

The default kind pairs are `block,multi` and `block,line`: a riding comment
first, a row-breaking one after it, which is the recipe. `--pair-cap N`
(default 400) subsamples a declaration with more pairs than that, seeded via
`--pair-seed` so a run replays and a finding can be reproduced.

```bash
python3 fuzz-idempotency.py --pairs -j 12                       # both default kind pairs
python3 fuzz-idempotency.py --pairs --pair-kinds multi,block -j 12   # breaker first
python3 fuzz-idempotency.py --pairs --pair-cap 0 -j 12          # no cap (long)
```

**It is opt-in, not part of the routine sweep.** A whole-corpus run at the
default cap is ~65 minutes at `-j 12` (~224,000 probes). Run it after a change
to how a construct's own row interacts with its contents — a header, a
container, anything whose layout a nested comment can decide.

The axis has been swept over the corpus with every finding classified upstream,
so it is green. Its non-vacuity does not rest on that count: with the `if`/`when`
header fix reverted it reports findings on `IfExpression.formatted.gren`, a
fixture the single-gap pass calls clean in every kind.

### Where the code lives

- **`tests/fuzz-idempotency.py`** — the driver: enumerates gaps, perturbs,
  formats twice, diffs. Invokes `../../gren-format/gren-format.sh` (the
  standalone CLI wrapper) as a subprocess — kept deliberately separate from
  `run-tests.sh` since it walks every gap in the corpus and is much slower
  than the fixture suite.
- **`tests/idempotency-known-baseline.json`** — the registered upstream
  findings the exit status forgives, keyed by `repro.py` label.
- **`tests/repro.py`** — rebuilds one finding from that label; see
  [Reproducing one finding](#reproducing-one-finding-repropy).

## Decision-stability gate (`check-decision-stability.py`)

### What it guards against

Self-consistency, like the fuzzer above — but it answers a different question
about the same failure. The idempotency fuzzer says *whether* a format is a
fixed point and hands back a byte diff. It cannot say **which decision** was
not, and that is the expensive part: with a non-zero residual of known
non-idempotent probes, every finding's culprit has to be traced by hand, and two
findings with the same cause look no more alike than two with different ones.

This gate asks the formatter directly, so findings that share a cause share a
name and the work-list becomes a histogram.

### What it checks

`--decisions` formats a file twice and reports which layout *decisions* differed
between the passes: `forceVertical` on a call, a comment's `CommentRole`,
`commentBreaksFlowRow`, whether a rendered child came back on one line. The
decisions carry **no positions** — a row is exactly what moves between two
formats, so a decision keyed on one would report every finding and explain none.

Three things shape what is reported, and each replaced a wrong first attempt:

- **Flips are confined to declarations whose rendered output moved.** Formatting
  a file that is not already canonical legitimately changes many decisions: the
  second pass reads the first pass's tidied rows, not the author's. Comparing
  the raw traces is therefore mostly the formatter *converging*.
  `Formatter.Render.renderRootChildren` exists so a declaration's two renderings
  can be compared; `convergedFlips` counts what the restriction throws away.
- **Flips are split into author-intent decisions and rendered-shape
  measurements** (`*.rendersOneLine`). Once a declaration reflows, every shape
  inside it has moved too — nine of the ten names in a finding are the reflow
  being observed rather than its cause. Probes are grouped by the *intent* set.
- **A "silent flip" check (a decision moved, the bytes did not) was tried and
  dropped as vacuous.** Over the corpus the input already *is* the output, so
  both traces come from identical text and nothing can differ. That is exactly
  the trap [the attic](llm/attic.md) records it under: when the two formats
  agree, an output-shaped comparison collapses back into the idempotency check we
  already have.

Two counters are the gate's own debt, printed every run: a probe whose bytes
moved with **no** flip at all (`UNEXPLAINED`), and one explained only by a
rendered shape. They come down by adding a decision to
`Formatter.Audit.DecisionTrace`, under that module's stated rule — **trace an
input, never a composite**. A traced value is either a flag read straight off
the LPT or the result of calling the renderer's own exported predicate with the
node's own children. Nothing here recomputes a formula that lives in
`MakeRenderBox`; that would be a mirror predicate, and mirror predicates are
what this codebase spent a refactor deleting.

### How to run it

```bash
cd gren-format-lib/tests
./check-decision-stability.py -j 12          # the corpus as written — the gate proper, green
./check-decision-stability.py -j 12 --gaps   # a comment in every gap — the instrument, red
./check-decision-stability.py --gaps --kind line -v testfiles/<SuiteDir>/Foo.formatted.gren
```

The plain mode is a real gate and passes over the whole fixture corpus. The
`--gaps` mode inherits `fuzz-idempotency.py`'s known-red residual, so its exit
status says nothing new; its value is the histogram.

Its probes are `fuzz-idempotency.py`'s, imported from that file by path rather
than copied, so the two gates cannot drift onto different gaps — and the first
whole-corpus run confirmed it, landing on exactly the probe set that gate
reports. It reuses the all-gaps fast path from there too, which matters more
here because `--decisions` formats twice.

### How to read the histogram

Probes are grouped by the *set* of decisions that flipped, and the group is the
diagnosis. Two readings, both off real sweeps:

- **`Comment.role` together with `Comment.endsItsLine` and
  `Comment.textCanRide`.** Those last two are functions of the comment's *text*,
  which cannot change — so they can only move if the comment changed **which
  declaration owns it**. A group carrying them is a comment relocating between
  declarations; the same group without them is a comment staying put and
  changing role.
- **`Comment.role=LeadsLine` lost against `Comment.role=Standalone` gained**,
  in bulk. That is the detached-comment class: a multi-line `{- … -}` written
  past a declaration's last token, which renders below the declaration and is
  re-homed to column 1 on reparse.

The second is the instrument paying for itself. The rule that fixes it was
already written down in `Comments.gren`'s module doc and already implemented
from source rows (`findOrCreateOrigRow`); asking the same question of the
finished tree (`detachOwnLineTrailer`) halved the idempotency fuzzer's finding
count in a day. Nothing in the byte diffs said which group a probe belonged to.

### Where the code lives

- **`src/Formatter/Audit/DecisionTrace.gren`** — the trace and the diff. Its
  module doc carries the rule about what may honestly be traced.
- **`src/Formatter/Render.gren`** — `renderRootChildren`, the per-declaration
  render the confinement needs. `renderRoot` is these joined with newlines.
- **`gren-format/src/Format.gren`** — `decisionsFile` / `decisionStabilityReport`:
  the same two passes `verifyReparse` runs, keeping both trees and leaving the
  AST comparison out (this flag is aimed at files that are *not* fixed points).
- **`tests/check-decision-stability.py`** — the driver and the histogram.

## Reproducing one finding (`repro.py`)

Both gates above report a finding as `<fixture>[<kind>]@<gap>` — a fixture, a
comment kind, and the byte offset the comment was spliced at. `repro.py` takes
that label directly and rebuilds the exact input, which is the first step of
every investigation: a byte diff cannot tell you *why* a comment moved, and the
answer is usually visible only in the roles the tree gave it.

```bash
cd gren-format-lib/tests
./repro.py TrickyComments.formatted.gren multi 100        # both passes + the diff
./repro.py <fixture> <kind> <gap> --input                 # just the spliced source
./repro.py <fixture> <kind> <gap> --lpt1 / --lpt2         # the tree each pass rendered from
./repro.py <fixture> <kind> <gap> --decisions             # which decisions differed
```

`<kind>` is `block` / `multi` / `line`, or one of those with an `xN` suffix
(`blockx2`) for a `--run N` finding, or several joined with `+`
(`block+multi+line`) for a `--mix*` one — so a label pasted off any gate's output
works unchanged. The fixture may be a bare basename; it is searched for under
`testfiles/`.

Two details are deliberate. It **imports the probe definitions from
`fuzz-idempotency.py` by path** rather than copying them, because a repro that
splices differently from the gate that found the finding is not a repro. And it
formats with `--show-first`, not `--show`, because `--show` runs the idempotency
comparison internally and fails — which is exactly the state under
investigation, so it would refuse to print the output you need.

## Whitespace-canonicalization fuzzer (`fuzz-whitespace.py`)

### What it guards against

Self-consistency of a different kind: that formatting is blind to the
author's original *whitespace* choices and depends only on structure and
comments. If a layout decision were ever accidentally sensitive to incidental
indentation or blank-line stretching in the input — rather than to
`forceVertical` / the author's actual row-break choices, which it is supposed
to read — this fuzzer is what would catch it.

### What it checks

It perturbs inter-token whitespace in each fixture and requires
`format(perturbed) == format(original)` — the canonical output must not depend
on which whitespace-equivalent variant of the input was formatted. Two
perturbation modes exercise this differently:

- `stretch` (default) — pads inter-token whitespace runs.
- `indent` — varies indentation depth.

### How to run it

```bash
cd gren-format-lib/tests
python3 fuzz-whitespace.py                 # default: stretch mode
python3 fuzz-whitespace.py --mode indent   # modes: stretch | indent
python3 fuzz-whitespace.py -j 12           # parallelise
```

### Where the code lives

- **`tests/fuzz-whitespace.py`** — the driver; walks the same fixture corpus
  (via `corpus.py`, all `testfiles/*/*.formatted.gren`) as the idempotency fuzzer.

## Construct × context syntax matrix (`matrix-syntax.py`)

### What it guards against

The fixture corpus only reaches syntax somebody thought to write by hand, and
both fuzzers above perturb *comments* or *whitespace* over that fixed
corpus — neither varies syntax itself. A bug that needs a conjunction of
features (a specific construct, in a specific context, in a specific layout
shape) has no fixture to trigger it. The matrix is the syntax axis: it embeds
every expression form in every context, in up to four layout variants, and
checks each cell — **2459 cells** at present.

### The layout variants

A flat-only matrix misses whatever needs a pre-broken atom — it let a
record-literal binop-field crash through — so every construct-in-context is
generated in up to four:

- `flat` — the paren-carrying atom on one line.
- `broken` — the same atom pre-broken across rows (valid in every context).
- `bareFlat` / `bareBroken` — the atom with its outer parens stripped, in
  value-position contexts only (record field, `let` binding, branch body,
  array item, …). This is the variant that catches value-position bugs
  specifically: a paren-carrying atom routes a multi-line operand through the
  *handled* `ParenBlock` arm, so only the bare form reaches some code paths.

### The four oracles

1. **Layout, both directions** — *flat-input variants only* (`flat`,
   `bareFlat`). Layout is author-driven, with no page width and no fitter, so
   a construct written flat renders flat unless its content forces a break,
   and anything involving `if`/`when`/`let` must break. Both
   over-approximation and under-approximation are failures here. This is a
   flat-*input* truth, so it doesn't run on `broken`/`bareBroken` — a
   pre-broken input has no local layout truth (gren can collapse a
   broken-but-fitting binop).
2. **`--show` round-trip** — internally does parse → render → reparse →
   AST-compare → render again → idempotency-compare, so a clean exit buys AST
   equivalence, idempotency, and "the output parses" in one call, over
   generated syntax rather than only the fixture corpus.
3. **`--audit-predicates` on every cell** — the same predicate/renderer
   agreement check described below, run over synthetic syntax the corpus may
   not contain.
4. **elm-format parity** — every cell is translated to Elm (one regex
   suffices and is exact, since cells are built from a vocabulary the script
   itself authors, and `when X is` → `case X of` is the only Gren/Elm
   difference in that vocabulary) and diffed against `elm-format --stdin`.
   Unlike 1–3, this is *not* a truth by itself — gren-format diverges from
   elm-format on purpose (see the [divergence catalogue](elmFormatComparison.md#divergence-catalogue)) — so it is
   gated against a reviewed baseline (`matrix-parity-baseline.json`) rather
   than a bare equality check. A cell that diverges *unregistered*, or a
   registered cell that no longer diverges, fails the matrix. Reviewed
   entries name a catalogue number; an `UNREVIEWED` or `BUG:` reason is
   counted and printed on every run, so debt — or a baseline entry that is
   really a known bug — never goes quiet.

### How to run it

```bash
cd gren-format-lib/tests
./matrix-syntax.py -j 12                                   # whole matrix (all variants)
./matrix-syntax.py -v                                      # source + output per failure
./matrix-syntax.py --variant broken --variant bareBroken   # author-broken variants only
./matrix-syntax.py --construct recordUpdate1 --context parenBinopArg
./matrix-syntax.py -k /tmp/failing                         # write failing cells out as .gren
./matrix-syntax.py --no-parity                              # skip oracle 4
./matrix-syntax.py --update-baseline                        # rewrite the parity baseline
```

Oracle 4 needs `elm-format` on `PATH`; without it the matrix says so loudly and
runs the other three rather than quietly reporting a thinner green.

### Where the code lives

- **`tests/matrix-syntax.py`** — the driver: construct/context vocabulary, the
  four oracles, the parity baseline gate.
- **`tests/matrix-parity-baseline.json`** — the reviewed divergence baseline
  for oracle 4.
- **[`elmFormatComparison.md`](elmFormatComparison.md)**'s "Divergence
  catalogue" — the human-readable explanation behind every registered
  baseline entry.

## The render invariant (no script — the compiler enforces it)

There used to be a gate here, `tests/check-render-invariant.py`. It is gone, and
what it guarded is now a type error. This section records what the rule is and
how the enforcement got moved, because "there is no check for this any more"
should never be read as "this stopped mattering".

### The rule

Comment placement is decided exactly once, in `Comments.gren`, and stored as a
`CommentRole`. Verticality is decided from author-intent flags plus the
*rendered box shape* (`isSingleLine` / `B.allSingles`). Neither is ever
re-derived from source rows once rendering starts. A renderer that reads the
author's rows can disagree with itself on the second format — its own output has
different rows — which is the oscillation and crash class the two-stage
architecture was built to remove.

### How it is enforced

`Formatter.RenderTree.lower` converts the LPT into a parallel tree with the
positions taken off:

- `RenderNode` replaces `LPNode`, dropping the seven cached position fields
  (`firstPos`, `lastPos`, `minRow`, `maxRow`, `lastBracketEnd`, `bracketStart`,
  and the two bracket booleans).
- `RenderShape` replaces `LPShape`, stripping the `Located` from the eight
  constructors that carry one (`UnbreakableText`, `SingleLineComment`,
  `BlockComment`, `DocComment`, `RecordUpdate`, `EmptyBracketed`, `PrefixGlue`,
  `MultilineString`) and reducing `OriginalRows` to the `SyntaxType` that is all
  the renderer ever read off it.

Every module under `src/Formatter/Render/` takes those. There is no row to read,
no accessor that accepts the type, and no `Located` to reach through.
`Formatter/Render.gren` is the doorway — the only render-side module that names
`LPNode` — and `lowerShape` is total over `LPShape`, so a constructor added there
fails to compile until it is mapped.

The handful of decisions that genuinely needed the author's rows are computed
once by `lower` and read back as booleans: `rnSharesRowWithPrevItem`,
`rnHasSourceContent`, `rnVariantsSpanRows`, `rnTypeSegmentsBroken`. They were
author-intent facts all along, so this finishes the doctrine
`AcrossOrVertical`'s `forceVertical` already followed.

### Why a type and not the script

The script was a regex over eight accessor names plus an allowlist of five
reviewed exceptions, and it had two holes of exactly the kind this repo keeps
finding in its own gates:

- `lpnBracketStart` was **not** among the eight names and **was** called in
  `Render/NodeClassify.gren`. No unreviewed violation existed only because that
  call happened to sit inside an allowlisted function — luck, not the gate. Same
  exposure for `lpnBracketEndExact`, `lpnBracketEndElastic`,
  `lpnWithBracketStart`.
- `OriginalRows` carried `{ first : Int, last : Int, stype }` — two literal
  source rows — and a read of `r.first` matched neither the eight names nor the
  `.start.row` / `.end.col` pattern. The script would never have seen it.

An enumeration can be short. A type cannot. That is the whole argument, and it
is why the second hole was found by *doing* the refactor rather than by
reviewing the script again.

### If you need a source row in the renderer

You almost certainly do not — most such needs are really about *placement*,
which belongs in `Comments.gren` as a `CommentRole`. If the need is real,
precompute it as a boolean in `Formatter.RenderTree.lower` and read the flag,
the way the four existing flags do. Do not widen `RenderShape`.

### Where the code lives

- **`src/Formatter/RenderTree.gren`** — `RenderNode`, `RenderShape`, `lower`,
  `lowerShape`, and the four precomputed flags.
- **`src/Formatter/Render.gren`** — the doorway.
- **`src/Formatter/Logical/LogicalPrintingTree.gren`** — the `CommentRole`
  docstring: the roles this rule exists to keep authoritative.
- **`src/Formatter/Logical/Comments.gren`** — `classifyCommentKind`, where each
  role is decided, with the fixture that pins each arm.
- **`docs/commentHandling.md`** — the reader-facing statement of what the
  formatter is trying to do with comments.

## Property-based random generator (`gen-random.py`)

### What it guards against

Every gate above walks a fixed space: the matrix enumerates known shapes,
both fuzzers perturb comments/whitespace over the fixed fixture corpus, and
the audit walks the corpus too. None of them vary **structure**. A bug that
needs a conjunction of features nobody wrote by hand — the axis the real-corpus
sweep proved most productive of all — has no fixture or matrix cell to trigger
it. `gen-random.py` builds random-but-legal Gren modules
(structure *and* comments) with bounded depth, and checks four oracles per
generated module. Full design in `GENERATOR.md`.

### The oracles

- **`--pre-ast` parses at all** — a failure here is a *generator* bug (it
  emitted something that isn't legal Gren), not a formatter finding. Failures
  land in `gen-out/<run>/quarantine/` and are reported separately from real
  findings; this bucket should stay ~0, since a nonzero rate here undermines
  trust in every other bucket's findings.
- **`--show` round-trip** — buys no-crash, AST-equivalence, idempotency, and
  "the output reparses", all in one call — the same property as the matrix's
  oracle 2.
- **Comment preservation** — compares the multiset of `(type, normalizedText)`
  comments from `--pre-context` on the input against the formatted output.
  Positions are discarded, so a comment that merely *moved* passes; only a
  drop, duplication, invention, or kind-change trips it. This catches what
  neither AST-compare (blind to a dropped comment) nor idempotency (only
  catches a *shift*, not a loss) can see.

### Reproducibility

Layout decisions are baked into the generated node tree, so emission is a
pure function of that tree: `--seed` replays a run exactly, and the shrinker
(tree-surgery + deterministic re-emit) minimizes any failure down to
`input.min.gren`.

### How to run it

```bash
cd gren-format-lib/tests
./gen-random.py -n 2000 -j 12               # sweep
./gen-random.py --seed 12345                # replay one seed, verbose (+ shrunk repro)
./gen-random.py -n 500 --max-depth 6        # deeper nesting
./gen-random.py --no-comments               # structure only
./gen-random.py --promote 12345 --name Foo  # turn a fixed find into a fixture
```

Artifacts land in gitignored `gen-out/run-NNNNNN/`, failures-only, bucketed by
kind (`crash` / `ast-mismatch` / `non-idempotent` /
`comment-loss`), each with a self-contained `report.txt` carrying the repro
command and a pre-computed diff.

### Adding grammar coverage

When adding a construct to the generator's grammar, check that the
quarantine rate stays ~0 after the addition — 0 quarantine and 0 emitter
exceptions is what makes the generator's crash/non-idempotent findings
trustworthy rather than noise. Note current Gren constructor patterns take at
most one argument (`Ctor a b` doesn't parse; multi-field variants carry a
record instead) — a fact the generator's pattern grammar has to encode rather
than assume.

### Long sweeps

The command above sweeps a range and exits. To grind through hundreds of
thousands of modules across many sessions — a time budget rather than a seed
count, a resumable cursor per settings profile, and a record of every failure —
use `fuzzrun.py`, which drives this generator. See
[fuzzTesting.md](fuzzTesting.md).

```bash
./fuzzrun.py run --for 2h     # sweep for two hours, then stop
./fuzzrun.py status           # coverage and findings so far
```

### Where the code lives

- **`tests/gen-random.py`** — the generator, shrinker, and oracle driver.
- **`GENERATOR.md`** — the full design spec (grammar, depth bounds, shrinking
  algorithm).
- **`tests/fuzzrun.py`** — the long-sweep coordinator ([fuzzTesting.md](fuzzTesting.md)).

## Real-corpus sweep (`corpus-check.py`)

### What it guards against

Everything else on this page is synthetic. The matrix builds cells from a
vocabulary this repo authors, both fuzzers perturb a corpus this repo wrote, and
`gen-random.py` generates modules from a grammar this repo specified — so all of
them reach the shapes somebody here thought of. Real published Gren does not
have that ceiling: it varies many axes at once, and the productive axis for bugs
is **feature co-occurrence**. The sweep that first ran this over ten published
packages found nine bugs in five classes, each a conjunction no single-axis gate
could produce — multi-line string × trailing whitespace × nesting;
author-broken record × arrow position; pipe × record arg × `else if`; binop ×
comment × bracket operand; call × three-or-more multi-line block arguments.

This gate is that sweep, made repeatable. It is the one oracle whose inputs
nobody in this project chose.

### What it checks

`--show` over every `.gren` file in a tree of real packages. That one call is
parse → format → reparse → AST-compare → format again → idempotency-compare, so
a clean exit per file buys no-crash, meaning-preserved, idempotent, and "the
output parses". Failures are bucketed by which of those broke — `crash`,
`ast-mismatch`, `non-idempotent`, `parse`, `unreadable` — so the report reads as
a work-list rather than a count.

A file the **parser** rejects is reported separately and not counted as a
formatter failure: gren-format cannot format what the compiler will not parse,
and the known instance is upstream (compiler-common#31, an unparenthesized
`Ctor arg as name`).

### How to run it

```bash
cd gren-format-lib/tests
./corpus-check.py -j 12                 # the default corpus root
./corpus-check.py /path/to/pkgs -j 12   # a different tree of packages
./corpus-check.py -v                    # first error line per failure
```

It needs a tree of real Gren packages to sweep; the default root is a
`gren-format-preview/pkgs` checkout beside this repo. Any directory of `.gren`
files works — this package's own `src/`, `core/`, a vendored dependency.

### Where the code lives

- **`tests/corpus-check.py`** — the driver, the bucketing, and the parser-class
  carve-out.

## Project fuzzer (`fuzz-project.py`)

### What it guards against

Every other gate on this page runs `--show` on **one file** and reads the
output. The modes people actually run — `gren-format` with no arguments, which
discovers a project and overwrites its sources, and `gren-format <paths>` — walk
source directories and *write*, and between them they had eight fixture tests.
Nothing swept them. Its first run duly found that the no-argument project run
did not normalize CRLF, because it reads sources through
`Outline.findSourceFiles` rather than `Format.readSource`, whose docstring
claimed to be "the one place every read funnels through".

### What it checks

Each trial builds a real project — a `gren.json` plus several `gen-random.py`
modules under `src/` — and holds the writing modes to what `--show` already
guarantees per file:

- **A** the no-argument project run exits 0.
- **B** every file on disk afterwards equals its own `--show` output.
- **C** the reported "N files reformatted" equals the number that changed.
- **D** a second run reformats 0 and rewrites nothing — project idempotency.
- **E** the same, for `--remove-unused-imports` against its own `--show`.
- **F** `gren-format src/` — a directory argument — lands the same bytes as the
  no-argument run.

Three more cover the edges that only exist for a mode that writes, and each is
about work that could be *lost* rather than merely mislaid:

- **G** a file that does not parse must not cost the others their formatting,
  and must itself come back byte-identical. A write mode that gives up halfway
  is the one failure here that destroys source.
- **H** a CRLF file formats in place to the same bytes as `--show`, and the
  result is a fixed point.
- **I** a lowercase-named `.gren` and a non-`.gren` file are not source files,
  and must be left alone.

### How to run it

```bash
cd gren-format-lib/tests
./fuzz-project.py -n 60 -j 6          # sweep
./fuzz-project.py --trial 7 --keep    # rebuild exactly trial 7, keep the project dirs
```

Trials are seeded, so `--trial N` replays one exactly; `--keep` leaves its
directory behind to inspect. `--max-depth` and `--comment-rate` are passed
through to the generator.

### Where the code lives

- **`tests/fuzz-project.py`** — the driver: builds the project, runs the modes,
  compares against the single-file path.
- **`tests/gen-random.py`** — imported directly, so the modules a trial contains
  are the same generated syntax that gate sweeps.

## Instruments, not gates (`_run_*.py`)

Five scripts in `tests/` carry a leading underscore, and it means something:
**they answer a question, they do not guard anything.** Nothing runs them
automatically, their exit status is meaningless, and a green run of one proves
nothing about the formatter. Each was written for an investigation that is now
closed; what is kept is the *method*, because the question recurs.

The question they all serve is the one a large pile of findings raises:
**is this pile a bug, or is my instrument asking the wrong question?** The way
to answer it is never to read the pile one cell at a time.

### The predicate three — is a pile a layout bug, or a grain mismatch?

Written when the run axis's predicate audit reported 8,527 `commentEndsItsLine`
findings — 96% of everything `--comment-runs` said. Not one was a layout bug:
the audit was asking per *comment* what only makes sense per *run*, and
re-graining it took the pile to 0 (the reasoning is in
[the audit's own section](#the-second-property-commentbreaksflowrow-both-ways)).

```bash
python3 _run_predicate_sample.py [stride] [-j N]        # 1. which way does the pile point?
python3 _run_predicate_parity.py <keep-dir> [stride]    # 2. does it lay out wrong?
python3 _run_predicate_census.py <keep-dir> [-j N]      # 3. the whole space, tallied
```

- **`_run_predicate_sample.py`** splits the pile by **claim direction** and by
  **run composition** before anything else. That order is the method:
  `flowCommentFindings` is bidirectional (a predicate promising a break the flow
  did not take, versus a flow breaking where the predicate promised nothing —
  the worse direction), and a pile whose direction is a pure function of
  composition is a scope mismatch, not a layout bug.
- **`_run_predicate_parity.py`** asks the question the audit cannot: elm-format.
  Byte-identical output on a cell whose predicate disagrees means the
  disagreement is internal and the work is the *audit's*; diverging in the
  comment's own rows means a layout claim to review and the work is the
  *formatter's*. Cells diverging for a reason the comment baseline already
  registers are reported separately, since inheriting a base divergence says
  nothing either way. Needs `elm-format` on `PATH`.
- **`_run_predicate_census.py`** reads every failing cell that `matrix-syntax.py
  -k` wrote out and tallies claim direction × composition × box kinds ×
  construct/context, so no sweep is needed. It is what turned "one family,
  probably" into "a pure function of composition, with no construct or context
  dependence" — which is what a grain mismatch looks like and a layout bug does
  not.

### The parity two — what would a run-axis elm-format baseline cost?

The comment-run axis deliberately has **no** elm-format baseline. These two are
the evidence for that standing decision, and the way to revisit it.

```bash
python3 _run_parity_sample.py [1-in-N] [-j N] [--seed S]   # how much debt, split how?
python3 _run_parity_review.py --kind multix2 --per-kind 150  # what IS the debt?
```

- **`_run_parity_sample.py`** measures the debt before anyone writes the
  baseline — `--update-baseline` rewrites the whole file and refuses a filtered
  run, so there is no other way to ask. It formats sampled cells with both
  formatters and prints how they classify, overall and **per run composition**,
  which is the cut that decides whether one comment kind is the entire debt.
  Two traps are baked into it: the sample is seeded-**random**, not every Nth
  cell (`run_cells` has a period of 18, so a stride sharing a factor with it
  draws 0 cells from whole compositions while printing a per-composition table
  as though it had covered them), and it computes the uncommented cells' output
  pairs by default, without which every `#23` cell reads as UNREVIEWED and the
  headline is inflated.
- **`_run_parity_review.py`** says what the debt *is* rather than how much:
  it buckets the unclassifiable cells on the **disagreement**, reusing
  `triage-comment-parity.py`'s own `shape`/`disagreement`, so one group here is
  one question `--interview` would ask. Names and literals are flattened and the
  context dropped, so the same disagreement in a call argument, a record field
  and a pipeline step is a single group with a count. It writes nothing.

### Where the code lives

- **`tests/_run_predicate_*.py`**, **`tests/_run_parity_*.py`** — the five
  instruments. Each one's docstring states what it answered and what part of it
  is meant to be reused.
- **`tests/triage-comment-parity.py`** — the classifier and `--interview` loop
  both parity instruments borrow their grouping from.

## Pathological-input sweeps (`pathological-nesting.py`, `pathological-other.py`)

### What they guard against

Every other gate feeds the formatter *plausible* input: the fixtures are code
somebody wrote, the matrix cells are code somebody might write, and
`gen-random.py` is bounded to the depth real programs reach. None of them ask
what happens at the edges — a thousand nested parens, a file that is nothing but
comments, an identifier ten thousand characters long, a file with no
declarations at all. Those are where a recursive renderer runs out of stack and
where an accidental `O(n²)` shows up as a hang rather than as a wrong answer.

Neither script is a pass/fail gate on the corpus. They **find a boundary** and
tell you which side of it the formatter is on.

### `pathological-nesting.py` — how deep before something breaks

Thirteen shapes, each nested to increasing depth: `parens`, `list`, `record`,
`lambda`, `ifchain`, `unaryminus`, `binopchain`, `pipelinechain`, and five
*conjunction* shapes (`lambdaarray`, `lambdarecord`, `pipeparenarg`,
`pipelambda`, `pipelambdaarg`) that nest one construct through another — the
conjunctions are the ones that found the double-render blowups, because a single
construct nested deeply never showed them.

It grows depth geometrically until something breaks, then **bisects to the exact
boundary depth**, and at that boundary runs `--pre-ast` as well to separate two
very different findings: the **parser** giving out first (a `compiler-common`
limit, not ours — recursive descent pays a native stack frame per level) from
the **formatter** giving out first (ours). A single probe at the crossover is
noisy, since native stack thresholds move a few percent run to run, so it
samples rather than trusting one point.

The one limitation this found and could not fix is in
[Known limitations](knownLimitations.md#very-deep-lambda-or-unary-minus-nesting-can-overflow-the-stack).

### `pathological-other.py` — everything that isn't depth

Seven **size shapes** swept geometrically — `long-identifier`, `long-string`,
`long-comment`, `wide-list`, `wide-record`, `wide-module` (many top-level
declarations), `wide-comments-only` (a file of nothing but comments) — plus five
one-shot **scenarios** that are about kind rather than size: `empty-module`,
`all-comment-file`, `unicode-identifiers`, `unicode-strings`, `crlf-corpus`.

The two size shapes behind the README's performance table are `wide-module` and
`wide-comments-only`.

### How to run them

```bash
cd gren-format-lib/tests
./pathological-nesting.py -v                       # all shapes, bisect each
./pathological-nesting.py --shape pipelambda -v    # one shape
./pathological-other.py -v                         # sizes + scenarios
./pathological-other.py --scenario-only            # skip the size sweeps
./pathological-other.py --size wide-module --max-size 40000
```

Both take `--start`, `--factor` and `--timeout`; the nesting prober takes
`--max-depth` and the size prober `--max-size`. A timeout is a finding here, not
an infrastructure problem — that is how a hang presents.

### Where the code lives

- **`tests/pathological-nesting.py`** — the depth prober, the bisection, and the
  parse-stage/format-stage split.
- **`tests/pathological-other.py`** — the size sweeps and the one-shot scenarios.

## Scaling (`bench-scaling.py`, and how to check a suspected blowup)

`bench-scaling.py` times the formatter against a rising comment count, with
`--stage lpt` / `pex` / `show` to say which stage the time is in. It is a
measuring instrument, not a gate — nothing fails on a slow number.

Layout here is author-driven, so there is no search to blow up; the blowups
this codebase has actually had came from *rendering the same subtree more than
once*. `makeBinopBox` rendered every operand to decide a layout and then
re-rendered it in the chosen one, which cost O(2^depth) on nested paren
operands (`1 + (1 + (…))`) — the same shape as the earlier nested
record-literal hang. Both were fixed by rendering once, up front, and having
every path consume the same items. That is the pattern to look for: a
suspected blowup is almost always a second render, not a slow function.

**Measured 2026-08-11.** A code review flagged quadratic patterns in
`Box.stackPrime`, `flattenBinopNodes`, `Comments.insertCommentIntoSubtree`'s
sibling scan and `FlowAssembly.leadingFor`, and asked whether
`subtreeHasComment` should be cached. Timing five structural shapes — binop
chain, record literal, `let` bindings, top-level declarations, pipeline — at
n = 50…3200, net of the ~57 ms node startup:

| shape | n=400 | n=800 | n=1600 | n=3200 |
|---|---|---|---|---|
| `let` bindings | 204 ms | 490 ms | 1212 ms | 3345 ms |
| binop chain | 124 ms | 254 ms | 566 ms | stack overflow |

Doubling n multiplies time by 2.0–2.8, not by 4. Nothing is quadratic in
practice at sizes several times larger than any real Gren module, and the
binop chain hits the known stack-overflow limit before any quadratic term
would surface. Those four spots are quadratic in the small — over one
construct's children, not over the file — and are not worth restructuring.

`subtreeHasComment` *is* cached now, on the node, beside the position bounds
(`lpnHasComment`). That was worth doing for consistency — it is the one
subtree fact that was answered by walking while the other seven were
cached — but it is **not** a measured speedup: on comment-dense nested input
the walk and the cache time the same to within noise. Do not cite it as a
performance fix.

## Predicate/renderer agreement audit (`audit-predicates.py`)

### What it guards against

Layout in this formatter is decided in two stages. Before anything is rendered,
a handful of **predicates** in `Formatter.Render.NodeClassify` answer questions
like *"does this subtree force a hard break?"* Callers use those answers to lay
out the code *around* a node — where to put a `|>`, whether a lambda body can
stay on the opening line, and so on. The predicate has to commit to an answer
before the node it is asked about is actually rendered.

Each predicate is therefore a **hand-written mirror of what the renderer will
do** — a second, separate implementation of the same decision. Nothing in the
type system or the build forces the two to stay in step. When they drift, the
predicate says "this breaks" but the renderer lays the node out on a single
line. Callers, trusting the predicate, then commit the surrounding code to a
vertical shape it never needed. The result is real code with wrong layout:
over-indented, broken where it should be inline, or both.

This is the gap no other gate sees. That mis-laid-out output is still
deterministic, still AST-equivalent to the input, still idempotent, and still
stable under both fuzzers — it passes every self-consistency check in the repo.
The only way to catch it is to compare the predicate against the thing it claims
to predict: the renderer itself. That is what this audit does, which makes it
one of the few genuine **oracles** in the suite.

### The property it checks

For every node in the Logical Printing Tree, the audit renders the node's own
box and checks a single one-directional implication:

```
predicate(node) == True   ==>   node's own box renders multi-line
```

In words: *if a predicate promised a break, the renderer must actually break.*
A predicate that says `True` while the box renders on one line is a **finding**
— an over-approximation, the failure mode described above.

The implication runs one way only. An **under**-approximation — a predicate that
says `False` on a node that does render multi-line — is deliberately **not**
reported. These predicates only claim the breaks that are *unconditional*; a
node can still break for reasons they intentionally do not model, most often the
author's own row layout (`forceVertical`). Reporting those would flag every such
case as a false positive, so the audit stays silent on them by design.

### The second property: `commentBreaksFlowRow`, both ways

One predicate is checked in **both** directions, because it is a different kind
of mirror. `commentBreaksFlowRow` is not a shape prediction about a subtree; it
is a hand-written summary of `FlowPolicy.decide`'s separator table — *a `--`
always breaks, a multi-line `{- … -}` always breaks, a single-line one mid-flow
does not, and only when a real item follows* — and its own docstring says it must
track `decide`. Under-approximating there is the *worse* direction: it is what
puts a comment-broken construct on the flat path, so format¹ renders flat, the
comment breaks the row anyway, and format² reads the break as the author's. The
file oscillates.

The check is per comment **run** — every maximal group of comments in one gap —
and it is asked of the assembly rather than of a second prediction:

```
commentBreaksFlowRow(run) == True  <==>  deleting the whole run lets the next
                                         item move back up onto the previous
                                         item's row
```

**The grain is the run, not the member**, and that distinction is worth knowing
because getting it wrong is expensive. Asked per comment, the audit reports
thousands of cells of `matrix-syntax.py --comment-runs`, none of them a layout
bug: deleting one member of a run does not close the gap, because
the other member breaks the row anyway. A member's own contribution and what the
*gap* does coincide only when that member is the sole reason for the break, and
in a run there is always another reason. A run of one is the single-comment case
unchanged.

Three things are out of scope, and `flowCommentFindings`' docstring argues each:
a **trailing** run (nothing after it to push), a **leading** one (the rows it
occupies above the construct are the comment's, not a break between items), and
a gap the two items **do not share even with the run deleted** — there the
difference of the gaps measures extra rows rather than "the next item starts a
fresh row", which is already true without the run, and the caller's
`forceVertical` is set by the broken gap either way.

### Root vs. propagated findings

A predicate of this kind can be recursive — an `Array.any <predicate> children`
fallback arm was typical of the retired shape predicates. When one is, a single
wrong answer at a leaf makes every ancestor above it answer wrong too, and every
caller reading those ancestors in turn, so one underlying bug surfaces as dozens
of findings.

To keep the work-list honest, each finding is tagged:

- **root** (`propagated == False`) — the predicate answered wrongly from *its
  own* arm, not by echoing a descendant. These are the actual bugs to fix.
- **propagated** (`propagated == True`) — the finding is real but not separately
  fixable; it is an ancestor echoing a wrong answer from a node below it, and it
  disappears once the root below it is fixed.

The driver groups findings by `(predicate, box kind)` and reports root causes
first, with the propagated echoes counted alongside. **Only root findings are a
work-list.** A green run means every audited predicate agrees with the renderer
on every node in the corpus.

Neither predicate audited today is recursive, so `propagated` should always come
back `False`. The tag is kept for the next predicate that is — and a `True` here
would itself be worth investigating.

### How to run it

```bash
cd gren-format-lib/tests
./audit-predicates.py -j 12                              # whole corpus
./audit-predicates.py -v                                 # list every finding, not just the summary
./audit-predicates.py -v testfiles/<SuiteDir>/Foo.formatted.gren   # one file
```

Exit status is non-zero if any finding is reported.

The corpus it walks is `testfiles/*/*.formatted.gren` (via `corpus.py`) — the
same fixture set the effectful suite uses. The matrix (`matrix-syntax.py`) additionally runs
`--audit-predicates` on every generated cell, so the audit also covers synthetic
syntax beyond what the corpus happens to contain.

### Where the code lives

- **`src/Formatter/Audit/PredicateAgreement.gren`** — the audit itself.
  `auditLpt` walks the tree bottom-up (so each node knows whether the lie started
  at it or below it), renders each node with `makePBox`, and compares
  `isSingleLine` against each audited predicate. `auditedPredicates` is the list
  of predicates under audit — every predicate consulted before layout owes the
  renderer agreement and belongs here.
- **`--audit-predicates <file>`** — the CLI flag on the standalone app that runs
  the audit on one file and prints the findings as JSON.
- **`tests/audit-predicates.py`** — the driver that runs the flag across the
  corpus, aggregates findings into the root/propagated work-list, and sets exit
  status.

### Current coverage — and why it is small

Most of the former shape predicates (`subtreeHasVerticalBox`, `nodeSpansRows`,
and friends) have been **retired**. Verticality is now read from the *rendered*
box (`isSingleLine` / `B.allSingles`) rather than predicted structurally, which
removes the mirror-drift risk at its source — there is no second implementation
to disagree when the renderer *is* the answer. What remains under audit is the
one structural query that genuinely still runs ahead of rendering
(`isMultilineLambdaParenBlockBox`).

This shrinking is the healthy direction: every predicate moved from "predict
structurally, then audit" to "read the rendered box" is one fewer mirror that
can drift. The audit still matters for the predicates that cannot be eliminated
that way — a new structural predicate added to `NodeClassify` should be added to
`auditedPredicates` so it is held to the same agreement. The background on why
layout decisions read the rendered box rather than source rows is in the
`Formatter.Render.NodeClassify` module comment.
