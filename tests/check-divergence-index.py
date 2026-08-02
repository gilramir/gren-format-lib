#!/usr/bin/env python3
"""Check that the divergence catalogue and its fixture suite stay 1:1.

`docs/elmFormatComparison.md` numbers each deliberate divergence from
elm-format, and `tests/testfiles/Divergence/` holds one fixture per entry, named
`D<nn><Name>`. The suite's value is that the mapping is exhaustive in both
directions: an entry with no fixture is an undocumented claim nothing tests, and
a fixture with no entry is a test nothing explains.

Run by `run-tests.sh` before the suite itself, so the failure names the drift
rather than showing up as a missing-file I/O error inside a test.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(HERE, "..", "docs", "elmFormatComparison.md")
FIXTURES = os.path.join(HERE, "testfiles", "Divergence")


def main():
    with open(DOC) as f:
        entries = {int(n) for n in re.findall(r'<a id="divergence-(\d+)">', f.read())}

    fixtures = {}
    for name in os.listdir(FIXTURES):
        if not name.endswith(".dirty.gren"):
            continue
        base = name[: -len(".dirty.gren")]
        m = re.match(r"D(\d{2})[A-Z]", base)
        if not m:
            sys.exit(f"{base}: fixture name must start with D<nn> and a capital")
        fixtures.setdefault(int(m.group(1)), []).append(base)

    problems = []
    for n in sorted(entries - set(fixtures)):
        problems.append(f"divergence #{n} has no fixture in testfiles/Divergence/")
    for n in sorted(set(fixtures) - entries):
        problems.append(f"{fixtures[n][0]} has no #{n} entry in {os.path.basename(DOC)}")
    for n, names in sorted(fixtures.items()):
        if len(names) > 1:
            problems.append(f"#{n} has {len(names)} fixtures: {', '.join(sorted(names))}")
    for n in sorted(entries & set(fixtures)):
        base = fixtures[n][0]
        if not os.path.isfile(os.path.join(FIXTURES, base + ".formatted.gren")):
            problems.append(f"{base} has no .formatted.gren")

    if problems:
        print("divergence catalogue / fixture suite out of step:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    print(f"divergence index ok: {len(entries)} entries, {len(entries)} fixtures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
