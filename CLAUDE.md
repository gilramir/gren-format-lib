# CLAUDE.md — gren-format-lib

`gilramir/gren-format-lib` is a Gren **package** (`platform: common`) that holds
the Gren formatter library. The formatter is consumed by:

- `gren-format/` — the standalone `gren-format` CLI (primary consumer)
- `compiler/` — the legacy `gren format` subcommand of the Haskell front-end

All formatter source lives in `src/Formatter/`. The package also hosts three
AST utility modules in `src/Compiler/`, moved here out of `compiler-common`
because only the gren-format tooling uses them:

- `Compiler.Ast.Compare` (`compareModules`) — semantic AST equality, used to
  verify a format preserves meaning
- `Compiler.Ast.Source.Json` (`encodeModule`) — JSON encoder for the source AST
- `Compiler.Parse.Context.Json` (`encodeContext`) — JSON encoder for parse context

All three are re-exposed by the package so the `gren-format` CLI and this
package's `tests/` can import them (their module names are unchanged).

## Sibling repos (expected at `../`)

| Path | Role |
|---|---|
| `../gren-format/` | Standalone CLI that imports this package |

## Build & check

Compile a module to surface type errors (use the **module name**, not a file path):

```bash
cd gren-format-lib
devbox run -- gren make Formatter
```

The package itself has no runnable app — it is a library. The `tests/` directory
is a separate Gren application that depends on this package locally.

## Tests

### Effectful suite (main gate)

```bash
cd gren-format-lib/tests
./run-tests.sh     # builds tests/app via devbox, then runs it
```

`run-tests.sh` recompiles the test harness against the formatter source directly
(the `tests/` app depends on `..` locally), so editing formatter source and
re-running `run-tests.sh` is enough — no separate library build step.

Test cases are in `tests/src/Test/Formatter/Format.gren`. Each calls:

```gren
assertPretty fsPerm "description" "FileBaseName"
```

which performs three checks:
1. **Formatting** — `format(testfiles/Formatter/<FileBaseName>.dirty.gren)` is
   byte-equal to `testfiles/Formatter/<FileBaseName>.formatted.gren`
2. **AST equivalence** — re-parsing the formatted output yields a semantically
   equal AST (catches formatting that changes meaning)
3. **Idempotency** — re-formatting the `.formatted` file changes neither the
   `Module` nor the comment/blank-line `Context`

**To add a test:** write both `<Name>.dirty.gren` and `<Name>.formatted.gren` in
`tests/testfiles/Formatter/`, then add an `assertPretty` line in `Format.gren`.
Generate the `.formatted` with:
```bash
node ../../gren-format/app --show <Name>.dirty.gren > testfiles/Formatter/<Name>.formatted.gren
```
Read it before trusting it — confirm the output is actually canonical.

### Idempotency fuzzer

Inserts a `{- ¤ -}` block comment into every inter-token gap, formats twice,
and requires byte-identical output. The safety net for comment-shift bugs.

```bash
cd gren-format-lib/tests
python3 fuzz-idempotency.py -j 12                                      # whole corpus
python3 fuzz-idempotency.py -v testfiles/Formatter/Foo.formatted.gren  # one file
```

**Rebuild the `gren-format` app first** (`cd ../../gren-format && ./build.sh`) —
fuzzers invoke `../../gren-format/gren-format.sh` as a subprocess, so they
require an up-to-date binary. Run after any change to comment handling, and
after adding any comment-bearing fixture.

### Construct × context syntax matrix

The corpus reaches only the syntax somebody thought to write, and both fuzzers
perturb *comments* and *whitespace* over that fixed corpus — **neither varies
syntax**. A bug needing a conjunction of features therefore has no fixture. This
is the syntax axis: it embeds every expression form in every context, in up to
four **layout variants**, and checks each one (**1738 cells**).

The variants are the author-broken axis (added 2026-07-18, after a record-literal
binop-field crash slipped through a flat-only matrix):
- `flat` — the paren-carrying atom on one line (the original 850 cells).
- `broken` — the same atom pre-broken across rows (valid in every context).
- `bareFlat` / `bareBroken` — the atom with its outer parens stripped, in
  **value-position contexts only** (record field, `let` binding, branch body,
  array item, …). This is the variant that catches value-position bugs: the
  paren-carrying atoms route a multi-line operand through the *handled*
  `ParenBlock` arm, so only the bare form reaches the crash's code path.

```bash
cd gren-format-lib/tests
./matrix-syntax.py -j 12                                  # whole matrix (all variants)
./matrix-syntax.py -v                                     # source + output per failure
./matrix-syntax.py --variant broken --variant bareBroken # author-broken variants only
./matrix-syntax.py --construct recordUpdate1 --context parenBinopArg
./matrix-syntax.py -k /tmp/failing                        # write failing cells out as .gren
./matrix-syntax.py --no-parity                            # skip oracle 4
./matrix-syntax.py --update-baseline                      # rewrite the parity baseline
```

**Rebuild the `gren-format` app first** — it shells out to it. Oracle 4 also
needs `elm-format` on PATH; without it the matrix says so loudly and runs the
other three rather than quietly reporting a thinner green.

Oracles 1–3 need no human review:

1. **Layout, both directions** — *flat-input variants only* (`flat`, `bareFlat`).
   Layout is author-driven — no page width, no fitter — so a construct written
   flat renders flat unless its content forces a break: a flat construct in a
   flat context **must** stay one line; anything involving `if`/`when`/`let`
   **must** break. Over-approximation (pre-breaking something that renders
   inline) fails the first; a construct that stops breaking fails the second.
   This is a flat-*input* truth, so it does not run on `broken`/`bareBroken` — a
   broken input has no local layout truth (gren collapses a broken-but-fitting
   binop), so those variants lean on oracles 2–4 instead.
2. `--show` internally does parse → render → reparse → AST-compare → render
   again → idempotency-compare, so a clean exit also buys AST equivalence,
   idempotency, and "the output parses". Each failure title is its own class.
3. `--audit-predicates` on every cell (see below), over generated syntax rather
   than only the corpus.

**Oracle 4 — elm-format parity.** Gren is a fork of Elm, so on shared constructs
the two formatters should agree byte-for-byte. Every cell is translated to Elm
and diffed against `elm-format --stdin`. Translating *real* Gren source to Elm is
lossy hand work — which is why the audit in the root `CLAUDE.md` is a manual
exercise — but the cells are built from a vocabulary this script authors, and
across all of it the only Gren-vs-Elm difference is `when X is` → `case X of`.
The translator is therefore one regex, and it is *exact* for that vocabulary
rather than approximate. A construct or context that is not valid Elm must
extend `to_elm`, or be given no Elm twin; a bad translation reports a fake
divergence.

Unlike 1–3, **oracle 4 is not a truth**: gren-format diverges from elm-format on
purpose (README "Divergence catalogue"), so it is gated against a reviewed
baseline in `matrix-parity-baseline.json`. Each diverging cell is registered with
a reason, and the matrix fails on a cell that diverges *unregistered*, or a
registered cell that *no longer* diverges (fixed, or the entry was always wrong).

The hazard is the fixtures' hazard — a baseline entry that is really a bug
freezes it as expected output. Three things push back: a reason of `UNREVIEWED`
is counted and printed on every run, so the debt is never silent; a reviewed
entry is expected to name a catalogue number, making registration a documentation
decision rather than a keystroke; and a divergence reviewed and found to be a
genuine bug gets a `BUG:` reason, which is **also** printed every run — being
understood is not the same as being acceptable, and a baseline entry is the
easiest place in this repo for a known bug to go quiet.

Current state: **1738/1738 pass oracles 1–3**; 1167 are byte-identical to
elm-format, with 571 registered divergences — 398 redundant parens (#10), 125
single-item-container collapse (#21, records *and* arrays), 38 precedence-split
binop chains (#17), 6 backward-`<|` flat layout (#14), 3 pipeline-`|>` alignment
(#19), 1 record-update `|>`-operand field indent (#22), **0 UNREVIEWED**, and
**0 known BUGs** — every divergence is a documented catalogue entry. The
author-broken axis found four real bugs, all **fixed**: a lambda body
over-indenting to +8 in array-item / nested-lambda-body positions
(`LambdaBodyIndentInBrackets`); a `let` as a `<|` body over-indenting its
`in`/result by 4 (`LetAsBackwardPipeBody`); a multi-line container operand
dropping below a dangling `|>` instead of gluing to it (`PipelineContainerOperand`);
and a bare `if`/`let` as an array item over-indenting its body by +4
(`BareIfListItem`, `BareLetListItem`). All four were the same class — an extra
`AcrossOrVertical` item-wrapper (or pipeline-step spread) stacking its +4 on a
block's own +4 — surfaced only because the author-broken axis feeds pre-broken
input. (A former divergence, a lambda record-field value keeping its head on the
`= ` line, was reviewed and eliminated the same way — it now drops whole below
`field =` like every other value, matching elm-format; the old catalogue #23 was
removed and later entries renumbered.) Use `-v` to see each divergence beside
elm-format's output.
`docs/redundantParens.md` is the reader-facing write-up of the #10 family,
every example verified against both formatters. gren-format never strips a
redundant paren, in any position, including call arguments — the former
one-layer-only call-argument stripping (and its `doubleParen/callArg*`
inconsistency) was removed entirely 2026-07-15.
`whenExpr/pipelineOperand` (a `(when …)` direct pipeline operand stranding the
`|>`) and `*/parenBinopArg` (a doubled `((if/when/let ...)` call argument
anchoring `else`/`in`/its inner `)` to the OUTER paren instead of the inner
one) were both fixed the same day, in `Render/MakeRenderBox.gren`:
`isMultilineLambdaParenBlockAnyBodyBox` now recognizes a ParenBlock whose sole
child is a `WhenFlow`, and `parenGenericFallbackBox` now checks
`parenContentLeadsWithBlockParen` (descends through `Pipeline`/`Binop` to the
leftmost operand) to pick the padded paren-wrap when that operand is itself a
paren-wrapped `if`/`when`/`let`.

**Reclassifying is not a formality.** When the 46 UNREVIEWED were reviewed, two
weaker tests both got it wrong: "same tokens once parens are deleted" cleared 45
of 46, and "does it still diverge with the parens stripped from the source"
cleared 39 — but the source-stripped form takes a different code path, so it
answers a different question. The decisive test is whether **elm's output has
fewer parens than gren's**: if elm keeps the same parens, the divergence cannot
be about parens. That found 4 cells where both formatters agree on the parens and
only the layout differs — real bugs that a blanket reclassification would have
frozen as expected output, including one already known.

Deliberately not covered, and stated in the script rather than hidden: multi-line
string literals (`"""x"""` does not parse on one line, so it cannot be a one-line
atom) and more than one comment per cell (a comment *run* has its own
all-or-nothing rules; `fuzz-idempotency.py`'s all-gaps pass generates one, but
without the elm-format oracle).

#### The comment axis (`--comments`)

Until 2026-07-31 comments were excluded here and left to the fuzzers. That left a
hole at the **intersection**: this matrix varies syntax and asks elm-format, the
fuzzers vary comments and ask only "is it stable?" — so a comment *placement*
divergence from elm-format was invisible to every gate in the repo. It is stable,
AST-equivalent and idempotent; nothing ever asked elm-format what it thought.
That hole hid both leading-`{- -}` pairing divergences (`7c20e15` in broken
calls, `cd774f5` in broken binop chains), and it was not slow-acting — `7c20e15`
was hand-checked against elm-format and gated the same day, and still shipped a
second divergence in a shape its author did not think to type. Manual parity
checking scales with imagination; an oracle over generated input does not.

`--comments` crosses the two axes: each syntax cell gets **one** comment injected
into an inter-token gap, then runs oracles 2–4. Four placements per gap (`{- -}`
/ `--`, each trailing the previous token or leading the next), because
trailing-vs-leading is exactly what the `CommentRole` classifier decides.
Atom-local gaps run for every cell; context-template gaps run once per context,
since they don't depend on which atom fills the hole.

```bash
./matrix-syntax.py --comments -j 12                     # whole axis (~39k cells, ~11 min)
./matrix-syntax.py --comments --construct binop --context top -v
./matrix-syntax.py --comments --comment-kind block --comment-pos lead
./matrix-syntax.py --comments --update-baseline         # rewrite the COMMENT baseline
```

It is a **deliberate gate, not part of a default run** — run it whole after
touching anything in the comment pipeline. A default run prints a line saying it
did not run, so the green never looks broader than it is.

Oracle 1 does not apply (a comment may legally force a break). Oracles 2 and 3
are unchanged truths, and one more is added that the syntax axis has no use for:
the output must contain the marker **exactly once** — a formatter can drop or
duplicate a comment and still be a stable fixed point, which no
diff-against-itself check can see. Oracle 4 gates against its own
`matrix-comment-baseline.json`.

Auto-classification composes with the syntax baseline: a comment cell whose
*uncommented* form already diverges is registered `INHERITED: <that reason>`
rather than booking fresh debt for the same #10. The only comment-position family
auto-classified is #13 (gren keeps a comment trailing the token it was written
after). A divergence where gren stranded the comment **alone on its own line** is
never auto-classified — that is the exact shape of both pairing bugs, so a
classifier that swept "the comment moved" into one family would have frozen the
very bug the axis was built to find.

**First run (2026-07-31) found 424 hard failures — all now fixed**, in three
commits:

- 26 emitted **invalid Gren** (a `--` inside a container's item let the container
  collapse to the flat form, putting the synthesized `]` inside the comment) —
  `8ce035b`.
- 184 were a `--` before a lambda body the author started on the `->` row —
  "Put a `--` before a lambda body inside the body".
- The last 214 were one mechanism: **a comment adds a source row, and the reparse
  reads a different layout**, because `forceVertical` (calls, binop chains) and
  the record-field lambda glue are decided from *source rows* — and the AST those
  rows come from has no comments in it. Fixed by folding the missing signal into
  the same decision: `NodeClassify.commentBreaksFlowRow` for the flow flags, and
  `renderGluedLambdaField` observing the body's rendered box for the field glue.
  That last one also fixed a comment-free instance of the same bug
  (`{ fld = \q -> { a = 1` / `, b = 2 } }` oscillated with no comment anywhere).

The axis now reports **0 failing cells**. What remains is ~16k unreviewed parity
divergences — debt, not failures; see `tbd.md`.

### Predicate/renderer agreement audit

Every other check in this repo is a **self-consistency** check — fixture diff,
AST equivalence, idempotency, both fuzzers. Output that is wrongly laid out but
deterministic, AST-equivalent and idempotent passes all of them. This audit is
the missing oracle: it checks the layout predicates against the renderer itself.

Several predicates in `Render/NodeClassify.gren` answer "does this subtree force
a hard break?" *before* rendering, so callers can lay out the code around it.
Each is a hand-written mirror of the renderer, and nothing forces them to agree.
The audit checks, per LPT node:

    predicate node == True   ==>   the node's own box renders multi-line

```bash
cd gren-format-lib/tests
./audit-predicates.py -j 12                              # whole corpus
./audit-predicates.py -v testfiles/Formatter/Foo.formatted.gren
```

**Rebuild the `gren-format` app first** — it shells out to `--audit-predicates`.

Findings are split into **root** and **propagated** (a recursive predicate's
`Array.any … children` fallback makes one wrong leaf answer wrong at every
ancestor too); only root findings are a work-list.

Under-approximation is deliberately not reported — these predicates claim only
the *unconditional* breaks, and a node can still break for reasons they do not
model (most often the author's own `forceVertical` layout).

The audit itself is `src/Formatter/Audit/PredicateAgreement.gren`. Most of the
former shape predicates (`subtreeHasVerticalBox`, `nodeSpansRows`, …) were
retired — verticality is now decided from the rendered box (`isSingleLine` /
`B.allSingles`), so the audit now covers only the one structural query that
remains (`isMultilineLambdaParenBlockBox`). See `docs/commentHandling.md`.

### Render-invariant check (`check-render-invariant.py`)

The architecture invariant — **no `Render/*` code reads a source row/position to
make a layout or comment-placement decision** (placement is the stored
`CommentRole`; verticality is the rendered box shape) — is enforced by
`tests/check-render-invariant.py`, which `run-tests.sh` runs first. It greps
`Render/*` (comment/string-aware) for row/position accessors and fails on any
outside a small allowlist of genuinely-structural functions. A new render-side
row-read is almost always a regression toward the oscillation/crash class this
architecture removed; if a use is truly structural, allowlist its function there
with a reason. Full model: `docs/commentHandling.md`; rationale: `comment-arch.md`.

### Whitespace-canonicalization fuzzer

Perturbs inter-token whitespace and requires `format(perturbed) == format(original)`.

```bash
cd gren-format-lib/tests
python3 fuzz-whitespace.py                 # default: stretch mode
python3 fuzz-whitespace.py --mode indent   # modes: stretch | indent
python3 fuzz-whitespace.py -j 12           # parallelise
```

This machine has 16 cores; both fuzzers default to `-j 2`. Use `-j 12` for a
fast whole-corpus sweep.

### Property-based random generator

Every gate above walks a fixed space: the matrix enumerates known shapes, both
fuzzers perturb *comments* / *whitespace* over the fixed corpus, the audit checks
the corpus. None vary **structure**, so a bug needing a conjunction of features
that nobody wrote by hand — the axis the 2026-07-18 corpus scan proved productive
— has no case anywhere. `gen-random.py` is that missing axis: it builds
random-but-legal Gren modules (structure **and** comments) with bounded depth and
checks four oracles per module. Full design in `GENERATOR.md`.

```bash
cd gren-format-lib/tests
./gen-random.py -n 2000 -j 12               # sweep
./gen-random.py --seed 12345                # replay one seed, verbose (+ shrunk)
./gen-random.py -n 500 --max-depth 6        # deeper nesting
./gen-random.py --no-comments               # structure only
./gen-random.py --promote 12345 --name Foo  # a fixed find → a fixture
```

The oracles: **`--pre-ast`** (parses at all — a failure is a *generator* bug, not
a formatter find; it lands in `gen-out/<run>/quarantine/` and is reported
separately, and this bucket must stay ~0); **`--show`** (buys no-crash +
AST-equiv + idempotent + reparses in one call); **comment preservation** (the
multiset of `(type, normalizedText)` from `--pre-context` on the input vs. the
formatted output — positions discarded, so a *moved* comment passes and only a
drop / duplication / invention / kind-change trips it; AST-compare is blind to a
dropped comment and idempotency only catches a *shift*); and **author-order
invariance** (`sort-order`) — the same module re-emitted with its import runs and
`exposing` lists in reversed order, each comment still on the same owner, must
format to the same bytes.

That last one is the only gate that sees a comment attached to the **wrong**
name: the multiset oracle discards positions on purpose, and a wrong-but-stable
attachment is still an idempotent fixed point, so both pass it. Emitting the same
module in two author orders is something only a generator can do. Two positions
are deliberately pinned, since a comment there anchors to the position rather
than to a name — the first slot of each import run (which owns the run's blank
line and its section-header comment) and index 0 of an exposing list (a comment
leading the first item is parsed as a header comment after `exposing`, so it does
not travel, while the same comment at index ≥ 1 does). Ties bail out, because a
stable sort makes author order observable there by design. See `GENERATOR.md`.

Layout decisions are baked into the node tree, so emission is a pure function of
the tree: `--seed` replays exactly, and the shrinker (tree-surgery + deterministic
re-emit) minimizes every failure to `input.min.gren`. Artifacts land in gitignored
`gen-out/run-NNNNNN/` — failures-only, bucketed (`crash` / `ast-mismatch` /
`non-idempotent` / `comment-loss` / `sort-order`), each with a self-contained
`report.txt` carrying the repro command and the pre-computed diff (for
`sort-order`, both author orders and both outputs). `--promote` copies the
minimized repro into `testfiles/Formatter/` and prints the `assertPretty` line.

**Rebuild the `gren-format` app first** — it shells out to `../../gren-format/app`.
When adding a construct to the grammar, verify the quarantine rate stays ~0 after
the addition (0 quarantine + 0 emitter exceptions = the generator is honest, and
only then are its crash/non-idempotent finds trustworthy). Note current-Gren
**constructor patterns take at most one argument** (`Ctor a b` does not parse;
multi-field variants carry a record) — a fact the generator encodes.

Two flags exist for unattended use: `--max-shrinks N` caps how many failures a
run minimizes (one bug can hit hundreds of seeds, and shrinking each one can eat
the whole run — the skipped count is printed and stored, never silent), and
`--seeds 1,2,3 --json` re-checks an explicit seed list with no shrinking and no
artifacts, one JSON verdict per line, exiting non-zero if any still fails.

### Long sweeps across sessions (`fuzzrun.py`)

`gen-random.py` sweeps a seed range and exits. `fuzzrun.py` drives it over days
without Claude Code in the loop: you give it a time budget, it splits that into
~10-minute chunks under `nice`, advances a persistent seed cursor per settings
profile, and records every failure with its repro.

```bash
cd gren-format-lib/tests
./fuzzrun.py run --for 2h      # sweep for two hours, then stop
./fuzzrun.py status            # cursors, coverage, failure counts
./fuzzrun.py failures -v       # what was found, with the report head
./fuzzrun.py resweep           # re-test open failures against this build
```

Config is `fuzzrun.toml` (tracked); state is `fuzzrun.db` (sqlite) and
`fuzzrun-out/` (both gitignored). Ctrl-C stops cleanly.

**Lanes.** Each `[lanes.NAME]` profile — comment density, nesting depth — has
its own cursor and a weight, and a session round-robins chunks across them by
weight so no profile starves. A lane's coverage is the contiguous prefix
`[base_seed, cursor)`: the cursor advances only when a chunk *completes*, so an
interrupted or timed-out chunk is re-swept rather than leaving a hole. Chunk size
is adaptive (measured seeds/sec, capped at 3× the lane's previous chunk), and the
final chunk of a session is sized to the time left — a chunk is never killed to
meet the deadline.

**Generations.** The grammar decides what a seed *means*, so `fuzzrun` hashes
`gen-random.py` and, when it changes, starts a new generation: cursors reset to
base, old results stay queryable under the old hash, and open failures become
`stale-grammar` — their seeds no longer generate the modules that failed, so
re-testing them proves nothing. **Promote any find you still care about to a
fixture before changing the grammar.** It asks before doing this (`--yes` to
skip the prompt, which a cron/`at` invocation needs). The same applies per-lane
when a lane's coverage-affecting parameters change.

**Failures dedupe** by `(bucket, minimized source)`, so one bug hit 400 times is
one entry with 400 hits. Past the per-chunk shrink cap, failures are recorded
unshrunk and dedupe by full source — that under-merges rather than hides, and
the unshrunk count is reported. `resweep` re-runs every recorded seed of each
open failure and closes the ones that now pass.

`run` refuses to start if the built app is older than the formatter sources —
a two-hour sweep of a stale binary tests the wrong code. Override with
`--allow-stale-app`.

## Inspecting formatter internals

Both the standalone CLI and the legacy `gren format` subcommand accept debug flags:

```bash
node ../gren-format/app --show       MyFile.gren   # formatted output to stdout
node ../gren-format/app --show-first MyFile.gren   # shows first formatting, to help debug non-idempotent cases
node ../gren-format/app --pre-ast    MyFile.gren   # parsed AST + context as JSON
node ../gren-format/app --pre-context MyFile.gren   # just the parse Context (comments) as JSON
node ../gren-format/app --lpt        MyFile.gren   # Logical Printing Tree as JSON
node ../gren-format/app --box        MyFile.gren   # the Box tree each decl renders to, as a JSON array
```

`--lpt` is the most useful debug flag for comment-placement and layout bugs.

## Formatter architecture

Pipeline: `Src.Module + Ctx.Context → LPT → Box → String`

```
Formatter                              entry point: prettyPrint
    Formatter.Logical                  logical-stage entry (module Formatter.Logical, file Logical.gren): runs lptFromAst then the comment/sort/blank-line passes
        Formatter.Logical.MakeLogical    AST → LogicalPrintingTree (lptFromAst — one OriginalRows per declaration)
            Formatter.Logical.InsertExpressions   expressions (one insert* per form)
            Formatter.Logical.InsertPatterns      patterns
            Formatter.Logical.InsertTypes         types
            Formatter.Logical.LPTHelpers          construction helpers (mkText*, plainAcross, …)
            Formatter.Logical.BinopPrecedence     operator fixity table
        Formatter.Logical.Comments            re-attaches comments from parse context
        Formatter.Logical.SortSymbols         sorts exposing lists + import groups
        Formatter.Logical.VerticalSpace       inserts blank lines between top-level items
    Formatter.Render                   render-stage entry (module Formatter.Render, file Render.gren): maps each RootBox child through the Box renderer, joins with newlines
        Formatter.Render.MakeRenderBox LPT → Box — recursive core: dispatch (one builder per LPBox constructor) + per-construct renderers
            Formatter.Render.BinopLayout   pure binop-chain layout assembly
            Formatter.Render.CommentBox    comment-node rendering (line / block / doc)
            Formatter.Render.FlowAssembly  FlowItem / SoftGlueAlignment types + pure flow-layout helpers
            Formatter.Render.NodeClassify  boolean predicates / structural queries over LPT nodes
            Formatter.Render.BoxOps        low-level Box / Line manipulation helpers
        Formatter.Render.Box           elm-format's Box IR (Line/Box, Tab tab-stops, prefix)
        Formatter.Render.FlowPolicy    shared inline/break decision layer
```

The Box renderer is the **sole backend** — the earlier `Formatter.Render.Doc`
renderer and the self-verifying Box/Doc guard were deleted at the full cutover.

`Render/MakeRenderBox.gren` was the whole Box renderer; its knot-free helpers
have been split into five sibling modules — `BinopLayout`, `CommentBox`,
`FlowAssembly`, `NodeClassify`, `BoxOps` — leaving `MakeRenderBox` as the
mutually-recursive dispatch (`makePBox`) plus the per-construct renderers. Gren
forbids circular imports, so only functions that never transitively reach the
`makePBox`/`buildFlowBox` recursion could move out. Import DAG:
`MakeRenderBox` → all five; `BinopLayout`/`CommentBox`/`FlowAssembly` →
`BoxOps`, `NodeClassify`; `BoxOps` and `NodeClassify` depend on neither.

Layout is **author-driven, not fit-driven**: there is no page width and no
layout search. Each box already knows whether it renders inline or vertical —
decided from the author's original source rows (`forceVertical`). Indent step:
**4** spaces (`grenIndent`, in `Render/MakeRenderBox.gren`).

**Key invariant:** every top-level declaration becomes exactly one `OriginalRows`
node directly under `RootBox`. Comments and blank lines are inserted as sibling
`OriginalRows` nodes by `Comments` and `VerticalSpace` after the tree is built.

## Authoritative documentation

- `docs/formatterRules.md` — what every formatting rule does, with worked
  examples for every construct. Read first when reasoning about formatter
  behavior. (`README.md` has a shorter version plus one worked example.)
- `docs/howItWorks.md` — a conceptual, step-by-step tour of the pipeline
  (parse → Logical Printing Tree → render plan → text).
- `docs/elmFormatComparison.md` — every place `gren format` deliberately
  diverges from `elm-format`, and why.
- `DEVELOPER.md` — orientation guide for extending the formatter
  with new syntax: the full checklist, position rules, comment-attachment
  hazards, and the "things to worry about" section.
- `docs/commentHandling.md` — the comment architecture: the `CommentRole`
  model (placement decided once in `Comments.gren`, never re-derived from rows
  in `Render/*`), how each renderer consumes the role, verticality from rendered
  box shape, and the enforced invariant. `comment-arch.md` is the companion
  rationale / bug-history that motivated it.
