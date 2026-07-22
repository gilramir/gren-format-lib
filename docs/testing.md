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

---

## Effectful test suite (`run-tests.sh`)

### What it guards against

The baseline gate: for a fixed set of hand-picked source files, does the
formatter produce exactly the expected output, does it preserve meaning, and
is its output a fixed point? Every other gate in this suite supplements this
one rather than replacing it.

### The three checks

Each fixture runs through `assertPretty fsPerm "description" "FileBaseName"`,
which performs three independent checks on one dirty/formatted pair:

1. **Formatting** — format `testfiles/Formatter/<FileBaseName>.dirty.gren` and
   diff the bytes against `testfiles/Formatter/<FileBaseName>.formatted.gren`.
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

### Adding a fixture

Add both `<FileBaseName>.dirty.gren` and `<FileBaseName>.formatted.gren` under
`tests/testfiles/Formatter/`, then add an `assertPretty` line in
`tests/src/Test/Formatter/Format.gren`. Generate the candidate `.formatted.gren`
with:

```bash
node ../../gren-format/app --show <FileBaseName>.dirty.gren > testfiles/Formatter/<FileBaseName>.formatted.gren
```

then read it before trusting it — nothing checks that the generated output is
actually canonical except your own review, since from that point on it *is*
the oracle for check 1.

### Where the code lives

- **`tests/src/Test/Formatter/Format.gren`** — the fixture list, one
  `assertPretty` call per case.
- **`tests/testfiles/Formatter/*.dirty.gren` / `*.formatted.gren`** — the
  fixture pairs; the `.formatted.gren` half also doubles as the corpus every
  other gate (matrix, both fuzzers, the audit) walks.
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
python3 fuzz-idempotency.py -j 12                                      # whole corpus (exit≠0 if any gap fails)
python3 fuzz-idempotency.py -v testfiles/Formatter/Foo.formatted.gren  # one file, with the format¹/format² diff per gap
```

**Rebuild the `gren-format` app first** (`cd ../../gren-format && ./build.sh`)
— the fuzzer shells out to the built CLI as a subprocess, so it exercises
whatever formatter source was last compiled, not the current working tree.
This machine has 16 cores; the default is `-j 2`, so pass a higher `-j` for a
fast whole-corpus sweep.

Run a full sweep after any change to comment handling, and especially after
adding a comment-bearing fixture — a new comment shape can surface a latent
gap no existing fixture exercised.

### Where the code lives

- **`tests/fuzz-idempotency.py`** — the driver: enumerates gaps, perturbs,
  formats twice, diffs. Invokes `../../gren-format/gren-format.sh` (the
  standalone CLI wrapper) as a subprocess — kept deliberately separate from
  `run-tests.sh` since it walks every gap in the corpus and is much slower
  than the fixture suite.

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

**Rebuild the `gren-format` app first** — like the idempotency fuzzer, it
shells out to the built CLI. This machine has 16 cores; both fuzzers default
to `-j 2`, so pass a higher `-j` for a full-corpus sweep.

### Where the code lives

- **`tests/fuzz-whitespace.py`** — the driver; walks the same
  `testfiles/Formatter/*.formatted.gren` corpus as the idempotency fuzzer.

## Construct × context syntax matrix (`matrix-syntax.py`)

### What it guards against

The fixture corpus only reaches syntax somebody thought to write by hand, and
both fuzzers above perturb *comments* or *whitespace* over that fixed
corpus — neither varies syntax itself. A bug that needs a conjunction of
features (a specific construct, in a specific context, in a specific layout
shape) has no fixture to trigger it. The matrix is the syntax axis: it embeds
every expression form in every context, in up to four layout variants, and
checks each cell — **1738 cells** at present.

### The layout variants

Added 2026-07-18, after a record-literal binop-field crash slipped through a
flat-only matrix:

- `flat` — the paren-carrying atom on one line (the original 850 cells).
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
   elm-format on purpose (see the README's divergence catalogue) — so it is
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

**Rebuild the `gren-format` app first** — it shells out to it. Oracle 4 also
needs `elm-format` on `PATH`; without it the matrix says so loudly and runs
the other three rather than quietly reporting a thinner green.

### Where the code lives

- **`tests/matrix-syntax.py`** — the driver: construct/context vocabulary, the
  four oracles, the parity baseline gate.
- **`tests/matrix-parity-baseline.json`** — the reviewed divergence baseline
  for oracle 4.
- **`README.md`**'s "Divergence catalogue" — the human-readable explanation
  behind every registered baseline entry.

## Render-invariant check (`check-render-invariant.py`)

### What it guards against

An architectural regression rather than a formatting bug directly: the
comment-placement architecture (see [`commentHandling.md`](commentHandling.md))
requires that placement be decided exactly once, in `Comments.gren`, and
stored as a `CommentRole` — never re-derived from source rows once rendering
starts — and that the renderer decide verticality from the *rendered box
shape* (`isSingleLine` / `B.allSingles`), never from source position. A
`Render/*` function that reads a row/position to make a layout or
comment-placement decision is almost always a regression back toward the
row-based oscillation and crash class this architecture was built to remove
— and that regression wouldn't necessarily show up as a fixture diff or a
failed fuzzer run until much later, on some unlucky input.

### What it checks

It greps every `Render/*` module (comment- and string-aware, so it doesn't
false-positive on a row accessor mentioned inside a comment or string
literal) for row/position-accessor calls, and fails on any occurrence outside
a small, explicitly-justified allowlist of genuinely structural functions.

### How to run it

```bash
cd gren-format-lib/tests
./check-render-invariant.py
```

It needs no build step — it's a pure grep over source — and is cheap enough
that `run-tests.sh` runs it first, before compiling the test harness.

### When it fires

If a change legitimately seems to need a new row/position read inside
`Render/*`, treat that as a signal to stop and reconsider first — most such
needs are really about *placement*, which belongs in `Comments.gren` as a
`CommentRole`, not in the renderer. In the rare case where the read really is
structural, allowlist the function in `check-render-invariant.py` with a
reason.

### Where the code lives

- **`tests/check-render-invariant.py`** — the grep + allowlist.
- **`docs/commentHandling.md`** — the full comment-architecture model this
  check enforces.
- **`comment-arch.md`** — the bug-history / rationale that motivated the
  architecture.

## Property-based random generator (`gen-random.py`)

### What it guards against

Every gate above walks a fixed space: the matrix enumerates known shapes,
both fuzzers perturb comments/whitespace over the fixed fixture corpus, and
the audit walks the corpus too. None of them vary **structure**. A bug that
needs a conjunction of features nobody wrote by hand — the exact axis the
2026-07-18 real-corpus sweep proved productive on — has no fixture or matrix
cell to trigger it. `gen-random.py` builds random-but-legal Gren modules
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

**Rebuild the `gren-format` app first** — it shells out to
`../../gren-format/app`. Artifacts land in gitignored `gen-out/run-NNNNNN/`,
failures-only, bucketed by kind (`crash` / `ast-mismatch` / `non-idempotent` /
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

### Where the code lives

- **`tests/gen-random.py`** — the generator, shrinker, and oracle driver.
- **`GENERATOR.md`** — the full design spec (grammar, depth bounds, shrinking
  algorithm).

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

### Root vs. propagated findings

The audited predicates are recursive: their fallback arm is typically
`Array.any <predicate> children`. So one wrong answer at a leaf makes every
ancestor above it answer wrong too, and every caller reading those ancestors in
turn. A single underlying bug can therefore surface as dozens of findings.

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

### How to run it

```bash
cd gren-format-lib/tests
./audit-predicates.py -j 12                              # whole corpus
./audit-predicates.py -v                                 # list every finding, not just the summary
./audit-predicates.py -v testfiles/Formatter/Foo.formatted.gren   # one file
```

**Rebuild the `gren-format` app first** (`cd ../../gren-format && ./build.sh`) —
the driver shells out to the built app's `--audit-predicates` flag, so it audits
whatever formatter source was last compiled, not the current working tree. Exit
status is non-zero if any finding is reported. This machine has 16 cores; the
driver defaults to `-j 2`, so pass a higher `-j` for a fast whole-corpus sweep.

The corpus it walks is `testfiles/Formatter/*.formatted.gren` — the same fixture
set the effectful suite uses. The matrix (`matrix-syntax.py`) additionally runs
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
layout decisions read the rendered box rather than source rows is in
[`commentHandling.md`](commentHandling.md).
