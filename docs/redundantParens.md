# Redundant parens: what each formatter strips

Every example below is real output — the input was run through `gren format` and
through `elm-format`, and both columns are what came back. elm-format's output is
shown in Gren syntax (`case … of` written back as `when … is`) so the two are
directly comparable.

**The short version.** elm-format normalizes parens down to the minimum the
meaning requires, at any nesting depth, in any position. gren-format keeps
whatever you wrote, everywhere, with no exceptions — this is a deliberate,
settled choice, not an oversight.

This is [divergence #10](../README.md#divergence-catalogue) in the catalogue. It
is the most common difference between the two formatters on real code.

## Around a `when`, `if`, or `let`

Wrap a block in parens anywhere a single expression is expected — the top of a
definition, a record field, an array item, a lambda body, a `let` binding, a
`when` or `if` branch, the body of a `<|` — and gren-format keeps them.

```gren
-- you wrote:
v = (if cond then one else two)

-- gren-format:
v =
    (if cond then
        one

     else
        two
    )

-- elm-format:
v =
    if cond then
        one

    else
        two
```

```gren
-- you wrote:
v = { fld = (when sel is Just w -> w) }

-- gren-format:
v =
    { fld =
        (when sel is
            Just w ->
                w
        )
    }

-- elm-format:
v =
    { fld =
        when sel is
            Just w ->
                w
    }
```

```gren
-- you wrote:
v = [ (let q = one in q) ]

-- gren-format:
v =
    [ (let
        q =
            one
       in
       q
      )
    ]

-- elm-format:
v =
    [ let
        q =
            one
      in
      q
    ]
```

**The indentation is not a second difference — it follows from the parens.** Once
the `(` is there the block hangs off it, so `else` and `in` sit one column right
of the `(` and the `)` gets a line of its own. Take the paren away and the block
simply starts the line. You cannot keep the parens *and* get elm-format's
columns; it is one difference, not two.

## Around a binary operator's operand

Parenthesize an applied function that an operator is applied to, and gren-format
keeps the parens. elm-format strips them, because application already binds
tighter than any operator, so they cannot change the meaning.

```gren
-- you wrote:
logBase base number =
    (Gren.Kernel.Math.log number) / (Gren.Kernel.Math.log base)
```

```gren
-- gren-format:
logBase base number =
    (Gren.Kernel.Math.log number) / (Gren.Kernel.Math.log base)

-- elm-format:
logBase base number =
    Gren.Kernel.Math.log number / Gren.Kernel.Math.log base
```

## Around a call argument

A positional call-argument slot can never make parens load-bearing — elm-format
strips them here too, down to the minimum every layer needs. gren-format keeps
them anyway, for the same reason it keeps every other redundant paren: what you
wrote is what you get, consistently, everywhere. There is no special case for
call arguments.

## Nested and redundant parens, side by side

| you write | gren-format | elm-format |
|---|---|---|
| `((a)) + ((b))` | `((a)) + ((b))` | `a + b` |
| `(a) + (b)` | `(a) + (b)` | `a + b` |
| `(((a)))` | `(((a)))` | `a` |
| `((f x)) + ((g y))` | `((f x)) + ((g y))` | `f x + g y` |
| `((a))` | `((a))` | `a` |
| `{ fld = ((a)) }` | `{ fld = ((a)) }` | `{ fld = a }` |
| `[ ((a)), ((b)) ]` | `[ ((a)), ((b)) ]` | `[ a, b ]` |
| `node "div" ({ foo = 1, bar = 2 }) []` | `node "div" ({ foo = 1, bar = 2 }) []` | `node "div" { foo = 1, bar = 2 } []` |
| `fn (a) last` | `fn (a) last` | `fn a last` |
| `fn ((a)) last` | `fn ((a)) last` | `fn a last` |
| `fn (((a))) last` | `fn (((a))) last` | `fn a last` |
| `fn ((f x)) last` | `fn ((f x)) last` | `fn (f x) last` |
| `fn (({ a = 1 })) last` | `fn (({ a = 1 })) last` | `fn { a = 1 } last` |

Two things worth reading off that table.

**elm-format's stripping is about meaning, not appearance.** `fn ((f x)) last`
keeps exactly one paren, because a call argument that is itself a call genuinely
needs it — while `((f x)) + ((g y))` keeps none, because an operator's operand
doesn't. It strips to the minimum and stops there.

**gren-format's table has no exceptions.** Every row keeps exactly what was
written, at every nesting depth and in every position, including call arguments.
There is no bug class here to track — the ⚠️ rows that used to mark an
inconsistent one-layer-only strip on call arguments are gone, because
gren-format no longer strips a call argument's parens at all.

## Why gren-format keeps the rest

Stripping a paren means proving it carries no meaning. For an operand that needs
the operator's precedence; in general it needs to know what each position can
hold. gren-format doesn't do that analysis, and won't: this is a settled design
choice, not a gap — nothing about the output is *wrong*, only more explicit than
elm-format's.
