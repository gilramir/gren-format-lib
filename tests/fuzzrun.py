#!/usr/bin/env python3
"""Time-boxed, resumable coordinator for long gen-random.py sweeps.

`gen-random.py` sweeps a *seed range* and exits. This drives it across many
sessions spread over days: you say "fuzz for two hours", it splits that into
~10-minute chunks under `nice`, advances a persistent seed cursor per settings
profile, and records every failure with enough to reproduce it.

    ./fuzzrun.py init                 # write a starter fuzzrun.toml
    ./fuzzrun.py run --for 2h         # sweep for two hours, then stop
    ./fuzzrun.py status               # cursors, coverage, open failures
    ./fuzzrun.py failures -v          # what has been found, and how to repro
    ./fuzzrun.py resweep              # re-test open failures against this build
    ./fuzzrun.py reset --lane NAME    # restart one lane's coverage from base

State lives in `fuzzrun.db` (sqlite); failure artifacts in `fuzzrun-out/`.

Three things this is built around:

**Lanes.** Each settings profile in the config (comment density, nesting depth)
is a lane with its own seed cursor and a weight. A session round-robins chunks
across lanes by weight, so every profile advances every session instead of one
starving the rest. A lane's coverage is always the contiguous prefix
`[base_seed, cursor)` — the cursor advances only when a chunk *completes*, so an
interrupted or timed-out chunk is re-swept rather than leaving a hole.

**Generations.** `gen-random.py`'s grammar decides what a seed means, so a change
to it invalidates every cursor. The runner hashes the generator and, on a change,
starts a new generation: cursors reset to base, prior results stay queryable
under the old hash, and open failures become `stale-grammar` (their seeds no
longer generate the same module, so re-testing them is meaningless — promote the
ones you care about to fixtures before changing the grammar). The same applies
per-lane when a lane's coverage-affecting parameters change in the config.

**Deduplication.** One formatter bug can fail hundreds of seeds. Failures collapse
by `(bucket, minimized source)`, so a session that hits one bug 400 times reports
one entry with 400 hits. Minimizing costs real time, so each chunk caps it
(`max_shrinks_per_chunk`); failures past the cap are recorded unshrunk and
deduped by their full source, which under-dedupes rather than lies — the count of
unshrunk failures is printed and stored, never silent.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "gen-random.py")
APP = os.path.join(HERE, "..", "..", "gren-format", "app")
DB_DEFAULT = os.path.join(HERE, "fuzzrun.db")
OUT_DEFAULT = os.path.join(HERE, "fuzzrun-out")
CONFIG_DEFAULT = os.path.join(HERE, "fuzzrun.toml")

# Source trees whose changes make a built app stale.
SRC_DIRS = [os.path.join(HERE, "..", "src"),
            os.path.join(HERE, "..", "..", "gren-format", "src")]

STARTER_CONFIG = '''\
# fuzzrun.toml — profiles for long gen-random.py sweeps.
#
# Each [lanes.NAME] table is a lane: its own seed cursor, swept independently.
# `weight` is the share of a session's chunks the lane gets (relative to the
# other enabled lanes).
#
# Changing max_depth, comment_rate or no_comments changes what the lane covers,
# so fuzzrun resets that lane's cursor when you do (it asks first). Changing
# jobs, weight or enabled does not.

[defaults]
jobs = 6                    # gen-random -j; this host has 16 cores
nice = 19                   # scheduling niceness for the child (0..19)
idle_io = true              # also run it at idle I/O priority
chunk_minutes = 10          # target wall-clock per chunk
bootstrap_seeds = 40        # first chunk of a lane, before its rate is known
max_shrinks_per_chunk = 20  # cap per-failure minimization; rest reported unshrunk

[lanes.dense-comments]
max_depth = 6
comment_rate = 0.7
base_seed = 1
weight = 2

[lanes.deep-structure]
max_depth = 8
no_comments = true
base_seed = 1
weight = 1

[lanes.default-mix]
max_depth = 5
comment_rate = 0.25
base_seed = 1
weight = 1
'''

BUCKETS = ["crash", "ast-mismatch", "non-idempotent", "comment-loss",
           "sort-order", "timeout", "gen-error"]

STOP = False        # set by SIGINT/SIGTERM; checked between and during chunks


# ───────────────────────────── helpers ────────────────────────────────────

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha1_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return "missing"


def parse_duration(s):
    """'2h', '90m', '45s', '1h30m' -> seconds. A bare number means minutes."""
    s = s.strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return int(float(s) * 60)
    total, seen = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", s):
        total += float(num) * {"h": 3600, "m": 60, "s": 1}[unit]
        seen = True
    if not seen or re.sub(r"(\d+(?:\.\d+)?)\s*([hms])", "", s).strip():
        raise ValueError("bad duration %r (try 2h, 90m, 1h30m)" % s)
    return int(total)


def fmt_duration(secs):
    secs = int(secs)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    if h:
        return "%dh%02dm" % (h, m)
    if m:
        return "%dm%02ds" % (m, s)
    return "%ds" % s


def human_int(n):
    return "{:,}".format(n)


def app_build_id():
    return sha1_file(APP)[:12]


def newest_source_mtime():
    newest = 0.0
    for d in SRC_DIRS:
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if fn.endswith(".gren"):
                    try:
                        newest = max(newest, os.path.getmtime(
                            os.path.join(root, fn)))
                    except OSError:
                        pass
    return newest


def confirm(prompt, assume_yes):
    sys.stdout.flush()   # the caller has just explained *why*; keep it in order
    if assume_yes:
        print("%s  [--yes]" % prompt)
        return True
    if not sys.stdin.isatty():
        print("%s\n  (not a tty and --yes not given — refusing to guess)"
              % prompt, file=sys.stderr)
        return False
    try:
        return input("%s [y/N] " % prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


# ───────────────────────────── config ─────────────────────────────────────

class Lane:
    def __init__(self, name, cfg, defaults):
        self.name = name
        self.max_depth = int(cfg.get("max_depth", 5))
        self.no_comments = bool(cfg.get("no_comments", False))
        self.comment_rate = (0.0 if self.no_comments
                             else float(cfg.get("comment_rate", 0.25)))
        self.base_seed = int(cfg.get("base_seed", 1))
        self.weight = float(cfg.get("weight", 1))
        self.enabled = bool(cfg.get("enabled", True))
        self.jobs = int(cfg.get("jobs", defaults.get("jobs", 6)))
        # DB-side state, filled in by sync_lanes
        self.id = None
        self.cursor = self.base_seed
        self.rate = None

    @property
    def params(self):
        """The coverage-affecting parameters. Changing any of these means the
        lane's swept range no longer describes what it claims to."""
        return {"max_depth": self.max_depth,
                "comment_rate": self.comment_rate,
                "no_comments": self.no_comments,
                "base_seed": self.base_seed}

    def params_json(self):
        return json.dumps(self.params, sort_keys=True)

    def describe(self):
        c = "no-comments" if self.no_comments else "comments %.2f" % self.comment_rate
        return "depth %d, %s, base %d" % (self.max_depth, c, self.base_seed)


def load_config(path):
    if not os.path.exists(path):
        raise SystemExit("no config at %s\n  run: ./fuzzrun.py init" % path)
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    defaults = cfg.get("defaults", {})
    lanes_cfg = cfg.get("lanes", {})
    if not lanes_cfg:
        raise SystemExit("%s defines no [lanes.NAME] tables" % path)
    lanes = [Lane(n, c, defaults) for n, c in lanes_cfg.items()]
    return defaults, lanes


# ───────────────────────────── database ───────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS generation (
  id INTEGER PRIMARY KEY,
  gen_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT);

CREATE TABLE IF NOT EXISTS lane (
  id INTEGER PRIMARY KEY,
  generation_id INTEGER NOT NULL REFERENCES generation(id),
  name TEXT NOT NULL,
  params_json TEXT NOT NULL,
  base_seed INTEGER NOT NULL,
  cursor INTEGER NOT NULL,
  seeds_per_sec REAL,
  retired INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS session (
  id INTEGER PRIMARY KEY,
  generation_id INTEGER NOT NULL REFERENCES generation(id),
  started_at TEXT NOT NULL,
  ended_at TEXT,
  requested_seconds INTEGER,
  status TEXT NOT NULL,
  app_build TEXT);

CREATE TABLE IF NOT EXISTS chunk (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES session(id),
  lane_id INTEGER NOT NULL REFERENCES lane(id),
  seed_from INTEGER NOT NULL,
  seed_to INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  seconds REAL,
  app_build TEXT,
  status TEXT NOT NULL,
  clean INTEGER DEFAULT 0,
  finds INTEGER DEFAULT 0,
  quarantine INTEGER DEFAULT 0,
  unshrunk INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS failure (
  id INTEGER PRIMARY KEY,
  generation_id INTEGER NOT NULL REFERENCES generation(id),
  lane_id INTEGER NOT NULL REFERENCES lane(id),
  dedup_key TEXT NOT NULL,
  bucket TEXT NOT NULL,
  first_seed INTEGER NOT NULL,
  shrunk INTEGER NOT NULL,
  message TEXT,
  repro TEXT,
  artifact_dir TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  first_app_build TEXT,
  hits INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL DEFAULT 'open',
  resolved_at TEXT,
  resolved_app_build TEXT,
  UNIQUE(generation_id, dedup_key));

CREATE TABLE IF NOT EXISTS failure_seed (
  failure_id INTEGER NOT NULL REFERENCES failure(id),
  seed INTEGER NOT NULL,
  chunk_id INTEGER,
  PRIMARY KEY (failure_id, seed));
"""


def open_db(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    # WAL + a busy timeout so `status` / `failures` can be run from another
    # terminal while a sweep is writing, instead of erroring out.
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript(SCHEMA)
    db.commit()
    return db


class SweepLock:
    """Two concurrent sweeps would hand out the same seeds and double-count
    coverage. One lock file per store, holding the owner's pid."""

    def __init__(self, out_root):
        self.path = os.path.join(out_root, "fuzzrun.lock")

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    pid = int(f.read().split()[0])
                os.kill(pid, 0)
            except (ValueError, IndexError, OSError):
                os.remove(self.path)        # stale: owner is gone
            else:
                raise SystemExit(
                    "another sweep is running (pid %d, %s).\n"
                    "  Wait for it, or remove the lock if you know it is dead."
                    % (pid, self.path))
        with open(self.path, "w") as f:
            f.write("%d %s\n" % (os.getpid(), now_iso()))
        return self

    def __exit__(self, *_exc):
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


def current_generation(db):
    return db.execute(
        "SELECT * FROM generation ORDER BY id DESC LIMIT 1").fetchone()


def new_generation(db, gen_hash, note):
    cur = db.execute(
        "INSERT INTO generation (gen_hash, created_at, note) VALUES (?,?,?)",
        (gen_hash, now_iso(), note))
    db.commit()
    return db.execute("SELECT * FROM generation WHERE id=?",
                      (cur.lastrowid,)).fetchone()


def ensure_generation(db, assume_yes):
    """Return the generation row to sweep under, starting a new one (with
    consent) when gen-random.py has changed since the last chunk."""
    gen_hash = sha1_file(GEN)
    row = current_generation(db)
    if row is None:
        return new_generation(db, gen_hash, "initial"), True
    if row["gen_hash"] == gen_hash:
        return row, False
    open_n = db.execute(
        "SELECT COUNT(*) FROM failure WHERE generation_id=? AND state='open'",
        (row["id"],)).fetchone()[0]
    swept = db.execute(
        "SELECT COALESCE(SUM(seed_to - seed_from + 1), 0) FROM chunk "
        "WHERE status='complete' AND lane_id IN "
        "(SELECT id FROM lane WHERE generation_id=?)", (row["id"],)).fetchone()[0]
    print("gen-random.py has changed since the last sweep:")
    print("  was %s ... now %s" % (row["gen_hash"][:12], gen_hash[:12]))
    print("  A seed no longer generates the same module, so the %s seeds"
          % human_int(swept))
    print("  already swept in generation %d no longer cover anything."
          % row["id"])
    if open_n:
        print("  %d open failure(s) will be marked stale-grammar (their seeds"
              % open_n)
        print("  cannot be re-tested; promote any you still care about with"
              " gen-random.py --promote).")
    if not confirm("Start a new generation (all cursors reset to base_seed)?",
                   assume_yes):
        raise SystemExit("aborted — revert gen-random.py, or re-run with --yes")
    db.execute("UPDATE failure SET state='stale-grammar' "
               "WHERE generation_id=? AND state='open'", (row["id"],))
    db.execute("UPDATE lane SET retired=1 WHERE generation_id=?", (row["id"],))
    db.commit()
    return new_generation(db, gen_hash, "gen-random.py changed"), True


def sync_lanes(db, gen, lanes, assume_yes):
    """Attach each config lane to its DB row, creating it or (with consent)
    retiring and recreating it when its coverage parameters changed."""
    for lane in lanes:
        row = db.execute(
            "SELECT * FROM lane WHERE generation_id=? AND name=? AND retired=0",
            (gen["id"], lane.name)).fetchone()
        if row is None:
            cur = db.execute(
                "INSERT INTO lane (generation_id, name, params_json, base_seed,"
                " cursor, seeds_per_sec, created_at) VALUES (?,?,?,?,?,?,?)",
                (gen["id"], lane.name, lane.params_json(), lane.base_seed,
                 lane.base_seed, None, now_iso()))
            db.commit()
            lane.id, lane.cursor, lane.rate = cur.lastrowid, lane.base_seed, None
            continue
        if row["params_json"] != lane.params_json():
            old = json.loads(row["params_json"])
            swept = row["cursor"] - row["base_seed"]
            print("lane %r parameters changed:" % lane.name)
            print("  was %s" % json.dumps(old, sort_keys=True))
            print("  now %s" % lane.params_json())
            print("  its %s swept seeds describe the old settings."
                  % human_int(max(0, swept)))
            if not confirm("Restart lane %r from base_seed %d?"
                           % (lane.name, lane.base_seed), assume_yes):
                raise SystemExit("aborted — restore the lane's settings, "
                                 "rename it, or re-run with --yes")
            db.execute("UPDATE lane SET retired=1 WHERE id=?", (row["id"],))
            cur = db.execute(
                "INSERT INTO lane (generation_id, name, params_json, base_seed,"
                " cursor, seeds_per_sec, created_at) VALUES (?,?,?,?,?,?,?)",
                (gen["id"], lane.name, lane.params_json(), lane.base_seed,
                 lane.base_seed, None, now_iso()))
            db.commit()
            lane.id, lane.cursor, lane.rate = cur.lastrowid, lane.base_seed, None
        else:
            lane.id = row["id"]
            lane.cursor = row["cursor"]
            lane.rate = row["seeds_per_sec"]
    return lanes


# ───────────────────────────── running chunks ─────────────────────────────

def nice_prefix(defaults):
    pre = []
    nice = int(defaults.get("nice", 19))
    if nice and shutil.which("nice"):
        pre += ["nice", "-n", str(nice)]
    if defaults.get("idle_io", True) and shutil.which("ionice"):
        pre += ["ionice", "-c3"]
    return pre


def gen_cmd(lane, defaults, base, count, out_dir, max_shrinks):
    cmd = nice_prefix(defaults) + [
        sys.executable, GEN,
        "-n", str(count), "--base-seed", str(base),
        "-j", str(lane.jobs), "--max-depth", str(lane.max_depth),
        "--out", out_dir, "--max-shrinks", str(max_shrinks)]
    if lane.no_comments:
        cmd.append("--no-comments")
    else:
        cmd += ["--comment-rate", "%g" % lane.comment_rate]
    return cmd


def run_child(cmd, hard_timeout, log_path):
    """Run gen-random in its own session so Ctrl-C reaches the runner only and
    the whole process group can be torn down deterministically.

    Returns (returncode, seconds, outcome) where outcome is
    'complete' | 'timeout' | 'interrupted'."""
    started = time.time()
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        outcome = "complete"
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            elapsed = time.time() - started
            if STOP:
                outcome = "interrupted"
            elif elapsed > hard_timeout:
                outcome = "timeout"
            if outcome != "complete":
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    proc.wait()
                except OSError:
                    proc.wait()
                rc = proc.returncode
                break
            time.sleep(0.4)
    return rc, time.time() - started, outcome


def ingest_chunk(db, gen, lane, chunk_id, run_dir, out_root):
    """Fold a completed chunk's failure artifacts into the store.

    Returns (finds, new_failures, unshrunk, quarantine)."""
    fdir_root = os.path.join(run_dir, "failures")
    finds = new_failures = unshrunk = 0
    if os.path.isdir(fdir_root):
        for seed_name in sorted(os.listdir(fdir_root), key=_as_int):
            fdir = os.path.join(fdir_root, seed_name)
            meta_path = os.path.join(fdir, "meta.json")
            if not os.path.isfile(meta_path):
                continue
            with open(meta_path) as f:
                meta = json.load(f)
            finds += 1
            if not meta.get("shrunk", True):
                unshrunk += 1
            min_path = os.path.join(fdir, "input.min.gren")
            try:
                with open(min_path, "rb") as f:
                    minsrc = f.read()
            except OSError:
                minsrc = b""
            # An unshrunk failure deduplicates by its full source, which
            # under-merges (two hits of one bug look distinct) rather than
            # over-merges. Better to over-report than to hide a second bug.
            key = hashlib.sha1(
                (meta["bucket"] + "\n").encode() + minsrc).hexdigest()
            row = db.execute(
                "SELECT * FROM failure WHERE generation_id=? AND dedup_key=?",
                (gen["id"], key)).fetchone()
            if row is None:
                dest = os.path.join(out_root, "failures", key[:12])
                if os.path.exists(dest):
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(fdir, dest)
                cur = db.execute(
                    "INSERT INTO failure (generation_id, lane_id, dedup_key,"
                    " bucket, first_seed, shrunk, message, repro, artifact_dir,"
                    " first_seen, last_seen, first_app_build, hits, state)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,'open')",
                    (gen["id"], lane.id, key, meta["bucket"], meta["seed"],
                     1 if meta.get("shrunk", True) else 0,
                     meta.get("msg", ""), meta.get("repro", ""),
                     _store_path(dest), now_iso(), now_iso(),
                     meta.get("app_build", "")))
                fid = cur.lastrowid
                new_failures += 1
            else:
                fid = row["id"]
                db.execute("UPDATE failure SET hits=hits+1, last_seen=? "
                           "WHERE id=?", (now_iso(), fid))
            db.execute("INSERT OR IGNORE INTO failure_seed (failure_id, seed,"
                       " chunk_id) VALUES (?,?,?)",
                       (fid, meta["seed"], chunk_id))
    # Quarantine = the generator emitted something that does not parse. Not a
    # formatter find, but it must not go unseen: it means the generator is
    # lying about the language, which makes its other verdicts suspect.
    qcount = 0
    qdir = os.path.join(run_dir, "quarantine")
    if os.path.isdir(qdir):
        keep = os.path.join(out_root, "quarantine", gen["gen_hash"][:12])
        os.makedirs(keep, exist_ok=True)
        for fn in sorted(os.listdir(qdir)):
            if fn.endswith(".gren"):
                qcount += 1
                if len(os.listdir(keep)) < 40:
                    shutil.copy2(os.path.join(qdir, fn),
                                 os.path.join(keep, fn))
    db.commit()
    return finds, new_failures, unshrunk, qcount


def _store_path(dest):
    """Record artifact dirs relative to tests/ when they live under it (the
    default), absolute otherwise — a ../../.. chain out of the tree is
    unreadable and breaks if the runner is invoked from elsewhere."""
    rel = os.path.relpath(dest, HERE)
    return rel if not rel.startswith(os.pardir) else os.path.abspath(dest)


def _as_int(s):
    try:
        return int(s)
    except ValueError:
        return 0


def pick_lane(lanes, chunks_run, disabled):
    """Weighted round-robin: whichever enabled lane is furthest behind its
    share. Ties break on config order, so a fresh session starts at lane one."""
    best, best_score = None, None
    for lane in lanes:
        if not lane.enabled or lane.name in disabled:
            continue
        score = chunks_run.get(lane.name, 0) / max(lane.weight, 0.0001)
        if best_score is None or score < best_score - 1e-9:
            best, best_score = lane, score
    return best


def cmd_run(args):
    with SweepLock(args.out):
        return run_sweep(args)


def run_sweep(args):
    global STOP
    defaults, lanes = load_config(args.config)
    if args.lane:
        wanted = set(args.lane)
        unknown = wanted - {ln.name for ln in lanes}
        if unknown:
            raise SystemExit("no such lane(s): %s" % ", ".join(sorted(unknown)))
        for ln in lanes:
            ln.enabled = ln.name in wanted
    if not any(ln.enabled for ln in lanes):
        raise SystemExit("no enabled lanes")

    if not os.path.exists(APP):
        raise SystemExit("app not found: %s\n  (cd ../../gren-format && "
                         "./build.sh)" % APP)
    if not args.allow_stale_app:
        newest = newest_source_mtime()
        if newest > os.path.getmtime(APP):
            raise SystemExit(
                "the built app is older than the formatter sources.\n"
                "  A long sweep of a stale binary tests the wrong code.\n"
                "  Rebuild:  cd ../../gren-format && ./build.sh\n"
                "  Or sweep the old build anyway: --allow-stale-app")

    total_seconds = parse_duration(args.for_)
    db = open_db(args.db)
    gen, fresh = ensure_generation(db, args.yes)
    lanes = sync_lanes(db, gen, lanes, args.yes)
    os.makedirs(os.path.join(args.out, "failures"), exist_ok=True)
    tmp_root = os.path.join(args.out, "tmp")
    os.makedirs(tmp_root, exist_ok=True)

    chunk_seconds = int(float(defaults.get("chunk_minutes", 10)) * 60)
    bootstrap = int(defaults.get("bootstrap_seeds", 40))
    max_shrinks = int(defaults.get("max_shrinks_per_chunk", 20))
    build = app_build_id()

    scur = db.execute(
        "INSERT INTO session (generation_id, started_at, requested_seconds,"
        " status, app_build) VALUES (?,?,?,'running',?)",
        (gen["id"], now_iso(), total_seconds, build))
    session_id = scur.lastrowid
    db.commit()

    def on_signal(_signum, _frame):
        global STOP
        if STOP:
            return
        STOP = True
        print("\n[stopping — finishing shutdown of the running chunk; its "
              "range will be re-swept next session]", flush=True)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    deadline = time.time() + total_seconds
    print("fuzzrun: generation %d (%s), app %s"
          % (gen["id"], gen["gen_hash"][:12], build))
    if fresh:
        print("         new generation — all lanes start at base_seed")
    print("         %s budget, ~%s chunks, lanes: %s"
          % (fmt_duration(total_seconds), fmt_duration(chunk_seconds),
             ", ".join("%s(w%g)" % (ln.name, ln.weight)
                       for ln in lanes if ln.enabled)))
    print("         ends at %s\n"
          % datetime.fromtimestamp(deadline).strftime("%H:%M:%S"))

    chunks_run, disabled, timeouts = {}, set(), {}
    totals = {"seeds": 0, "finds": 0, "new": 0, "quarantine": 0, "unshrunk": 0,
              "chunks": 0}

    # Below this much remaining, stop rather than start a chunk too short to be
    # worth its startup. Never larger than the chunk target itself, or a short
    # chunk_minutes would throw away most of a small budget.
    floor = min(60, max(10, chunk_seconds // 2))

    while not STOP:
        remaining = deadline - time.time()
        if remaining < floor:
            break
        lane = pick_lane(lanes, chunks_run, disabled)
        if lane is None:
            print("all lanes disabled — stopping early")
            break
        target = min(chunk_seconds, int(remaining))
        if lane.rate:
            n = int(lane.rate * target)
            prev = db.execute(
                "SELECT seed_to - seed_from + 1 AS n FROM chunk WHERE lane_id=?"
                " AND status='complete' ORDER BY id DESC LIMIT 1",
                (lane.id,)).fetchone()
            if prev:                       # never leap on one fast chunk
                n = min(n, prev["n"] * 3)
            n = max(10, n)
        else:
            n = bootstrap
        base = lane.cursor
        hard = max(180, target * 3)

        chunk_dir = os.path.join(tmp_root, "chunk-%d-%s" % (session_id, lane.name))
        shutil.rmtree(chunk_dir, ignore_errors=True)
        ccur = db.execute(
            "INSERT INTO chunk (session_id, lane_id, seed_from, seed_to,"
            " started_at, app_build, status) VALUES (?,?,?,?,?,?,'running')",
            (session_id, lane.id, base, base + n - 1, now_iso(), build))
        chunk_id = ccur.lastrowid
        db.commit()

        left = fmt_duration(max(0, deadline - time.time()))
        print("[%s] %-16s seeds %s..%s (n=%s)  %s left"
              % (datetime.now().strftime("%H:%M:%S"), lane.name,
                 human_int(base), human_int(base + n - 1), human_int(n), left),
              flush=True)

        log_path = os.path.join(tmp_root, "chunk-%d.log" % chunk_id)
        cmd = gen_cmd(lane, defaults, base, n, chunk_dir, max_shrinks)
        rc, secs, outcome = run_child(cmd, hard, log_path)
        chunks_run[lane.name] = chunks_run.get(lane.name, 0) + 1

        if outcome != "complete" or rc not in (0, 1):
            # Cursor is NOT advanced: the range is re-swept rather than trusted.
            why = outcome if outcome != "complete" else "exit %s" % rc
            db.execute("UPDATE chunk SET status=?, ended_at=?, seconds=? "
                       "WHERE id=?", ("aborted:%s" % why, now_iso(), secs,
                                      chunk_id))
            db.commit()
            print("       chunk aborted (%s) after %s — range will be re-swept"
                  % (why, fmt_duration(secs)))
            if outcome == "timeout":
                timeouts[lane.name] = timeouts.get(lane.name, 0) + 1
                lane.rate = (lane.rate or (n / max(secs, 1))) / 2
                db.execute("UPDATE lane SET seeds_per_sec=? WHERE id=?",
                           (lane.rate, lane.id))
                db.commit()
                if timeouts[lane.name] >= 2:
                    disabled.add(lane.name)
                    print("       lane %r timed out twice — disabling it for "
                          "this session (seeds %s.. look pathological; try "
                          "./gen-random.py --seed %s)"
                          % (lane.name, human_int(base), base))
            elif outcome != "interrupted":
                # An unexpected exit code is a runner-visible failure of the
                # generator itself; keep its log and stop using the lane.
                print("       see %s" % log_path)
                disabled.add(lane.name)
            shutil.rmtree(chunk_dir, ignore_errors=True)
            if outcome == "interrupted":
                try:
                    os.remove(log_path)     # a Ctrl-C log says nothing
                except OSError:
                    pass
            continue

        timeouts[lane.name] = 0
        run_dir = os.path.join(chunk_dir, "run-000000")
        finds, newf, unshrunk, quar = ingest_chunk(
            db, gen, lane, chunk_id, run_dir, args.out)
        clean = n - finds - quar
        lane.cursor = base + n
        rate = n / max(secs, 0.001)
        lane.rate = rate if lane.rate is None else 0.6 * rate + 0.4 * lane.rate
        db.execute("UPDATE lane SET cursor=?, seeds_per_sec=? WHERE id=?",
                   (lane.cursor, lane.rate, lane.id))
        db.execute("UPDATE chunk SET status='complete', ended_at=?, seconds=?,"
                   " clean=?, finds=?, quarantine=?, unshrunk=? WHERE id=?",
                   (now_iso(), secs, clean, finds, quar, unshrunk, chunk_id))
        db.commit()
        shutil.rmtree(chunk_dir, ignore_errors=True)
        try:
            os.remove(log_path)
        except OSError:
            pass

        totals["seeds"] += n
        totals["finds"] += finds
        totals["new"] += newf
        totals["quarantine"] += quar
        totals["unshrunk"] += unshrunk
        totals["chunks"] += 1
        note = "%s clean" % human_int(clean)
        if finds:
            note += ", %d find(s), %d new" % (finds, newf)
        if unshrunk:
            note += ", %d unshrunk" % unshrunk
        if quar:
            note += ", %d QUARANTINE" % quar
        print("       %s in %s (%.1f seeds/s)"
              % (note, fmt_duration(secs), rate), flush=True)

    status = "interrupted" if STOP else "done"
    db.execute("UPDATE session SET ended_at=?, status=? WHERE id=?",
               (now_iso(), status, session_id))
    db.commit()

    lines = ["", "session %d %s — %s of sweeping, %d chunks"
             % (session_id, status, fmt_duration(
                 total_seconds - max(0, deadline - time.time())),
                totals["chunks"]),
             "  seeds swept:   %s" % human_int(totals["seeds"]),
             "  failures:      %d (%d newly distinct)"
             % (totals["finds"], totals["new"])]
    if totals["unshrunk"]:
        lines.append("  unshrunk:      %d — hit the %d-per-chunk shrink cap; "
                     "these dedupe by full source" % (totals["unshrunk"],
                                                      max_shrinks))
    if totals["quarantine"]:
        lines.append("  QUARANTINE:    %d — the generator emitted unparseable "
                     "Gren; its other verdicts are suspect until that is 0"
                     % totals["quarantine"])
    open_rows = db.execute(
        "SELECT bucket, COUNT(*) c FROM failure WHERE generation_id=? AND "
        "state='open' GROUP BY bucket ORDER BY c DESC", (gen["id"],)).fetchall()
    if open_rows:
        lines.append("  open failures: " + ", ".join(
            "%s %d" % (r["bucket"], r["c"]) for r in open_rows))
        lines.append("                 ./fuzzrun.py failures -v")
    lines.append("")
    lines.append("lane cursors:")
    for lane in lanes:
        if lane.id is None:
            continue
        row = db.execute("SELECT cursor FROM lane WHERE id=?",
                         (lane.id,)).fetchone()
        lines.append("  %-16s %s covered (next seed %s)"
                     % (lane.name,
                        human_int(row["cursor"] - lane.base_seed),
                        human_int(row["cursor"])))
    out = "\n".join(lines)
    print(out)
    sdir = os.path.join(args.out, "sessions")
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "session-%04d.txt" % session_id), "w") as f:
        f.write(out + "\n")
    return 1 if totals["new"] else 0


# ───────────────────────────── status / failures ──────────────────────────

def cmd_status(args):
    db = open_db(args.db)
    gen = current_generation(db)
    if gen is None:
        print("no sweeps recorded yet — ./fuzzrun.py run --for 30m")
        return 0
    live = sha1_file(GEN)
    print("generation %d  %s  (started %s)"
          % (gen["id"], gen["gen_hash"][:12], gen["created_at"]))
    if live != gen["gen_hash"]:
        print("  !! gen-random.py is now %s — the next run starts a new "
              "generation" % live[:12])
    print("  app build %s%s" % (app_build_id(),
                                "" if os.path.exists(APP) else "  (MISSING)"))
    print()
    lanes = db.execute(
        "SELECT * FROM lane WHERE generation_id=? AND retired=0 ORDER BY id",
        (gen["id"],)).fetchall()
    print("%-16s %12s %12s %10s  %s"
          % ("lane", "covered", "next seed", "seeds/s", "settings"))
    for ln in lanes:
        p = json.loads(ln["params_json"])
        desc = "depth %d, %s" % (
            p["max_depth"],
            "no-comments" if p["no_comments"]
            else "comments %.2f" % p["comment_rate"])
        print("%-16s %12s %12s %10s  %s"
              % (ln["name"], human_int(ln["cursor"] - ln["base_seed"]),
                 human_int(ln["cursor"]),
                 "%.1f" % ln["seeds_per_sec"] if ln["seeds_per_sec"] else "-",
                 desc))
    tot = db.execute(
        "SELECT COALESCE(SUM(seed_to-seed_from+1),0) s, COALESCE(SUM(seconds),0)"
        " t, COUNT(*) c FROM chunk WHERE status='complete' AND lane_id IN "
        "(SELECT id FROM lane WHERE generation_id=?)", (gen["id"],)).fetchone()
    print("\n%s seeds over %s of CPU-wall in %d chunks"
          % (human_int(tot["s"]), fmt_duration(tot["t"]), tot["c"]))
    ab = db.execute(
        "SELECT COUNT(*) c FROM chunk WHERE status LIKE 'aborted%' AND lane_id "
        "IN (SELECT id FROM lane WHERE generation_id=?)", (gen["id"],)).fetchone()
    if ab["c"]:
        print("%d aborted chunk(s) — their ranges were not counted as covered"
              % ab["c"])
    q = db.execute(
        "SELECT COALESCE(SUM(quarantine),0) q FROM chunk WHERE lane_id IN "
        "(SELECT id FROM lane WHERE generation_id=?)", (gen["id"],)).fetchone()
    if q["q"]:
        # This is a generator problem, not a formatter find — but it undermines
        # every other verdict in the generation, so it belongs in `status`.
        print("QUARANTINE: %s module(s) the generator emitted did not parse — "
              "its\n  crash/idempotency verdicts are only trustworthy while "
              "this is 0.\n  Samples: %s"
              % (human_int(q["q"]),
                 os.path.join(args.out, "quarantine", gen["gen_hash"][:12])))
    rows = db.execute(
        "SELECT state, bucket, COUNT(*) c, SUM(hits) h FROM failure WHERE "
        "generation_id=? GROUP BY state, bucket ORDER BY state, c DESC",
        (gen["id"],)).fetchall()
    if rows:
        print("\nfailures (this generation):")
        for r in rows:
            print("  %-14s %-16s %d distinct, %d hits"
                  % (r["state"], r["bucket"], r["c"], r["h"]))
    else:
        print("\nno failures recorded in this generation")
    stale = db.execute(
        "SELECT COUNT(*) c FROM failure WHERE state='stale-grammar'").fetchone()
    if stale["c"]:
        print("  (%d failure(s) from earlier generations are stale-grammar)"
              % stale["c"])
    s = db.execute("SELECT * FROM session ORDER BY id DESC LIMIT 3").fetchall()
    if s:
        print("\nrecent sessions:")
        for r in s:
            print("  #%d %s  %s -> %s  (%s requested)"
                  % (r["id"], r["status"], r["started_at"],
                     r["ended_at"] or "…", fmt_duration(r["requested_seconds"])))
    return 0


def cmd_failures(args):
    db = open_db(args.db)
    gen = current_generation(db)
    if gen is None:
        print("nothing recorded yet")
        return 0
    q = "SELECT f.*, l.name lane FROM failure f JOIN lane l ON l.id=f.lane_id"
    where, params = [], []
    if not args.all_generations:
        where.append("f.generation_id=?")
        params.append(gen["id"])
    if args.state != "all":
        where.append("f.state=?")
        params.append(args.state)
    if args.bucket:
        where.append("f.bucket=?")
        params.append(args.bucket)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY f.state, f.bucket, f.hits DESC"
    rows = db.execute(q, params).fetchall()
    if not rows:
        what = "failures" if args.state == "all" else "%s failures" % args.state
        if args.bucket:
            what = "%s %s" % (args.state if args.state != "all" else "",
                              args.bucket) + " failures"
        scope = ("in any generation" if args.all_generations
                 else "in generation %d" % gen["id"])
        print("no %s %s" % (what.strip(), scope))
        return 0
    for r in rows:
        print("#%-4d %-15s %-16s seed %-10d hits %-5d %s"
              % (r["id"], r["bucket"], r["lane"], r["first_seed"], r["hits"],
                 r["state"]))
        print("      %s" % (r["message"] or "").strip()[:110])
        print("      repro: %s" % r["repro"])
        if not r["shrunk"]:
            print("      NOT MINIMIZED (shrink cap) — input.min.gren is the "
                  "full module")
        print("      %s" % r["artifact_dir"])
        if args.verbose:
            rp = os.path.join(HERE, r["artifact_dir"] or "", "report.txt")
            if os.path.isfile(rp):
                with open(rp) as f:
                    body = f.read().splitlines()
                for line in body[:args.lines]:
                    print("      | %s" % line)
                if len(body) > args.lines:
                    print("      | … %d more lines in report.txt"
                          % (len(body) - args.lines))
        print()
    print("%d failure(s)" % len(rows))
    return 0


def cmd_resweep(args):
    """Re-test open failures against the current build and close the fixed."""
    db = open_db(args.db)
    gen = current_generation(db)
    if gen is None:
        print("nothing recorded yet")
        return 0
    live = sha1_file(GEN)
    if live != gen["gen_hash"]:
        raise SystemExit(
            "gen-random.py has changed (%s -> %s).\n"
            "  These seeds no longer generate the modules that failed, so "
            "re-testing them proves nothing.\n"
            "  Run `./fuzzrun.py run` to start a new generation (it marks "
            "them stale-grammar)."
            % (gen["gen_hash"][:12], live[:12]))
    if not os.path.exists(APP):
        raise SystemExit("app not found: %s" % APP)
    rows = db.execute(
        "SELECT f.*, l.params_json FROM failure f JOIN lane l ON l.id=f.lane_id"
        " WHERE f.generation_id=? AND f.state='open'"
        + (" AND f.id=?" if args.id else ""),
        (gen["id"], args.id) if args.id else (gen["id"],)).fetchall()
    if not rows:
        print("no open failures to re-sweep")
        return 0
    build = app_build_id()
    # Group by lane parameters: one gen-random invocation per distinct setting.
    groups = {}
    for r in rows:
        seeds = [x["seed"] for x in db.execute(
            "SELECT seed FROM failure_seed WHERE failure_id=?",
            (r["id"],)).fetchall()] or [r["first_seed"]]
        groups.setdefault(r["params_json"], []).append((r, seeds))
    print("re-sweeping %d open failure(s) against app %s ..."
          % (len(rows), build))
    fixed = still = 0
    for params_json, entries in groups.items():
        p = json.loads(params_json)
        all_seeds = sorted({s for _r, ss in entries for s in ss})
        cmd = [sys.executable, GEN, "--seeds", ",".join(map(str, all_seeds)),
               "--json", "-j", str(args.jobs),
               "--max-depth", str(p["max_depth"])]
        if p["no_comments"]:
            cmd.append("--no-comments")
        else:
            cmd += ["--comment-rate", "%g" % p["comment_rate"]]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        verdict = {}
        for line in proc.stdout.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            verdict[d["seed"]] = d["bucket"]
        if not verdict:
            print("  gen-random produced no verdicts for depth %d:\n%s"
                  % (p["max_depth"], proc.stderr[-800:]))
            continue
        for r, seeds in entries:
            bad = [s for s in seeds if verdict.get(s, "?") != "ok"]
            if bad:
                still += 1
                buckets = {verdict.get(s) for s in bad}
                extra = ("" if buckets == {r["bucket"]}
                         else "  (now: %s)" % ", ".join(sorted(
                             b for b in buckets if b)))
                print("  still failing  #%-4d %-15s seeds %s%s"
                      % (r["id"], r["bucket"],
                         ",".join(map(str, bad[:6])), extra))
                db.execute("UPDATE failure SET last_seen=? WHERE id=?",
                           (now_iso(), r["id"]))
            else:
                fixed += 1
                print("  FIXED          #%-4d %-15s (%d seed(s) now clean)"
                      % (r["id"], r["bucket"], len(seeds)))
                db.execute("UPDATE failure SET state='fixed', resolved_at=?,"
                           " resolved_app_build=? WHERE id=?",
                           (now_iso(), build, r["id"]))
    db.commit()
    print("\n%d fixed, %d still open" % (fixed, still))
    return 0


def cmd_reset(args):
    db = open_db(args.db)
    gen = current_generation(db)
    if gen is None:
        print("nothing to reset")
        return 0
    if args.lane:
        row = db.execute(
            "SELECT * FROM lane WHERE generation_id=? AND name=? AND retired=0",
            (gen["id"], args.lane)).fetchone()
        if row is None:
            raise SystemExit("no active lane %r in generation %d"
                             % (args.lane, gen["id"]))
        if not confirm("Restart lane %r from seed %d (discards %s seeds of "
                       "coverage)?" % (args.lane, row["base_seed"],
                                       human_int(row["cursor"] - row["base_seed"])),
                       args.yes):
            return 1
        db.execute("UPDATE lane SET cursor=?, seeds_per_sec=NULL WHERE id=?",
                   (row["base_seed"], row["id"]))
        db.commit()
        print("lane %r reset to %d" % (args.lane, row["base_seed"]))
        return 0
    if not confirm("Start a NEW generation (every lane restarts; open failures "
                   "become stale-grammar)?", args.yes):
        return 1
    db.execute("UPDATE failure SET state='stale-grammar' WHERE generation_id=? "
               "AND state='open'", (gen["id"],))
    db.execute("UPDATE lane SET retired=1 WHERE generation_id=?", (gen["id"],))
    db.commit()
    g = new_generation(db, sha1_file(GEN), "manual reset")
    print("generation %d started (%s)" % (g["id"], g["gen_hash"][:12]))
    return 0


def cmd_init(args):
    if os.path.exists(args.config) and not args.force:
        raise SystemExit("%s already exists (--force to overwrite)"
                         % args.config)
    with open(args.config, "w") as f:
        f.write(STARTER_CONFIG)
    print("wrote %s\n  edit the lanes, then: ./fuzzrun.py run --for 2h"
          % args.config)
    return 0


# ───────────────────────────── main ───────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DB_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--config", default=CONFIG_DEFAULT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="sweep for a fixed amount of time")
    r.add_argument("--for", dest="for_", default="1h", metavar="DURATION",
                   help="2h, 90m, 1h30m (a bare number means minutes)")
    r.add_argument("--lane", action="append",
                   help="restrict to this lane (repeatable)")
    r.add_argument("-y", "--yes", action="store_true",
                   help="answer generation/lane reset prompts with yes")
    r.add_argument("--allow-stale-app", action="store_true",
                   help="sweep even though the app is older than the sources")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="cursors, coverage, failure counts")
    s.set_defaults(func=cmd_status)

    f = sub.add_parser("failures", help="list recorded failures")
    f.add_argument("--state", default="open",
                   choices=["open", "fixed", "stale-grammar", "all"])
    f.add_argument("--bucket", choices=BUCKETS)
    f.add_argument("--all-generations", action="store_true")
    f.add_argument("-v", "--verbose", action="store_true",
                   help="include the head of each report.txt")
    f.add_argument("--lines", type=int, default=30,
                   help="report lines to show with -v (default 30)")
    f.set_defaults(func=cmd_failures)

    w = sub.add_parser("resweep",
                       help="re-test open failures against the current build")
    w.add_argument("--id", type=int, help="just this failure id")
    w.add_argument("-j", "--jobs", type=int, default=6)
    w.set_defaults(func=cmd_resweep)

    t = sub.add_parser("reset", help="restart a lane, or start a generation")
    t.add_argument("--lane", help="reset just this lane's cursor")
    t.add_argument("-y", "--yes", action="store_true")
    t.set_defaults(func=cmd_reset)

    i = sub.add_parser("init", help="write a starter fuzzrun.toml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
