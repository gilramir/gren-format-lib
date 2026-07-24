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

> **Status.** The plain-name ordering, the single-line-comment cases, and the
> comment-chain rule below are deliberately specified and fixture-covered. The
> remaining **multiline block comment** cases are documented here as *current
> behavior* — they fall out of the same start-row rule that governs single-line
> comments, but they were never a deliberate design decision. Treat those parts of
> the "Comment handling" subsections as descriptive until reviewed; see
> [Open questions](#open-questions-not-yet-deliberately-specified).

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

This is independent of the module's doc comment. elm-format instead reorders a
module's exposing list to follow the `@docs` directives in its doc comment when
they are present, falling back to alphabetical only when they are absent;
gren-format deliberately does not couple the two (a divergence — see the
README's "Comparison with elm-format", point 3).

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

  Without the chain the `--` would be an own-line comment leading `apple`, and if
  the name below it sorted to the front, the comment would be carried across the
  list away from the name it describes. `SortingCommentZoo` covers this.

- **On its own line, between two names** (`itemA` ⏎ `{- note -}` ⏎ `, itemB`):
  the comment leads the name *below* it (`itemB`) and travels with that name.
  gren-format attaches an own-line comment to the following name, not the
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
  `(`), so the sort emits it there directly. See the "Bug B" history in
  `tbd.md` / `comment-arch.md`. A comment leading a name that sorts to any
  *non-first* slot stays inside the bracket, on its own line above that name.

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

#### Multiline block comments (current behavior)

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
  sorts first), same as a single-line own-line comment.

- A comment starting on the row where a multiline block *closes* is part of that
  block's run and belongs to the same name — see "Trailing a comment that trails
  a name" above. This is the one multiline case that has been decided on purpose
  rather than inherited from the start-row rule.

---

## Import-statement sort

### Runs and boundaries

`import` statements sort alphabetically by module name, but only within a
**run** — a stretch of imports with nothing between them: no blank line, and no
comment on its own line. A blank line or an own-line comment is a **boundary**:
it never moves, and it splits the imports around it into independently sorted
groups. A run is fine with multi-row imports (a wrapped exposing list does not
break it) — only a blank line or an own-line comment does.

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
import Mango
import Zebra
-- a section note
import Apple
import Kiwi

import Delta
```

`[Zebra, Mango]` and `[Kiwi, Apple]` are separate runs (split by the comment),
each sorted independently; `Delta` is alone in its own run (blank line above it),
so there is nothing to sort. The comment and the blank line stay exactly where
they were.

### Trailing comments travel, and don't break the run

A comment trailing an import on that import's *own* source row is the one
exception — unlike an own-line comment, it does not break the run, and it travels
with its import if that import moves within the group:

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

### Multiline block comments (current behavior)

Classification again follows the `{-`'s start row:

- On its own line between two imports → a run boundary, just like an own-line
  single-line comment. It stays put and splits the runs around it.
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

## Open questions (not yet deliberately specified)

The multiline-block-comment behavior above is *emergent* — it follows the
start-row classification rule without ever having been chosen on purpose. Cases
worth deciding explicitly:

- A multiline block comment whose `{-` starts on an item/import row but whose
  `-}` closes several rows down: it currently counts as "same-row trailing" (by
  its start) and so travels and does not break a run. Is start-row the intended
  rule, or should a comment that *visually occupies* the gap between two items be
  a boundary/own-line comment regardless of where it opens?
- A multiline block comment sitting alone between two exposing-list items, when
  its owning name sorts to first place: it hoists to between `exposing` and `(`
  like a single-line comment. Confirm the multi-row rendering there is the
  desired shape.

**Decided** (2026-07-23), previously listed here as "interaction of a trailing
multiline block with a following own-line comment on the same item": a comment
starting on the row where the preceding comment ends belongs to that comment's
run, and the run belongs to the name it started on. See "Trailing a comment that
trails a name" above. Before this, such a comment led the *next* name — and if
that name sorted to the front, it travelled across the list away from the name it
described.

`SortingCommentZoo` is the fixture for all of this: it is registered in the test
suite (`tests/src/Test/Formatter/Format.gren`), so a change to any rule on this
page shows up as a diff in `SortingCommentZoo.formatted.gren`. Inspect that diff
before promoting anything above from "current behavior" to "specified".
