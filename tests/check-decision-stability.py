#!/usr/bin/env python3
"""Decision-stability gate: format twice, diff the *decisions* rather than the bytes.

`fuzz-idempotency.py` answers "is this a fixed point". When the answer is no it
hands back a byte diff, and the culprit has to be inferred by hand. With a
residual of hundreds of known non-idempotent probes that inference is the whole
cost, and it does not amortise: two findings with the same cause look no more
alike than two with different ones.

This gate asks the formatter instead. `--decisions` formats a file twice and
reports which layout decisions differed between the passes -- `forceVertical` on
a call, a comment's `CommentRole`, whether a rendered child came back on one
line -- as named branches with no positions in them. Findings that share a cause
then share a name, and the work-list is a histogram instead of a pile of diffs.

Two modes:

    ./check-decision-stability.py               # the corpus as written
    ./check-decision-stability.py --gaps        # a comment in every gap
    ./check-decision-stability.py --gaps --run 2      # a RUN of two per gap
    ./check-decision-stability.py --gaps --mix-pairs  # runs of MIXED kinds

The first is the gate proper and should be green: every `.formatted.gren` is a
fixed point, so nothing may move.

**`--run` / `--mix` reach this gate 2026-08-08, and until then `--gaps` had
never seen a comment RUN.** That is the hole this instrument is worst placed to
have: `fuzz-idempotency.py --run 2` and `--mix-pairs` both *find* run findings
and neither can name a cause, which is the entire job of this script. The kinds
come from the same `run_kind` / `mixed_kind` / `mix_sequences` the fuzzer builds
its probes from — imported, not reimplemented — so a run reported there is the
run probed here and `repro.py` takes the label unchanged.

The second is the instrument, and it is red for the same reason
`fuzz-idempotency.py --gaps` is -- it runs that gate's probes, imported from it
rather than reimplemented, so the two cannot drift onto different gaps. Its
value is not the exit status but the histogram it prints.

Flipped decisions are reported in two groups, because after a declaration
reflows *everything* inside it has moved and a flat list buries the cause:

  - **author-intent decisions** -- `forceVertical`, a comment's `CommentRole`,
    `commentBreaksFlowRow` -- read off the tree before anything is rendered.
    These are the candidate causes, and probes are grouped by which set of them
    moved.
  - **rendered-shape measurements** (`*.rendersOneLine`) -- usually the reflow
    being observed rather than what caused it, though not always: some decisions
    legitimately render a child and measure it.

**Two counters are this gate's own debt**, printed every run and never
swallowed: a probe whose bytes moved with *no* flip at all is `UNEXPLAINED`, and
one explained only by a rendered shape has no named intent behind it. Both come
down by adding a decision to `Formatter.Audit.DecisionTrace` -- see that
module's doc for the rule about what may honestly be traced, which is narrower
than "whatever would make the number smaller".

Rebuild the app first (`cd ../../gren-format && ./build.sh`) -- this shells out
to it, and neither this script nor the fuzzers check freshness.
"""

import argparse
import collections
import concurrent.futures
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

from corpus import add_corpus_argument, corpus_files_for

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE.parent.parent / "gren-format" / "app"


def _load_fuzz_idempotency():
    """The probe definitions live in `fuzz-idempotency.py`, whose name is not an
    identifier. Import it by path rather than copying `gap_indices` and the
    `KINDS` table over here: this gate exists to explain that gate's findings,
    and it can only do that if both are probing the same gaps."""
    spec = importlib.util.spec_from_file_location(
        "fuzz_idempotency", HERE / "fuzz-idempotency.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FI = _load_fuzz_idempotency()


def run_decisions(workdir, source):
    """Write `source` into the worker's project and run `--decisions` on it.

    Returns `(report, status)`: a parsed report and `None`, or `None` and a
    reason. A probe the parser rejects is `"skip"` -- those are parser
    limitations (a comment between two type variables, say), the same ones
    `fuzz-idempotency.py` skips and counts."""
    path = os.path.join(workdir, "src", "Fuzz.gren")
    with open(path, "w") as f:
        f.write(source)
    try:
        proc = subprocess.run(
            ["node", str(APP), "--decisions", path],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "timed out"

    blob = proc.stdout + proc.stderr
    if "FAILED TO PARSE" in blob or "Could not format" in blob:
        return None, "skip"
    if proc.returncode != 0 or not proc.stdout.strip():
        first = (proc.stderr or proc.stdout).strip().splitlines()
        return None, first[0] if first else f"exit {proc.returncode}"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as e:
        return None, f"bad JSON: {e}"


def is_measurement(name):
    """Whether a decision name is a measurement of a rendered box rather than a
    flag read off the tree.

    The distinction is what keeps the histogram readable. Once a declaration
    reflows, every `rendersOneLine` inside it moves too, so a run of findings
    with one cause still shows ten flipped names. Nine of them are the reflow
    being observed; the interesting ones are the author-intent flags that were
    read *before* anything was rendered.

    A measurement is not automatically a consequence -- `makeSignatureBox`
    decides by rendering the flow and asking whether it came back on one line,
    so there the measurement *is* the cause. It is only ranked lower."""
    return name.endswith(".rendersOneLine")


class Findings:
    """What a run accumulates. Kept in one place because both modes report the
    same things and the summary must not drift between them."""

    def __init__(self):
        self.by_input = collections.Counter()  # author-intent decision -> probes
        self.by_measurement = collections.Counter()  # rendered-shape flip -> probes
        self.by_signature = collections.Counter()  # the input name-set -> probes
        self.example = {}  # that name-set -> one probe that has it
        self.members = collections.defaultdict(list)  # that name-set -> every probe
        self.by_branch = collections.Counter()  # (name=choice, gained|lost) -> probes
        self.unexplained = []  # bytes moved, nothing flipped at all
        self.measurement_only = []  # bytes moved, only rendered shapes flipped
        self.moved = 0  # probes whose bytes differed
        self.skipped = 0
        self.errors = []  # (label, message)
        self.known = collections.Counter()  # upstream issue -> probes caused by it
        self.known_labels = set()  # those probes, for marking them in the listing
        self.issue_of = {}  # probe label -> its issue

    def record(self, label, report, issue=None):
        if not report["bytesDiffer"]:
            return
        self.moved += 1
        if issue:
            # Reported, never subtracted: the probe still counts as moved. The
            # name is here so a family that has already been diagnosed and filed
            # is recognisable while picking the next one off the histogram,
            # rather than after a second investigation.
            self.known[issue] += 1
            self.known_labels.add(label)
            self.issue_of[label] = issue
        names = sorted({f["name"] for f in report["flips"]})
        inputs = [n for n in names if not is_measurement(n)]
        measurements = [n for n in names if is_measurement(n)]
        for n in measurements:
            self.by_measurement[n] += 1
        for n in inputs:
            self.by_input[n] += 1

        # Which way each branch moved. A decision name says which question was
        # answered differently; this says what the answer went from and to,
        # which is the difference between "some comment's role changed" and
        # "a comment stopped leading its declaration and became detached".
        for f in report["flips"]:
            if is_measurement(f["name"]) or f["before"] == f["after"]:
                continue
            direction = "gained" if f["after"] > f["before"] else "lost"
            self.by_branch[(f"{f['name']}={f['choice']}", direction)] += 1

        if not names:
            self.unexplained.append(label)
        elif not inputs:
            self.measurement_only.append(label)
        else:
            key = tuple(inputs)
            self.by_signature[key] += 1
            self.example.setdefault(key, label)
            self.members[key].append(label)

    def _histogram(self, title, counter):
        if not counter:
            return
        print(f"\n== {title} ==")
        width = max(len(n) for n in counter)
        for name, count in counter.most_common():
            print(f"  {count:5d}  {name:<{width}}")

    def report(self, verbose):
        self._histogram(
            "flipped author-intent decisions (the candidate causes)", self.by_input
        )
        self._histogram(
            "flipped rendered-shape measurements (usually the reflow being observed)",
            self.by_measurement,
        )

        if self.by_branch:
            print("\n== which way each branch moved ==")
            width = max(len(k) for k, _ in self.by_branch)
            for (branch, direction), count in self.by_branch.most_common():
                print(f"  {count:5d}  {branch:<{width}}  {direction}")

        if self.by_signature:
            print("\n== probes grouped by the author-intent decisions that moved ==")
            for names, count in self.by_signature.most_common(15):
                print(f"  {count:5d}  {' + '.join(names)}")
                print(f"         e.g. {self.example[names]}")
                if verbose:
                    # A decision set is not a shape. One shape routinely reaches
                    # two roles and lands in two groups, so the group alone can
                    # hide a family that the *fixture* names give away at a
                    # glance -- which is how the module-header family was found.
                    by_file = collections.Counter(
                        m.split("[", 1)[0] for m in self.members[names]
                    )
                    for fixture, n in by_file.most_common():
                        mine = [m for m in self.members[names] if m.startswith(fixture)]
                        probes = " ".join(
                            m.split(".formatted.gren", 1)[1] for m in mine
                        )
                        known = {
                            self.issue_of[m] for m in mine if m in self.known_labels
                        }
                        mark = f"  [known: {', '.join(sorted(known))}]" if known else ""
                        print(f"         {n:4d} {fixture}  {probes}{mark}")
            if len(self.by_signature) > 15:
                print(f"  ... and {len(self.by_signature) - 15} more combinations")

        if self.errors:
            print(f"\n== {len(self.errors)} error(s) ==")
            for label, msg in self.errors[: (None if verbose else 10)]:
                print(f"  {label}: {msg}")

        def listing(label, items):
            print(f"  {label:<28}: {len(items)}")
            if items and verbose:
                for item in items:
                    print(f"      {item}")

        print()
        print(f"probes whose bytes moved      : {self.moved}")
        for issue, count in self.known.most_common():
            print(
                f"  {'known upstream':<28}: {count}  ({issue} — the parser hands the\n"
                f"      formatter a tree the source did not mean; still counted, still red)"
            )
        print(
            f"  {'named by an intent flip':<28}: "
            f"{self.moved - len(self.unexplained) - len(self.measurement_only)}"
        )
        listing("rendered-shape flip only", self.measurement_only)
        listing("UNEXPLAINED (nothing moved)", self.unexplained)
        if (self.unexplained or self.measurement_only) and not verbose:
            print("      (-v lists them; each is a decision the trace does not record)")
        print(f"skipped (parser)              : {self.skipped}")


def sweep_corpus(pool, base, files, findings, verbose):
    """The corpus exactly as written. Every fixture is a fixed point, so nothing
    here may move -- this is the mode that can go green."""

    def probe(path):
        src = open(path).read()
        report, status = run_decisions(FI.worker_workdir(base), src)
        return path, report, status

    for path, report, status in pool.map(probe, files):
        name = os.path.basename(path)
        if status == "skip":
            findings.skipped += 1
        elif status is not None:
            findings.errors.append((name, status))
        else:
            findings.record(name, report)
            if report["bytesDiffer"] and verbose:
                print(f"  {name}: bytes moved, {len(report['flips'])} flip(s)")


def sweep_gaps(pool, base, file_data, kinds, findings, verbose):
    """One comment per inter-token gap, in each kind -- `fuzz-idempotency.py`'s
    per-gap pass, reporting decisions instead of bytes.

    Its fast path is reused too, and matters more here: `--decisions` formats
    twice where `--show` formats once, so probing every gap of every file would
    cost double the gate this one is meant to explain. A file whose all-gaps
    variant is already a fixed point has no finding to explain and is skipped
    whole, exactly as there."""
    for label, text, splice, can_fast in kinds:
        print(f"== per-gap comment pass [{label}] ==")
        if can_fast:
            # `per_gap` is the run length: the fast path counts markers over the
            # whole file, so a run of two would look like twice as many comments
            # as gaps and every file would fall to the slow path -- green, and
            # four times the cost.
            per_gap = text.count("¤")
            fast = list(
                pool.map(
                    lambda t: FI.fast_check(base, t[1], t[2], text, per_gap), file_data
                )
            )
        else:
            fast = ["slow"] * len(file_data)

        for (path, src, gaps), verdict in zip(file_data, fast):
            name = os.path.basename(path)
            if verdict == "ok":
                print(f"OK  {name} [{label}]: {len(gaps)} gaps, 0 moved")
                continue

            def probe(g, src=src, splice=splice, text=text):
                return g, run_decisions(FI.worker_workdir(base), splice(src, g, text))

            moved_here = 0
            for g, (report, status) in pool.map(probe, gaps):
                site = f"{name}[{label}]@{g}"
                if status == "skip":
                    findings.skipped += 1
                elif status is not None:
                    findings.errors.append((site, status))
                else:
                    before = findings.moved
                    # Only a probe that actually moved pays for the extra parse
                    # the attribution costs.
                    issue = (
                        FI.known_upstream_issue(
                            FI.worker_workdir(base), splice(src, g, text)
                        )
                        if report["bytesDiffer"]
                        else None
                    )
                    findings.record(site, report, issue)
                    moved_here += findings.moved - before
            status_word = "BUG" if moved_here else "OK "
            print(f"{status_word} {name} [{label}]: {len(gaps)} gaps, {moved_here} moved")


def resolve_kinds(ap, args):
    """The comment kinds one run probes with, as `fuzz-idempotency.py`'s own
    4-tuples.

    Only the *flags* are spelled twice; every probe's text, label and splice
    function comes from that module (`run_kind`, `mixed_kind`,
    `mix_sequences`), so the two gates cannot end up injecting different runs
    into the same gap -- the reason `--gaps` imports it by path at all. A label
    built here is that gate's label, so a finding still hands straight to
    `repro.py`."""
    if not 1 <= args.run <= FI.MAX_RUN:
        ap.error(f"--run must be between 1 and {FI.MAX_RUN}")
    mixes = list(args.mix or [])
    if args.mix_pairs:
        mixes += [",".join(s) for s in FI.mix_sequences(2)]
    if args.mix_triples:
        mixes += [",".join(s) for s in FI.mix_sequences(3)]
    if mixes and (args.kind or args.run != 1):
        ap.error(
            "--mix/--mix-pairs/--mix-triples replaces the kind list and spells "
            "its own run length; it cannot be combined with --kind or --run"
        )
    if not mixes:
        return [
            FI.run_kind(k, args.run)
            for k in FI.KINDS
            if not args.kind or k[0] in args.kind
        ]
    known = {k[0] for k in FI.KINDS}
    seqs = []
    for spec in mixes:
        labels = [s.strip() for s in spec.split(",")]
        if len(labels) < 2 or any(l not in known for l in labels):
            ap.error(f"--mix {spec!r}: want two or more of {', '.join(sorted(known))}")
        seqs.append(labels)
    return [FI.mixed_kind(labels) for labels in seqs]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", action="store_true", help="list every probe the trace could not name")
    ap.add_argument("-j", "--jobs", type=int, default=2, help="concurrent formats (default 2)")
    ap.add_argument(
        "--gaps",
        action="store_true",
        help="probe mode: inject one comment into every gap (fuzz-idempotency.py's per-gap pass)",
    )
    ap.add_argument(
        "--kind",
        action="append",
        choices=[k[0] for k in FI.KINDS],
        help="restrict --gaps to one comment kind (repeatable; default all three)",
    )
    ap.add_argument(
        "--run",
        type=int,
        default=1,
        metavar="N",
        help=f"inject a RUN of N comments per gap instead of one (1-{FI.MAX_RUN})",
    )
    ap.add_argument(
        "--mix",
        action="append",
        metavar="A,B[,C]",
        help="inject a run of DIFFERENT kinds per gap, in the order given; "
        "replaces the kind list and spells its own length",
    )
    ap.add_argument(
        "--mix-pairs",
        action="store_true",
        help="every ordered pair of two different kinds (6 sequences)",
    )
    ap.add_argument(
        "--mix-triples",
        action="store_true",
        help="every ordered triple that is not all one kind (24 sequences)",
    )
    add_corpus_argument(ap)
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv[1:])

    if not APP.exists():
        sys.exit(f"{APP} not found -- run (cd ../../gren-format && ./build.sh) first")

    files = args.files or corpus_files_for(args.corpus)
    kinds = resolve_kinds(ap, args)
    findings = Findings()

    with tempfile.TemporaryDirectory() as base:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            if args.gaps:
                file_data = [(p, open(p).read()) for p in files]
                file_data = [(p, s, FI.gap_indices(s)) for p, s in file_data]
                sweep_gaps(pool, base, file_data, kinds, findings, args.v)
            else:
                sweep_corpus(pool, base, files, findings, args.v)

    findings.report(args.v)

    if args.gaps:
        # Probe mode inherits fuzz-idempotency.py's known-red residual, so a
        # non-zero exit says nothing new. What it is here for is the histogram.
        print(f"\n{'FAIL' if findings.moved else 'PASS'}: {findings.moved} probe(s) moved")
        return 1 if findings.moved else 0

    bad = findings.moved + len(findings.errors)
    print(f"\n{'FAIL' if bad else 'PASS'}: {bad} finding(s) over {len(files)} fixture(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
