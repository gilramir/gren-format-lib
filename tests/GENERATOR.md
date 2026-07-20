# Property-based random AST generator (`gen-random.py`)

Status: **v1.3** — v1's core expression grammar (module header, imports,
function declarations, binops, records, record updates, arrays, `let`, `when`,
`if`, lambda, calls, field access, parens, atoms), plus line/block comment
injection; v1.1 added top-level type aliases, custom types (unions), and ports;
v1.2 added author-broken (multi-line, one `->` segment per line) types for
function signatures, type-alias RHS, and port types; v1.3 added **multi-line
(triple-quoted) string literals** as a value/argument-position atom, with
content-mutation (escaped quotes, embedded `\"\"\"`, trailing backslash,
literal tabs, blank rows, extra indentation) — see
[Multi-line string literals](#multi-line-string-literals) below. Doc comments
and patterns beyond the core set are the next expansion targets (see
[Grammar scope](#grammar-scope)).

This is `qe.md` avenue #2. Every other gate in this repo varies **one** axis over
a fixed base — `matrix-syntax.py` embeds one construct in one context,
`fuzz-idempotency.py` perturbs comments over the corpus, `fuzz-whitespace.py`
perturbs whitespace, `audit-predicates.py` checks predicate/renderer agreement.
The 2026-07-18 real-corpus scan proved the productive axis is **feature
co-occurrence** — every bug it found (A–E) was a *conjunction* of features no
single-axis tool could reach. A corpus reaches only the co-occurrences somebody
already wrote. This generator samples the co-occurrence axis directly, and
independent of which real packages happen to exist: it builds random-but-legal
Gren modules with bounded depth and checks the standing invariants on each.

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
./gen-random.py --promote <seed> --name SomeDescriptiveName
```

copies `input.min.gren` → `testfiles/Formatter/SomeDescriptiveName.dirty.gren`,
runs `--show` to produce the `.formatted.gren`, and prints the exact
`assertPretty` line to paste into `tests/src/Test/Formatter/Format.gren`. A
random find becomes a frozen regression fixture: the generator's job is
*discovery*, the fixture suite's job is *preventing recurrence*.

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
./gen-random.py --promote <seed> --name Foo   # promote a fixed find into the fixture suite
```

**Rebuild the `gren-format` app first** (`cd ../../gren-format && ./build.sh`) —
this shells out to `../../gren-format/app`, same as the other gates.

## Grammar scope

**v1 (implemented):** module header, `import` (incl. `exposing`/`as`), function
declarations with optional signature, binop chains (all operators from
`Formatter.Logical.BinopPrecedence`, including `|>`/`<|`), record literals,
record updates, arrays, `let`/`in`, `when`/`is`, `if`/`then`/`else`, lambdas,
function calls, field access, parenthesized expressions, atoms (int, string,
var, qualified name, constructor); core patterns (var, `_`, literal,
constructor-with-args, record destructure); line and block comments at the
bug-prone gaps (own-line before decl / `let` binding / `when` branch / broken
container item; inline block before an atom; trailing after a binding or decl).

**v1.1 (implemented 2026-07-19):** top-level `type alias` (record RHS, arrow
RHS, con/var/app RHS; 0-2 type params), custom types / unions (`type Name =
Ctor1 | Ctor2 T | Ctor3 { .. }`, flat or author-broken variant list, per-variant
lead/trailing comments), and ports (`port module` header emitted iff the module
has ≥1 port; both the `Type -> Cmd msg` and `(Type -> msg) -> Sub msg` shapes).

**Variant payloads are capped at 0 or 1 argument**, matching current real Gren
— [gren-lang.org/news/161224_gren_24w](https://gren-lang.org/news/161224_gren_24w)
states custom-type variants are limited to 0 or 1 parameter (`type Person =
Person String Int` is no longer valid; use a record: `Person { name : String,
age : Int }`). An early version of this generator instead allowed 2-3 bare
arguments per variant (`Circle Int`, `Rectangle Int Int` — the shape the
existing `TypeUnion.formatted.gren` / `UnionLayoutByAuthor.formatted.gren`
fixtures already use) and found that **this repo's parser does not actually
enforce the 0-or-1 rule**: `Ctor Int Int` (2 bare constructor names) parses
fine, but `Ctor b Int` or `Ctor (Array a) Int` (a var/paren'd/app type in a
non-final slot) fails right after that argument, while the same shapes with
the complex argument moved LAST (`Ctor Int b`, `Ctor Int (Array a)`) parse
fine. That's not a deliberate "last argument may be complex" grammar rule —
it's the parser inconsistently enforcing a restriction the language spec says
is unconditional (reject any variant with >1 argument, full stop; instead it
only rejects some >1-argument shapes and accepts others). Filed upstream as
[compiler-common#32](https://github.com/gren-lang/compiler-common/issues/32).
The generator sidesteps the inconsistency entirely by capping variant payloads
at ≤1 argument (con/var/app/arrow, or a record), matching current valid Gren —
which also makes moot the separate observation that a `record`-type payload
must be a variant's sole argument (`Ctor { field : T } X` fails to parse right
after the `}`): with arity capped at 1, a record is simply the one argument
and there is no "several bare arguments" case to collide with.

**v1.2 (implemented 2026-07-19):** author-broken (multi-line) types for
function signatures, type-alias RHS, and port types (the class-B shape). Per
README's "Type signatures": a signature/alias-RHS/port-type that's an arrow
chain can be author-broken across rows, one `->`-segment per line, `->`
leading each continuation (`emit_type_multiline`); a non-arrow RHS (record,
con, var, app) has no `->` boundary and always stays inline. A `broken` flag
is baked per-declaration (`Decl.sig_broken`, `TypeAliasDecl.broken`,
`PortDecl.broken`), same pattern as the existing bracketed-container `broken`
flags. Nested/inner types (record field types, `app` args, a paren'd atom)
stay single-line always — only the outermost type of a signature/alias/port
is ever multi-line.

**Bug found and fixed while building this:** `gen_type`'s "arrow" branch can
recursively nest an arrow tuple inside one of its own elements (e.g.
`("arrow", [("arrow", [A, B]), C])`), which is harmless for single-line
`emit_type` (string-joining with the same `" -> "` separator is associative,
so a nested-vs-flat tree renders as the identical string) but under-counts
`->` boundaries for the per-line segment breaker, producing an incorrect
layout like `A -> B` / `-> C` (2 lines, 1 real segment merged with another)
instead of the canonical `A` / `-> B` / `-> C` (3 lines, one segment each).
Fixed by flattening the arrow tree (`_flatten_arrow`) before splitting into
lines; a `("paren", ...)`-wrapped arrow correctly does NOT get flattened,
since that genuinely represents one opaque parenthesized segment (e.g. README's
own `(String -> Bool)` example, or a port's `(Type -> msg) -> Sub msg`).

### Multi-line string literals

**v1.3 (implemented 2026-07-19):** `MultilineStr` — a `"""..."""` triple-quoted
string, generated as a bare atom (`atom()`) and as a full value-position
alternative (`value()`), so it appears both as a whole declaration/binding body
and glued into binop chains / call arguments, matching real usage like
`"prefix " ++ x ++ """..."""`. It never needs parens — like any string — so
it's included directly alongside the leaf/field/paren atoms, not treated as a
block construct the way `if`/`when`/`let` are.

Content-line legality was verified directly against the built app before
writing the generator (not assumed): a content row may be indented **deeper**
than the block's base column freely (that extra indent is just part of the
row's own text), but never **less** — under-indenting is a real parse error,
"Multi-line string lines are not indented equally". A row may also be **wholly
empty** (zero characters, no padding at all) — that's the one exception.
`multiline_string_line()` never emits an under-indented row. Content mutation
covers the shapes real bugs have come from before: an escaped quote pair
(`\"word\"`), an embedded escaped triple-quote (`\"\"\"word\"\"\"`), a trailing
escaped backslash, a literal embedded tab character, and a wholly blank row.

**Real formatter bug found and fixed** (not a generator issue): a 1000-seed
sweep immediately found 22 non-idempotent cases, all one class — a `--`
comment trailing a multi-line string's closing `"""` at the end of a
declaration stayed indented on the first format, then dropped to column 0 on
the second:

```gren
fn0 =
    """
    alpha
    """
    -- k1        (format 1: indented)
-- k1            (format 2: dropped to column 0 — non-idempotent)
```

Root cause: `Comments.gren`'s `prevLineGlueRow` and `prevBlockGlueRow` — the
functions that decide which row a following `--`/`{- -}` comment glues onto —
each match on `LPBox` kind, and neither had a case for `MultilineString`, so
both silently fell through to their `_ -> -1` default. That made the
classifier think a same-row trailing comment could never glue onto a multi-line
string's close, so it always emitted `LeadsOwnLine` — pushing the comment onto
its own new line "for now" at the body's indent, which is not the same
decision the *next* format pass makes for that new (now own-row) position,
hence the oscillation. Fixed by adding `MultilineString _ -> lastRenderedRow
node` to both functions (the same delegation `ParenBlock` and the union-variant
`AcrossOrVertical` case already use) — a multi-line string is *always*
multi-line, so no conditional check is needed, unlike `ParenBlock`, which only
delegates when it actually spans multiple rows. Verified against all 22
originally-failing seeds, the full effectful suite (225, up from 224 — new
fixture `MultilineStringTrailingLineComment`), both fuzzers, audit-predicates,
and the 1738-cell matrix — all clean, 0 regressions. Re-swept 3000 seeds clean
after the fix (was 978/1000 before).

**Next expansion targets:** doc comments; richer patterns (`as`, list
patterns — respecting the parenthesized-`as` parser gap); comments *inside* a
broken type signature (a `--`/block comment riding a `->`, per README
divergence #5 — not yet generated, v1.2's broken types carry no comments);
comments *inside* a multi-line string's surrounding expression aside from the
one trailing-comment shape just fixed; referencing a module's own declared
union constructors from `pattern()`/`leaf()` (currently generated unions are
declared but never constructed/matched elsewhere in the module — a coverage
gap, not a correctness one).

The generator is intentionally started small and correct (0 quarantine on the
core grammar) and expanded one construct at a time, verifying the quarantine rate
stays at ~0 after each addition. (2026-07-19: 10000 seeds (1..10000) + 800 at
`--max-depth 7` clean after the type-alias/union/port addition; a further 3000
clean after capping variant arity at ≤1; a further 7000 seeds (1..7000) + 800
at `--max-depth 7` clean after the author-broken-arrow-type addition; a further
3000 clean after the multi-line-string addition and its trailing-comment fix.)
