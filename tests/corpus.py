"""Where the fixture corpus lives.

Fixtures are grouped one directory per test suite under `testfiles/` —
`Formatter/` is the general corpus, `Divergence/` is the one-fixture-per-entry
index of `docs/elmFormatComparison.md`. Every gate that sweeps "the corpus"
means all of them, so it asks here rather than hard-coding a directory: a new
suite directory is then covered by the fuzzers and the audit the day it is
added, instead of the day somebody remembers to widen four globs.
"""

import os

TESTFILES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testfiles")


def corpus_dirs():
    """Every fixture directory under testfiles/, sorted."""
    return sorted(
        os.path.join(TESTFILES, d)
        for d in os.listdir(TESTFILES)
        if os.path.isdir(os.path.join(TESTFILES, d))
    )


def corpus_files(suffix=".formatted.gren"):
    """Every fixture with the given suffix, across all fixture directories."""
    out = []
    for d in corpus_dirs():
        out.extend(
            os.path.join(d, f) for f in os.listdir(d) if f.endswith(suffix)
        )
    return sorted(out)
