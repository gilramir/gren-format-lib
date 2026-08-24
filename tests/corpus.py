"""Where the fixture corpus lives, and which HALF of it a gate sweeps.

Fixtures are grouped one directory per test suite under `testfiles/` — e.g.
`BracketComments/`, `KitchenSink/`, and `Divergence/` (the one-fixture-per-entry
index of `docs/elmFormatComparison.md`). Every gate that sweeps "the corpus"
means all of them, so it asks here rather than hard-coding a directory: a new
suite directory is then covered by the fuzzers and the audit the day it is
added, instead of the day somebody remembers to widen four globs.

# The two halves

Each fixture is a PAIR: `<Name>.dirty.gren` (the input the suite formats) and
`<Name>.formatted.gren` (what it must produce). They are not interchangeable
inputs, and the difference is exactly what the gates need to be told about:

  - a `.formatted.gren` is already a fixed point, so a gate that formats it is
    asking the formatter to *perform no rewrite*, and any instability it finds
    comes from the probe the gate spliced in;
  - a `.dirty.gren` is not, so the formatter performs a real rewrite, and the
    probe interacts with that rewrite. Whole rule families — anything keyed on
    the author's rows that the formatting itself moves — are only reachable from
    this half.

The comment fuzzers swept only the formatted half until 2026-08-23. Sweeping the
dirty half found 24 findings in 66,252 probe sites, in three distinct rule
families, none of which the formatted half could reach. That is why the half is
a named axis with a flag rather than a constant in each gate: it is the same
mistake this module's first paragraph exists to prevent, one level down.

`add_corpus_argument` / `corpus_files_for` are the wiring, so every gate spells
the flag identically and a gate's default is the one thing it chooses.
"""

import os

TESTFILES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testfiles")

#: The corpus halves a gate can sweep, and the suffixes each selects.
HALVES = {
    "formatted": (".formatted.gren",),
    "dirty": (".dirty.gren",),
    "both": (".formatted.gren", ".dirty.gren"),
}


def corpus_dirs():
    """Every fixture directory under testfiles/, sorted."""
    return sorted(
        os.path.join(TESTFILES, d)
        for d in os.listdir(TESTFILES)
        if os.path.isdir(os.path.join(TESTFILES, d))
    )


def corpus_files(suffix=".formatted.gren"):
    """Every fixture with the given suffix, across all fixture directories.

    `suffix` may be one suffix or an iterable of them (see `HALVES`); the
    result is sorted, so a `both` sweep interleaves the two halves by name
    rather than running one half and then the other.
    """
    suffixes = (suffix,) if isinstance(suffix, str) else tuple(suffix)
    out = []
    for d in corpus_dirs():
        out.extend(
            os.path.join(d, f)
            for f in os.listdir(d)
            if f.endswith(suffixes)
        )
    return sorted(out)


def corpus_files_for(half):
    """Every fixture in the named half (`formatted` / `dirty` / `both`)."""
    return corpus_files(HALVES[half])


def add_corpus_argument(parser, default="both"):
    """Add the standard `--corpus` flag to a gate's argument parser.

    The default is the gate's own call: it is what the gate sweeps when run
    with no arguments, which is what "the standing configuration" means.
    """
    parser.add_argument(
        "--corpus",
        choices=sorted(HALVES),
        default=default,
        help=(
            "which half of the fixture corpus to sweep when no files are "
            f"named: the already-formatted fixed points, the dirty inputs the "
            f"formatter actually rewrites, or both (default {default})"
        ),
    )
