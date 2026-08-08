#!/usr/bin/env python3
"""Reproduce ONE probe from `fuzz-idempotency.py` / `check-decision-stability.py`.

Both of those gates report findings as `<fixture>[<kind>]@<gap>` -- a fixture, a
comment kind, and a byte offset into the fixture where the comment was spliced.
That triple is enough to rebuild the exact input, and rebuilding it by hand is
the first step of every investigation: you cannot reason about a placement bug
from a byte diff, you have to see the two formats and then look at the roles the
tree gave the comment.

This script is that first step. It splices the marker at the gap, formats twice
with `--show-first`, and prints both passes and the diff; `--lpt1` / `--lpt2`
dump the tree each pass was rendered from, which is where a comment's
`CommentRole` and its owning declaration are actually visible.

**The probe definitions are imported from `fuzz-idempotency.py` by path**, not
copied, for the same reason `check-decision-stability.py` does it: a repro that
splices differently from the gate that found the finding is not a repro.

`--show-first` rather than `--show` is deliberate -- `--show` runs the
idempotency comparison internally and fails, which is precisely the state being
investigated, so it would refuse to print the output you need.

Usage:
    ./repro.py <fixture> <kind> <gap>            # both passes + the diff
    ./repro.py <fixture> <kind> <gap> --input    # just the spliced source
    ./repro.py <fixture> <kind> <gap> --lpt1     # the LPT pass 1 rendered from
    ./repro.py <fixture> <kind> <gap> --lpt2     # the LPT pass 2 rendered from
    ./repro.py <fixture> <kind> <gap> --decisions  # which decisions differed

`<kind>` is one of `block` / `multi` / `line` (the three the formatter
distinguishes; see `fuzz-idempotency.py`), or one of those with an `xN` suffix
(`blockx2`) for a finding from that gate's `--run N` pass — a RUN of N comments
spliced into the one gap — or two or more joined with `+` (`block+multi`,
`block+multi+line`) for one of its `--mix` / `--mix-pairs` / `--mix-triples`
passes, a run of DIFFERENT kinds in that order. `<fixture>` may be a path or a
bare basename, which is searched for under `testfiles/`.

    # a finding reported as  TrickyComments.formatted.gren[multi]@100
    ./repro.py TrickyComments.formatted.gren multi 100

Exit status is 0 when the two passes agree (STABLE) and 1 when they do not
(MOVED) -- so it composes into a bisect. Anything else is 2: a bad argument, or
a probe whose source the parser rejects. That last one is not a finding, it is
the same `skipped (parser)` bucket both gates report (a comment between two type
variables, say), so it is named rather than dressed up as a failure.
"""

import argparse
import difflib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE.parent.parent / "gren-format" / "app"


def _load_fuzz_idempotency():
    """Import the probe definitions by path -- `fuzz-idempotency.py`'s name is
    not an identifier, and copying `KINDS` here would let the repro drift onto a
    different splice from the gate that reported the finding."""
    spec = importlib.util.spec_from_file_location(
        "fuzz_idempotency", HERE / "fuzz-idempotency.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FI = _load_fuzz_idempotency()


class Bail(Exception):
    """Anything that means "this run answers nothing" -- kept distinct from
    MOVED so exit 1 never means "the tool could not run"."""


def resolve_fixture(name):
    """A path as given, or a bare basename looked up under `testfiles/`. The
    gates report basenames, so pasting one straight off a findings list works."""
    direct = pathlib.Path(name)
    if direct.exists():
        return direct
    matches = sorted(HERE.glob(f"testfiles/*/{pathlib.Path(name).name}"))
    if not matches:
        raise Bail(f"no fixture named {name!r} under {HERE / 'testfiles'}")
    if len(matches) > 1:
        listing = "\n  ".join(str(m) for m in matches)
        raise Bail(f"{name!r} is ambiguous:\n  {listing}")
    return matches[0]


def make_workdir(base):
    wd = pathlib.Path(tempfile.mkdtemp(dir=base))
    (wd / "src").mkdir()
    (wd / "gren.json").write_text('{ "type": "application" }')
    return wd


def run_app(workdir, flag, source):
    """Write `source` into the throwaway project and run one flag over it."""
    path = workdir / "src" / "Fuzz.gren"
    path.write_text(source)
    return subprocess.run(
        ["node", str(APP), flag, str(path)], capture_output=True, text=True
    )


def format_once(workdir, source, label):
    proc = run_app(workdir, "--show-first", source)
    if proc.returncode == 0 and proc.stdout:
        return proc.stdout
    blob = (proc.stderr or proc.stdout).strip()
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        raise Bail(
            f"{label}: the spliced source does not parse -- this is the "
            f"`skipped (parser)` bucket, not a finding.\n\n{blob}"
        )
    raise Bail(f"{label} failed:\n\n{blob}")


def kind_label(text):
    """Validate a kind label the way the gates SPELL it, rather than against a
    list of the ones that existed when this was written.

    An enumeration went stale the day `--mix-triples` landed: it crossed the
    three kinds pairwise, so `block+multi+line` -- a label a gate had just
    printed -- was rejected by the one tool whose job is to take that label. A
    mixed run is any `+`-joined sequence of two or more kinds, and `mixed_kind`
    has always built one of any length."""
    known = {k[0] for k in FI.KINDS}
    if "+" in text:
        labels = text.split("+")
        if len(labels) >= 2 and all(l in known for l in labels):
            return text
    else:
        base, _, n = text.partition("x")
        if base in known and (not n or (n.isdigit() and 1 <= int(n) <= FI.MAX_RUN)):
            return text
    raise argparse.ArgumentTypeError(
        f"{text!r}: want one of {', '.join(sorted(known))}, a run of one of them "
        f"(`blockx2`, up to x{FI.MAX_RUN}), or two or more joined with `+` "
        "(`block+multi+line`)"
    )


def main(argv):
    ap = argparse.ArgumentParser(
        description="Reproduce one fuzz-idempotency / decision-stability probe."
    )
    ap.add_argument("fixture", help="path, or a bare basename under testfiles/")
    ap.add_argument(
        "kind",
        type=kind_label,
        help="comment kind, a RUN of them (`blockx2` — fuzz-idempotency's --run), "
        "or a MIXED run of any length (`block+multi`, `block+multi+line` — its "
        "--mix / --mix-pairs / --mix-triples)",
    )
    ap.add_argument("gap", type=int, help="byte offset the gate reported")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--input", action="store_true", help="print the spliced source and stop"
    )
    mode.add_argument(
        "--lpt1", action="store_true", help="the LPT pass 1 rendered from"
    )
    mode.add_argument(
        "--lpt2", action="store_true", help="the LPT pass 2 rendered from"
    )
    mode.add_argument(
        "--decisions", action="store_true", help="which decisions differed"
    )
    ap.add_argument(
        "-U", "--context", type=int, default=6, help="diff context lines"
    )
    args = ap.parse_args(argv[1:])

    if not APP.exists():
        raise Bail(f"{APP} not found -- run (cd ../../gren-format && ./build.sh) first")

    path = resolve_fixture(args.fixture)
    src = path.read_text()
    if "+" in args.kind:
        _, text, splice, _ = FI.mixed_kind(args.kind.split("+"))
    else:
        base_kind, _, n = args.kind.partition("x")
        kind = next(k for k in FI.KINDS if k[0] == base_kind)
        _, text, splice, _ = FI.run_kind(kind, int(n) if n else 1)
    probe = splice(src, args.gap, text)

    if args.input:
        sys.stdout.write(probe)
        return 0

    with tempfile.TemporaryDirectory() as base:
        wd = make_workdir(base)

        if args.decisions:
            proc = run_app(wd, "--decisions", probe)
            sys.stdout.write(proc.stdout or proc.stderr)
            return 0 if proc.returncode == 0 else 2

        if args.lpt1:
            proc = run_app(wd, "--lpt", probe)
            sys.stdout.write(proc.stdout or proc.stderr)
            return 0 if proc.returncode == 0 else 2

        first = format_once(wd, probe, "format1")

        if args.lpt2:
            proc = run_app(wd, "--lpt", first)
            sys.stdout.write(proc.stdout or proc.stderr)
            return 0 if proc.returncode == 0 else 2

        second = format_once(wd, first, "format2")

    print("=== format1 ===")
    sys.stdout.write(first)
    print("=== format2 ===")
    sys.stdout.write(second)

    diff = list(
        difflib.unified_diff(
            first.splitlines(True),
            second.splitlines(True),
            "format1",
            "format2",
            n=args.context,
        )
    )
    if not diff:
        print("=== STABLE (the two passes agree) ===")
        return 0
    print("=== diff (format1 -> format2) ===")
    sys.stdout.writelines(diff)
    print("MOVED")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Bail as bail:
        print(bail, file=sys.stderr)
        sys.exit(2)
