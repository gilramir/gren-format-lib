# How the Gren formatter works

This package is the library behind `gren format`: given a Gren source file,
it produces a tidied-up version of the same file — consistent spacing,
consistent indentation, comments and blank lines kept where they belong.

This page is a guided tour of *how* it does that, at a conceptual level.

---

## Overview

Turning your source file into its formatted version happens through a pipeline
of steps, each step handing its result to the next:

```
your source code (text)
        │
        │  parsed by Gren's compiler-common parser
        ▼
code structure  +  comments
        │
        │  Step 1
        ▼
a layout tree
        │
        │  Step 2
        ▼
a render plan
        │
        │  Step 3
        ▼
formatted code (text)
```

**Before this library gets involved**, the compiler-common parser reads your file and
splits it into two pieces:

- the Abstract Syntax Tree (AST) — a description of which function calls which,
  what a `let` contains, what a type looks like, and so on
- a separate list of every comment you wrote, since these
  don't change what the code means, but they do matter for how it looks

This library's job starts from those two pieces and ends with the formatted
text. It never changes what your code means — it only decides how it looks
on the page.

---

## Step 1: building the layout tree

The first step walks over your code's structure and builds a **layout
tree**: one entry for every piece of your program (a function, an
expression, a list, a comment, a blank line, and so on), arranged in the
same shape as your code.

Each entry in this tree isn't the final text yet — it's a *layout decision*.
Some examples of the kinds of decisions recorded here:

- "these pieces can sit next to each other on one line, or if they don't
  fit that way, each one gets its own line"
- "this is a block whose body always starts on the next line, indented"
- "this is a list that's either written all on one line, or with one item
  per line — never a mix"

Where those decisions come from matters: the formatter mostly follows *your*
original line breaks. If you wrote a list across several lines, the layout
tree records "spread this out"; if you wrote it on one line, it records
"keep this together." The tree is really a map of those choices, ready to
be turned into text later.

### Where comments and blank lines fit in

Comments and blank lines aren't part of your code's structure, so they
arrive separately, each tagged with the line and column where you wrote it.

Once the rest of the layout tree is built from your code alone, this step
goes back through and puts each comment and blank line in place —
finding the spot in the tree that sits at that same line and column, and
inserting it right there, next to the code it was originally written
beside. A comment on the same line as some code attaches to that code; a
comment on its own line becomes its own entry, positioned between whatever
came before and after it in your file. The same idea applies to blank
lines: the formatter notices where you left gaps and preserves them as
their own entries in the tree.

The result is a layout tree that has everything: code, comments, and blank
lines, all in the right order and all carrying their layout decisions.

---

## Step 2: turning the layout tree into a render plan

The layout tree says *what could* happen ("these items can go on one line or
several"). The next step turns each of those decisions into something much
more concrete: a small set of building blocks that say exactly what to
print — a piece of text, a line break, or "indent everything from here by
one more level."

This step doesn't do any guessing or searching for the "best" way to lay
things out. Because the layout tree already recorded each decision (based
on how you originally wrote the code), this step just follows those
decisions directly. That's why the same input always produces the same
output, and why there's no "line width" setting to configure — the
formatter isn't trying to fit your code into 80 columns or any other
target, it's reproducing the shape you already chose.

---

## Step 3: turning the render plan into text

The last step is the simplest: walk over the render plan from the previous
step and produce the actual characters of the formatted file — inserting
real newlines, real spaces, and the right amount of indentation at each
level. What comes out the other end is the finished, formatted source file.

---

## Why this design?

The formatter's guiding idea is: **your line breaks are your layout
decisions.** Rather than trying to choose the "best" way to
arrange your code, it honors how you already wrote it and simply makes that
consistent everywhere. This keeps the whole process predictable — running
the formatter twice in a row always produces the same result, and a change
to one part of a file never surprises you by reshuffling an unrelated part.

---

## Where to go next

- [`src/Formatter/README.md`](src/Formatter/README.md) — a full reference
  of formatting rules with worked examples, for anyone using `gren format`
  day to day.
- [`src/Formatter/DEVELOPER.md`](src/Formatter/DEVELOPER.md) — an
  orientation guide for anyone extending the formatter with new syntax.
