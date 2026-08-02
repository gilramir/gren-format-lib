#!/usr/bin/env python3
"""Find *.dirty.gren / *.formatted.gren fixture pairs that are byte-identical.

Every dirty fixture is supposed to differ from its formatted counterpart
(otherwise the test doesn't exercise any formatting change). This scans
every fixture directory under testfiles/ for pairs whose contents hash the same
and reports them.
"""

import hashlib
import sys
from pathlib import Path

from corpus import corpus_files


def md5_of(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    dirty_files = [Path(p) for p in corpus_files(".dirty.gren")]
    if not dirty_files:
        print("error: no *.dirty.gren fixtures found under testfiles/", file=sys.stderr)
        return 1

    identical = []
    missing_pair = []

    for dirty_path in dirty_files:
        base = dirty_path.name[: -len(".dirty.gren")]
        label = f"{dirty_path.parent.name}/{base}"
        formatted_path = dirty_path.parent / f"{base}.formatted.gren"

        if not formatted_path.is_file():
            missing_pair.append(label)
            continue

        dirty_hash = md5_of(dirty_path)
        formatted_hash = md5_of(formatted_path)

        if dirty_hash == formatted_hash:
            identical.append((label, dirty_hash))

    print(f"Scanned {len(dirty_files)} dirty fixtures under testfiles/\n")

    if missing_pair:
        print(f"Missing .formatted.gren pair ({len(missing_pair)}):")
        for base in missing_pair:
            print(f"  {base}")
        print()

    if identical:
        print(f"Byte-identical dirty/formatted pairs ({len(identical)}) — these need tweaking:")
        for base, digest in identical:
            print(f"  {base:50s} md5={digest}")
    else:
        print("No byte-identical pairs found — every dirty fixture differs from its formatted output.")

    return 1 if identical else 0


if __name__ == "__main__":
    sys.exit(main())
