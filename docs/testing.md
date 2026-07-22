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

<!-- TODO -->

## Idempotency fuzzer (`fuzz-idempotency.py`)

<!-- TODO -->

## Whitespace-canonicalization fuzzer (`fuzz-whitespace.py`)

<!-- TODO -->

## Construct × context syntax matrix (`matrix-syntax.py`)

<!-- TODO -->

## Render-invariant check (`check-render-invariant.py`)

<!-- TODO -->

## Property-based random generator (`gen-random.py`)

<!-- TODO -->

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
