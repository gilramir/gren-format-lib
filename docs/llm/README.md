# `docs/llm/` — written for a model, not for a reader

Nothing in this directory is part of the documentation. A person reading about
`gren-format`, or working on it, should never need to open these files; the
documentation proper is one level up in [`docs/`](../).

What lives here is the material a model working on this repo would otherwise
**re-derive** — usually by proposing something that has already been tried and
backed out, because the rejected thing is the locally obvious one and nothing
in the source signals it was attempted.

| file | what it holds |
|---|---|
| [`attic.md`](attic.md) | approaches tried and reverted, work deliberately deferred and the measurement that settled it, designs specified and never built. **Read it before proposing a change to comment placement, glue, or the test gates.** |
| [`generator-log.md`](generator-log.md) | `gen-random.py`'s version history: what each grammar generation added, and what it found. The generator's *spec* is `tests/GENERATOR.md`. |
| [`comment-parity-triage.md`](comment-parity-triage.md) | the 2026-07-31 triage of 16,141 unreviewed comment-parity divergences, with the per-family evidence the verdicts rest on. Two families were real bugs and are fixed; the rest are documented divergences. |

Two properties to preserve when adding to this directory:

- **Dated is fine here.** Everywhere else in `docs/` a sentence should describe
  what the formatter does today. These files are records of *when* and *why*
  something changed, and that is the whole point of them.
- **A record, not a plan.** Work that is still to be done belongs in an issue or
  in the doc for the thing itself, not in the attic. An entry here is closed.
