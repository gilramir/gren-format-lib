# Property-based random AST generator (`gen-random.py`)

Status: **v1.8** — v1's core expression grammar (module header, imports,
function declarations, binops, records, record updates, arrays, `let`, `when`,
`if`, lambda, calls, field access, parens, atoms), plus line/block comment
injection; v1.1 added top-level type aliases, custom types (unions), and ports;
v1.2 added author-broken (multi-line, one `->` segment per line) types for
function signatures, type-alias RHS, and port types; v1.3 added multi-line
(triple-quoted) string literals as a value/argument-position atom, with
content-mutation (escaped quotes, embedded `\"\"\"`, trailing backslash,
literal tabs, blank rows, extra indentation); v1.4 added doc comments
(`{-| ... -}`, module-level and per-declaration); v1.5 added **richer
when-branch patterns** — string/char/array-literal patterns and `as`
aliasing — see [Richer patterns](#richer-patterns) below; v1.6 added
**constructor references** — a generated module now actually constructs and
pattern-matches the unions it declares, instead of only ever declaring them —
see [Constructor references](#constructor-references) below; v1.7 added **unary
minus, float literals, string/char escape sequences, destructuring `let`
patterns, and `exposing (..)`**; v1.8 added **char literal expressions, local
`let` function bindings (`f a b = ...`) with optional signatures, the bare
`.field` accessor function, and operator references (`(+)`, `(|>)`)** — see
[Char/accessor/operator atoms and let functions](#char-accessor-operator-atoms-and-let-functions)
below. Comments inside a broken type signature or a multi-line string's
surrounding expression, `as` nested in non-top-level pattern positions, and the
remaining coverage gaps (qualified constructor/type references, richer type
application, extensible record types, type/operator exposing, hex literals,
infix and effect-module declarations) remain the next expansion targets (see
[Grammar scope](#grammar-scope)). List patterns beyond fixed-length arrays are
NOT a gap — Gren has none.

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

### Doc comments

**v1.4 (implemented 2026-07-19):** `{-| ... -}` doc comments, both module-level
(`Module.doc`) and per-declaration (`Decl`/`TypeAliasDecl`/`UnionDecl`/
`PortDecl.doc`, mutually exclusive with the existing regular `lead` comment on
the same declaration — a doc comment stacked above a floating comment is an
untested combination, so the generator never produces it). Content is plain
prose (`doc_comment()`'s own word pool) since doc comments are AST-level, not
Context — excluded from the comment-preservation oracle entirely, so no
unique `kN` tokens are needed.

Placement rules were verified directly against the app first, not assumed:
a module doc gets exactly **one** blank line after the module header, and then
the *same* spacing logic that already follows the header applies again (one
blank before imports if any follow, else the standard two blanks before the
first top-level declaration) — modeled by simply emitting the module doc as
part of the header block rather than adding a parallel set of spacing rules.
A per-declaration doc glues directly above its declaration with zero blank
lines, exactly like the existing `lead` comment list already does structurally
— `emit_leading()` is the single shared function both `emit_decl` and
`emit_function_decl` call, so doc-vs-comment handling can't drift between
declaration kinds. (Aside, not modeled: a multi-line doc — opener alone on its
own line — gets a blank line auto-inserted before `-}` if the content doesn't
already end with one; verified stable/idempotent either way, so which the
generator picks doesn't matter. This APPEARS to be in tension with README
divergence #11's "gren-format leaves the entire doc-comment body exactly as
the author wrote it" claim, since it does change body content, not just
placement — flagged here as a possible doc inaccuracy, not fixed, since it's
tangential to this addition and doesn't trip any oracle.)

**Two more real formatter bugs found and fixed** by the very next sweep after
adding doc comments (0 quarantine, but 2 non-idempotent findings in the first
6000 seeds combined) — neither involves a doc comment directly; both are in
the pre-existing record-update comment-handling machinery, reached for the
first time via the RNG shift a new generator feature always causes:

1. **`makeRecordUpdateVerticalBox` ignored `CommentRole` entirely**, always
   placing a trailing comment on its own line regardless of role — violating
   the documented invariant that `RidesInline` glues exactly like
   `TrailsPrevious` everywhere except the flat-line-eligibility check
   (`LogicalPrintingTree.gren`'s own `CommentRole` doc comment says so
   explicitly). Verified against real elm-format that gluing is at least as
   reasonable a choice as the old behavior (elm-format itself does something
   third and different here, already an established area of divergence, so
   "matches elm-format" wasn't the bar — "matches the project's own stated
   invariant, and is idempotent" was). Fixed to glue onto a preceding REAL
   FIELD only, gated by `lastWasField` — mirrors `commentBracketListBox`'s
   more careful `pending`-item tracking, NOT `makeUnionBodyVerticalBox`'s
   simpler always-glue-onto-whatever-was-last approach, since the latter would
   have also chained a comment onto an unrelated PRECEDING comment (confirmed
   by a first attempt at this fix, which the checked-in `KitchenComments`
   fixture caught: two comments flanking `|` merged onto one line that should
   have stayed separate). Fixture `RecordUpdateFieldTrailingComment`;
   `KitchenComments.formatted.gren` regenerated (3 lines changed — exactly the
   comments that directly trail a real field value; everything else
   unchanged).
2. **`EmptyBracketed` (`[]`/`{}`) built via plain `lpnLeaf` instead of
   `lpnBracketNode`**, so its closing-bracket position was marked *inexact*
   even though it's always exactly known (`loc.end`) — unlike a populated
   bracket-list, which genuinely needs the separate exact-vs-fallback
   distinction. That let a same-row trailing comment one or two columns past
   `[]`/`{}` fall inside the inexact-close slack window and get absorbed into
   the surrounding field on reparse, flipping `{ z | next0 = [] } -- c`
   (correctly forced open by the comment) back to flat on the second format.
   Fixed by constructing both empty-literal sites in `InsertExpressions.gren`
   via `lpnBracketNode` with an explicit exact close. Fixture
   `EmptyBracketFieldTrailingLineComment`.

Both verified against the full effectful suite (227, up from 224), both
fuzzers, audit-predicates, and the 1738-cell matrix — all clean, 0
regressions, same registered-divergence counts.

### Richer patterns

**v1.5 (implemented 2026-07-19):** `PStr`/`PChar`/`PArray` (string/char/
fixed-length-array literal patterns — Gren has no cons/spread list pattern,
only fixed shapes like `[]`/`[ a ]`/`[ a, b ]`, matching array literal
expressions structurally) and `PAs` (`inner as name` aliasing). `PAs` is only
ever generated at the outermost position of a `when`-branch pattern
(`pattern()`) — every nested pattern position (lambda/function params, ctor
args, array items) calls `pattern_base()` directly instead, since nesting
`as` inside those hasn't been verified against the parser and isn't needed for
this addition.

The parenthesization rule for `PAs`'s inner pattern was verified directly
against the app, not assumed from the existing README/known-limitations
writeup — which turned out to **understate** the gap. The documented rule
("accepts `as` after a bare variable or wildcard, and after a parenthesized
constructor application, but not after an unparenthesized one" —
compiler-common#31) describes only the constructor-with-payload case,
but bare `as` also fails after a **0-argument** constructor and after a
bare **Int literal**:

```
n as whole        -- OK (bare var)
_ as whole        -- OK (bare wildcard)
"hi" as s         -- OK (bare string literal)
'a' as c          -- OK (bare char literal)
[ a, b ] as whole -- OK (bare array pattern)
{ x, y } as whole -- OK (bare record pattern)
(Just n) as whole -- OK (parenthesized ctor-with-payload — the documented case)
Nothing as whole  -- FAILS ("Expected keyword '->'") — 0-arg ctor, undocumented
0 as n            -- FAILS ("Expected keyword '->'") — Int literal, undocumented
(Nothing) as whole -- OK (parenthesized)
(0) as n           -- OK (parenthesized)
```

`emit_pat`'s `PAs` case parenthesizes exactly `PCtor`/`PInt`, matching this.

**Real formatter bug found and fixed — this one an ast-mismatch, not a
non-idempotency** (the formatted *output does not parse at all*, a more
serious class than the prior rounds' oscillations): the very first 2000-seed
sweep found 51 ast-mismatch cases, all one class. gren-format was **stripping
a semantically required paren**:

```gren
-- input:
(8) as z -> 0

-- gren-format's (buggy) output:
8 as z -> 0     -- does not parse!
```

This is not the "redundant parens" case (gren-format's documented, deliberate
policy of never stripping a paren the author wrote, anywhere) — this paren is
*required* for the output to parse at all, the same undocumented gap above.
Root cause: `InsertPatterns.gren`'s `argNeedsParens` — the predicate deciding
when a pattern needs parens before `as` — only covered `PCtor`/`PCtorQual`
*with an argument*, matching the documented (incomplete) compiler-common#31
description, missing the 0-arg-constructor and Int-literal cases. **Not
fixed by widening `argNeedsParens`** — it's shared with two other contexts
(a plain function-argument pattern, a nested constructor-payload pattern)
where a bare `0`/`Nothing` is already correct and doesn't need parens, so
widening it would be an unrelated, unnecessary behavior change there. Instead
added a new, narrower `aliasBaseNeedsParens` predicate used only at the
`PAlias` call site (`PInt` / any `PCtor` / any `PCtorQual` → parens; else
falls back to `argNeedsParens`). Verified against all 51 originally-failing
seeds, the full effectful suite (229, up from 227 — new fixtures
`AliasPatternIntNeedsParens` and `AliasPatternZeroArgCtorNeedsParens`), both
fuzzers, audit-predicates, and the 1738-cell matrix — all clean, 0
regressions. Re-swept 3000 seeds clean after the fix.

**2026-07-20: comments riding a broken signature's `->` (README divergence
#5), and the crash it found.** `Gen.maybe_arrow_comment` puts a `--` or
single-line `{- -}` on a random non-first segment of a broken arrow type
(`Decl.sig`, `TypeAliasDecl.rhs`, `PortDecl.type_`); `emit_type_multiline`
grew an `arrow_comment` parameter to place it (a line comment pushes its
segment to the next line with no `->`, since `--` can't share a line with
anything after it; a block comment glues `-> {- c -} Type` on one line) —
both shapes verified directly against the built app before wiring into the
generator. A `-n 500` sweep at the default comment-rate came back clean, but
raising `--comment-rate` to 0.5 (to exercise the new path harder — it only
fires on an already-1-in-8-ish broken-arrow-with-comment combination) found a
**pre-existing crash unrelated to the new grammar**: seed 809 shrunk to `{ y |
next0 = \a -> 0 } -- k13`, a decl-trailing line comment on a single-line
record UPDATE whose last field's value is a lambda. `gren format` emitted `{
y | next0 = \a -> 0 -- k13 }` — the comment swallowed the `}` that had to
follow it on the same line, producing unparseable output.

Root cause chain (three code paths, all in the comment-role/render-role
split from [[project_comment_arch_plan]]): (1) `Comments.gren`'s
`boxKeepsTrailingCommentOutside` had no entry for `SoftIndentedBlock` (a
lambda body / port payload has no bracket of its own to register, so
`commentInsideTrailingBracket` can never rescue a genuinely-inside comment
there) — a comment trailing the whole declaration sank past the field into
the lambda body's own children instead of stopping at the field boundary.
(2) Once excluded, the escaped comment needed a same-row glue rule to land on
the body's last rendered line rather than dropping to its own line —
`prevLineGlueRow`/`prevBlockGlueRow` gained a `SoftIndentedBlock` case
mirroring `AcrossOrVertical`'s call-flow rule (line comment glues
unconditionally; block only if the body ends in a bracket). (3)
`MakeRenderBox.gren`'s `commentForcesBracketOpen` — the check deciding
whether a comment forces the whole record update onto the vertical layout —
only scanned RecordUpdate's DIRECT children; a multi-field update wraps each
field in an `IndentedBlock`, so the escaped comment (now IndentedBlock's own
child, not RecordUpdate's) stayed invisible, and the flat-layout path glued
` }` onto the un-terminated comment's line regardless. Fix: a new
`recordUpdateForcesOpen` reaches one level into an `IndentedBlock` field
wrapper. All three were verified necessary and sufficient — reverting any one
reintroduces the crash. **First attempt was wider and wrong**: adding
`RecordUpdate` itself to `boxKeepsTrailingCommentOutside` (mirroring
`AllAcrossOrAllVertical`) also fixed the crash, but silently changed the
already-fixture-verified `RecordUpdateFieldTrailingComment` /
`RecordUpdateCommentBinopValue` behavior (a comment meant to trail the last
field, staying inside before `}`, moved outside instead) — caught by
`run-tests.sh`, not by the fuzzer, underscoring why the effectful suite runs
before any fuzzer sweep is trusted. Fixed forward-clean: 229 effectful tests,
both fuzzers, 1738-cell matrix (0 UNREVIEWED/BUGs), predicate audit,
corpus-check, the original seed 809, and a fresh 8000-seed sweep at
`--comment-rate 0.5` all clean.

### Constructor references

**v1.6 (implemented 2026-07-20):** until this addition, a generated module's
`union()` calls declared custom types that nothing else in the module ever
referenced — `pattern()`/`leaf()` only ever drew constructor NAMES from a
fixed generic pool (`Just`/`Nothing`/`Ok`/`Err`/`Leaf`/`Node`), unconnected to
any real declaration, so a declared union's variants were never actually
constructed or matched anywhere. This closed that gap: `Gen.declared_ctors`
(a `[(name, kind)]` list, `kind` ∈ `"none"`/`"record"`/`"value"`, mirroring
`variant_payload`'s three payload shapes) is appended to as each `union()`
call builds its variants, then read back by two new methods:

- `ctor_ref()` — replaces `leaf()`'s bare-`Ctor` branch. Half the time picks a
  declared constructor instead of the generic pool; a 1-arg declared
  constructor is applied to a matching-shape argument (`"record"` → a bare
  record-literal argument, `"value"` → a plain atom). **Always single-line**
  (`broken=False`, argument via the new `_flat_leaf()` — Var/Int/Str/Qual
  only, never recursing back into `ctor_ref`) — required because `leaf()` is
  called directly by `inline()`, whose contract is a GUARANTEED single-line
  result (`one_line`'s assertion). The richer, possibly-multi-line applied
  form lives in `mk_call` instead (below), which has no such contract.
- `pctor_ref(depth)` — replaces `pattern_base`'s constructor-pattern tail.
  Half the time matches a declared constructor with its REAL arity (0-arg
  bare, `"record"` via `PRecord`, `"value"` via a nested pattern) rather than
  the generic pool's arity chosen independently of any real declaration.
- `mk_call` (used only from `value()`, which carries no single-line
  contract) also now sometimes applies a declared 1-arg constructor, with the
  full depth/broken richness a normal call gets — including, for a
  `"record"`-payload constructor, a **bare record-literal call argument**
  (`Ctor { a = 1, b = 2 }`, no parens) — legal Gren, verified directly
  against the app before wiring in, and a shape the existing `arg()`
  machinery could never produce on its own (it only ever offers `atom()` or
  `Paren(value)` as a call argument, never an unparenthesized record
  literal).

**Generator bug found and fixed while building this** (not a formatter bug):
the first `ctor_ref()` draft applied a declared "record"-payload constructor
via `Call(Ctor(name), [self.mk_record(1)], broken=self.chance(0.3))` directly
inside `leaf()`. A 2000-seed sweep immediately hit a Python
`AssertionError: one_line on multiline node Paren` inside `emit_when`'s
scrutinee — `inline()` had called `leaf()`, which produced a `Call` wrapping a
`Record` that could itself go multi-line (a broken `Record`, or a field value
recursing through `value()` into a block), breaking `inline()`'s single-line
guarantee two frames away from where the violation was introduced. Fixed by
splitting the single-line-safe path (`ctor_ref`/`_flat_leaf`, used from
`leaf()`) from the richer possibly-multi-line path (`mk_call`, never reached
from a single-line context) rather than trying to thread a "must stay flat"
flag through `leaf()` itself.

Verified: 8000 seeds (1..8000) + 2000 at `--comment-rate 0.6 --max-depth 7`
clean (0 quarantine, 0 findings) after the fix, plus the full gate suite (231
effectful tests, both fuzzers, 1738-cell matrix — 0 UNREVIEWED/BUGs, unchanged
divergence counts — and the predicate audit) all clean, confirming this
generator-only change didn't perturb the formatter itself.

### Char/accessor/operator atoms and let functions

**v1.8 (implemented 2026-07-21):** three narrow expression-position gaps the
AST-vs-generator audit surfaced, plus local `let` functions.

- **Char literal *expressions* (`Chr`).** Char literals previously appeared only
  as `when`-branch patterns (`PChar`); the char-atom escape/normalization path
  in expression position (a `\u{...}` escape survives with its hex lowercased —
  the same normalization the string-escape path exercises, now reached in char
  position too) was never driven. Added to `leaf()`/`_flat_leaf()`, reusing the
  existing `char_content()` mutation, and eligible for an inline `{- -}` comment
  like every other single-line atom.
- **Bare `.field` accessor function (`Accessor`)** and **operator references
  (`OpRef`, `(+)`/`(|>)`).** Function-valued atoms — distinct from `Field`
  (`x.name`, which has a base) and from an inline `Binop` operator. Every
  operator in `BINOPS + PIPES` was verified to parse as `(op)` in value
  position, and both forms were verified legal in the tricky positions `leaf()`
  reaches (`if` condition, `when` scrutinee, binop operand, call fn/argument) —
  gren-format only parses and formats, so a function value used where a concrete
  value is expected is a *type* error it never sees, and does not quarantine.
- **Local `let` function bindings (`f a b = ...`) with optional signatures.**
  `let` bindings were value/destructure-only; `LetBind` grew `params` (making it
  a function binding, `lhs` then a `PVar` name) and `sig` (a single-line
  `name : Type` on the line directly above the binding, `name : Int -> Int` for a
  function). This is the let-flow blank-line / signature-attachment machinery
  that only *fixtures* reached before — now on the random co-occurrence axis.
  Params use `pattern_base` like lambda/decl params; a bare ctor param parses as
  separate params, which is harmless because the node tree is only an emission
  recipe and every oracle compares format-vs-reformat, not tree-vs-parse.

The shrinker needs no new cases: `Chr`/`Accessor`/`OpRef` are childless leaves
(replaced wholesale by the trivial-atom step), and a `LetBind`'s new `params`
(patterns) and `sig` (a type) are not expression slots, so `child_slots` is
unchanged. Verified: 7000 seeds — 2000 default, 2000 `--comment-rate 0.6`, 3000
`--max-depth 7 --comment-rate 0.5` — all clean (0 quarantine, 0 findings).

**Remaining expansion targets** (the still-open coverage gaps from the
2026-07-21 AST-vs-generator audit, in rough value order): local-function bodies
aside, **qualified constructor patterns (`Maybe.Just x`)** and **qualified type
references (`Maybe.Maybe a`)**; **richer type application** (concrete/multi-arg/
nested — `Dict String Int`, `Array (Array a)`, currently capped at `Array`/
`Maybe` of a single var); **extensible record types (`{ r | field : T }`)**;
**type/operator exposing** (`T(..)`, `(|=)`) and explicit module-header export
lists (always `(..)` today); **hex literals** (`0xFF`, expr and pattern); and the
low-frequency **infix declarations** and **effect modules**. Also still open:
comments *inside* a multi-line string's surrounding expression aside from the
trailing-comment shape already fixed; `as` nested in non-top-level pattern
positions (lambda/function params, ctor args, array items) — deliberately not
generated yet, unverified against the parser; list patterns beyond fixed-length
arrays (Gren has none — not a gap).

The generator is intentionally started small and correct (0 quarantine on the
core grammar) and expanded one construct at a time, verifying the quarantine rate
stays at ~0 after each addition. (2026-07-19: 10000 seeds (1..10000) + 800 at
`--max-depth 7` clean after the type-alias/union/port addition; a further 3000
clean after capping variant arity at ≤1; a further 7000 seeds (1..7000) + 800
at `--max-depth 7` clean after the author-broken-arrow-type addition; a further
3000 clean after the multi-line-string addition and its trailing-comment fix;
a further 10000 seeds (1..10000) + 800 at `--max-depth 7` clean after the
doc-comment addition and its two record-update/EmptyBracketed fixes; a further
3000 clean after the richer-patterns addition and its alias-pattern-parens
fix; a further 8000 seeds at `--comment-rate 0.5` clean after the
arrow-comment addition and its RecordUpdate/SoftIndentedBlock crash fix; a
further 8000 seeds (1..8000) + 2000 at `--comment-rate 0.6 --max-depth 7`
clean after the constructor-references addition and its single-line-guarantee
generator fix; and a further 7000 seeds — 2000 default + 2000 at
`--comment-rate 0.6` + 3000 at `--max-depth 7 --comment-rate 0.5` — clean after
the v1.8 char-expression / accessor / operator-reference / let-function
addition.)
