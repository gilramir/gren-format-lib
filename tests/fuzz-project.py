#!/usr/bin/env python3
"""Fuzz the modes that WRITE FILES: in-place formatting of a whole project.

Every other gate in this repo runs `--show` on one file. The modes people
actually run -- `gren-format` with no arguments (format the project) and
`gren-format <paths>` -- discover a project, walk its source directories,
and overwrite files, and they had eight fixture tests between them. This
builds a real project out of `gen-random.py` modules and holds those modes
to what `--show` already guarantees per file.

Six oracles per trial, all comparing against the single-file path that the
other gates have already swept:

  A  the no-arg project run exits 0
  B  every file on disk afterwards equals its own `--show` output
  C  the reported "N files reformatted" equals the number that changed
  D  a second run reformats 0 and changes nothing (project idempotency)
  E  the same for `--remove-unused-imports`, against its own `--show`
  F  `gren-format src/` (a directory argument) lands the same bytes as
     the no-arg run

Three more about the edges of a mode that WRITES, each built on top of the
same generated project:

  G  a file that does not parse must not cost the others their formatting,
     and must itself come back byte-identical -- a write mode that gives up
     halfway is the one failure here that loses work
  H  a CRLF file formats in place to the same bytes as `--show`, and the
     result is a fixed point (the app normalises line endings when it reads)
  I  a lowercase-named `.gren` and a non-`.gren` file are not source files
     and must be left alone

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
NOT_SOURCE = {
    "lowercase.gren": "this file is not a Gren source file -- lowercase name\n",
    "notes.txt": "nor is this one\n",
}


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

    # I: files that are not Gren sources.
    root_i = root + "-notsource"
    src_i = build_project(root_i, modules)
    for name, text in NOT_SOURCE.items():
        with open(os.path.join(src_i, name), "w") as f:
            f.write(text)
    try:
        run_app([], cwd=root_i)
        for name, text in NOT_SOURCE.items():
            if read(os.path.join(src_i, name)) != text:
                return trial, "non-source-file-rewritten", name, root_i
    finally:
        keep_or_remove(root_i)
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
