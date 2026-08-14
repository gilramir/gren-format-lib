# Property-based random AST generator (`gen-random.py`)

Every other gate in this repo varies **one** axis over a fixed base:
`matrix-syntax.py` embeds one construct in one context, `fuzz-idempotency.py`
perturbs comments over the corpus, `fuzz-whitespace.py` perturbs whitespace,
`audit-predicates.py` checks predicate/renderer agreement. The productive axis
is **feature co-occurrence** — a real-corpus sweep found five bug classes and
every one of them was a *conjunction* of features no single-axis tool could
reach — and a corpus reaches only the co-occurrences somebody already wrote.

This generator samples that axis directly, independent of which real packages
happen to exist: it builds random-but-legal Gren modules with bounded depth and
checks the standing invariants on each. What it can emit is
[Grammar scope](#grammar-scope), below; how that grew, generation by generation,
is [`docs/llm/generator-log.md`](../docs/llm/generator-log.md), which is also
where a `v1.x` reference in a source comment resolves.

## What it checks (the oracles)

The generator produces **input**; the formatter is under test. Per module:

1. **Parseable** — `app --pre-ast file`. A parse failure here is a *generator*
   bug (or a parser bug), **never** a formatter find. These go to `quarantine/`
   and are reported separately. This bucket trending to ~0 is how we know the
   generator is honest; any residual is a template to fix.
2. **The three standing invariants, for free** — `app --show file`. `--show`
   internally does parse → format → reparse → AST-compare → format-again →
   idempotency-compare, so one clean exit buys **no-crash + meaning-preserved
   (AST-equivalent) + idempotent + reparses**. A non-zero exit is a real find;
   `--show`'s own message names the class (crash / ast-mismatch /
   non-idempotent).
3. **Comment preservation** (a 4th oracle this generator uniquely enables) — the
   formatter must never drop, invent, duplicate, or change the kind of a
   comment. See below.
4. **Author-order invariance** (a 5th, likewise only possible here) — the same
   module rewritten with its sortable lists in a different order must format to
   the same bytes. See below.
5. **Predicate/renderer agreement** — `app --audit-predicates file`. A layout
   predicate that claims a node breaks must be telling the truth about that
   node's own rendered box. See below.
6. **The `--remove-unused-imports` transform** — the CLI's *other* whole-file
   transform, held to the same four invariants plus two of its own. See below.

### Remove-unused-imports oracle

**Added 2026-08-10.** `--remove-unused-imports` deletes imports, trims
exposing lists, invents `-- removed import Foo` placeholders and renumbers
every row after a cut (`ShiftPositions`). It is a second transform over the
same AST and comment stream as the formatter, and until this oracle existed
**nothing but a fixture suite had ever run it** — no fuzzer, no matrix cell,
no gate — even though row-shifting underneath comments is the exact shape
that keeps producing comment bugs on the ordinary path.

Per module (skipped entirely when the module has no imports, where the
transform has nothing to do):

1. `--remove-unused-imports --show` buys the same four invariants `--show`
   buys without it — no crash, reparses, AST-equivalent, idempotent — but
   about the **post-removal** AST. Buckets are prefixed `rui-` so a find is
   never confused with one on the ordinary path; that path cannot be at
   fault, because oracle 2 already passed on this very module.
2. **Removal is a fixed point**: running it again on its own output must
   change nothing (`rui-not-fixpoint`).
3. **No pair of surviving comments swaps unless the author or the sort asked
   for it** (`rui-comment-order`). Removal may legitimately *delete* a
   comment — one inside a removed import, or trailing a trimmed name on that
   name's row — but never invent, duplicate, or reorder one, and a comment
   that moves relative to its neighbours has changed which declaration it
   belongs to. The generator's unique `kN` tokens make this exact, and the
   placeholders carry no `kN`, so they are correctly invisible to it.

   Getting the baseline right took three tries, and the false positives are
   worth knowing about because each one is a legitimate mechanism: the import
   run **sorts**, and a comment riding an import travels with it (so the
   formatted order is right); *removing* an import takes its comment out of
   the run and it falls back to where the author wrote it (so the authored
   order is right); and a module can do both at once. Since a PAIR has only
   two possible orders, the two baselines together allow at most both — so
   when they agree on a pair, that is the only order either mechanism can
   produce, and the check flags exactly the pairs that flip anyway. A third
   mechanism it cannot model — a `-- removed import Foo` placeholder becomes
   the lead of the import below it and the two travel to that import's sorted
   position (fixture `GroupsSeparate`) — stands the check down for any module
   whose output contains a placeholder. Measured cost of that gate: of the
   three seeds that caught the row-overlap bug, two go quiet and one still
   fires.

**It found two real bugs on its first day**, both in the row bookkeeping the
transform does under comments, and both invisible to every gate that existed
before it.

**The first**, at ~2.3% (28 finds in 1,200 modules across the three lane
settings): *emptying* the import list re-homes a doc comment.
`Compiler.Parse.Module` parses the module's docs slot **before** its imports,
so a `{-| ... -}` above the first declaration is that declaration's doc only
because an import stands between it and the header. Remove the last import
and the same comment reparses as the MODULE's doc — the declaration silently
loses its documentation. No output spelling avoids it: the parser skips line
and block comments while looking for the docs slot, so the existing
`-- removed import Foo` placeholder does not shield it. Fixed by giving the
docs slot a `{-| removed import Foo -}` of its own
(`RemoveUnusedImports.docShield`); six fixtures in the CLI suite pin the
shape, including the three shapes that must NOT get one.

**The second**, found by check 3 at ~4% once the first was fixed: a removed
import frees a row that a surviving comment is still standing on. A block
comment glued in front of an import (`{- a` / `b -} import Foo`) opens on an
earlier row and closes on the import's own, and survives the removal because
its START row is outside the import's range — while going on occupying every
row it spans. Freeing those rows shifted the next comment UP INTO the middle
of it, and the formatter then rendered the two in the order their collided
rows implied: the comment written below came out on top. Fixed by charging
each cut for the rows a surviving comment still covers
(`RemoveUnusedImports.chargeOverlappingComments`), fixture
`GluedLeadSpansRows`.

Both were reachable only through the flag: `--show` alone exits 0 on the very
same modules.

`tests/test-oracle-rui.py` proves the oracle is not vacuous. Two of its three
buckets are proven against the real app — stash either fix, rebuild, and the
seeds above fire (`rui-ast-mismatch` on 30000030/43/52/83,
`rui-comment-order` on 40000007/67/101) — and `rui-not-fixpoint`, which no
live bug reaches, is proven by driving the oracle with a stubbed `run_app`.
The same script pins the two shapes that must NOT fire: a comment legitimately
*deleted* by the transform, and a module with no imports (which must not even
invoke the app).

### Comment-preservation oracle

Comments live in the parse **Context**, not the AST, so oracle 2's AST-compare is
blind to a *dropped* comment, and idempotency only catches a comment that
*shifts*, not one that vanishes on the first format. This oracle closes that gap.

Extraction uses the **real lexer**, never regex (regex over Gren source trips on
`--` inside a string, `{-` inside a `"""…"""`, char literals):

```
comments(f) = app --pre-context f  →  JSON .comments[]  →  multiset of keys
key = (type, normalizedText)          # positions discarded
assert  comments(input.gren) == comments(formatted.gren)
```

- **Positions are discarded** — the formatter is *supposed* to move comments, so
  reordering / re-indenting / re-attaching to a different token is invisible
  here, as it should be. Only a genuine **drop, duplication, invention, or
  kind-change** trips it. Whether a surviving comment landed in the *right* place
  is a different question, owned by oracle 2's idempotency check (a mis-attached
  comment that oscillates) and by promoted fixtures.
- **type** ∈ `line` / `block`, straight from the Context JSON, so a `-- x` never
  spuriously matches a `{- x -}`, and a kind-change (a real bug) is caught.
- **normalizedText** — `block` verbatim (the "block-comment verbatim" rule means
  their bytes don't change); `line` right-trimmed of trailing whitespace (the one
  normalization the formatter legitimately does). If the formatter turns out to
  rewrite a comment interior in a way this doesn't model, widen the
  normalization to match — but verbatim is expected to hold.
- **multiset, not set** — two identical `{- x -}` must stay two; a dropped
  duplicate is a real find. (The generator numbers its comments — `{- k0 -}`,
  `-- k1` — so every comment is unique unless a duplicate was intended, making
  "dropped vs. moved" unambiguous.)
- **Doc comments (`{-| … -}`) are not covered here** — they are carried in the
  **AST** (module / declaration documentation), not the Context comment stream,
  so oracle 2's AST-compare already covers them. The generator does not emit doc
  comments in v1.

### Author-order invariance oracle (`sort-order`)

The formatter sorts two things: the names in an `exposing ( … )` list, and the
imports within a run (`docs/sorting.md`). The point of sorting is that the
author's order stops mattering — so:

```
assert  format(m) == format(permute(m))
```

where `permute` rewrites the same module with those lists in a different order,
each comment still attached to the same owner. Emitting *both* orders is
something only a generator can do; every other gate in this repo has a single
fixed input per case.

**What it catches that nothing else does.** A comment that travels with the
wrong neighbour is invisible to oracle 3, which discards positions by design,
and invisible to idempotency, because a wrong-but-stable placement is still a
fixed point. It shows up here as two author orders disagreeing. This is the
`ExposingSortCommentToFront` / comment-chain bug class — historically found by
reading a fixture diff, which only works for cases somebody wrote by hand.

**Reversal, not a shuffle.** It is a maximal reordering and it is deterministic,
so `--seed` stays an exact replay.

**Two pinned positions**, both because a comment there is anchored to the
*position* rather than to a name, so moving the name out from under it changes
the output for a legitimate reason:

- **The first slot of each import run**, which carries the run's blank line and
  its `anchor` section header. Those describe the slot; the imports beneath them
  move, they don't.
- **Index 0 of an exposing list.** A comment leading the first item is not
  attached to that item at all — the parser hands it back as a header comment
  after `exposing` (`docs/sorting.md`, "A comment written before the first
  name"), so it stays at the front while the names sort, whereas the same
  comment at index ≥ 1 travels with its name. Verified directly against the app:
  the two shapes format differently, so permuting across that boundary would
  report a false find.

The oracle also **bails on a tie** — duplicate module names in a run, or
duplicate base names in an exposing list — since a stable sort makes the
author's order observable there on purpose (`ImportSameModuleStableSort`).

Both pins are load-bearing rather than superstition, and that was measured, not
assumed: removing the index-0 pin makes the oracle fire on 1/120 seeds, and
letting the run's blank/anchor travel with its import makes it fire on 33/100.
Roughly **85%** of generated modules have something to permute.

A failure of any kind on the reordered twin — including a crash or a
non-idempotency that only the twin triggers — is bucketed as `sort-order`,
because the artifact a human needs is the *pair* of inputs; the twin's own class
is named in the report's message. The failure directory holds `input.gren`,
`permuted.gren`, both formatted outputs, and the unified diff between them.

### Predicate/renderer agreement oracle (`predicate-lie`)

Oracles 1-4 are all **self-consistency** checks: they compare the formatter
against itself or against its own input. Output that is wrongly laid out but
deterministic, AST-equivalent, idempotent and comment-preserving passes every one
of them. This is the one oracle here that is not.

`audit-predicates.py` runs the same check over the fixture corpus and
`matrix-syntax.py` over its generated cells; until 2026-08-09 nothing ran it over
**random structure**, which is the only place a hand-written mirror predicate
meets a conjunction of features nobody thought to type.

Only **root** findings count — a recursive predicate's `Array.any … children`
fallback makes one wrong leaf wrong at every ancestor too, so the propagated ones
are the same bug counted again. An audit that cannot RUN is a `gen-error`, not a
finding: the module already parsed and formatted by the time this runs, so a
non-zero exit is this harness talking to the app wrongly.

**Proved non-vacuous by breaking what it watches**: adding a `\_ -> True`
predicate to `auditedPredicates` takes a 20-seed run from 20/20 clean to 20/20
`predicate-lie`, each report naming the predicate, the box kind and the node.
Zero-because-nothing-is-checked reads exactly like zero-because-all-agree.

It costs about **18%** of throughput (17.1 → 14.1 seeds/s at `-j 12`,
`--max-depth 5`), which is one extra app invocation on top of the five the other
oracles already make.

## Legal-layout emission (the crux)

Gren is layout-sensitive (`let`/`in` binding alignment, `when` branch alignment,
"a body indented past its head", top-level decls at column 0), so newlines cannot
just be sprinkled — that mostly yields non-parsing garbage. Instead the tool is a
**deliberately-dumb second pretty-printer** that makes layout choices *different
from and independent of* the real formatter, but only ever **legal** ones:

- **Baked decisions, pure emission.** Generation resolves every random choice
  (flat vs. broken per node, indent widths, which gaps carry comments) and
  stores it *in the node tree*. Emission is then a pure function of the tree.
  This is what makes `--seed` replay exact and — critically — makes **shrinking**
  sound: tree surgery + deterministic re-emit reproduces the same failure minus
  the removed part, because the surviving nodes keep their baked layout.
- **Randomized but legal.** Bracketed constructs (records, arrays, parens, calls)
  are layout-free and get a free flat/broken coin-flip. The layout-sensitive
  constructs (`let`, `when`, `if`, function bodies) always emit correct
  alignment, randomizing only within a legal range (indent width, body
  same-line-vs-next-line).
- **Parenthesize to stay parseable.** A block expression (`if`/`when`/`let`/
  `lambda`) or a binop chain is emitted **bare only in delimited or definition
  value positions** where it is legal without parens — record field value, array
  item, `let`-binding body, `when`-branch body, lambda body, `else` position —
  and **parenthesized everywhere else** (call argument, binop operand, `then`
  position, `when` scrutinee, field-access / update base). This is *exactly* the
  distribution that surfaced the author-broken bugs (`BareIfListItem`,
  `LambdaBodyIndentInBrackets`, …): the bare-in-value-position forms route a
  multi-line block through the code paths those bugs lived in.
- **The parse-check is the safety net.** Any template that ever emits
  non-parsing output shows up in `quarantine/` with the offending `.gren`, so
  loosening layout randomization is safe — a mistake is quarantined, not counted
  as a formatter find.

## Shrinking

A raw random failure is a huge unreadable module. On every failure the shrinker
greedily minimizes the **node tree**, re-emitting deterministically and re-running
the same oracle, keeping any change that preserves the failure:

- drop a top-level declaration (keep ≥1),
- replace a subtree expression with a trivial atom (`0`),
- drop a record field / array item / `when` branch / `let` binding (keep ≥1),
- drop a comment,
- reduce a call to its function, unwrap a paren.

`input.min.gren` — the shrunk reproducer — is the file you open to fix the bug.

## Artifact management

Gitignored tree under `tests/gen-out/` (override with `--out`), **failures-only
by default** so a 5000-case run doesn't write 5000 files:

```
tests/gen-out/
  latest -> run-000123/                 # symlink to the most recent run
  run-000123/
    run.json                            # master seed, -n, weights, max-depth,
                                         #   app build id (hash of the app), counts per bucket
    SUMMARY.txt                         # one scannable line per failure: seed · class · min size · one-liner
    quarantine/                         # PARSE failures = generator bugs, NOT formatter finds
      <seed>.gren
      <seed>.stderr
    failures/
      <seed>/
        input.gren                      # full generated source (unshrunk), for context
        input.min.gren                  # ← the shrunk minimal reproducer; the file you open
        formatted.gren                  # formatter output (empty if it crashed)
        formatted2.gren                 # 2nd format, present for non-idempotent finds
        report.txt                      # class · seed · exact repro cmd · --show stderr · the diff
```

Design choices that make a *find* into a *fix*:

- **Buckets by failure class** — `crash / ast-mismatch / non-idempotent /
  comment-loss`, plus `quarantine` for parse-fails walled off from the finds.
- **`report.txt` is self-contained** — class, exact reproduce command, raw
  `--show` stderr, and the *relevant diff already computed*: format¹-vs-format²
  for non-idempotent, the missing/extra comment list for comment-loss.
- **Durable repro = the stored `.gren`, not just the seed.** A seed reproduces
  byte-identically only against the *same generator code*; the moment the
  generator is edited the seed drifts. So the actual source files are the
  permanent artifact; the seed is for "re-run under the current generator", and
  the **app build id** (hash of the `app` binary) records which formatter build
  produced the failure, so an already-fixed stale failure is obvious.

### The bridge to a permanent fix — `--promote`

The payoff loop, matching how this repo already works. Once a bug is fixed:

```
./gen-random.py --promote <seed> --name SomeDescriptiveName --dir SuiteDirName
```

copies `input.min.gren` → `testfiles/SuiteDirName/SomeDescriptiveName.dirty.gren`
(the fixture corpus is one directory per suite — `--dir` names which suite this
find belongs to, e.g. `BracketComments`), runs `--show` to produce the
`.formatted.gren`, and prints the exact `assertPrettyIn` line to paste into
`tests/src/Test/Formatter/Format.gren`. A random find becomes a frozen
regression fixture: the generator's job is *discovery*, the fixture suite's job
is *preventing recurrence*.

Workflow: run → open `latest/SUMMARY.txt` → pick a class → open
`failures/<seed>/report.txt` + `input.min.gren` → fix the formatter → rerun that
seed to confirm → `--promote` into the fixture suite.

## CLI

```
./gen-random.py                       # default N random modules, report failures
./gen-random.py -n 5000               # how many modules
./gen-random.py -j 12                 # parallel workers (this machine has 16 cores)
./gen-random.py --seed 12345          # replay one master seed (single module, verbose)
./gen-random.py --max-depth 6         # expression nesting budget
./gen-random.py --comment-rate 0.3    # probability a legal gap gets a comment
./gen-random.py --no-comments         # structure only (isolate layout bugs)
./gen-random.py --keep-all            # also write passing cases (debug the generator)
./gen-random.py --out /path           # artifact root (default tests/gen-out)
./gen-random.py --promote <seed> --name Foo --dir SuiteDirName  # promote a fixed find
```

**Rebuild the `gren-format` app first** (`cd ../../gren-format && ./build.sh`) —
this shells out to `../../gren-format/app`, same as the other gates.

## Grammar scope

What the emitter can produce today. The generation-by-generation history — what
each shape was added for and what it found — is
[`docs/llm/generator-log.md`](../docs/llm/generator-log.md).

**Module and declarations.** A module header (plain, `port module`, or
`effect module … where { command =, subscription = }`), and one that deliberately
disagrees with its body's contents. `import`, with `exposing` and `as`. Function
declarations with an optional signature, `type alias`, custom types, ports, and
`infix` fixity declarations.

**Types.** Constructors, variables, application, records, extensible records and
arrows; qualified type names; nested application beyond one argument; comments
inside a record type.

**Expressions.** Binop chains over every operator in
`Formatter.Logical.BinopPrecedence`, `|>` and `<|` included, optionally ending in
a bare lambda; record literals and updates; arrays; `let`/`in`, including
let-bound functions; `when`/`is`; `if`/`then`/`else`; lambdas; calls; field
access; parentheses. Atoms are ints (decimal and hex), floats (plain and
scientific), strings, `"""…"""` multi-line strings, chars, vars, qualified names,
constructors, accessors and operator references.

**Patterns.** Variables, `_`, named wildcards (`_foo`), literals of every atom
kind, constructors with arguments (qualified included), record destructuring, and
`as` aliasing — nested, and aliasing an alias (`(x as a) as b`).

**Comments.** Line, block and doc comments, at the gaps where placement bugs
live: own-line before a declaration, a `let` binding, a `when` branch or a broken
container item; inline before an atom; trailing a binding or declaration; glued
to the front of an import; the two slots a lambda has; before a multi-line
string. A comment may span several rows. Several comments may be **chained** onto
one row, or written as a **run** of one or two, each on its own row, at a `when`
branch's lead and a `let` binding's lead. Import runs are generated with the
anchoring shapes `docs/sorting.md` specifies, and so is a module header's own
`exposing` list.

**Author layout.** Layout here is author-driven, so a construct with two legal
authorings must be able to emit both. Containers, types and parameter-slot
array/record patterns carry a break flag; `when` branches, `if`, lambdas and
definition bodies carry their own layout flags (body glued to the `->` or on its
own row, blank lines between branches or not).
