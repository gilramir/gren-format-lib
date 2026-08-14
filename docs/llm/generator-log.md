# `gen-random.py` — the grammar log

The version history of the generator's grammar: what each generation added, why
that shape was unreachable before, and what adding it found. Entries are dated
and are **records**, not statements of current behaviour — a claim here was true
when it was written.

The generator's spec — its oracles, its emission rules, shrinking, artifacts and
CLI — is [`tests/GENERATOR.md`](../../tests/GENERATOR.md), which also states what
the grammar covers today. Read that first; come here when you need to know
whether a shape has already been tried, or what a past generation's sweep found.

Some entries reference `tbd.md`, `qe.md` and `scan.md`, three working documents
that were deleted when 1.0.0 was prepared. What survived of them is in
[`attic.md`](attic.md) and in `docs/testing.md`.

---

## The grammar, generation by generation

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
(Since v1.31 the header keyword also *disagrees* with the body ~12% of the
time — see [Mismatched `port module` header](#mismatched-port-module-header).)

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

### Qualified constructor patterns

**v1.9 (implemented 2026-07-21):** `PCtor` grew an optional `mod` — a
qualifying module (`Maybe.Just y`), drawn from the same fake-module pool
`Qual`/`_flat_leaf` already use for qualified *value* references (`String`/
`Array`/`Dict`/`Maybe` — arbitrary pairing, since gren-format never
type-checks). Only applied to a **generic-pool** pick (`self.ctors`), never a
`declared_ctors` one — those are the generated module's own unions, already in
scope unqualified, so qualifying them would be a shape nothing real ever
writes. Wired into both callers that emit a generic-pool constructor pattern:
`pctor_ref` (when-branch / lambda / array item / ctor-arg position, bare or
applied to a nested pattern) and `let_pattern` (let-binding LHS, arity-0 only,
same constraint as the unqualified case). Verified directly against the app in
every position each reaches before wiring in — bare 0-arg, applied to a
pattern argument, a bare qualified 0-arg let LHS, a record-payload argument,
and `as`-aliased (`(Maybe.Just b) as whole`) — all parse and format identically
to the unqualified case (`emit_pat` just prepends `mod + "."`; the existing
paren rules — arity-1 needs parens as a let LHS, `PAs`'s inner always
parenthesizes a `PCtor` — are unaffected by qualification, since they test
`isinstance(..., PCtor)`, not the name).

The shrinker needs no new case: patterns aren't expression slots (`child_slots`
never descends into a `when`-branch pattern, a lambda param, or a let LHS —
only their *bodies*), so a `PCtor.mod` is invisible to `variants()` regardless.
Verified: 9000 seeds — 3000 default (1..3000), 3000 `--comment-rate 0.6`
(80000..82999), 3000 `--max-depth 7 --comment-rate 0.5` (90000..92999) — all
clean (0 quarantine, 0 findings). No formatter source changed, so no gate-suite
rerun was needed (generator-only change).

### Qualified type references

**v1.10 (implemented 2026-07-21):** type *names* can now be qualified with a
fake module (`Maybe.Int`, `Array.String`, `Maybe.Array a`), via a new
`qualify_type_name` helper that ~30% of the time prepends `self.pick(self.mods)
+ "."` — the same fake-module pool (`String`/`Array`/`Dict`/`Maybe`) that `Qual`
value references and v1.9 constructor patterns already draw from (arbitrary
pairing, since gren-format never type-checks). Wired into the two type-name
emitters, `gen_type` and `variant_arg_type`, at both the 0-arg `con` pick and
the `app` head. Emission is transparent — `emit_type` renders the name string
verbatim, so a qualified name needs no new emit case (exactly like `PCtor.mod`
in v1.9). Verified directly against the app before wiring in — as a signature
type, alias RHS, record field, port payload, and variant-arg type, both as a
bare `con` and as an `app` head — all parse and format identically to the
unqualified name.

Only the *name* is qualified, not the type variables or the whole application
(`Maybe.Array a`, never `Maybe.(Array a)`), matching how real qualified type
references are written. The shrinker needs no new case (a type is not an
expression slot; `child_slots` never descends into a signature).

### Richer type application

**v1.11 (implemented 2026-07-21):** type application was capped at `Array a` /
`Maybe a` — a single-var argument under one of two arity-1 heads. It now spans
the real shape space: **concrete args** (`Array String`), **multi-arg heads**
(`Dict String Int`, `Result Error a`), and **nested application** (`Array (Array
a)`, `Maybe (Dict String Int)`). Two new helpers replace the old inline `app`
tuple in `gen_type` and `variant_arg_type`:

- `gen_type_app` picks a `(head, arity)` from the new `self.type_apps` pool
  (`Array`/`Maybe` arity 1, `Result`/`Dict` arity 2 — mirroring Gren core
  types) and emits `arity` arguments. The head is run through
  `qualify_type_name` like any other type name, so `Array.Dict String Int` can
  occur (the parser does not enforce arity, so a qualified/fake head with any
  count still parses).
- `gen_type_arg` produces one argument — a concrete `con`, a `var`, or (when
  depth allows) a nested `gen_type_app` — bounded by the same depth counter as
  the rest of type generation.

No new emit case: `emit_type`'s `app` arm already renders `head arg…`, and
`_type_atom` already parenthesizes a nested `app` (or `arrow`) argument. This
parenthesization is load-bearing for variant payloads — a variant's single arg
is emitted via `_type_atom`, so a multi-arg app becomes `Ctor (Dict String
Int)`, never `Ctor Dict String Int` (which would reparse as a three-argument
variant, a different AST). Arguments are kept to con/var/app; a bare `arrow` or
`record` argument (`Array (a -> b)`, `Array { f : T }`) needs its own
verification pass and is left as a separate target. The concrete leaf-con list
that was duplicated inline in `gen_type`/`variant_arg_type`/`gen_type_arg` is
now the module-level `TYPE_CONS`.

Verified against the app before wiring in — concrete, multi-arg, nested, and
qualified-head applications as a signature type, alias RHS, record field, port
payload, and variant arg all parse and format identically to their canonical
form. The shrinker needs no new case (a type is not an expression slot).

### Extensible record types

**v1.12 (implemented 2026-07-21):** the record-type branch of `gen_type` now
~35% of the time emits an **extensible record** `{ base | field : T, … }`
instead of a plain `{ field : T, … }`. The `base` is a type variable drawn from
the same pool as a bare `var` type — which for an alias RHS is the alias's own
params (`gen_type(2, params)`), so `type alias Ext a = { a | … }` falls out
naturally; gren-format never type-checks, so any lowercase base parses. New IR
kind `("exrecord", base, fields)` with one new `emit_type` arm; no `_type_atom`
change (a record is self-delimiting by `{ }`, never parenthesized as an arg) and
no shrinker case (a type is not an expression slot). Like every record type it
is emitted inline — the formatter breaks it (base on the `{` line, `|`/`,`
fields +4 beneath) only when the author wrote it broken, which this generator
never does for a record type.

**Surfaced a real formatter bug, fixed the same day** (`InsertTypes.gren`): an
extensible record *type* built its LPT node via plain `lpnNode`, which records
no closing-`}` position, so a trailing `--` after `}` at a declaration's end
oscillated col 4 ↔ col 0, and an own-line comment before `}` oscillated too. A
record-*update expression* and a plain record type both build through
`lpnBracketNode` (which stores the `}` as `lastBracketEnd` for the
comment-placement descent guards) and were fine. Routing the extensible type
through `lpnBracketNode locType.end` too fixed both shapes. Fixture:
`ExtensibleRecordTypeTrailingComment`. This is the class the corpus/matrix can't
reach — a comment adjacent to a construct nobody had hand-written a fixture for.

### Type / operator exposing

**v1.13 (implemented 2026-07-21):** exposing lists gained the two shapes the
formatter sorts but the generator never emitted — **type exposing** (`T` and the
constructor-opening `T(..)`) and **operator exposing** (`(|=)`) — in both
positions:

- **Import `exposing` lists** (`import_exposing_items`): were value names only;
  now a mixed, arbitrary-order list of value names, type names (bare or open
  `T(..)`, from a fake `exp_types` pool), and operators (`exp_ops`). These name
  things in *other* modules, which a generated single module never defines, so
  any parseable spelling is fine (verified they sort operators → types →
  values).
- **The module header export list** (`module_exposing`): was always the wildcard
  `(..)`; now ~50% an EXPLICIT list built from the module's OWN declared names —
  a union may be exposed open (`Name(..)`) or closed, every other decl by its
  bare name. Explicit lists reference only real declarations, so the module is
  well-formed, not merely parseable. Stored pre-rendered on `Module.exposing`;
  the shrinker resets it to `(..)` when it drops a decl, so a reduced module
  never exposes a removed name.

The lists are emitted in arbitrary author order precisely so the formatter's
sort (operators → types → values, alpha within each — `SortSymbols`) is
exercised on every run; the sort is AST-safe (exposing order is already
canonicalized for the existing corpus, so AST-compare treats it as a set). No
new emit path beyond the header/import strings, and no shrinker case beyond the
`(..)` reset.

### Hex integer literals

**v1.14 (implemented 2026-07-21):** an int literal (expression *and* pattern) is
now ~25% a **hex literal** rather than always decimal. `gen_int` / `gen_pint`
pick a log-uniform magnitude up to 2^44 — spanning the everyday small values and
the `>= 2^35` range — and `Int`/`PInt` carry a `hex` flag rendered by `_int_text`
as `"0x" + format(v, "x")` (LOWERCASE digits, so the formatter's uppercasing —
`intToHex` — is exercised; `0xdeadbeef` → `0xDEADBEEF`). The shrinker is
unaffected (a hex `Int` is an atom; its slot-replacement placeholder stays the
decimal `Int(0)`).

Hex generation deliberately reaches above 2^35 to guard a formatter bug this
target uncovered and fixed first (commit `6428cbf`): `intToHex` recursed on
`n // 16`, and Gren's `//` compiles to JS `(a / b) | 0` — 32-bit-signed — so any
literal `>= 2^35` was silently corrupted and the AST check then *refused to
format valid input* (`0x800000000`). The fix (`floor (toFloat n / 16)`) makes
hex round-trip up to 2^53 - 1, the largest exact JS integer; beyond that the
parser itself is lossy, so the generator caps at 2^44. Pinned by the pure
`intToHex` unit suite and the `HexLiteralLarge` end-to-end fixture.

### Infix declarations

**v1.15 (implemented 2026-07-22):** `infix left 6 (+++) = infixFn0` — fixity
declarations. `Compiler.Ast.Source.Module` carries these on a dedicated
`binops : Array (Located Infix)` field, parsed by a standalone loop
(`Compiler.Parse.Module.operatorLoopParser`) that runs strictly after imports
and before every other top-level declaration — so they live on a new
`Module.infixes` list, never mixed into `decls`, and are always emitted as a
single contiguous block right after the imports. The formatter side needed no
work (`processInfixDecls`/`StInfixDecl` in `MakeLogical.gren` already existed,
documented in README's "Infix operator declarations" and already covered by the
`InfixWrapped`/`KitchenSink` fixtures) — this addition is purely the missing
generator emitter for a construct the formatter already handled.

Verified directly against the app before wiring in, not assumed from the
existing fixtures: the exact grammar (`infix (left|right|non) <int> (<symbol>)
= <lowerName>`, symbol built only from `Compiler.Parse.Operator`'s accepted
charset `+-/*=.<>:&|^?%!` and distinct from its five reserved exact-match
tokens `.`/`|`/`->`/`=`/`:`); that a custom symbol never used elsewhere in the
module (e.g. `+++`, `<+>`, `^^`) parses and formats fine standalone, with no
need to also emit a binop expression using it; that an own-line leading
comment, a same-row trailing comment, and an own-line comment *between* two
infix declarations in a group all parse, format, and stay idempotent (`--show`
on a hand-written probe file, exit 0); and that `InfixDecl` gets no `doc` field
(`Src.Infix` has none, unlike every other declaration kind) — mirrored by
`emit_leading`'s existing doc/lead mutual-exclusivity falling through to plain
`lead` automatically via `getattr`.

Emission is always single-line (`emit_infix`) — README states an infix
declaration is always written on one line regardless of input layout, and the
already-checked-in `InfixWrapped` fixture covers the author-broken-collapses-to-flat
case directly, so the generator does not need an author-broken variant to
exercise that path. `Module.infixes` is wired into the shrinker generically:
`list_containers` yields `(m, "infixes", 0)` so the existing "drop a list item"
step can remove individual infix declarations down to zero, and
`comment_clearers` gained a loop over `m.infixes` for `lead`/`trailing` — no new
case was needed in `variants()`'s "drop a top-level decl" step, since infix
comments/removal are fully covered by the generic list-container and
comment-clearer machinery already in place for every other list-typed field.

Verified: 8000 seeds — 3000 default (1..3000), 3000 `--comment-rate 0.6`
(1600000..1602999), 2000 `--max-depth 7 --comment-rate 0.6`
(1700000..1701999) — all clean (0 quarantine, 0 findings). No formatter source
changed, so no gate-suite rerun was needed (generator-only change, same as
v1.9/v1.10).

### Effect modules

**v1.16 (implemented 2026-07-22):** `effect module Foo where { command = MyCmd,
subscription = MySub } exposing (..)` — the effect-manager module-header form.
`Compiler.Ast.Source.Module.effects : Effects` is a 3-way sum
(`NoEffects`/`Ports`/`Manager`), and `Manager` itself is `Cmd`/`Sub`/`Fx { cmd,
sub }` — command-only, subscription-only, or both, never neither. The formatter
side needed no work (`MakeLogical.gren`'s `processModuleLine`/`buildWhereBlock`,
~13 existing fixtures, README's "Comments in an effect module's header" section)
— purely a missing generator emitter, like v1.15.

Verified directly against the app before wiring in: `effect module` is legal in
*any* user module (no parser-level restriction — the one `-- TODO` comment in
`Compiler.Parse.Module.gren` about ports+effects is about that specific
combination, not effect modules generally, so the generator simply never emits
a `port` declaration alongside an `effect module` header, matching how ports
and effects are separate, mutually exclusive header keywords); `where { ... }`
is mandatory the moment the `effect` keyword is used (`effect module Foo
exposing (..)` with no `where` clause fails to parse — confirmed, not assumed);
and — the one surprising finding — **gren-format unconditionally canonicalizes
a two-handler clause to command-then-subscription order**, regardless of which
order the author wrote, confirmed both with and without a comment present. A
subscription-first input still round-trips fine (`AmbiguousEffectModule`
already covers that reordering), but a comment riding the reordered handler
lands somewhere the input's own column position no longer predicts, so the
generator always bakes canonical (command-first) order — it gets full coverage
of the single-line and comment-attachment paths without redundantly
re-exercising the already-fixture-covered reordering path. A short block
comment glued to either handler's name (mid-clause on `command`, or trailing on
the last handler) was verified to stay attached exactly as README describes,
in canonical order.

`Gen.effect_header` bakes which handler(s) are present (never neither), each
naming a fresh `manager_type` — a `UnionDecl` forced to a single `msg` type
param (`type EffCmd7 msg = ...`, matching `core/src/Task.gren`'s `type MyCmd msg`
convention, though the parser doesn't check this at all) via a new optional
`name`/`params` override on `Gen.union`. The manager decls are spliced to the
front of `Module.decls` so they participate in every existing decl-level
mechanism for free — `module_exposing`'s explicit-list building, the
shrinker's variant-list dropping, `declared_ctors` (so `ctor_ref`/`pctor_ref`
can construct/match the manager type's own constructors elsewhere in the
module, same bonus v1.6 gave regular unions). `Module.effect` is `None` or a
list of `(field, name, comment|None)` tuples in emission order; `emit_where`
renders it, always inline (no broken variant, same reasoning as `emit_infix`).

Shrinker: dropping a top-level decl (`variants()` step 1) now also drops that
decl's entry from `m.effect` if it was a manager type, collapsing `m.effect` to
`None` if that empties it — an effect module can never end up with a `where {}`
naming a removed type, or a `where` clause with neither handler. `m.effect`
itself was deliberately **not** added to `list_containers` (which would let the
generic "drop a list item" step remove a handler independently of its manager
decl) — that path is only useful for reaching a state the decl-drop path
doesn't already reach, and an orphaned handler-with-no-backing-type is a
confusing repro for no shrinking benefit. `comment_clearers` gained a case for
`m.effect`'s per-handler comment. Verified by direct unit exercise of
`variants`/`comment_clearers` on a generated both-handlers-with-comments
module, not just by the sweep: dropping either manager decl correctly leaves
the other handler's clause entry intact and reflows the where-clause; dropping
both empties `m.effect` to `None`.

Verified: 8000 seeds — 3000 default (1..3000), 3000 `--comment-rate 0.6`
(1800000..1802999), 2000 `--max-depth 7 --comment-rate 0.6`
(1900000..1901999) — all clean (0 quarantine, 0 findings). No formatter source
changed, so no gate-suite rerun was needed (generator-only change).

### Nested `as` patterns

**v1.17 (implemented 2026-07-22):** `as` aliasing, previously only generated at
the outermost position of a `when`-branch pattern (v1.5), now nests inside
lambda/function/let-bound-function params, a constructor's own argument, and
array items — `f ((Just n) as whole) = whole`, `Just (n as whole) -> whole`,
`[ n as first, m ] -> first`.

Verified directly against the app before wiring in, not assumed from v1.5's
top-level rules: `as` is grafted onto exactly one parser production
(`Pattern.parser` = `parserNoAlias` + optional `as` suffix), not part of
`parserNoAlias` itself or the ctor-argument/param-list machinery — but `parser`
is reached recursively from a parenthesized sub-pattern and from an array
item's own parsing, so those two positions accept a **bare** alias with no
extra wrapping beyond what v1.5's existing rule already adds (0-arg-ctor/`Int`
alias bases still need their own inner paren, unchanged by nesting depth). A
constructor's own argument slot and a function/lambda/let-bound-function
**parameter slot** are the two positions that parse via the narrower
non-alias-aware production directly, so a `PAs` used there needs exactly one
*additional* outer pair of parens around the whole alias, on top of whatever
inner paren the base already needs — confirmed for every inner-pattern kind
(var/wildcard/0-arg-ctor/ctor-with-arg/array/record) via direct probes, and
this single rule (`emit_pat`'s ctor-argument loop, and the new `emit_param`
used at every param call site) was sufficient in every case.

**The one genuine hazard, caught only by direct verification, not reasoning
from the v1.5 rules:** a *fully bare* (zero extra parens) alias of a
ctor-with-argument in a parameter slot doesn't fail to parse — it silently
**reparses as two separate parameters**. `f Just n as whole = whole` (no
parens at all) is accepted, but as `Just` (a bare 0-arg pattern) followed by
`n as whole` (a separate, fully independent aliased param) — because the
ctor's own argument-consumption doesn't reach across the `as` at all, so the
parser backs off to treating `Just` alone as its whole first param and
resumes independently from there. This is a **different AST**, not a parse
failure, so no oracle (parseable / AST-equiv / idempotent / comment-preserving)
would ever catch a generator emitting this by accident — it would just
silently fail to exercise the intended shape while still passing every check.
Always emitting the full double-wrap (`emit_param`) sidesteps this
entirely — the reason `emit_param` is a hard requirement here rather than an
optional canonicalization.

Implementation: `pattern_base`'s existing body became `_pattern_base_core`;
the public `pattern_base` wraps ~8% of results in a bare `PAs` (matching
`pattern()`'s own top-level rate), which is enough to reach the ctor-argument,
array-item, and param-slot recursions this function already backs — no new
call sites were needed, since every non-top-level pattern position already
goes through `pattern_base`. `pattern()` gained an `isinstance(base, PAs)`
guard so it never wraps an already-`PAs` result in a second, untested
alias-of-an-alias (`(x as a) as b` — not generated, not verified). Rendering
needed two changes: `emit_pat`'s ctor-argument loop gained an `elif
isinstance(a, PAs)` case (one extra wrap), and a new `emit_param` (used at
every function-decl/lambda/let-binding param call site, replacing bare
`emit_pat`) adds the same extra wrap for the parameter-slot position — array
items needed no change at all, since a plain `emit_pat(item)` inside
`PArray`'s existing loop was already correct.

No shrinker changes: patterns are not expression slots (`child_slots` never
descends into a pattern), so a nested `PAs` anywhere in a pattern tree is
already opaque to the shrinker exactly like every other pattern-level addition
before it (v1.9's `PCtor.mod`, v1.13's exposing lists) — confirmed by direct
unit exercise of `variants()`/`comment_clearers()` on a generated module
containing nested `as`, not just by the sweep coming back clean.

Verified: 8000 seeds — 3000 default (1..3000), 3000 `--comment-rate 0.6`
(2000000..2002999), 2000 `--max-depth 7 --comment-rate 0.6`
(2100000..2101999) — all clean (0 quarantine, 0 findings). No formatter source
changed, so no gate-suite rerun was needed (generator-only change).

**v1.19 (implemented 2026-07-22): record type comments.** `gen_type`'s
record/exrecord branches previously ALWAYS emitted flat/single-line (noted as
a known gap in the v1.11 write-up's comment on `emit_type`'s `exrecord`
branch) — this generator had never once broken a record TYPE across lines,
let alone put a comment inside one, even though the formatter has hand-written
fixture coverage for exactly that shape (`RecordTypeLayoutByAuthor`,
`ExtensibleRecordTypeTrailingComment`, `SignatureRecordTypeComment`) —
including a real bug (`ExtensibleRecordTypeTrailingComment`, v1.12's
comment-oscillation fix) that a property-based sweep could never have
reproduced or guarded against a regression of, since the generator was
structurally incapable of emitting the shape that triggered it.

`gen_type` gained a `top` parameter: `True` only at the two call sites that
generate the WHOLE type of a function signature or a `type alias` RHS
(`Gen.decl`'s no-arrow-segment case, `Gen.type_alias`), `False` everywhere
else (every nested field type, type-app argument, and arrow segment). Only a
`top` record/exrecord may be generated `broken` (a fresh coin flip,
independent of the existing arrow-breaking `broken` flag), and only a
`broken` record's fields may carry an own-line `lead` comment before their
own line. This `top`-gating is the safety property the whole addition rests
on: the flat, single-line `emit_type` has no way to render a comment, so if a
*nested* record type could end up `broken` with a `lead`, that comment would
be silently dropped from the output the instant it appeared anywhere flat
`emit_type` is reached — restricting `broken`/`lead` to exactly the two call
sites that route through the new multi-line emitter closes that hole by
construction, not by a runtime check.

Two broken shapes, both verified directly against the built app before
writing the generator (not assumed): a plain record glues field 0 onto the
`{` line (`{ f0 : T0` / `, f1 : T1` / `}`), so only fields **after** the first
have an own line to hold a lead comment; an extensible record puts `base`
alone on the `{` line and gives **every** field, including the first, its own
`| `/`, ` line beneath it — so field 0 is eligible there too. A field's lead
comment rides at the field's own column (not the `{`/base line's column) in
both shapes — confirmed empirically, not assumed (a hand-written probe with
the comment indented to the `{` column got reformatted onto the field's
column). A comment before the record's opening `{` itself (i.e., between the
signature's `:` and the type) is a real, different shape the app also accepts
— but it was left out of scope for this addition (it's a comment on the type
as a whole, not on a field) and is noted here rather than silently ignored.

New `emit_record_type(kind, base, fields)` renders both shapes in local
coordinates (0 = the record's own `{` column); `emit_type_multiline` checks
the record/exrecord's own embedded broken flag (`t[-1]`) before its existing
arrow-breaking check, since the two are independent flags on different
tuple kinds. `emit_function_decl` needed a matching fix — it only ever
special-cased `d.sig_broken and d.sig[0] == "arrow"` to route through the
multi-line emitter, so a broken record/exrecord signature (which never sets
`d.sig_broken`, a flag that only ever tracks arrow-breaking) would have fallen
through to the flat branch and dropped its comments the same way a nested
record would have; it now also checks the type's own `t[-1]` flag. Record
fields are now uniformly `(name, type, lead)` triples everywhere a record/
exrecord field list appears, including a union variant's record payload
(`variant_payload`/`emit_variant_payload`), which stays flat and comment-free
by construction (`lead` always `None` there) — updated only for shape
consistency with `emit_type`'s new 3-tuple destructuring, not to add coverage
there.

No shrinker changes: types are not expression slots (`child_slots`/
`list_containers`/`comment_clearers` never descend into `Decl.sig`/
`TypeAliasDecl.rhs` at all — confirmed by reading them, not assumed), so a
field's `lead` comment is exactly as opaque to the shrinker as every other
type-level attribute already is (the arrow-breaking `arrow_comment`, by
contrast, IS shrinkable — but only because it lives on the surrounding `Decl`/
`TypeAliasDecl` object directly, not nested inside the type tuple itself). A
finding here would still shrink down to "drop every other declaration",
matching the existing precedent for pattern-level additions (v1.9, v1.17).

Verified: 8000 seeds against the fixed generator — 3000 `--comment-rate 0.6
--max-depth 6` (5000000..5002999, the profile most likely to hit the new
`top`-gated paths), 3000 default (5100000..5102999), 2000 `--max-depth 7
--comment-rate 0.6` (5200000..5201999) — all clean (0 quarantine, 0 findings).
No formatter source changed, so no gate-suite rerun was needed (generator-only
change). Seed 5000030 was hand-inspected mid-sweep and confirmed to exercise
both the extensible-record field-comment path and the whole-type trailing
comment together in one module, formatting clean.

**v1.20 (implemented 2026-07-22): scientific-notation float literals.**
`float_lit()` previously only picked from a fixed pool of plain decimals
(`"0.5"`, `"3.14"`, …), even though `1e10`/`2.5e-3`/`3E+4` is valid Gren —
confirmed via `compiler-common/src/Compiler/Parse/Number.gren`'s
`exponentParser` (an exponent, `e`/`E` plus optional `+`/`-` sign plus digits,
may follow either an integer or a fractional literal) — and confirmed the
formatter does **not** normalize it: case and sign are echoed verbatim
(unlike a hex literal's forced-lowercase digits), since `FloatingPoint.text`
is emitted as-is with no recomputation. ~30% of `float_lit()`'s calls now
build `mantissa + e|E + sign|"" + digits`, verified directly against the app
in every position the plain pool already reaches (bare, negated via `Neg`,
call argument, binop operand, with a leading inline comment) before wiring
in. No generator-internal structure changed (still a single `FloatLit`
leaf), so no shrinker/emitter plumbing was needed beyond the pool itself.
Verified clean as part of the v1.21 sweep below (the two additions were
verified together).

**v1.21 (implemented 2026-07-22): import-statement comments — found 2 real,
undocumented formatter bugs.** `Module.imports` was a list of pre-rendered
plain strings with no comment support at all — own-line comments, trailing
comments, and blank-line group boundaries around imports were entirely
untested by this generator (fixture-only coverage), despite `SortSymbols`
having a documented, non-trivial rule specifically about them (imports sort
alphabetically only within a contiguous run with no blank line or own-line
comment between them — see `README`'s "Import group sort"). Replaced with a
structured `Import` class (`mod`, `as_name`, `exposing`, `lead`/`blank`
group-boundary markers, `trailing`, and `item_lead`/`item_trailing` — a
comment on one item of a list-form `exposing`, forcing it to break across
lines) and a new `emit_import`. `imports` is now a normal shrinkable list
container (`list_containers`) with matching `comment_clearers` entries, same
as every other list-of-declarations in the generator.

This is exactly the kind of construct this generator exists to reach: within
one 3000-seed targeted sweep (`--comment-rate 0.6 --max-depth 6`, seeds
6000000..6002999), **294 modules (~10%) came back non-idempotent** — not a
generator mistake (every other bucket, including `comment-loss` and
`ast-mismatch`, stayed at 0; the generator itself is producing legal,
well-formed input) but two distinct, real, previously-undocumented formatter
bugs, both now written up in full in `../tbd.md` with root-cause tracing:

- **Bug A** — a comment trailing `exposing (..)` (either the module header's
  own, or an import's) is non-idempotent: `Exposing.Open` carries no AST
  position at all (unlike `Explicit`, whose array of positioned items is
  exactly why that branch already needed, and got, a `locImport.end` anchor
  after an earlier non-idempotency finding — see the comment right next to
  it in `MakeLogical.gren`). `Open` never got the equivalent fix, so a `--`
  comment forced onto its own row has no stable anchor to compute its indent
  from — a `{- -}` comment that stays glued inline never hits this path,
  which is why only line comments trigger it.
- **Bug B** — an exposing-list comment that ends up leading the item
  `SortSymbols` moves to the front (alphabetically, or an operator/type/value
  rank change) renders two different ways depending on which pass produced
  it (own line before `(` vs. glued after `exposing` before `(`, with
  different continuation alignment) — so formatting one produces the other,
  never a fixed point. Isolated precisely: the same shape with the comment on
  an item that stays in the *middle* after sorting is stable; only the
  post-sort-*first* item's leading comment triggers it.

294 findings decompose as 274 Bug A + 20 Bug B (confirmed by inspecting every
failing seed's minimized repro — 0 unclassified, 0 crashes, 0 ast-mismatches,
0 comment-loss). The generator was left as-is (still generates both shapes)
rather than taught to avoid its own finds — that would defeat the point of
having added this coverage; both bugs will keep resurfacing in sweeps until
fixed, which is the correct, expected state per `tbd.md`.

Verified (structural/plumbing correctness of the new generator code, not a
"clean sweep" — this addition is expected to keep finding Bugs A/B until they
are fixed in the formatter): the 3000-seed sweep above found exactly the two
known bugs and nothing else; every non-`non-idempotent` oracle (parse,
crash, ast-mismatch, comment-loss) was 0/3000. Re-run this same sweep after
Bug A/B are fixed to confirm 0 remaining findings, then promote minimized
repros as fixtures (see `tbd.md`'s "notes for whoever picks this up").

**v1.22 (implemented 2026-07-23): the import-run anchoring shapes + the
author-order oracle.** `docs/sorting.md` was extracted the same week, and
reading the generator against it showed three of its rules were unreachable *by
construction* — not thinly covered, impossible to generate — and all three
belonged to `cd1afeb`, the most recently changed rule in the file:

- **A comment above the first import of a run.** `import_stmt` gated its
  boundary markers behind `i > 0`, on the reasoning that a marker before the
  first import merely doubles up with the header's own spacing. That reasoning
  predates `cd1afeb`, which made the head of a run obey the same rule as the
  rest — so the head became precisely the position worth generating, and was
  the one position excluded. Gate removed.
- **A comment with a blank line *under* it** (the section header that stays put
  while the run below it sorts). `emit_import` emitted `blank` *before* `lead`,
  so a comment could only ever appear *below* the blank, never above it — the
  two shapes are a comment on either side of the same blank line, and only one
  of them existed. New `Import.anchor` field, emitted before the blank.
- **A comment below the run's last import.** Nothing emitted anything after the
  final import. New `Module.imports_tail`.

The first two are `ImportRunCommentAnchors`'s two halves. Import runs were also
lengthened (`nimp` 0–3 → a 0–7 distribution skewed small): a run only exercises
a sort if several imports are in it, and the boundary markers split what is
generated into shorter runs still.

Alongside these, the `sort-order` oracle above — the two are the same work, in
that the new shapes are exactly the ones whose *anchoring* the oracle has to pin
in order not to report false finds, and pinning them correctly is what proves
the shapes were understood.

Verified: 3000 seeds (500000..502999) at the default rate, **3000/3000 clean, 0
quarantine**, plus 3000 more (6000000..6002999) at `--comment-rate 0.6
--max-depth 6` — the configuration that surfaced Bugs A and B in v1.21, and
where import comments concentrate — also **3000/3000 clean, 0 quarantine**. The
new emission shapes are legal and the new oracle reports no finds against the
current formatter. The shapes are not rare: across 3000
modules, 32.5% carry a marker on the first import of a run, 22.9% a section
header with a blank under it, and 13.4% a comment below the run's last import —
three shapes that were previously generated 0% of the time. The oracle costs one
extra `--show` per module that has something to permute (~85%).

Also fixed while here: `--no-comments` did not actually produce comment-free
modules. Sites whose whole job is to place a comment (`lead`, `item_lead`, and
the new `anchor`/`imports_tail`) wrote `self.comment() or <a comment>` to
override the rate roll, which under rate 0 turned into an unconditional comment
— 261/299 rate-0 modules contained one. Those sites now go through
`Gen.forced_comment`, which keeps the override but stops at rate 0. Doc
comments (`{-| … -}`) still appear, by design: they are AST-level, not Context,
and are not what `--comment-rate` governs.

**v1.23 (implemented 2026-07-23): the module header's `exposing` list — found a
real formatter bug.** The header list was emitted as a flat, comment-free
string, so every header-side case in `docs/sorting.md` was fixture-only even
though the header sorts and carries comments under exactly the same rules as an
import's list. `Module.exposing` is now a real list (or the string `"(..)"`)
with the same fields `Import` has — `exposing_broken` (one item per row),
`exposing_item_lead` / `exposing_item_trailing`, and `header_trailing` (a
comment on the header's last row, the `(..)` or the closing `)`). Header lists
feed the author-order oracle too, sharing `_reverse_exposing_items` with the
import path — same pinned index 0, same reason.

Frequencies across 3000 modules, all previously 0%: broken header list 15.4%,
header item comment 7.2%, header trailing comment 16.9%.

**The find:** a comment written past a *vertical* header list's `)` was attached
to whichever name was written last and rode that name to its sorted position, so
`( apple, zebra ) -- c` and `( zebra, apple ) -- c` — the same module — formatted
differently. 154 of 3000 seeds (5%). `SortSymbols.sortExposedChildren` now keeps
such a comment out of the sortable clusters and appends it after them, pinning it
above the `)`; fixed in the same change, with `ModuleExposingClosePinned` as the
fixture. The two existing fixtures for this area could not have caught it —
both were written with the names already in sorted order, which makes "pinned
above the `)`" and "rides the last-written name" produce identical bytes.

**Two shapes are exempted, both deliberately and both narrowly:**

- An `effect module`'s `exposing (..)` with a trailing comment is not
  **generated**. It oscillates indented ↔ column 0, but
  `MakeLogical.processModuleLine` documents that as intentional: the Bug A fix
  anchored `(..)` at a real position for plain modules and imports, while an
  effect module's exposing column depends on the untracked `where { … }`
  contents. Generating it would rediscover a documented limitation every sweep.
- A **flat** header list carrying a trailing comment is generated, and checked by
  every oracle except **author-order invariance**, which it cannot satisfy: on
  one row a comment past the `)` and a comment trailing the last name are written
  in the same place, and the `)` has no AST position to separate them, so a
  comment close enough is read as trailing that name and travels with it. That
  ambiguity is the README's "A comment past a flat list", and the exemption lives
  in `_reverse_header_exposing` beside the other pins. Exempting the oracle
  rather than suppressing the shape keeps it covered for crashes, AST
  equivalence, idempotency, and comment preservation.

Also fixed here: a failure report's repro line printed `--seed N` without the
sweep's `--comment-rate` / `--max-depth`, so any find from a non-default sweep
replayed as `ok` from its own artifact.

Verified on one sweep run three times — 3000 seeds (700000..702999) at
`--comment-rate 0.5`, 0 quarantine throughout: **154** sort-order findings before
the formatter fix, **74** after it (every one classified, 74/74 the flat shape and
0 vertical — so the fix cleared that class outright), and **0** once the flat
shape was exempted from the invariance oracle. Alongside: 262 effectful tests,
the idempotency fuzzer, both whitespace-fuzzer modes, and the predicate audit all
clean.

### Trailing comment chains

**v1.24 (implemented 2026-07-24):** `docs/sorting.md`'s "Trailing a comment
that trails a name" rule — a comment starting on the row where the *preceding*
comment ends joins that comment's run, and the whole run travels with whatever
it trails (`zebra {- one -} -- two`, both riding `zebra` through the sort) —
was fixture-only (`SortingCommentZoo`) until now: every trailing-comment field
in the generator held at most one comment, so the chain-vs-single-comment
distinction was never exercised on the random axis.

Verified directly against the app before wiring in, not assumed from the doc:
since every comment this generator emits is single-row (the separate,
still-open "multiline block comments" gap below), "starts on the row the
previous one ends" collapses to "glued on the same row" — `Mango {- one -}
-- two` and `Mango {- one -} {- two -}` both round-trip exactly as
`SortingCommentZoo` describes, in both the vertical (broken) and flat
exposing-list layouts, for an import's own trailing comment, an import's own
`exposing`-item trailing comment, the module header's trailing comment, and
the header's exposing-item trailing comment — the four sortable-list trailing
positions. The flat-list shape reproduces the pre-existing, already-understood
"A comment past a flat list" ambiguity (`_reverse_header_exposing`'s existing
exemption): a chain written after a *flat* list's `)` gets torn apart by the
gap-width heuristic (`{- one -}` reads as trailing the last name, `-- two` as
trailing the list) rather than staying one glued run — legal, stable,
idempotent, just not the shape the chain rule describes, and already excluded
from the author-order oracle for exactly this reason. An import's own flat
`exposing` list has no equivalent ambiguity (checked directly: reversing the
item order under a flat-list trailing chain produces byte-identical output),
matching why `_reverse_exposing` (unlike `_reverse_header_exposing`) has no
such exemption.

New `Gen.comment_chain(forced=False, max_len=3)` replaces the single-comment
roll at exactly those four sites (`Import.trailing`, `Import.item_trailing`'s
comment payload, `Module.header_trailing`, `Module.exposing_item_trailing`'s
comment payload — never a `lead`/`anchor`/`item_lead`, which is the separate
"stacked own-line comments" gap below): a normal single comment most of the
time, but a 30%-per-step roll keeps appending another `forced_comment()`
while the last link is `block` (a `line` comment eats the rest of its row, so
it can only ever be the chain's last link — the loop only extends past a
`block`), capped at 3 links. The field now always holds `None` or a non-empty
**list**
of `(kind, text)` rather than a bare tuple, so a length-1 chain (the common
case) is representationally identical to before; a new `emit_comment_chain`
renders the list by gluing each link's text with a space, and every render
site that used to call `comment_text` on these four fields now calls it
instead. No change was needed anywhere else: `_reverse_exposing_items`
already treated the comment payload as opaque (`remap`'s `t[1]` passes
through unchanged regardless of its shape), and `comment_multiset`'s oracle
reads comments back from the real lexer's `--pre-context` output, not from
the generator's own objects, so it needed no changes either.

The shrinker gained one new case per site: alongside the existing "clear the
whole field" step, `comment_clearers` now also yields a "drop the chain's last
link" step when a chain has more than one link (`chain_pop` for the plain-list
shape, `indexed_chain_pop` for the `(index, chain)` shape) — so a repro that
only needs a 2-link chain shrinks down to one instead of stopping at "chain vs.
no chain".

Verified: 8000 seeds — 3000 default (9900000..9902999), 3000
`--comment-rate 0.6 --max-depth 6` (6100000..6102999), 2000 `--max-depth 7
--comment-rate 0.6` (9200000..9201999) — all clean (0 quarantine, 0 findings).
A direct frequency check (3000 modules at `--comment-rate 0.6 --max-depth 6`,
outside the sweep) found 1071 modules (~36%) carrying at least one chain of
length > 1, spread across all four sites (923 import-trailing, 86
header-trailing, 50 import-item-trailing, 12 header-item-trailing — the
skew matches how much more often a bare import/header trailing comment is
generated than an item-level one), with chains up to the length-3 cap
observed. No formatter source changed, so no gate-suite rerun was strictly
needed, but the full effectful suite (269 tests) was run anyway and stayed
clean.

### Multi-row block comments — 2 real bugs fixed, plus a deeper class found here and fixed 2026-07-24

**v1.25 (implemented 2026-07-24):** every comment this generator had ever
emitted was single-row — `docs/sorting.md`'s "Multiline block comments"
sections (and both of that document's open questions) were entirely
unreachable, a gap that predated even v1.24's comment chains. `Gen.comment_chain`
now applies a new `Gen.maybe_multirow` to each `block` link (a `line` comment
can't span rows — `--` eats the rest of its own row — so it's untouched), and
the same helper is applied at every OTHER sorting-axis site that generates a
`block`-eligible comment: `Import.lead`, `Import.anchor`, `Import.item_lead`
(both an import's own list and the module header's), and `Module.imports_tail`.
`Gen.multirow_block_lines` builds 1-2 extra content lines, each with a random
0/4/8/12-space indent and occasional trailing whitespace — mirroring
`multiline_string_line`'s randomized-indent style, and verified directly
against the app first: a block comment's continuation lines have **no**
indentation-legality floor at all (even column 0 parses), unlike a multi-line
STRING's "not indented equally" rule, so indentation here is purely a
co-occurrence axis to fuzz, not a legality constraint to respect.

**Representation.** A comment tuple's `text` is now `str` (single-row, as
before) or `list[str]` (multi-row, one element per physical content line) —
never ambiguous, since only `maybe_multirow`'s handful of call sites can
produce the second shape. New `comment_rows(c)` renders either shape as a
list of raw text ROWS (`{-` glued onto the first, `-}` onto the last);
`emit_comment_chain` now returns rows instead of a single string, gluing a
later link's first row onto the previous link's *last* row (unchanged chain
rule, now multi-row-aware); two new small helpers, `_append_trailing` and
`_append_own_line`, replace the direct `comment_text` calls at every emission
site this touches so a multi-row comment's extra rows land correctly whether
it's gluing onto an existing line or leading its own.

**Oracle fix needed first, verified before wiring anything in:** the
comment-preservation oracle's "block verbatim" assumption
(`comment_multiset`) turned out to hold only for a **single-row** block
comment — confirmed directly (`{-   padded text   -}` round-trips its interior
padding byte-for-byte). The instant a block comment spans multiple physical
rows, the formatter re-derives each continuation line's leading whitespace
(re-based under `{-`, though the lines' *relative* indentation to each other
survives) and right-trims every line's trailing whitespace — confirmed by
diffing a multi-row comment's `--pre-context` value before and after a
`--show` round-trip. Comparing raw bytes there would report a false
comment-loss finding on *every* multi-row comment purely from that legitimate
re-layout, so `comment_multiset` now compares a multi-row value (detected by
an embedded `\n`) as its sequence of per-line-**stripped** content instead —
extending the same "positions are discarded, content is not" principle a
`line` comment's right-trim already applies, one physical row at a time. A
single-row block comment's comparison is untouched (still raw-byte verbatim),
so this is a strict widening with no effect on any previously-generated shape.

**Bug found and fixed** (the dominant class — 28 of the first 29 findings, plus
a crash): a same-row multi-row `{- -}` trailing an import's or module header's
**flat, `ListParen`** exposing list classified `LeadsOwnLine` and rendered on a
fresh row; reparsing that output then read the comment as an unattached
`Standalone` top-level comment (its start row no longer fell inside the
import's/header's own declared row range) instead of the construct's own
trailing comment — an oscillation (and, when two such comments chained
together, an outright `box: multi-line comment cannot space-join` render
crash) only a multi-row comment could trigger, unreachable before v1.25. Root
cause: `Comments.gren`'s `prevBlockGlueRow` — the function `classifyBlock`
asks "which row does a same-row block comment glue onto?" — answered `-1`
("refuse, stay own-line") for `AllAcrossOrAllVertical`/`ParenBlock` whenever
the bracket itself rendered single-line, a deliberate stylistic rule for a
*single-row* comment that is never correct for a comment that is itself
multi-row (it can never render truly inline regardless of what precedes it,
so refusing a glue row there only produces an unstable placement). Fixed by
threading the comment's own multi-line-ness into `prevBlockGlueRow` as a new
parameter (one call site, so no wider ripple) and overriding the refusal when
it's set.

**First attempt was broader and wrong, caught by the fixture suite, not the
generator:** applying the override to `ParenBlock` and to `AllAcrossOrAllVertical`
generally fixed the same bug but also changed an already-fixture-verified,
deliberately-own-line shape — a record *pattern* `{ x } {- multi⏎line -} as
whole` (`KitchenSink`'s "3-line block comments at every round-tripping
position") started gluing onto `{ x }` instead of staying own-line, because
`AllAcrossOrAllVertical` is shared by record/array patterns and literals
(`ListCurly`/`ListSquare`), not just exposing lists. Narrowed to
`AllAcrossOrAllVertical ListParen` specifically — `SortSymbols.gren`'s own
comment states `ListParen` is exclusively exposing lists, so this cannot reach
a pattern or literal — and the `ParenBlock` change (never actually needed by
any found bug; added speculatively "for symmetry") was reverted outright
rather than risk an equally unverified change to a much more heavily-used
code path. `run-tests.sh` (269/269), the idempotency fuzzer, `matrix-syntax.py`
(1738/1738, same divergence counts), and `audit-predicates.py` (0 findings)
all confirmed clean after narrowing.

**Shrinker bug found and fixed** (seed 700046): dropping a top-level
declaration resets a module's explicit header export list to `"(..)"`
(`variants()`'s step 1) but wasn't clearing `header_trailing` alongside the
other exposing-related fields — for an **effect module** specifically, `exposing
(..)` plus a trailing comment is the one already-known, deliberately-exempted
gap (`header_exposing_comments`'s `known_gap`, never generated on purpose,
per `MakeLogical.processModuleLine`'s own documentation of why an effect
module's exposing column is untracked). Leaving `header_trailing` attached
across the reset let the shrinker "minimize" a real, different bug into that
unrelated known gap instead — a confusing, invalid repro once traced back
through the unshrunk `input.gren`, which reproduced the real bug directly.
Fixed by clearing `header_trailing` in the same step.

**A deeper, rarer class in the same family — deferred when v1.25 landed,
FIXED 2026-07-24** (see the end of this passage for what the fix was). After
the fix above, three configurations totalling 8000 seeds still found 39
non-idempotent, 5 sort-order, and 1 crash finding (≈0.5-1%, scaling with
`--comment-rate`) — all one further class: a comment **chain** trailing a
**module header's** (not an import's) exposing list, where a non-last link is
multi-row, can still detach on reparse — both for a flat header list and,
differently, for a vertical one (700020: a chain's *interior* link ends up
attached to the sorted-last exposing item instead of pinned above `)`). Root
cause traced one level deeper than the fix above reaches: `MakeLogical.gren`
computes each declaration's `OriginalRows` row range (`processModuleLine`'s
`origRow`, extended by exactly one row for a vertical list's own `)`) *before*
any comment is attached, so a comment that itself needs more rows than the
header's own tokens do — a multi-row link, or a chain whose links together
span further than the header's static range accounts for — falls outside that
range once rendered, and reparsing detaches it as a `Standalone` top-level
comment instead of the header's own child. An import doesn't have this
problem (`locImport.end` already anchors its own range correctly for every
shape tried), which is why the fix above was sufficient there but not for the
module header. The **crash** variant needs one more ingredient: an **effect
module** header specifically, where two *consecutive* links are each
multi-row (`box: multi-line comment cannot space-join` — a Box-render-level
limit on gluing two already-multi-line boxes side by side) — reproducible
with a plain module in the same shape only as a (non-crashing) non-idempotency,
so effect modules route this case through a less-robust code path than
`gluedExposingBox`'s "coupled" one (plausibly `exposingLineFallback`'s generic
flow, since an effect module's exposing position is already known to be
partly untracked). Not scoped away — per the v1.21 precedent, teaching the
generator to avoid its own finds would defeat the point of having added this
coverage.

**Fixed 2026-07-24, at exactly the layer this tracing predicted.** The module
header's `exposing ( … )` list is now built with an *elastic* closing-bracket
position (`LogicalPrintingTree.lpnElasticBracketNode`): the derived `)` row
grows as each comment is placed inside it, and the header's `OriginalRows`
range follows, so a later chain link can no longer fall past the list. A
comment that reaches the list is unconditionally *inside* it and pinned above
the `)`, one per line, so a chain stays together and stops depending on which
name was written last. All three symptoms are gone, the crash included — it
was, as guessed, the generic `exposingLineFallback` flow, reached because the
detached chain left a comment as the header's last child; with the chain
pinned, the coupled `gluedExposingBox` path handles it. A **flat** header list
gained the same treatment, which incidentally removed the gap-width rule that
was `tbd.md`'s second entry. Fixtures: `ModuleExposingCloseChain`,
`ModuleExposingCloseChainVertical`.

Verified: 8000 seeds — 3000 default (700000..702999) plus a direct chain-frequency
check alongside it (1071/3000 modules, ~36%, carrying a chain of length > 1
across all four sites; see v1.24's own count for the pre-multi-row baseline),
3000 `--comment-rate 0.6 --max-depth 6` (720000..722999), 2000 `--max-depth 7
--comment-rate 0.6` (730000..731999) — 0 quarantine throughout (the generator
itself stayed honest); non-`Standalone`-family findings (crash/non-idempotent/
sort-order) were the deferred class above, isolated and root-caused by hand
before accepting them as "expected residual" rather than assumed. `run-tests.sh`
(269/269), `fuzz-idempotency.py`, `matrix-syntax.py`, and `audit-predicates.py`
all clean; `fuzz-whitespace.py` has one PRE-EXISTING, unrelated failure
(`*NamedLineTrailer` fixtures' stretch-mode format-drift) confirmed via
`git stash` to reproduce identically against the formatter *before* this
addition's source changes, so not a regression from this work.

**Remaining expansion targets (closed by v1.26-v1.30 below):** the 2026-07-21
AST-vs-generator audit's gap list (local-function bodies, infix declarations,
effect modules, nested `as`) is now fully closed. What was left: comments
*inside* a multi-line string's surrounding expression aside from the
trailing-comment shape already fixed; list patterns beyond fixed-length arrays
(Gren has none — not a gap, still true). On the sorting axis specifically,
`docs/sorting.md` had two rules this generator could not reach: an import
carried at most one `lead` (never a stack of own-line comments), and a leading
block comment was never *glued* onto the import line (`{- c -} import Foo`,
the `LeadsInline` role). The module header's exposing list was on this list
until v1.23 closed it; trailing comment chains until v1.24; single-row-vs-
multi-row comments (including both of `docs/sorting.md`'s open questions)
until v1.25 — though v1.25 also *found* rather than closed a new, narrower gap
of its own: the module-header row-range/reparse-detach class documented above,
since fixed (2026-07-24). A separate audit (2026-07-25, reviewing the AST and
parser directly rather than `docs/sorting.md`) turned up two more: an
alias-of-an-alias pattern (`(x as a) as b`), and a NAMED wildcard pattern
(`_foo`, `AST.PAnything` with a non-empty name) — both real, legal Gren that
this generator had never produced. All five closed 2026-07-25, v1.26-v1.30.

**v1.26 (implemented 2026-07-25): stacked own-line import leads.** `Import.lead`
was a single optional comment; `import_stmt` now generates a non-empty LIST of
1-2 own-line comments (`Gen.forced_comments`, each independently
`maybe_multirow`-able) stacked directly above an import, mutually exclusive
with the new `glued_lead` (v1.27) the same way `doc`/`lead` are elsewhere in
this generator. `emit_import` loops `_append_own_line` over the list instead of
emitting one. No render-side change needed — `_reverse_run` (the author-order
permutation oracle) moves whole `Import` objects, so a list-valued `lead`
travels with its import for free, same as before. `comment_clearers` gained a
`chain_pop`-style finer shrink (drop the last stacked lead) alongside the
existing whole-list clear. Verified: 1500 seeds at `--comment-rate 0.6` clean;
manual scan of ~1000 seeds found 328 modules containing a 2-comment stack.

**v1.27 (implemented 2026-07-25): leading glued import comment (`LeadsInline`).**
The other named `docs/sorting.md` gap: `{- c -} import Foo`, a block comment
glued to the front of the `import` keyword on the SAME row — the formatter's
own `LeadsInline` `CommentRole` (fixed 2026-07-23, see
`project_leading_block_comment_glue_tbd`) had no generator emitter at all.
New `Import.glued_lead` field (a single `block`-kind comment, optionally
`maybe_multirow`, restricted to `block` since a `line` comment eats the rest
of its row and could never precede anything on it); `emit_import` glues its
last row onto `head` before any of the exposing-list branches run, so the glue
composes uniformly regardless of which branch fires. Mutually exclusive with
`lead` (v1.26) — combining an own-line stack with a front-glued comment on the
same import is a further, untested interaction, not this addition's scope.
Verified: 1500 seeds at `--comment-rate 0.6` clean, all four oracles passing
on a hand-picked glued-lead seed replayed through `--seed`, including one where
the glued comment survives a run-sort still attached to its own import (author-
order invariance holds because `glued_lead`, like `lead`, is a per-`Import`
field that travels with the whole object when a run reorders).

**v1.28 (implemented 2026-07-25): MultilineStr leading comment (closes the
"surrounding expression" gap).** `mk_multiline_str`'s `MultilineStr.trailing`
(v1.3, extended to "every surrounding-expression position" thereafter) covered
the comment riding the CLOSING `"""`; the front of the OPENING `"""` had no
equivalent. Every other atom type gets this via `.pre`
(`Gen.maybe_inline_comment`, glued by `_inline` onto a single-line atom's own
text) — MultilineStr was excluded from that isinstance check, and even adding
it there wouldn't have been enough: `_inline` operates on a single rendered
string, but `emit_multiline_str` returns a LIST of lines and never went
through `_inline` at all. Fixed both: `MultilineStr` gained a `.pre` field
and joined `maybe_inline_comment`'s isinstance tuple, and `emit_multiline_str`
now glues `n.pre` onto the opener itself and — the part that needed verifying
directly against the app first — WIDENS its own `col` by the glued prefix's
width before laying out content/close rows. A content row indented only to
the original `col` fails to parse once a glued prefix pushes `"""` further
right on its own row ("Multi-line string lines are not indented equally");
widening `col` uniformly (a no-op when `.pre` is absent) fixes it. Confirmed
by hand against the app that a decl-body position re-canonicalizes this shape
(hoists the comment onto the `=` line, same as it already does for a plain
`{- c -} "str"`) — expected, and harmless, since this generator's oracles
check round-trip properties of the ACTUAL formatter output, not a byte-match
against this script's own (non-canonical-by-construction) emission.
`value()`'s dedicated MultilineStr branch (the one path that bypasses `atom()`
entirely, unlike every other atom type reached from a value position) now
calls `maybe_inline_comment` explicitly so it isn't left out. Verified: 500
seeds default clean, 1500 at `--comment-rate 0.6` clean; manual scan found 352
true glued-opener occurrences (`-} import ` false-positives from the v1.26
own-line-lead scan were caught and excluded first) across ~1000 seeds.

**v1.29 (implemented 2026-07-25): alias-of-an-alias pattern `(x as a) as b`.**
`Gen.pattern` (the only caller that adds the OUTERMOST `as` — see its own
docstring) previously refused to wrap an already-`PAs` base at all. Verified
directly against the app before wiring in: `(x as a) as b`, `((Just y) as a)
as b`, and `({ y } as a) as b` all parse and round-trip; the bare, unparen-
thesized `x as a as b` does NOT ("Expected keyword '->'") — confirming the
INNER `PAs` needs its own parens exactly like a `PCtor`/`PInt` alias base
already does. `emit_pat`'s `PAs` case now checks for a `PAs` inner too (three
cases, one tuple). `pattern()` now allows a second, smaller-probability wrap
when `pattern_base` already produced one. Scoped to THIS position only —
`pattern_base`'s own separate alias wrap (ctor args, array items, params)
keeps its original guard; a nested alias there would additionally need
`emit_param`'s extra paren layer verified to compose with a nested one, not
done here. Verified: 800 seeds at `--comment-rate 0.5` clean; manual scan
found 43 alias-of-alias occurrences across 2000 seeds.

**v1.30 (implemented 2026-07-25): named wildcard pattern `_foo`.** Every
`PWild` this generator ever produced rendered bare `_`; real Gren also allows
a NAMED wildcard (`AST.PAnything` with a non-empty name, `compiler-common`'s
`Pattern.gren`) — purely cosmetic, since a `_`-prefixed identifier can never
be referenced in expression position at all (verified directly against the
app: `_y` as a function body expression fails with "Wildcard patterns are not
allowed in expressions"). That restriction never collides with anything this
generator does, since a let/lambda-bound name is never referenced back in its
own body anyway (bodies are generated independently of their own params, same
as every other bound name here). `PWild` gained an optional `name`; a new
`Gen.wild()` helper names it ~25% of the time, called from both existing
`PWild()` call sites (`let_pattern`, `_pattern_base_core`). Verified directly
against the app in every position `PWild` reaches (let-binding LHS, when-
branch/array-item/ctor-arg via `pattern_base`, function/lambda params) before
wiring in. Verified: 2000 seeds at `--comment-rate 0.6` + 1000 at `--max-depth
7 --comment-rate 0.5` clean; manual scan found 363 named-wildcard occurrences
across 2000 seeds.

### Mismatched `port module` header

**v1.31 (implemented 2026-07-26): a module keyword that disagrees with the
body.** Until now the generator emitted `port module` exactly when the module
declared a port — which is also exactly what the formatter writes, so the whole
class of *disagreeing* headers was generated 0% of the time and the formatter's
rewrite was never exercised by any gate.

The parser doesn't record which keyword was written; it derives port-ness from
the declarations. On 2026-07-26 that was settled as the intended design
(compiler-common#33, in preparation for the module-level `port` keyword becoming
optional or going away): the formatter honours the AST and rewrites the header
to match the body. README, "The `port` in `port module` follows the ports".
Both disagreeing shapes are legal input — `port module` with no ports, and
plain `module` with a port declaration — and both parse; verified directly
against the app before wiring in (each formats to the agreeing header).

`Module` gained `port_mismatch`, set on ~12% of non-effect modules (an effect
module's keyword is `effect module` either way, so it's never set there);
`emit_module` flips the body-derived keyword when it's set. The flip survives
shrinking without extra care: dropping the module's last port just swaps which
direction the disagreement runs in, and both directions are legal.

No oracle needed changing. The rewrite touches only the header keyword, which
the AST doesn't record and the comment multiset doesn't contain, so
AST-equivalence and comment preservation are blind to it by construction, and
the formatted output — always the agreeing header — is a fixed point. What the
sweep actually buys is the *column shift*: adding or dropping `port ` moves
every token on the header row, so a header-row comment and the `exposing` list
below it are laid out from a different starting column than the author wrote.

Verified: 5100 seeds clean — 600 at `--comment-rate 0.6` (9800000..9800599),
3000 default (9810000..9812999), 1500 at `--max-depth 7 --comment-rate 0.6`
(9820000..9821499); 0 quarantine, 0 findings. Manual scan over 1000 seeds found
119 disagreeing headers (110 `port module` with no ports, 9 plain `module` with
a port), against 683 agreeing ones and 198 effect modules.

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
addition; a further 17000 fresh seeds (50000..59999 default, 60000..62999 at
`--comment-rate 0.6`, 70000..73999 at `--max-depth 7 --comment-rate 0.5`), run
2026-07-21 with no generator changes, still clean; and a further 9000 seeds —
3000 default (1..3000) + 3000 `--comment-rate 0.6` (80000..82999) + 3000
`--max-depth 7 --comment-rate 0.5` (90000..92999) — clean after the v1.9
qualified-constructor-pattern addition; and a further 8000 seeds — 3000
`--comment-rate 0.6` (100000..102999) + 3000 default (1..3000) + 2000
`--max-depth 7 --comment-rate 0.6` (200000..201999) — clean after the v1.10
qualified-type-reference addition. The `--comment-rate 0.6` (100000..102999)
sweep originally surfaced a pre-existing formatter non-idempotency bug — a
lambda-body leading comment before a `<|`-rooted mixed pipeline with the `|>`
buried in a binop operand — unrelated to this generator change (it reproduced
with a fully unqualified hand-written repro); that bug is now fixed
(`LambdaCommentPipelineBinopSeed`, `exprAlwaysBreaks` in `insertLambda`), and
the sweep is clean against the fixed formatter; and a further 8000 seeds — 3000
default (1..3000) + 3000 `--comment-rate 0.6` (500000..502999) + 2000
`--max-depth 7 --comment-rate 0.6` (600000..601999) — clean after the v1.11
richer-type-application addition; and a further 8000 seeds — 3000 default
(1..3000) + 3000 `--comment-rate 0.6` (800000..802999) + 2000 `--max-depth 7
--comment-rate 0.6` (900000..901999) — clean after the v1.12 extensible-record
addition, which itself surfaced (and drove the fix of) a formatter
non-idempotency in the extensible-record-*type* comment path — a trailing `--`
after `}` and an own-line comment before `}` both oscillated because the type
node was built via plain `lpnNode` with no closing-`}` position; routing it
through `lpnBracketNode` like a record-update expression fixed both
(`ExtensibleRecordTypeTrailingComment`); and a further 8000 seeds — 3000 default
(1..3000) + 3000 `--comment-rate 0.6` (1100000..1102999) + 2000 `--max-depth 7
--comment-rate 0.6` (1200000..1201999) — clean after the v1.13
type/operator-exposing addition (no formatter bug surfaced; generator-only); and
a further 6000 seeds — 3000 default (1..3000) + 3000 `--comment-rate 0.6`
(1400000..1402999) — clean after the v1.14 hex-literal addition; and a further
3000 seeds at `--max-depth 7 --comment-rate 0.6` (1500000..1502999), run
2026-07-22, clean — closing the deep-nesting gap this addition had left open.
The v1.14 hex work is what uncovered the intToHex 2^35 `//`-truncation bug,
fixed first in `6428cbf`.)

**Post-fix re-sweep (2026-07-22, after `ada1dd8`):** the multiline-string
`--`-swallow bug documented in `tbd.md` (binop/pipeline operator gluing onto a
`"""…""" -- c` operand's comment line) is fixed — at the time by
`subtreeEndsWithLineComment` in `Render/NodeClassify.gren`, consulted at the
pipeline `hasBoundaryComment` gate and the binop-group `AlreadyTerminated`
override; since 2026-08-07 by `B.endsOpen`, read off the rendered box at those
same two places. A further 9000 fresh
seeds against the fixed formatter — 4000 default (3000000..3003999), 3000
`--comment-rate 0.6` (3100000..3102999), 2000 `--max-depth 7 --comment-rate
0.6` (3200000..3201999) — all clean (0 quarantine, 0 findings), confirming no
regression and no further instance of the bug class.

**Higher-volume / deeper-nesting round (2026-07-22):** pushed both axes past
their previous ceilings with no formatter change in between (pure
verification). A `-n 50 --max-depth 8 --comment-rate 0.6` sanity check (base
3900000) came back clean before committing to the full run. Then 20000 fresh
seeds: 10000 default (4000000..4009999), 6000 `--comment-rate 0.6`
(4100000..4105999), 4000 `--max-depth 8 --comment-rate 0.6`
(4200000..4203999) — the first sweep at depth 8 (prior ceiling was 7). All
clean (0 quarantine, 0 findings).

### Lambda comment slots

**v1.32 (implemented 2026-08-05):** the two comment positions a lambda has that
no other node offers — in FRONT of the lambda (`{- c -} \q -> q`) and between
its `->` and its body (`\q -> {- c -} q`).

Both were **unreachable before**. `maybe_inline_comment` only ever sets `.pre` on
a single-line ATOM (`Int`/`Str`/`Var`/`Qual`/`Ctor`/`Chr`/`MultilineStr`), and
`mk_lambda` had no comment slot of its own, so neither shape could be generated
at any seed, depth or comment rate.

That gap is not hypothetical: **three formatter non-idempotency bugs fixed on
2026-08-05 lived in exactly these two positions** — a `{- -}` leading a lambda
that is a `<|` operand (`86ef9d7`), the same comment leading a lambda operand of
a `|>` step, and a `{- -}` between a lambda's `->` and its body inside a record
field (`1af273a`, where `Array.popLast` mistook the comment for the lambda head).
Every one was found by `fuzz-idempotency.py` sweeping the fixture corpus, because
this generator could not reach them.

**Scoped to a single-line body.** `E.pre`'s docstring restricts leading comments
to atoms because "a comment with continuation lines would misalign children"; a
lambda whose body is single-line renders on one row, so no child's column moves
and the restriction is met by construction rather than by node kind. The
multi-line branch of `emit_lambda` therefore never sees either slot set, and says
so — a leading comment there would move the `\` off `col` while `own_line` keeps
the body at `col + INDENT`.

**Verify the shapes actually emit before trusting a clean sweep.** A grammar
addition that generates nothing new is indistinguishable from a clean run:
replay a few seeds at `--comment-rate 0.9` and grep for the shape
(`{- k[0-9]+ -\} \\` and `-> {- k[0-9]+ -\}`) — note the second also matches a
type arrow's comment, so check the context is a lambda.

**Sweep after this addition:** 8000 fresh seeds — 3000 `--comment-rate 0.6`
(7000000..7002999), 2500 `--max-depth 7 --comment-rate 0.6` (7100000..7102499),
2500 default (7200000..7202499) — all clean, **0 quarantine** in all three runs.
No formatter bug surfaced; the three bugs the addition was written for were
already fixed.


### Lambda as a pipeline operand

**v1.33 (implemented 2026-08-05):** the chain built by `mk_pipeline` may end in a
BARE lambda (`seed |> f <| \q -> q`), which brings v1.32's two comment slots
with it.

**v1.32 alone did not close the gap it was written for.** `mk_pipeline` builds
its operands from `arg()`, which offers only an atom or a `Paren`, so a lambda
could never be a pipeline operand at all — v1.32 put the comment slots on
`mk_lambda`, but nothing ever put a lambda *there*. `fn <| {- c -} \q -> q`, the
shape [`86ef9d7`] fixed (a comment leading a lambda operand rendered on the wrong
side of the operator), stayed ungeneratable until this.

**LAST operand only.** A lambda body swallows everything to its right, so
`a |> \q -> q |> b` parses as one lambda body containing `|> b` rather than a
three-operand chain. At the end of the chain there is nothing left to swallow.
This is a legality claim, and the thing that actually enforces it is the
**quarantine count** — a chain that failed to parse would land there, so a
non-zero quarantine after this change means the restriction is wrong.

`emit_binop` needed no change: it emits an operand with `one_line` when the chain
is inline and `emit(operand, ocol)` at the operator's own column when broken,
which dispatches to `emit_lambda` and takes the body's `own_line` indent from
there.

**Sweep:** 8000 fresh seeds — 3000 `--comment-rate 0.6` (8000000..8002999), 2500
`--max-depth 7 --comment-rate 0.6` (8100000..8102499), 2500 default
(8200000..8202499) — all clean, **0 quarantine** in all three runs. Verified the
shape emits before trusting that: replaying seeds 100..180 at
`--comment-rate 0.95` produces `<| {- k10 -} \{ name, count } acc -> {- k11 -} .v`,
which is `86ef9d7`'s bug in generated form.


### Author-broken patterns

**v1.34 (implemented 2026-08-09):** an array/record pattern in a **parameter
slot** may carry a baked author-break, rendering one item per row —
`\{ alpha` ⏎ `, beta` ⏎ `} -> …`. Wired into all five slots that take one: a
lambda parameter, a declaration parameter, a `let`-bound function's parameter, a
`let` binding's LHS, and a `when` branch pattern.

Until this, **every pattern this generator emits was one row**: `emit_pat`
returns a `str` and has no multi-row path at all. Nor did anything else here have
one — `matrix-syntax.py` wrote every lambda as `\q ->` until 2026-08-09, and the
whole fixture corpus holds a single broken pattern
(`CoreExpressions/PatternLayoutByAuthor`'s `multilineRecord { alpha` ⏎ `, beta` ⏎
`} =`, a declaration parameter with no comment near it). Closing the same hole in
the matrix immediately found the `makeMultilineLambdaArgBox` column-loss bug; this
is that axis inside random nesting, and crossed with comments.

`emit_pat_rows` is the multi-row renderer and `append_params` lays a parameter
list out around one, both keyed on `_end_col`'s rule that **row 0 of an emitted
group is headless** while every later row carries absolute padding. `emit_pat`
is untouched and still renders every nested pattern, so only the outermost
pattern of a slot can break — the same restriction `emit_type_multiline` puts on
a signature's type.

**The continuation column is nearly free, and the exception is what a first
probe missed.** A pattern is not layout-sensitive the way an expression is:
probing all five slots at every column from 0 to base+5 — comparing each broken
authoring's parsed AST against the one-row authoring's with positions stripped,
so a *silent misparse* would show as a different tree rather than a parse error —
reported every column legal. It was wrong, because each probe slot held **one**
`let` binding. With a second binding, a continuation row one column left of the
binding column dedents out of the `let` and the module stops parsing; three of
the first 300 seeds quarantined on exactly that. `cont_delta` is therefore never
negative — deeper cannot terminate a block, and `>= 0` needs no knowledge of the
enclosing column, since the pattern's own start column is already at or past it.

The shrinker gets a step of its own (`variants`' step 5, over
`broken_patterns`): unbreak one pattern. A find that survives every other
reduction but not that one is a find **about** the break; one that survives it
too never was.

**First find, fixed the same day.** 4 of the first 400 seeds, one family: the
formatter emitted a file that neither `compiler-common` nor `gren make` will
parse. `trySoftGlueFlow`'s `flowItemInlineLine` — the fast path written for
`multilineRecord { alpha` ⏎ `, beta` ⏎ `} =` — accepted **any** single-line block
comment as a gluable prefix item, asking the comment's shape
(`commentTextCanRide`'s question) instead of its stored role. So a `LeadsOwnLine`
comment was glued onto a broken pattern's first row, and a `let` binding that is
not the first cannot begin with a comment on its row at all. The same comment in
front of a one-row pattern had always stacked above it. Scoped with
`commentDoesNotGlue` (already defined in that file); a *trailing* comment, which
is what the fast path's docstring is about, still glues. Fixture
`PatternComments/LetBrokenPatternLeadingComment`, which fails against a pre-fix
binary and pins the no-comment soft glue as the boundary.

### Own-line comment RUNS at inner slots

**v1.35 (implemented 2026-08-09):** the two inner own-line comment slots — a
`when` branch's lead and a `let` binding's lead — take a **run** of one or two
comments (`Gen.comment_run`), each on its own row, kinds mixed freely and a
member optionally multi-row.

Every other gate that varies run length varies it over the **fixed corpus**
(`fuzz-idempotency.py --run N` / `--mix`) or over the matrix's own vocabulary
(`--comments --comment-runs`). Neither reaches a run inside randomly generated
structure, and a run is where the rules about a comment's *neighbour* live —
`commentRendersOwnLine`, `spanTrailingOwnLine`, `FlowPolicy`'s inline arm all
discriminate on what is next to a member, and in a run that neighbour is another
comment.

Distinct from `comment_chain`, which glues its links onto ONE row; here every
member gets a row, so a `line` comment is legal in any position of the run.
`comment_clearers` pops a member at a time before dropping the whole run, so a
find needing two comments is not minimized straight past the shape it needs.

Measured over seeds 1..500 (`--max-depth 5 --comment-rate 0.25`): **133 runs of
two** against 390 of one — the axis is live, not nominally present.

### Author layout for the BLOCK constructs

**v1.36 (implemented 2026-08-10):** `when`, `if`, lambda and definition bodies
each take an **author-layout flag**, the way containers have taken a `broken`
flag since v1.2.

Until now every one of those constructs had exactly one spelling in this
emitter: a `when` branch body always went on its own row with a blank line
between branches, a lambda body went on the `->` row iff it was single-line, an
`if`'s `else` always got a blank line above it and always started a fresh row,
and a single-line definition value always hung after `= `. Layout here is
author-driven — the formatter reads the rows you wrote — so each of those was
one of two legal authorings, and the other was unreachable:

| flag | what it writes |
|---|---|
| `When` branch `body_glued` | `Just q -> q` — the body on the `->` row |
| `When.blanks` | branches back-to-back, no blank between |
| `Lambda.body_below` | a single-line body on the row *under* the `->` |
| `If.no_blank` | no blank line above `else` |
| `If.chain_else` | `else if c then` instead of `else` ⏎ `if c then` |
| `LetBind.val_below` / `Decl.body_below` | a single-line value on its own row under the `=` |

**Two of these are a different TREE, not a different spelling**, which is the
reason the axis is worth more than a layout permutation:

- `chain_else` — `else if` is **one** `If` carrying two branches; `else` ⏎ `if`
  is an `If` sitting in another's else slot. `value()` reaches the nested
  spelling ~3% of the time and the chained one never, so the multi-branch `If`
  had no generated case at all. (`mk_if` now builds the chain deliberately,
  ~25%, recursing on `d - 1` so the depth budget bounds it.)
- `body_below` on a lambda — a body starting on a later row reparses as an
  `IndentedBlock`, a different container with its own comment-redirect arm.
  That distinction is exactly what the 2026-08-09 `\[ 1 ] ->` bug turned on,
  and it was reachable here only through a body that was *already* multi-line.

The container `broken` axis (v1.2, and the matrix's `broken`/`bareBroken`
variants) found four real bugs for the same reason this one exists: pre-broken
input reaches renderers that canonically-spaced input never does. It covered
records, arrays, calls, binops, parens, patterns and types — every bracketed
form — and none of the four block constructs.

**Legality was verified against the app before wiring in**, including the
combinations that are not obvious: a glued body behind a **broken** pattern
(`{ alpha` ⏎ `, beta` ⏎ `} -> 0` — the glue lands on the pattern's last row,
which is the row carrying the `->`), branches with no blank line and *non*-glued
bodies, a lambda body dropped below the `->` in every position a lambda reaches
(bare, paren'd call argument, array item, record field value), and an `else if`
chain whose tail is itself inline (`else if c then 0 else 1`).

Two guards are for the SHRINKER rather than the generator, which can swap a
node out from under a flag: `emit_when` re-tests `not multiline(body)` before
gluing, and `emit_if`'s chain arm re-tests `isinstance(n.els, If)` — expression
slots are replaced with `Int(0)` by variant step 3, so a flag can outlive the
node it was set for.

`layout_resetters` is variant step **6**, and its rationale is
`broken_patterns`' one construct family over: a find that survives every other
reduction but not this one is a find ABOUT the authored layout, and one that
survives this too was never about it. Every reset moves *toward* the pre-v1.36
shape, so a shrunk repro reads as ordinary Gren.

Measured over seeds 1..500 (`--max-depth 5 --comment-rate 0.25`), counted from
the trees AND from the emitted text rather than assumed: 123 glued branch bodies
(85 modules), 159 blank-less `when`s (114), 170 dropped `let` values (104), 61
blank-less `if`s (53), 60 dropped declaration bodies (58), 34 `else if` chains
(28), 27 dropped lambda bodies (25). `-n 2000` **clean**, 0 quarantine and 0
emitter exceptions — the generator is still honest about what it emits, which is
what makes its crash/non-idempotency finds trustworthy.

### A row-breaking comment inside an `if`/`when` HEADER — v1.38

`if <cond> then` and `when <subject> is` are each one logical row spanning the
whole condition, so a comment nested inside the condition decides the header's
shape. The generator could not write one. Both feed their condition from
`inline()`, whose contract is a guaranteed single-line expression, and the only
comment reaching it was `maybe_inline_comment`'s riding single-line `{- -}` —
so the generator produced one half of the shape and never the other, however
long a sweep ran.

Three pieces, all of which have to be there:

- **`Call.arg_break`** — one entry per argument, a comment written on the
  argument's own row above it, of a kind that ENDS its row (a `--` or a
  multi-row `{- -}`). It is the one comment slot here whose presence makes the
  call multi-line by itself, which is why it is separate from an atom's `.pre`.
  `emit_call` places it, and steps the argument column off the head row's first
  REAL token rather than off `col`: a `{- c -}` riding in front of the function
  pushes that token right, and an argument indented from `col` alone reads as a
  DEDENT and ends the expression (`{- k7 -} item` ⏎ `····Ok` is not `item Ok` —
  the parser stops at `item`). Getting that wrong is what the first sweep's
  nine quarantines were.
- **`If.flat_head` / `When.flat_head`** — the header form that keeps `if` and
  `then` on one logical row and lets the condition WRAP it. This is the one
  that matters: the other form (`if` ⏎ condition ⏎ `then`) tells the renderer
  "vertical" through its source rows, so the header never has to work it out
  from the comment, and the bug this axis exists for cannot fire.
- **`Gen.break_header_cond`** — wires them together, on a `Call` condition with
  arguments, which is the smallest shape that reproduces.

Its continuation rows come from `multirow_block_lines`, the shape already
verified against the app. A hand-rolled `text + "b"` was tried first and
collides with the comment identity the RUI oracle extracts — `k7b` reads as a
second `k7`, and six seeds reported a duplicate comment that was not there.

**Verified non-vacuous against the bug it was built for.** With the `if`/`when`
header fix reverted, seed 284 reduces to

```gren
fn3 =
    if {- k8 -} y Maybe.y
                    -- k7
                    node then
        0

    else
        0
```

which formats to output that does not reparse. With the fix in, `-n 300` is
**300/300 clean**.
