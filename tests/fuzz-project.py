#!/usr/bin/env python3
"""Fuzz the modes that WRITE FILES: in-place formatting of a whole project.

Every other gate in this repo runs `--show` on one file. The modes people
actually run -- `gren-format` with no arguments (format the project) and
`gren-format <paths>` -- discover a project, walk its source directories,
and overwrite files, and they had eight fixture tests between them. This
builds a real project out of `gen-random.py` modules and holds those modes
to what `--show` already guarantees per file.

Oracles per trial, all comparing against the single-file `--show` path that
the other gates have already swept:

  A  the no-arg project run exits 0
  B  every file on disk afterwards equals its own `--show` output
  C  the reported "N files reformatted" equals the number that changed
  D  a second run reformats 0 and changes nothing (project idempotency)
  E  the same for `--remove-unused-imports`, against its own `--show`
  F  `gren-format src/` (a directory argument) lands the same bytes as
     the no-arg run
  J  `--diff` writes nothing, names exactly the files that would change, and
     the patch it prints APPLIES to what is on disk and yields exactly what
     the write would have produced -- then says nothing at all once the
     project is formatted

The edges of a mode that WRITES, each built on the same generated project:

  G  a file that does not parse is not written to and does not corrupt the
     others (a run that stops early, leaving later files untouched, is
     tolerated -- G does not require the run to carry on)
  H  a CRLF file formats in place to the same bytes as `--show`, and the
     result is a fixed point (the app normalises line endings when it reads)
  H2 a project already formatted EXCEPT for its line endings is still rewritten
  H3 the same, reached as a positional argument, with `--diff` agreeing
  I  a non-`.gren` file is ignored by a run that really runs
  I2 a lowercase-named `.gren` makes the no-arg run REFUSE, writing nothing
  K  both kinds, reached as a positional argument -- `expandPathToFiles` has
     its own filter, separate from `Outline.findSourceFiles`
  L  `--diff` alongside a file that does not parse still writes nothing

These are a LIST over two axes -- how the files were found (no-arg / `src`
positional / a named file / `--remove-unused-imports` / `--diff`) and what is
wrong with them (dirty / already formatted / CRLF-dirty / CRLF-clean / one
unparseable / a non-source file present). Read as a MATRIX the list has holes,
and a green oracle in one cell says nothing whatever about its neighbours.

That is not hypothetical. Two cells have already been caught out:

  - positional x CRLF-clean was EMPTY and had a bug in it. `Format.readSource`
    handed the path-argument mode only the normalized text, so a CRLF-but-
    otherwise-formatted file compared equal to its own LF output and kept its
    `\r`s forever, while the no-arg run rewrote the same bytes. H3 fills it.
  - non-source x no-arg was FILLED and VACUOUS, which is worse: oracle I
    discarded the exit code, and its `lowercase.gren` made the run refuse the
    project outright, so nothing was ever formatted and the assertion held for
    the wrong reason. Split into I / I2, with the exit code checked.

Still empty, roughly in the order they look worth filling:

  - a named FILE argument (`gren-format src/Mod0.gren`) is an entire column:
    every oracle here passes `src`, the directory, and `expandPathToFiles`
    takes a different branch for the two. The file branch applies no filter at
    all, so a named `lowercase.gren` IS formatted where `src` skips it.
  - `--remove-unused-imports` against anything but dirty and already-formatted
  - CRLF x `--remove-unused-imports`, and CRLF-dirty x positional -- the
    lowest value of the lot now that a single `isAlreadyFormatted` predicate
    answers that question for all three modes.

Trials are seeded and replayable: `--trial N` rebuilds exactly one and
leaves the project directory behind for inspection.

    ./fuzz-project.py -n 40 -j 6
    ./fuzz-project.py --trial 7 --keep
"""
import argparse
import concurrent.futures
import importlib.util
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "..", "gren-format", "app")

spec = importlib.util.spec_from_file_location("genrandom", os.path.join(HERE, "gen-random.py"))
G = importlib.util.module_from_spec(spec)
sys.modules["genrandom"] = G
spec.loader.exec_module(G)

GREN_JSON = """{
    "type": "application",
    "platform": "node",
    "source-directories": [ "src" ],
    "gren-version": "0.6.5",
    "dependencies": { "direct": {}, "indirect": {} }
}
"""

# `module Foo exposing (..)`, and the `port`/`effect` spellings of it. Only the
# NAME is rewritten -- an effect module's `where { .. }` clause sits between the
# name and `exposing` and has to survive untouched.
MODULE_HEADER = re.compile(r"^((?:port |effect )?module\s+)([A-Za-z0-9_.]+)", re.M)


def run_app(args, cwd=None, timeout=120):
    return subprocess.run(["node", APP] + args, capture_output=True, text=True,
                          cwd=cwd, timeout=timeout)


def first_line(s):
    for ln in s.splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def read(path):
    # newline="" -- text mode translates `\r\n` to `\n` on the way in and would
    # hide every line-ending question this fuzzer exists to ask. The oracle
    # watching for a file that KEPT its carriage returns read them away instead,
    # and reported ok against a build that kept every one of them.
    with open(path, newline="") as f:
        return f.read()


def snapshot(src_dir):
    """Module name -> contents, keyed the way `modules` is (no extension)."""
    return {n[:-5]: read(os.path.join(src_dir, n))
            for n in sorted(os.listdir(src_dir)) if n.endswith(".gren")}


def reported_count(stdout):
    m = re.search(r"(\d+) files? reformatted", stdout)
    return int(m.group(1)) if m else None


HUNK = re.compile(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@")


def content_lines(text):
    """A file's lines the way the formatter counts them: a file ending in a
    newline has no empty final line, though `split` invents one."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def diff_sections(stdout):
    """`--diff` output -> {path as printed: the hunk lines for it}.

    The two header lines are skipped BY POSITION, never by prefix: a deleted
    Gren line comment renders as `-` + `-- foo` == `--- foo`, which is
    indistinguishable from a `--- path` header by looking at it.
    """
    out, cur, skip = {}, None, 0
    # `content_lines`, not `split`: stdout ends in a newline, and the empty
    # element that invents would land in the LAST file's hunks. Every real
    # body line carries a ' ', '+', '-' or '\\' prefix, so a bare '' is never
    # one -- which is how the applier's strictness caught this.
    for line in content_lines(stdout):
        if line.startswith("diff "):
            cur = line.rsplit(" ", 1)[-1]
            out[cur] = []
            skip = 2
            continue
        if cur is None:
            continue
        if skip:
            skip -= 1
            continue
        out[cur].append(line)
    return out


def apply_unified(before, hunks):
    """Apply one file's hunks to its line list and return the result.

    Every context and removed line is checked against the source, so a diff
    that does not apply cleanly raises -- which is itself the finding. That is
    the whole point of applying it rather than eyeballing it: a hunk header
    with the wrong line number, or a context line the file does not have, is a
    diff that would mangle a real user's file under `patch`.
    """
    out, si, i = [], 0, 0
    while i < len(hunks):
        m = HUNK.match(hunks[i])
        if not m:
            i += 1
            continue
        start = max(0, int(m.group(1)) - 1)
        if start < si:
            raise ValueError("hunk header goes backwards: %r" % hunks[i])
        out.extend(before[si:start])
        si = start
        i += 1
        while i < len(hunks) and not HUNK.match(hunks[i]):
            line = hunks[i]
            i += 1
            if line.startswith("\\"):
                continue                     # a note about the file, not a line
            if line.startswith(" ") or line.startswith("-"):
                if si >= len(before) or before[si] != line[1:]:
                    raise ValueError("line %d does not match the source" % (si + 1))
                if line.startswith(" "):
                    out.append(before[si])
                si += 1
            elif line.startswith("+"):
                out.append(line[1:])
            else:
                raise ValueError("unrecognised diff line: %r" % line)
    out.extend(before[si:])
    return out


def build_project(root, modules):
    """`root/gren.json` + `root/src/<Name>.gren`, one file per module."""
    src = os.path.join(root, "src")
    os.makedirs(src, exist_ok=True)
    with open(os.path.join(root, "gren.json"), "w") as f:
        f.write(GREN_JSON)
    for name, text in modules.items():
        with open(os.path.join(src, name + ".gren"), "w") as f:
            f.write(text)
    return src


def generate_modules(rng, count, max_depth, comment_rate):
    """`count` modules, each renamed to match the file it will be written to."""
    out = {}
    for i in range(count):
        seed = rng.randrange(10 ** 9)
        text = G.emit_module(G.generate(seed, max_depth, comment_rate))
        name = "Mod%d" % i
        text, n = MODULE_HEADER.subn(lambda m: m.group(1) + name, text, count=1)
        if n != 1:
            return None          # a header this script cannot rename: skip
        out[name] = text
    return out


def one(job):
    trial, max_depth, comment_rate, keep = job
    rng = random.Random(trial)
    modules = generate_modules(rng, rng.randint(2, 5), max_depth, comment_rate)
    if modules is None:
        return trial, "gen-error", "could not rename a module header", None
    root = tempfile.mkdtemp(prefix="fuzzproj-%d-" % trial)
    try:
        return check_project(trial, root, modules)
    finally:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
        else:
            print("  kept: %s" % root)


def check_project(trial, root, modules):
    src = build_project(root, modules)

    # The single-file path is the oracle, so a module it already fails on is
    # not this fuzzer's find -- the other gates own that one.
    expected, expected_rui = {}, {}
    for name in modules:
        path = os.path.join(src, name + ".gren")
        r = run_app(["--show", path])
        if r.returncode != 0:
            return trial, "baseline-fail", first_line(r.stdout + r.stderr), None
        expected[name] = r.stdout
        r2 = run_app(["--remove-unused-imports", "--show", path])
        if r2.returncode != 0:
            return trial, "baseline-rui-fail", first_line(r2.stdout + r2.stderr), None
        expected_rui[name] = r2.stdout

    before = snapshot(src)
    changed = sum(1 for n, t in before.items() if expected[n] != t)

    # A / B / C -- the no-arg project run.
    r = run_app([], cwd=root)
    if r.returncode != 0:
        return trial, "project-run-failed", first_line(r.stdout + r.stderr), root
    after = snapshot(src)
    for name in modules:
        if after[name] != expected[name]:
            return trial, "in-place-differs-from-show", name, root
    said = reported_count(r.stdout)
    if said is not None and said != changed:
        return trial, "wrong-reformatted-count", "said %s, %d changed" % (said, changed), root

    # D -- second run.
    r2 = run_app([], cwd=root)
    if r2.returncode != 0:
        return trial, "second-run-failed", first_line(r2.stdout + r2.stderr), root
    if snapshot(src) != after:
        return trial, "project-not-idempotent", "", root
    said2 = reported_count(r2.stdout)
    if said2 not in (None, 0):
        return trial, "rewrote-formatted-files", "said %s on a formatted project" % said2, root

    # F -- a directory argument must land the same bytes as the no-arg run.
    root_f = root + "-paths"
    build_project(root_f, modules)
    rf = run_app(["src"], cwd=root_f)
    if rf.returncode != 0:
        shutil.rmtree(root_f, ignore_errors=True)
        return trial, "path-arg-run-failed", first_line(rf.stdout + rf.stderr), root
    same = snapshot(os.path.join(root_f, "src")) == after
    shutil.rmtree(root_f, ignore_errors=True)
    if not same:
        return trial, "path-arg-differs", "src/ argument vs no-arg run", root

    # J -- `--diff` on the dirty project. H3 exercises `--diff` only on
    # CRLF-clean input, where the body is EMPTY and all that prints is the
    # note, so until this nothing had ever checked a hunk -- the mode's
    # headline use was the one thing no sweep looked at.
    root_j = root + "-diff"
    src_j = build_project(root_j, modules)
    try:
        before_j = snapshot(src_j)
        rj = run_app(["--diff"], cwd=root_j)
        if rj.returncode != 0:
            return trial, "diff-run-failed", first_line(rj.stdout + rj.stderr), root_j
        if snapshot(src_j) != before_j:
            return trial, "diff-wrote-to-disk", "--diff must be a dry run", root_j
        sections = diff_sections(rj.stdout)
        for name in modules:
            key = "src/%s.gren" % name
            if before_j[name] == expected[name]:
                if key in sections:
                    return trial, "diff-spoke-for-an-unchanged-file", name, root_j
                continue
            if key not in sections:
                return trial, "diff-stayed-silent", name, root_j
            try:
                patched = apply_unified(content_lines(before_j[name]), sections[key])
            except ValueError as e:
                return trial, "diff-does-not-apply", "%s: %s" % (name, e), root_j
            if patched != content_lines(expected[name]):
                return trial, "diff-applied-differs-from-show", name, root_j
        # ... and once the project IS formatted, `--diff` says nothing at all.
        run_app([], cwd=root_j)
        rj2 = run_app(["--diff"], cwd=root_j)
        if rj2.returncode != 0:
            return trial, "diff-clean-run-failed", first_line(rj2.stdout + rj2.stderr), root_j
        if rj2.stdout != "":
            return trial, "diff-spoke-for-a-formatted-project", first_line(rj2.stdout), root_j
    finally:
        keep_or_remove(root_j)

    # E -- the same story with the removal flag, from a fresh copy.
    root_r = root + "-rui"
    src_r = build_project(root_r, modules)
    rr = run_app(["--remove-unused-imports"], cwd=root_r)
    try:
        if rr.returncode != 0:
            return trial, "rui-project-run-failed", first_line(rr.stdout + rr.stderr), root_r
        after_r = snapshot(src_r)
        for name in modules:
            if after_r[name] != expected_rui[name]:
                return trial, "rui-in-place-differs-from-show", name, root_r
        rr2 = run_app(["--remove-unused-imports"], cwd=root_r)
        if rr2.returncode != 0:
            return trial, "rui-second-run-failed", first_line(rr2.stdout + rr2.stderr), root_r
        if snapshot(src_r) != after_r:
            return trial, "rui-project-not-idempotent", "", root_r
        said_r = reported_count(rr2.stdout)
        if said_r not in (None, 0):
            return trial, "rui-rewrote-formatted-files", "said %s" % said_r, root_r
    finally:
        if not os.environ.get("FUZZ_PROJECT_KEEP"):
            shutil.rmtree(root_r, ignore_errors=True)
    return check_edges(trial, root, modules, expected)


BROKEN = "module Broken exposing (f)\n\n\nf =\n    ( 1\n"
# Two different reasons a file in `src/` is not source, and the two modes do
# NOT treat them alike -- see oracles I, I2 and K.
NOT_GREN = {"notes.txt": "this file is not a Gren source file\n"}
LOWERCASE_GREN = {"lowercase.gren": "-- a .gren whose name is not a module name\n"}


def keep_or_remove(path):
    """`--keep` has to keep the per-oracle copies too: every finding below
    names one of THOSE directories, not the base project."""
    if os.environ.get("FUZZ_PROJECT_KEEP"):
        print("  kept: %s" % path)
    else:
        shutil.rmtree(path, ignore_errors=True)


def check_edges(trial, root, modules, expected):
    """G / H / I -- the shapes a mode that writes files has to survive."""
    # G: one unparseable file among good ones.
    root_g = root + "-broken"
    src_g = build_project(root_g, modules)
    with open(os.path.join(src_g, "Broken.gren"), "w") as f:
        f.write(BROKEN)
    try:
        run_app([], cwd=root_g)
        after_g = snapshot(src_g)
        if after_g.get("Broken") != BROKEN:
            return trial, "broken-file-rewritten", "a file that does not parse was written to", root_g
        for name in modules:
            if after_g[name] not in (expected[name], snapshot_of(modules, name)):
                return trial, "partial-write-alongside-parse-error", name, root_g
    finally:
        keep_or_remove(root_g)

    # H: CRLF on the way in.
    root_h = root + "-crlf"
    crlf = {n: t.replace("\n", "\r\n") for n, t in modules.items()}
    src_h = build_project(root_h, crlf)
    try:
        rh = run_app([], cwd=root_h)
        if rh.returncode != 0:
            return trial, "crlf-run-failed", first_line(rh.stdout + rh.stderr), root_h
        after_h = snapshot(src_h)
        for name in modules:
            if after_h[name] != expected[name]:
                return trial, "crlf-differs-from-show", name, root_h
        run_app([], cwd=root_h)
        if snapshot(src_h) != after_h:
            return trial, "crlf-not-idempotent", "", root_h
    finally:
        keep_or_remove(root_h)

    # H2: a project that is ALREADY FORMATTED except for its line endings.
    # The check above cannot see this one -- its projects are dirty enough to
    # be rewritten for other reasons, so a build that treats "CRLF but
    # otherwise formatted" as already-formatted still passes it, leaves every
    # `\r` on disk, and calls the job done. `--show` prints `\n`, so in-place
    # has to write `\n`: line endings are formatting.
    root_h2 = root + "-crlf-clean"
    src_h2 = build_project(root_h2, {n: t.replace("\n", "\r\n")
                                     for n, t in expected.items()})
    try:
        rh2 = run_app([], cwd=root_h2)
        if rh2.returncode != 0:
            return trial, "crlf-clean-run-failed", first_line(rh2.stdout + rh2.stderr), root_h2
        after_h2 = snapshot(src_h2)
        for name in modules:
            if after_h2[name] != expected[name]:
                return trial, "crlf-clean-kept-its-carriage-returns", name, root_h2
    finally:
        keep_or_remove(root_h2)

    # H3: the same CRLF-clean project, reached as a POSITIONAL argument.
    # H2 cannot see this one -- it runs the no-arg mode, and the two modes
    # read their sources through different functions. The path-argument mode
    # got only the line-ending-normalized text and so believed a CRLF file was
    # already formatted, while the no-arg run three lines up rewrote the very
    # same bytes. `--diff` has to agree with whichever one ran, too, so it is
    # asked first and its verdict held against what the write actually did.
    root_h3 = root + "-crlf-clean-paths"
    src_h3 = build_project(root_h3, {n: t.replace("\n", "\r\n")
                                     for n, t in expected.items()})
    try:
        rd3 = run_app(["--diff", "src"], cwd=root_h3)
        if rd3.returncode != 0:
            return trial, "crlf-clean-paths-diff-failed", first_line(rd3.stdout + rd3.stderr), root_h3
        rh3 = run_app(["src"], cwd=root_h3)
        if rh3.returncode != 0:
            return trial, "crlf-clean-paths-run-failed", first_line(rh3.stdout + rh3.stderr), root_h3
        after_h3 = snapshot(src_h3)
        for name in modules:
            if after_h3[name] != expected[name]:
                return trial, "crlf-clean-paths-kept-its-carriage-returns", name, root_h3
            # whatever changed on disk, `--diff` had to have said so
            if ("%s.gren" % name) not in rd3.stdout:
                return trial, "crlf-clean-paths-diff-stayed-silent", name, root_h3
    finally:
        keep_or_remove(root_h3)

    # I: a non-`.gren` file is ignored -- by a run that REALLY RUNS.
    #
    # This oracle used to carry `lowercase.gren` as well and to discard the
    # exit code, and it passed for the wrong reason the whole time: a
    # lowercase `.gren` makes `Outline.findSourceFiles` refuse the entire
    # project, so nothing was ever formatted and "the non-source file is
    # unchanged" was trivially true. A gate that cannot fail is not a gate.
    # The two files are separate oracles now and the exit code is checked.
    root_i = root + "-notsource"
    src_i = build_project(root_i, modules)
    for name, text in NOT_GREN.items():
        with open(os.path.join(src_i, name), "w") as f:
            f.write(text)
    try:
        ri = run_app([], cwd=root_i)
        if ri.returncode != 0:
            return trial, "notsource-run-failed", first_line(ri.stdout + ri.stderr), root_i
        after_i = snapshot(src_i)
        for name in modules:
            if after_i[name] != expected[name]:
                return trial, "notsource-blocked-formatting", name, root_i
        for name, text in NOT_GREN.items():
            if read(os.path.join(src_i, name)) != text:
                return trial, "non-source-file-rewritten", name, root_i
    finally:
        keep_or_remove(root_i)

    # I2: a lowercase-named `.gren` is not a module name, and the no-arg run
    # REFUSES the project over it rather than skipping it. Pinned because it
    # is inherited behaviour, and because the positional mode does not do it
    # (K) -- two modes disagreeing about one file is the shape that produced
    # the CRLF bug.
    root_i2 = root + "-lowercase"
    src_i2 = build_project(root_i2, modules)
    for name, text in LOWERCASE_GREN.items():
        with open(os.path.join(src_i2, name), "w") as f:
            f.write(text)
    try:
        before_i2 = snapshot(src_i2)
        ri2 = run_app([], cwd=root_i2)
        if ri2.returncode == 0:
            return trial, "lowercase-gren-did-not-stop-the-run", "expected a refusal", root_i2
        if snapshot(src_i2) != before_i2:
            return trial, "lowercase-gren-refused-but-wrote-anyway", "", root_i2
    finally:
        keep_or_remove(root_i2)

    # K: both kinds, reached as a POSITIONAL argument. `expandPathToFiles`
    # filters with its own code -- entity type, extension, capital first
    # letter -- entirely separate from `Outline.findSourceFiles`, and no
    # oracle had ever run it.
    root_k = root + "-notsource-paths"
    src_k = build_project(root_k, modules)
    extras = dict(NOT_GREN)
    extras.update(LOWERCASE_GREN)
    for name, text in extras.items():
        with open(os.path.join(src_k, name), "w") as f:
            f.write(text)
    try:
        rk = run_app(["src"], cwd=root_k)
        if rk.returncode != 0:
            return trial, "notsource-paths-run-failed", first_line(rk.stdout + rk.stderr), root_k
        after_k = snapshot(src_k)
        for name in modules:
            if after_k[name] != expected[name]:
                return trial, "notsource-paths-differs-from-show", name, root_k
        for name, text in extras.items():
            if read(os.path.join(src_k, name)) != text:
                return trial, "non-source-file-rewritten-by-path-arg", name, root_k
    finally:
        keep_or_remove(root_k)

    # L: `--diff` alongside a file that does not parse. The mode's whole
    # promise is that it writes nothing, and that promise matters MOST on the
    # run that is going to fail: a dry run that half-writes on its way to an
    # error is the worst outcome available here.
    root_l = root + "-diff-broken"
    src_l = build_project(root_l, modules)
    with open(os.path.join(src_l, "Broken.gren"), "w") as f:
        f.write(BROKEN)
    try:
        before_l = snapshot(src_l)
        rl = run_app(["--diff"], cwd=root_l)
        if rl.returncode == 0:
            return trial, "diff-ignored-a-parse-error", "expected a nonzero exit", root_l
        if snapshot(src_l) != before_l:
            return trial, "diff-wrote-alongside-a-parse-error", "--diff must never write", root_l
    finally:
        keep_or_remove(root_l)
    return trial, "ok", "", None


def snapshot_of(modules, name):
    """The file's ORIGINAL text -- 'untouched' is as acceptable as 'formatted'
    when another file in the project failed to parse; what is not acceptable
    is anything else."""
    return modules[name]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--count", type=int, default=25)
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--trial", type=int, help="run exactly this trial")
    ap.add_argument("--keep", action="store_true", help="leave project dirs behind")
    ap.add_argument("--max-depth", type=int, default=5)
    ap.add_argument("--comment-rate", type=float, default=0.4)
    a = ap.parse_args()
    trials = [a.trial] if a.trial is not None else list(range(a.count))
    if a.keep:
        os.environ["FUZZ_PROJECT_KEEP"] = "1"
    jobs = [(t, a.max_depth, a.comment_rate, a.keep) for t in trials]
    counts, finds = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for trial, bucket, msg, root in ex.map(one, jobs):
            counts[bucket] = counts.get(bucket, 0) + 1
            if bucket != "ok":
                finds.append((trial, bucket, msg, root))
    for b in sorted(counts):
        print("%-32s %d" % (b, counts[b]))
    for trial, bucket, msg, root in finds:
        print("FIND trial=%d %s %s%s" % (trial, bucket, msg,
                                         ("  [%s]" % root) if root else ""))
    return 1 if finds else 0


sys.exit(main())
