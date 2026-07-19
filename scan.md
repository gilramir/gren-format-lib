# Real-corpus sweep: published-package formatting failures

> **RESOLUTION (2026-07-18).** All five in-scope classes A–E are **fixed**, each
> with a regression fixture in `tests/testfiles/Formatter/` and an `assertPretty`
> in `Format.gren` (`MultilineStringTrailingWhitespace` + updated
> `MultilineStringContent` for A, `SignatureBrokenRecordArg` for B,
> `BinopCommentBracketOperand` for C, `ElseIfAfterRecordPipe` for D,
> `PipelineCallMultiBlockArgs` for E). Effectful suite 211/211, both fuzzers 0
> findings, matrix 1738/1738 (parity baseline unchanged), predicate audit 0
> findings. The sweep is now a repeatable gate: `tests/corpus-check.py`.
>
> Two write-ups below were partly wrong about the *mechanism* (the symptoms were
> real):
> - **A2** is not a value change — triple-quote content *is* escape-processed, so
>   `"` → `\"` round-trips (AST-preserving). It was cosmetic over-escaping: gren
>   escaped *every* quote. Fixed to escape only 3+ runs (a real `"""` hazard),
>   matching elm-format's intent, and *not* the trailing quote (gren always puts
>   the closing `"""` on its own line, so a trailing quote is never adjacent to
>   it). The AST-mismatch that class-10 actually reported was an **A1** trailing-
>   whitespace strip elsewhere in the same file.
> - **B** is not a missing "flatten". elm-format does **not** flatten an author-
>   broken record type; it keeps it broken and forces the *whole signature*
>   vertical (`-> X` drops to its own line). Fix: force the signature vertical
>   when a segment *head* carries a multi-line record (the old check skipped the
>   head position, and `signatureForceVertical` only saw `->`-boundary breaks).
>
> C and D did **not** share a root cause (nor with E): C was a binop-operand
> predicate blind to a leading comment; D was a stale row computation
> (`lpnLastBracketEnd` preferred over the later real token); E was a genuinely
> reachable "unreachable" arm (a pipeline-step call's trigger `suffix` with 2+
> multi-line block args). The original failure descriptions follow unchanged.



`qe.md` avenue #1 ("broader real-corpus sweep") run against 10 published
Gren packages that failed to format cleanly. Source list:
`~/prj/gren-format-preview/pkgs/format_failed.txt`, produced by
`format-packages check` + `format-packages format` against packages cloned
into `~/prj/gren-format-preview/pkgs/`. Each failure below was reproduced
directly with `gren-format.sh --show`/`--show-first` against the real file,
then minimized. They resolve into 5 distinct bug classes plus one
out-of-scope parser issue — **not 10 independent bugs**.

## A) MultilineString content corruption — 5/10 failures, HIGH PRIORITY

Affected packages: `blaix/gren-tui/tests/unit`, `blaix/prettynice`,
`icidasset/markdown-gren/tests`, `joeybright/gren-turso/integration_tests`,
`lue-bird/gren-extra-checks/tests`.

The renderer corrupts the literal *content* of `"""…"""` strings in two
independent ways — both confirmed with minimal repros via
`gren-format.sh --show-first` (bypasses the AST-equivalence abort so the
corrupted output is visible):

**A1 — trailing whitespace on a content line is stripped.**

```gren
x : String
x =
    """
    foo   
    bar
    """
```

formats to `foo` + `bar` (the three trailing spaces after `foo` are gone).
Per `CLAUDE.md`, `Render/Box.gren`'s `render` "right-trims every line" —
that's correct for *code* lines but is evidently also being applied to
`MultilineString` content lines, where trailing whitespace is significant
string data, not formatting.

Real-world instances: `blaix/gren-tui/tests/unit/src/Test/UI.gren` (a
box-drawing-character test fixture with trailing spaces to align columns),
`blaix/prettynice/src/Prettynice/Internal/Props.gren` (a code-generation
template — `"""\n        Encode.object \n                [ {{FIELDS}}\n …`
— trailing space after `Encode.object` and deliberately "weird" indentation
are semantically load-bearing, per the file's own comment "Weird indentation
is to look right in generated output"), `icidasset/markdown-gren/tests/…/Codespan.gren`
(uses explicit `\u{0020}` escapes specifically to defend trailing-whitespace
test fixtures — the formatter destroys them anyway since it operates on the
rendered line, not the escape sequence), `joeybright/gren-turso/…/Db.gren`
(a multi-statement SQL string with `"...123; \n SELECT..."` — the trailing
space before the embedded newline is dropped).

**A2 — internal `"` characters get spuriously backslash-escaped.**

```gren
x : String
x =
    """
    module A exposing (a)
    {- TODO -}
    a = Debug.log "u"
    """
```

Minimal top-level repro does **not** reproduce this — it only shows up
nested inside a call-argument/record-field position (confirmed via
`lue-bird/gren-extra-checks/tests/src/Tests.gren`, a `source = """…"""`
field several levels deep inside `Test.test (\{} -> { …, files = [ { path =
…, source = """…""" } ] })`). In that context, `--show-first` shows
`Debug.log "u"` reformatted to `Debug.log \"u\"` — a literal backslash
character added to the string's parsed value (triple-quote content isn't
escape-processed, so this isn't a no-op re-encoding; it changes what the
string parses to). Needs a repro that matches the real nesting depth/shape
before fixing — the shallow repro above passed through untouched, so
whatever code path escapes the quote is only reached in some contexts, not
all `MultilineString` rendering.

**Why this matters:** both are correctness bugs — `gren format` silently
changes the meaning of string literals, not just their layout. Given `"""`
strings are commonly used for embedded templates/fixtures/DSL snippets
where whitespace and quoting matter, this is likely under-counted even at
5/10 in this sample.

**Suggested fix approach:** find wherever `MultilineString (Array String)`
content lines get turned into `Box`/`Line` values in
`Render/MakeRenderBox.gren` (per `CLAUDE.md`: "rendered with hard newlines
so content aligns with the `\"\"\"` delimiters") and (1) ensure those lines
are emitted as raw `B.Text` (or equivalent) that bypasses the generic
right-trim, and (2) find why some path re-escapes `"` inside multiline
string content and stop it. Both are likely in the same renderer function;
audit call sites of whatever converts a `MultilineString` element to a
`Line`/`Box`.

## B) Signature record-type "no flatten" crash

Affected package: `blaix/gren-tui` (`src/Tui.gren`, the `defineProgram`
signature).

Minimal repro:

```gren
f :
    { a : Int
    , onInput : Input -> msg
    } -> X
f a =
    a
```

crashes with `box: inline signature segment unexpectedly broke across
lines` (from `Render/MakeRenderBox.gren:1857`, inside `makeSignatureBox`).
Bisected: requires (a) a record type with **2+ fields**, (b) the record
written broken-across-lines by the author, and (c) the record sitting where
`forceVertical` for the whole signature is `False` (no `->`-boundary break,
no comment) — i.e. the *overall* signature would otherwise render inline.
Parenthesizing the function-typed field (`(Input -> msg)`) makes no
difference; a single-field record doesn't trigger it; a record the author
wrote flat on one line (`{ a : Int, onInput : Input -> msg }`) renders fine
even in the same position.

Root cause is self-documented at the crash site (lines ~1843-1848): a
2+-field record **type** always renders multi-line once the LPT records it
as author-broken (mirroring the "2+-field record **literal** is
`AlwaysVertical`" rule — see `CLAUDE.md`'s `LogicalPrintingTree` notes), but
elm-format's actual behavior is to *flatten* an author-broken record type
back onto one line when it's not at a `->` segment boundary. This renderer
has no flatten capability ("Box has no flatten"), so instead of emitting
the wrong (still-broken) form, it deliberately `Err`s. This is a documented,
intentional gap, not an oversight — but it's a real missing capability, and
it crashes the whole file rather than degrading.

**Suggested fix approach:** either (1) give the record-type-in-signature
path an actual flatten operation (render each field via `buildFlowBox 0`
and join with `, ` when the overall segment isn't forced vertical), or (2)
at minimum, confirm this is the *only* place `Err`ing on this gap and scope
how common author-broken-record-type-mid-signature is in real code before
deciding how much investment the flatten deserves.

## C) `++`/binop + comment + bracket-operand — idempotency bug

Affected package: `gilramir/gren-html-autodropdown` (`src/AutoDropdown.gren`,
`viewItem`'s `liAttrs` binding).

Source:

```gren
liAttrs = config.liAttrs ++ [
    -- Use mousedown to capture the event before the input's blur event fires (which would
    -- hide the dropdown)
    onMouseDown (config.mouseDownMsg item),
    onMouseEnter (config.mouseEnterMsg idx)
    ]
```

Format pass 1 produces:

```gren
liAttrs =
    config.liAttrs ++ -- Use mousedown to capture the event before the input's blur event fires (which would
       -- hide the dropdown)
       [ onMouseDown (config.mouseDownMsg item)
       , onMouseEnter (config.mouseEnterMsg idx)
       ]
```

Format pass 2 (re-formatting pass 1's own output) produces a **different**
layout — the operand drops to its own line under the operator:

```gren
liAttrs =
    config.liAttrs
        ++ -- Use mousedown to capture the event before the input's blur event fires (which would
           -- hide the dropdown)
           [ onMouseDown (config.mouseDownMsg item)
           , onMouseEnter (config.mouseEnterMsg idx)
           ]
```

Not yet bisected to a minimal repro. Hypothesis: the layout decision (glue
operand to the operator's line vs. drop it below) depends on the comment's
row position relative to the operator/bracket in the *original* source,
and that position looks different after pass 1 has already re-laid the
comment out — i.e. the force-vertical/glue determination for this binop
shape isn't a stable fixed point under its own output. Possibly the same
underlying instability as class E below (comment riding a binop/pipe
operator into a bracket operand).

## D) `else if` condition layout — idempotency bug

Affected package: `icidasset/markdown-gren` (`src/Markdown/Parser/Blocks.gren`,
around line 541).

Source (author wrote the condition flat):

```gren
else if par == "" then
    initial
        |> Array.pushLast (RawParagraph { closed = False, string = line })
        |> Loop
```

Format pass 1 keeps it flat (matches source). Format pass 2 (re-formatting
pass 1's output) force-breaks the condition:

```gren
else if
    par == ""
then
    ...
```

Not yet bisected. Hypothesis: something about the surrounding `when`/`if`-
chain's width or an adjacent line's re-layout in pass 1 pushes this
particular `else if` just past whatever threshold decides
flat-vs-broken for `IfCondition`, and that shift doesn't reverse on further
passes (should confirm whether pass 3 == pass 2, i.e. it's actually a
2-cycle oscillation or a one-way drift).

## E) "unreachable: multi-line item soft-glued after a block" crash

Affected package: `lue-bird/gren-extra-checks` (`src/IntroducedNameIsUsed.gren`,
bisected to the `inspectExtraFile` function, ~line 691).

Crash site: `Render/MakeRenderBox.gren:3246`, inside the generic inline-flow
assembler. The comment at that `Err` explicitly claims this branch is
unreachable because "a multi-line binop operand that is a bracket / record /
paren now forces the whole chain vertical … so a block never precedes
another operand here" — that invariant is false for at least this shape.

The trigger involves a `|> -- comment\n   nextCall` pipe step (comment
riding the `|>` before its next operand) **nested inside a call-argument
lambda** (`Array.foldl (\line soFar -> …) Dict.empty` deep inside a record
field deep inside an `if`/`when` branch) rather than a top-level pipeline.
A shallow top-level repro of the same `|> -- comment\n   next` shape does
**not** crash — top-level pipelines go through the dedicated `PipelineStep`
path (per `CLAUDE.md`), which apparently handles this correctly; the crash
is specific to the generic inline-flow (`assembleFlow`) path used when a
pipe/binop chain is itself an argument or nested expression rather than a
`let`/top-level binding's whole RHS.

**Suggested fix approach:** build a repro that matches the real nesting
(pipe chain as a call argument to `Array.foldl`, itself inside a record
field, itself inside an `if`/`when` branch) to find the minimal trigger,
then either restore the invariant (make the chain force-vertical in this
context too, matching the top-level `PipelineStep` behavior) or handle the
soft-glue-after-block case for real instead of asserting it can't happen.
Given C and D both involve comment placement destabilizing binop/pipe
layout decisions, it's worth checking whether C, D, and E share one root
cause before fixing them independently.

## F) Parser `FAILED TO PARSE` — OUT OF SCOPE for gren-format

Affected package: `gilramir/gren-lrucache/tests` (parses
`../src/LRUCache/LinkedList.gren`, which fails before formatting starts).

```gren
(LinkedList {firstAndLast, nodes} as listToUse) =
```

fails with `Expected character ')'` right after the record pattern, before
`as`. This is the same underlying parser gap as
[compiler-common#31](https://github.com/gren-lang/compiler-common/issues/31)
(documented in `README.md` under "An unparenthesized constructor pattern
can't be aliased with `as`"), just with a record-pattern constructor arg
instead of a simple one, **and** with the parens in the wrong place for the
documented workaround: the README's workaround is `(Just y) as whole`
(parens tight around the constructor application only, `as` outside), but
this source has `(Ctor arg as name)` — parens around the *whole* pattern
including `as name`, so the constructor application inside the parens is
still unparenthesized and hits the same gap one level in. A real user
hitting #31 naturally reached for the "wrong" paren placement.

Per user decision (2026-07-18): **not fixing this** — `compiler`/
`compiler-common` are frozen for gren-format's active work, and this is
purely a parser issue, not a formatter one. Noting it here as a confirmed
real-world instance of #31 in case it's useful signal for whoever picks
#31 back up; not otherwise actioned.

## Summary table

| # | Package | Failure mode | Class | Scope |
|---|---|---|---|---|
| 1 | `blaix/gren-tui` | crash | B | in scope |
| 2 | `blaix/gren-tui/tests/unit` | AST mismatch | A1 | in scope |
| 3 | `blaix/prettynice` | AST mismatch | A1 | in scope |
| 4 | `gilramir/gren-html-autodropdown` | non-idempotent | C | in scope |
| 5 | `gilramir/gren-lrucache/tests` | parse failure | F | **out of scope** |
| 6 | `icidasset/markdown-gren` | non-idempotent | D | in scope |
| 7 | `icidasset/markdown-gren/tests` | AST mismatch | A1 | in scope |
| 8 | `joeybright/gren-turso/integration_tests` | AST mismatch | A1 | in scope |
| 9 | `lue-bird/gren-extra-checks` | crash | E | in scope |
| 10 | `lue-bird/gren-extra-checks/tests` | AST mismatch | A2 | in scope |

9 of 10 failures are in-scope, real formatter bugs, resolving to 5 fix
tickets (A, B, C, D, E — with C/D/E possibly sharing a root cause worth
investigating together first). Class A (5/10 failures) is highest priority:
it's a correctness bug (silently changes string literal meaning), not just
a layout preference.
