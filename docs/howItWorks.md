# How the formatter works

A guided tour of *how* `gren-format` turns your source file into its formatted
version, at a conceptual level. For the rules themselves — what each construct
looks like before and after — see [Gren Formatter Rules](formatterRules.md).

## Table of contents

- [Step 1: building the Logical Printing Tree](#step-1-building-the-logical-printing-tree)
  - [Where comments and blank lines fit in](#where-comments-and-blank-lines-fit-in)
  - [Example](#example)
- [Step 2: turning the Logical Printing Tree into a render plan](#step-2-turning-the-logical-printing-tree-into-a-render-plan)
  - [Example](#example-1)
- [Step 3: turning the render plan into text](#step-3-turning-the-render-plan-into-text)
  - [Example](#example-2)
- [Why this design?](#why-this-design)
- [Where to go next](#where-to-go-next)

---

## Step 1: building the Logical Printing Tree

The first step walks over your code's structure and builds a **Logical
Printing Tree**: one entry for every piece of your program (a function, an
expression, a list, a comment, a blank line, and so on), arranged in the
same shape as your code.

Each entry in this tree isn't the final text yet — it's a *layout decision*.
Some examples of the kinds of decisions recorded here:

- "these pieces sit on one line if you wrote them on one line, or each gets
  its own line if you spread them across rows"
- "this is a block whose body always starts on the next line, indented"
- "this is a list that's either written all on one line, or with one item
  per line — never a mix"

Where those decisions come from matters: the formatter mostly follows *your*
original line breaks. If you wrote a list across several lines, the Logical
Printing Tree records "spread this out"; if you wrote it on one line, it records
"keep this together." The tree is really a map of those choices, ready to
be turned into text later.

### Where comments and blank lines fit in

Comments and blank lines aren't part of your code's structure, so they
arrive separately, each tagged with the line and column where you wrote it.

Once the rest of the Logical Printing Tree is built from your code alone,
this step goes back through and puts each comment and blank line in place —
finding the spot in the tree that sits at that same line and column, and
inserting it next to the code it was originally written
beside. A comment on the same line as some code attaches to that code; a
comment on its own line becomes its own entry, positioned between whatever
came before and after it in your file. The same idea applies to blank
lines: the formatter notices where you left gaps and preserves them as
their own entries in the tree.

The result is a Logical Printing Tree that has everything: code, comments,
and blank lines, all in the right order and all carrying their layout
decisions.

### Example

Comments are what make this genuinely hard: they carry meaning for a human
reader, but the parser doesn't attach them to any particular piece of code —
they just sit in a separate list, tagged with a position. Take this
file where spacing is messy and non-standard:

```gren
module Sample exposing (greet)


import String


-- Greets someone by name
greet:String->String
greet name =
  "Hello, "  ++    name
```

(The parser doesn't care whether `:` and `->` have surrounding spaces —
`greet:String->String` parses exactly like `greet : String -> String`;
whitespace around most tokens is not meaningful.)

The parser splits this into an AST — which never mentions the comment at
all — and a Context that holds only the comment, tagged with the row and
column where it starts:

```
Module "Sample"
├── exports: [ greet ]
├── imports: [ String  (4:1–4:14) ]
└── values
    └── greet(name)                                (8:1–10:24)
        ├── signature: String -> String             (8:7–8:21)
        └── body: Binop "++"                        (10:3–10:24)
            ├── left:  String "Hello, "
            └── right: Var name

Context
└── comments: [ Line "-- Greets someone by name"  (7:1–7:26) ]
```

Building the Logical Printing Tree means walking that AST first, then going
back and re-inserting the comment at row 7 — right where it was written,
directly above the signature it sits beside:

```
RootBox
├── OriginalRows[module]       "module Sample exposing (greet)"
├── EmptyLine
├── OriginalRows[import]       "import String"
├── EmptyLine
├── EmptyLine
├── OriginalRows[lineComment]  "-- Greets someone by name"
├── OriginalRows[funcSig]      "greet : String -> String"
└── OriginalRows[funcDecl]
    ├── AcrossOrVertical        "greet name ="
    └── BodyBlock
        └── Binop "++"
            ├── "Hello, "
            └── OpAndRhs  "++ name"
```

Notice there's no `EmptyLine` between the comment, the signature, and the
function itself — all three stay glued together as one declaration unit.
(See [Blank lines around comments](formatterRules.md#blank-lines-around-comments)
for the general rule.)

---

## Step 2: turning the Logical Printing Tree into a render plan

The Logical Printing Tree says *what could* happen ("these items can go on
one line or several"). The next step turns each of those decisions into
something much more concrete: a small set of building blocks that say
exactly what to print — a piece of text, a line break, or "indent
everything from here by one more level."

This step doesn't do any guessing or searching for the "best" way to lay
things out. Because the Logical Printing Tree already recorded each
decision (based on how you originally wrote the code), this step just
follows those decisions directly. That's why the same input always
produces the same output, and why there's no "line width" setting to
configure — the formatter isn't trying to fit your code into 80 columns or
any other target, it's reproducing the shape you already chose.

### Example

Continuing the same example, the Logical Printing Tree from Step 1 becomes
this render plan — one entry per root item, each a small tree of concrete
building blocks. A `Stack` is a box that is already committed to printing as
2 or more actual lines; everything else (`Seq`, and the bare pieces inside
it — text, `Space`, `Tab`) stays on the current line:

```
[0] Seq[ "module", Space, "Sample", Space, "exposing", Space, "(", "greet", ")" ]

[1] ""

[2] Seq[ "import", Space, "String" ]

[3] ""
[4] ""

[5] "-- Greets someone by name"

[6] Seq[ "greet", Space, ":", Space, "String", Space, "->", Space, "String" ]

[7] Stack
    ├── Seq[ "greet", Space, "name", Space, "=" ]
    └── Seq[ Tab, "\"Hello, \"", Space, "++", Space, "name" ]
```

The comment (entry `[5]`) is just a bare piece of text sitting between two
blank lines (entries `[3]`/`[4]`, each a bare empty string) and the
signature — nothing left to decide about it. The whole
signature (entry `[6]`) is one `Seq` with an ordinary `Space` between every
token; there's no separate "could this become a newline?" node the way
there was in Step 1, because a signature you wrote on one line is already
settled at this stage (see [Type signatures](formatterRules.md#type-signatures)).
The function (entry `[7]`) is where a real decision shows up: it's a `Stack`,
meaning it *will* print as 2 lines no matter what, because you wrote `=`
and the body on separate rows. The `Tab` at the start of the second line is
what becomes the body's indentation once Step 3 renders it — not a fixed
"4 spaces" but a jump to the next indent stop, the same primitive
elm-format itself uses. Step 3 doesn't choose between staying flat or
breaking; it just executes whichever this tree already committed to.

---

## Step 3: turning the render plan into text

The last step is the simplest: walk over the render plan from the previous
step and produce the actual characters of the formatted file — inserting
real newlines, real spaces, and the right amount of indentation at each
level. What comes out the other end is the finished, formatted source file.

### Example

Rendering the plan from Step 2 produces the finished file:

```gren
module Sample exposing (greet)

import String


-- Greets someone by name
greet : String -> String
greet name =
    "Hello, " ++ name
```

The two blank lines around `module`/`import` collapsed to one, `:` and
`->` each got a surrounding space, the four spaces around `++` collapsed
to one, the 2-space body indent became 4, and the comment landed exactly
where it started — still glued to the signature, with no blank line
between them.

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

- [Gren Formatter Rules](formatterRules.md) — a full reference of
  formatting rules with examples, for anyone using `gren format` day
  to day.
- [Adding new Gren syntax to the formatter](addingSyntax.md) — the orientation
  guide for a new AST node, declaration kind, or expression form.
