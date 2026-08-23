# What lowering to a RenderNode costs

`Formatter.RenderTree.lower` builds a **second tree**: one `RenderNode` per
`LPNode`, allocated up front, alive at the same time as the LPT it came from.
That is the price of making the render barrier a compile-time property
(see [Testing gates](testing.md#render-invariant-check-check-render-invariantpy)
for what the barrier is and why it is worth a type rather than a grep).

The obvious worry is memory bloat. Measured, it is **not detectable** on real
inputs. This page records the measurement, because "we allocate a whole parallel
tree and it costs nothing" is the kind of claim nobody should take on trust, and
because the *method* matters more than the numbers: the first instrument reached
for gave a confidently wrong answer.

Measured 2026-08-23, node 22, on the Tier 1 branch vs. the published
`gilramir/gren-format-lib` 1.0.1 immediately before it.


## Peak RSS is the wrong instrument

The natural measure — `/usr/bin/time -v node app --show FILE`, best of several
runs — says this:

| file | before | after | delta |
|---|---|---|---|
| PrettyExpressive.gren (108K) | 83.1 MB | 96.2 MB | **+15.8%** |
| Comments.gren (161K) | 102.7 MB | 106.1 MB | +3.3% |
| MakeRenderBox.gren (230K) | 179.4 MB | 178.1 MB | **−0.7%** |

(working set, i.e. peak RSS minus the ~60 MB `node` + app baseline.)

Those numbers do not describe a change that allocates a fixed extra fraction per
node. A **negative** delta on the largest file is the tell: peak RSS in V8 is
where the garbage collector happened to decide to grow the heap, and a small
shift in allocation timing pushes a run over or under a growth step. Repeated
sampling does not fix this — it is not run-to-run jitter, it is a different
question being answered. Nine samples of the +15.8% file were internally
consistent (before 144–162 MB, after 157–164 MB) and still meaningless.


## Minimum heap is the right one

What actually matters is how much live heap the work *requires*. Bisect
`--max-old-space-size` for the smallest value in which each binary can still
format the file (±2 MB, the bisect's own resolution):

| file | before | after |
|---|---|---|
| PrettyExpressive.gren (108K) | 21 M | 23 M |
| Comments.gren (161K) | 25 M | 25 M |
| MakeRenderBox.gren (230K) | 33 M | 33 M |
| CssHslReferenceData.gren (365K) | 23 M | 23 M |

Three of four unchanged; the fourth differs by one bisect step. The live-heap
requirement is the same.

(Note that the 365K file needs *less* heap than the 230K one — size is not the
variable, shape is. `CssHslReferenceData.gren` is a huge flat table;
`MakeRenderBox.gren` is deeply nested.)


## Why it is this cheap

1. **`RenderNode` shares the `LPShape`.** `lower` copies a pointer, not a
   payload. The bytes in this tree are in the `Located String`s — comment text,
   identifiers, literals — and every one of them is pointed at, not duplicated.
2. **`RenderNode` is smaller than `LPNode`.** Seven fields against eleven: the
   position cache it drops (`firstPos`, `lastPos`, `minRow`, `maxRow`,
   `lastBracketEnd`, `bracketStart`, and the two bracket booleans) is bigger than
   the four booleans it adds. A node-for-node parallel tree is still cheaper per
   node than the tree it parallels.
3. **The LPT was never the dominant term.** The parser's AST and `Context`, the
   `Box` tree, and the output strings all coexist with it.

Both trees genuinely are alive at once — `renderRoot`'s `root` parameter keeps
the LPT reachable while `lower`'s result is in use — so this is not a case of one
replacing the other. It simply does not amount to much.


## Recursion depth

`lower` is a new recursive descent over the whole tree, so the nesting ceiling
could have dropped. It did not. `tests/pathological-nesting.py`, run against both
binaries, reports every shape same-or-deeper afterwards — `lambda` 411 → 419,
`pipeparenarg` 234 → 237, `pipelambda` 189 → 191, the rest unchanged.

That gate's standing `FAIL: 5 shape(s) break in the FORMATTER before the parser's
own depth limit` is **identical before and after**. It is a pre-existing finding
about the renderer's own recursion, not about lowering.


## The one place that lowers more than once

`Formatter.Audit.PredicateAgreement` (`--audit-predicates`) walks the **LPT** —
it reports the source row and column of each finding, and only the LPT has
those — lowering each node on the spot for the predicates and the render. That is
one lowering per node, so O(n²) allocation over the walk.

It is not a new asymptotic: `checkShapePredicates` already renders every node's
own box, so the walk was quadratic in subtree size before this. Each lowered
subtree is garbage immediately, so nothing accumulates. Measured on
`FlowAssembly.gren` (67K): 0.43 s → 0.45 s wall, 19 M → 19 M minimum heap.

`Formatter.Audit.DecisionTrace` used to lower twice per formatting pass — once
inside `Render.renderRootChildren` and once for its own walk, so four times per
`--decisions` run. `Formatter.Render.renderLoweredChildren` exists to let it
lower once and use the result for both.


## If you are re-measuring

- Bisect `--max-old-space-size`; do not trust peak RSS.
- **Rebuild against the working tree.** `gren-format/gren.json` pins
  `gilramir/gren-format-lib` as a *published* package, so `./build.sh` compiles
  the last published tarball, not your checkout. Switch the pin to
  `local:../gren-format-lib` first — and check with
  `grep -c <a-new-identifier> gren-format/app` that the binary really contains
  your change. `gren-format/DEPLOY.md` covers this; it is easy to measure two
  identical binaries and conclude the change is free.
- Revert the pin to the published version before committing.
