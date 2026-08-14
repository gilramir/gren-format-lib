# Sorting: exposing lists and import statements

`gren-format` reorders two things automatically: the names inside an
`exposing ( ... )` list, and a run of `import` statements. Both are alphabetical,
both are independent of each other, and both must keep every comment attached to
whatever it was describing — across arbitrarily many reformats. This document is
the authoritative spec for how they behave, including the comment cases, which
are where the subtleties live.

The code is `Formatter.Logical.SortSymbols` (`sortExposingLists` and
`sortImportGroups`); comment attachment happens earlier, in
`Formatter.Logical.Comments` (see [commentHandling.md](commentHandling.md)).

---

## Exposing-list sort

### The order

A module's `exposing ( ... )` list and every import's own `exposing ( ... )` list
sort into three groups — **operators**, then **types**, then plain **values** —
and alphabetically (by base name) within each group. This is always the order,
independent of the module's doc comment.

```gren
module Demo exposing (zebra, Kiwi, apple, Mango, (|=))
```

becomes

```gren
module Demo exposing ((|=), Kiwi, Mango, apple, zebra)
```

An operator exposes as `(op)`; a type exposes as `Name` or `Name(..)` (the
`(..)` variant-exposing suffix does not change its sort key). The layout —
flat on one line, or one-per-row — follows what you wrote; sorting never
changes flat-vs-vertical.

elm-format instead reorders a module's exposing list to follow the `@docs` directives in its doc comment when
they are present, falling back to alphabetical only when they are absent;
gren-format deliberately does not couple the two (a divergence — see
[Comparison with elm-format](elmFormatComparison.md#divergence-3), point 3).

### Comment handling

Each name in the list can carry comments, and they travel with the name when it
moves. Which name a comment belongs to is decided by **where the comment starts**
— specifically, by what its starting row already holds — not by what it says:

- **Trailing, same row as a name** (`zebra -- note` / `zebra {- note -}`): the
  comment belongs to that name and moves with it.

  ```gren
  module Demo exposing
      ( zebra -- the last one
      , Kiwi
      , apple
      )
  ```

  becomes (the comment rides `zebra` to its sorted position)

  ```gren
  module Demo exposing
      ( Kiwi
      , apple
      , zebra -- the last one
      )
  ```

- **Trailing a comment that trails a name** (`zebra {- one -} -- two`): comments
  chain. A comment starting on the last row of the comment before it joins that
  comment's run, and the whole run belongs to the name the run started on. This
  is what makes the multi-row case read the way a person reads it — the `--`
  below starts on the row where Mango's block comment closes, not on Mango's own
  row, and it still belongs to `Mango`:

  ```gren
  module Demo exposing
      ( zebra
      , Mango {- mango's
          comment -} -- and mango's trailing line comment
      , apple
      )
  ```

  becomes (the whole run rides `Mango`, glued to the row it was written on)

  ```gren
  module Demo exposing
      ( Mango {- mango's
                 comment -} -- and mango's trailing line comment
      , apple
      , zebra
      )
  ```

  Without the chain the `--` would be a line-leading comment belonging to
  `apple`, and if the name below it sorted to the front, the comment would be
  carried across the list away from the name it describes. `SortingCommentZoo` covers this.

- **On its own line, between two names** (`itemA` ⏎ `{- note -}` ⏎ `, itemB`):
  the comment leads the name *below* it (`itemB`) and travels with that name.
  gren-format attaches a line-leading comment to the following name, not the
  preceding one — this is a deliberate divergence from elm-format, which attaches
  it to the preceding name. The `-- describes zebra` in
  `ExposingListSortCommentBarrier` is this case.

- **Own-line comment whose name sorts to first place**: when the name a leading
  comment travels with ends up *first* after sorting, the comment is rendered on
  its own line between `exposing` and the opening `(`, list below:

  ```gren
  import Foo exposing
      ( zebra
      {- k0 -}
      , apple
      , mango
      )
  ```

  becomes

  ```gren
  import Foo exposing
      {- k0 -}
      ( apple
      , mango
      , zebra
      )
  ```

  This is required for idempotency: a comment before a bracket's first item never
  survives a reparse *inside* the bracket (the parser attaches it ahead of the
  `(`), so the sort emits it there directly. A comment leading a name that sorts
  to any *non-first* slot stays inside the bracket, on its own line above that
  name.

  **On an `effect module` header a single-line hoisted comment glues onto the
  `exposing` row instead**, and this is forced rather than chosen. That header's
  `exposing` keyword carries no position — its real column depends on the
  untracked width of the `where { … }` block — so everything past it is the
  header's position-less tail, which `Comments.gren` glues a single-line comment
  onto (`headerTailGlue`). Emitting the own-line shape there is not a fixed
  point: the reparse re-glues it. A multi-line `{- … -}` breaks its line wherever
  it lands and keeps its own row on both spellings.

  ```gren
  effect module Foo where { subscription = MySub } exposing {- describes apple -}
      ( apple
      , mango
      , zebra
      )
  ```

  Fixtures `EffectExposingSortCommentToFront` (both block kinds) and
  `EffectExposingSortCommentToFrontLine` (`--`).

- **A comment written before the first name** (`( -- describes zebra` ⏎
  `zebra`): this one does **not** travel with a name at all. Because the opening
  `(` has no position in the AST, a comment ahead of the first item is attached
  as a header-level comment right after `exposing`, and it renders there every
  time regardless of which name ends up first:

  ```gren
  module Demo exposing
      ( -- describes zebra
        zebra
      , Kiwi
      , apple
      )
  ```

  becomes

  ```gren
  module Demo exposing
      -- describes zebra
      ( Kiwi
      , apple
      , zebra
      )
  ```

  Note the visual result is the same shape as the sort-to-first case above, but
  the *meaning* differs: here the comment is anchored to the front of the list,
  not to `zebra`.

- **Past the closing `)` of a vertical list**: like the comment before the first
  name, this one leads no name, so it does not travel with one. It is pinned to
  the *end* of the list, rendered on its own line above the `)`:

  ```gren
  module Demo exposing
      ( zebra
      , apple
      ) -- about the list, not about a name
  ```

  becomes

  ```gren
  module Demo exposing
      ( apple
      , zebra
      -- about the list, not about a name
      )
  ```

  Writing the two names in the other order gives the same result — which is the
  point. `ModuleExposingClosePinned` is the fixture.

  A whole **chain** of such comments — links written one after another on the
  same row, any of which may itself span rows — is pinned together, in authored
  order, one per line. `ModuleExposingCloseChainVertical` is the fixture. This
  needs the close bracket to be *elastic*
  (`LogicalPrintingTree.lpnElasticBracketNode`): the derived `)` row grows as
  comments are placed inside it, so a later link cannot land past the list and
  escape the module header's row range.

- **Past the closing `)` of a flat list**: a flat list's `)` has no position and
  no row of its own, so a comment past it and a comment trailing the last name
  occupy the *same place*. Rather than guess from the gap width, the formatter
  reads any comment after the last name as the list's, pins it above the `)`
  like the vertical case, and lets the list open up to make room.
  `ModuleExposingCloseChain` and `ModuleLineFloatingComment` are the fixtures.

  "After the last name" includes a comment the author wrote on the row *below*
  the list, not only one on the list's own row — the derived `)` has no row to
  be above or below, and nothing else follows the list inside the header, so
  both go to the same place (`ModuleExposingCommentBelowFlatList`). Reading only
  the same-row case as the list's would make the two shapes format to each other
  in turn — non-idempotent rather than merely inconsistent.

  This makes a flat list order-independent too: `(apple, zebra) {- c -}`,
  `(zebra, apple) {- c -}`, `(apple, zebra {- c -})` and any spacing in between
  all produce identical bytes.

  Two consequences worth knowing. A comment cannot be attached to the **last**
  name of a *flat* list — write the list vertically if you want that, since
  there the `)` has a row to distinguish them. And the pin is decided on the
  last name in **authored** order *and* the last in **sorted** order (usually
  the same name): authored-last is what makes a genuinely-past-the-`)` comment
  order-independent, and sorted-last is what makes the result a fixed point —
  without it, `(b {- c -}, a)` renders flat as `(a, b {- c -})`, where `c` is
  now on the last name and the next format would pin it. A comment on a name
  that is neither keeps that name (`(apple {- just apple -}, zebra)` is
  untouched), which is why `(b, a {- c -})` and `(a {- c -}, b)` still differ —
  the residue of a `)` the parser never recorded.

#### Multiline block comments

A `{- ... -}` that spans multiple source rows is classified by the row its `{-`
starts on, exactly like a single-line comment:

- Starting on a name's own row → trailing that name, travels with it. Continuation
  lines pad to align under the `{-`.

  ```gren
  import Foo exposing
      ( zebra {- trails zebra
         across rows -}
      , apple
      )
  ```

  becomes

  ```gren
  import Foo exposing
      ( apple
      , zebra {- trails zebra
                 across rows -}
      )
  ```

- On its own line → leads the name below it (or hoists to the front if that name
  sorts first), same as a single-line line-leading comment.

- A comment starting on the row where a multiline block *closes* is part of that
  block's run and belongs to the same name — see "Trailing a comment that trails
  a name" above.

---

## Import-statement sort

### Runs and boundaries

`import` statements sort alphabetically by module name, but only within a
**run** — a stretch of imports with no blank line anywhere in it. A **blank line
is the only boundary**: it never moves, and it splits the imports around it into
independently sorted groups. Multi-row imports are fine (a wrapped exposing list
does not break a run), and neither do comments.

```gren
import Zebra
import Mango
-- a section note
import Kiwi
import Apple

import Delta
```

becomes

```gren
import Apple
-- a section note
import Kiwi
import Mango
import Zebra

import Delta
```

`[Zebra, Mango, Kiwi, Apple]` is one run — the comment does not split it — sorted
as one; `Delta` is alone in its own run (blank line above it), so there is
nothing to sort. The blank line stays exactly where it was, and the comment
travels with `Kiwi`, the import it leads.

Rows of imports separated only by comments read as one block, which is why a
comment is not a boundary. elm-format agrees on this much — it sorts every
import as a single list regardless of comments — though it then hoists all the
comments above the block and drops the blank lines, which gren-format does not.

### Which import a comment travels with

- **Trailing an import on that import's own row** — belongs to that import:

  ```gren
  import Foo -- deprecated, remove soon
  import Bar
  import Baz
  ```

  becomes

  ```gren
  import Bar
  import Baz
  import Foo -- deprecated, remove soon
  ```

- **On its own line directly above an import**, no blank line between — belongs
  to the import below it, and moves with it. Several stacked comments all travel
  together, and so does a comment leading the *first* import of a run (there is
  no special case for the head of a run).

- **With a blank line under it** — it leads no import, so it stays where it is
  while the run below it sorts. This is how a section header keeps its place:

  ```gren
  -- Third-party

  import Zebra
  -- the fast one
  import Apple
  ```

  becomes

  ```gren
  -- Third-party

  -- the fast one
  import Apple
  import Zebra
  ```

- **Below the run's last import** — also leads nothing, so it stays at the end of
  the block.

  `ImportRunCommentAnchors` is the fixture for the last two.

### Multiline block comments

Classification again follows the `{-`'s start row:

- On its own line between two imports → leads the import below it and travels
  with it, just like a line-leading single-line comment.
- Starting on an import's own row (trailing) → travels with that import and does
  **not** break the run, even though the comment's later rows sit below the import
  line. Continuation lines pad to align under the `{-`.

  ```gren
  import Zebra {- starts here
     continues -}
  import Mango
  import Apple
  ```

  becomes (all three are one run; the comment rides `Zebra`)

  ```gren
  import Apple
  import Mango
  import Zebra {- starts here
                  continues -}
  ```

---

## What enforces this page

`SortingCommentZoo` is the fixture that carries every comment shape an exposing
list or an import run can hold, in one module. It is registered in the test
suite (`tests/src/Test/Formatter/Format.gren`), so a change to any rule on this
page shows up as a diff in `SortingCommentZoo.formatted.gren` — read that diff
before deciding a rule change was intended.

`gen-random.py` generates import runs and `exposing` lists with comments in
these positions and checks, among its other oracles, **author-order
invariance**: the same module re-emitted with its runs and lists in reversed
order must format to the same bytes (`GENERATOR.md`, "Author-order invariance
oracle"). That is the one check that can see a comment attached to the *wrong*
name — the comment-multiset oracle discards positions on purpose, and a
wrong-but-stable attachment is still an idempotent fixed point.

The oracle encodes the rules on this page as its two pinned positions: the first
slot of an import run (the blank line and the section header above it belong to
the position, not the import) and index 0 of an exposing list (a comment before
the first name is a header comment, not that name's). If a rule here changes,
those pins have to change with it or the sweeps start reporting false finds.

The generated shapes cover both exposing lists (the module header's and an
import's own) and import runs, with stacked line-leading comments, a block
comment glued onto an import line, trailing comment chains, and multiline block
comments — including a chain whose links span rows. `GENERATOR.md` is the
inventory.
