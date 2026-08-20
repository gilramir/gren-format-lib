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
    ./fuzzrun.py export -o bugs.txt   # bundle failures + their .gren to send on
    ./fuzzrun.py reset --lane NAME    # restart one lane's coverage from base

    ./fuzzrun.py coordinate --for 2h  # spread one sweep across several hosts …
    ./fuzzrun.py worker --master h:9999 -j 12     # … run on each of the others
    ./fuzzrun.py status --master h:9999           # ask the coordinator, not the db

State lives in `fuzzrun.db` (sqlite); failure artifacts in `fuzzrun-out/`.

Four things this is built around:

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

**Distributed sweeps.** `coordinate` is the same runner with a second transport:
one master hands seed ranges to `worker` processes on other hosts over TCP, all
of them sharing one directory (config, database, artifacts) over NFS. The master
does no sweeping of its own. The lane picker, chunk sizer, deadline rule and
`ingest_chunk` are shared with `run` — only "how does a chunk get executed"
differs. The one invariant that is not free with several workers is the
contiguous-prefix cursor: a lane's cursor advances to the **low-water mark**, the
first seed of the oldest still-in-flight chunk, so a chunk that finishes ahead of
a laggard is banked but not counted until the laggard lands. Design and rationale
in `docs/distributedFuzzing.md`.
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
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

# Distributed sweeps (./fuzzrun.py coordinate / worker). Only the coordinator
# reads this table; workers are stateless and are told everything they need.
[distributed]
port = 9999
bind = ""                   # "" = this host's own name. NOT 0.0.0.0: a port
                            # that accepts "here are my results" should be
                            # reachable on purpose, not by default.
lease_seconds = 120         # no heartbeat for this long -> chunk reissued
drain_grace_minutes = 35    # after the deadline, how long to wait for
                            # in-flight chunks before giving up on their ranges
'''

BUCKETS = ["crash", "ast-mismatch", "non-idempotent", "comment-loss",
           "sort-order", "predicate-lie", "rui-crash", "rui-ast-mismatch",
           "rui-non-idempotent", "rui-not-fixpoint", "rui-comment-order",
           "stranded-operator", "spontaneous-break", "break-ignored",
           "timeout", "gen-error"]

STOP = False        # set by SIGINT/SIGTERM; checked between and during chunks

# The lock file's mtime is a heartbeat, touched this often while a sweep runs.
# A lock held by ANOTHER host is never auto-reaped (see SweepLock), so its age
# is the only evidence a reader has about whether the owner is still alive.
LOCK_HEARTBEAT_SECONDS = 30
LOCK_STALE_SECONDS = 300        # older than this, a remote lock is *reported*
                                # as probably dead — still never auto-reaped

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 4 << 20


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
    return defaults, lanes, cfg.get("distributed", {})


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
  app_build TEXT,
  mode TEXT NOT NULL DEFAULT 'local',
  coordinator_host TEXT,
  port INTEGER);

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
  unshrunk INTEGER DEFAULT 0,
  worker_id INTEGER,
  leased_at TEXT,
  last_heartbeat TEXT);

CREATE TABLE IF NOT EXISTS worker (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES session(id),
  host TEXT NOT NULL,
  addr TEXT,
  jobs INTEGER NOT NULL,
  gen_hash TEXT,
  app_build TEXT,
  connected_at TEXT NOT NULL,
  last_seen TEXT,
  disconnected_at TEXT,
  state TEXT NOT NULL DEFAULT 'live');

CREATE TABLE IF NOT EXISTS worker_rate (
  worker_id INTEGER NOT NULL REFERENCES worker(id),
  lane_id INTEGER NOT NULL REFERENCES lane(id),
  seeds_per_sec REAL,
  PRIMARY KEY (worker_id, lane_id));

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


def open_db(path, check_same_thread=True):
    """Open the store.

    **No WAL, deliberately and unconditionally.** The database lives beside the
    artifacts, which for a distributed sweep means it lives on NFS — and WAL
    needs an mmap'd `-shm` file, which SQLite documents as unsupported on
    network filesystems. `journal_mode=TRUNCATE` (not DELETE: the journal is
    reused and truncated instead of created and unlinked, which is fewer NFS
    metadata round-trips for the same guarantee) plus `synchronous=FULL`, which
    the tiny once-per-chunk write rate makes free.

    One code path rather than "detect a network filesystem and switch": the
    local-disk case loses nothing it can measure, and there is no
    environment-dependent behaviour to reason about.

    **The journal mode is read back and asserted.** The old code set WAL and
    never checked, so on an NFS store SQLite may have been quietly refusing the
    switch all along — that is exactly the class of fact that should be known
    rather than hoped for.

    The concurrency this gives up is covered by `SweepLock`: one writer at a
    time, every db-writing command taking the lock.
    """
    db = sqlite3.connect(path, timeout=30, check_same_thread=check_same_thread)
    db.row_factory = sqlite3.Row
    got = db.execute("PRAGMA journal_mode=TRUNCATE").fetchone()[0]
    if str(got).lower() != "truncate":
        raise SystemExit(
            "sqlite refused journal_mode=TRUNCATE on %s (it is %r).\n"
            "  Another connection may hold the db in WAL mode; close it and "
            "retry." % (path, got))
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript(SCHEMA)
    migrate_db(db)
    db.commit()
    return db


def migrate_db(db):
    """Add the distributed-mode columns to a database written before them.

    Additive only: nothing existing changes meaning, so `status` on an old store
    keeps reading. The new tables are handled by CREATE TABLE IF NOT EXISTS."""
    for table, col, decl in (
            ("chunk", "worker_id", "INTEGER"),
            ("chunk", "leased_at", "TEXT"),
            ("chunk", "last_heartbeat", "TEXT"),
            ("session", "mode", "TEXT NOT NULL DEFAULT 'local'"),
            ("session", "coordinator_host", "TEXT"),
            ("session", "port", "INTEGER")):
        have = {r["name"] for r in db.execute("PRAGMA table_info(%s)" % table)}
        if col not in have:
            db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))


class LockInfo:
    def __init__(self, host, pid, stamp, mode, port, age):
        self.host, self.pid, self.stamp = host, pid, stamp
        self.mode, self.port, self.age = mode, port, age

    @property
    def is_local(self):
        return self.host == socket.gethostname()

    def describe(self):
        where = "this host" if self.is_local else "host %s" % self.host
        extra = ""
        if self.mode == "distributed" and self.port:
            extra = ", coordinating on port %d" % self.port
        return ("a %s sweep is running on %s (pid %d, started %s%s; lock "
                "touched %s ago)"
                % (self.mode, where, self.pid, self.stamp, extra,
                   fmt_duration(self.age)))


def lock_path(out_root):
    return os.path.join(out_root, "fuzzrun.lock")


def read_lock(out_root):
    """The live lock, or None. Never reaps anything — callers decide."""
    path = lock_path(out_root)
    try:
        with open(path) as f:
            parts = f.read().split()
        age = max(0.0, time.time() - os.path.getmtime(path))
    except (OSError, ValueError):
        return None
    if len(parts) < 3:
        return None
    host, pid, stamp = parts[0], parts[1], parts[2]
    mode = parts[3] if len(parts) > 3 else "local"
    port = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None
    try:
        pid = int(pid)
    except ValueError:
        return None
    if host == socket.gethostname():
        try:
            os.kill(pid, 0)
        except OSError:
            return None                 # stale: our own dead process
    return LockInfo(host, pid, stamp, mode, port, age)


class SweepLock:
    """Two concurrent sweeps would hand out the same seeds and double-count
    coverage — and with the database on a shared filesystem they would also be
    two SQLite writers over NFS, which is corruption of the record itself
    rather than a confusing split brain.

    **The lock records `hostname pid timestamp mode port`, and the `os.kill`
    liveness test applies only when the hostname matches.** The old lock held a
    bare pid, so a second master on another host asked *its own* kernel about a
    pid belonging to somebody else's, almost always found nothing, concluded
    "stale" and started. The guard that existed to prevent two masters was what
    walked into it, silently.

    A lock held by another host is therefore never auto-reaped. The owner
    heartbeats it (touches the mtime) while running, so a reader can see how
    stale it is, and clearing it takes an explicit `--force-unlock`. A dead
    remote master stays recoverable; a live one stays protected."""

    def __init__(self, out_root, mode="local", port=None, force=False):
        self.path = lock_path(out_root)
        self.mode, self.port, self.force = mode, port, force
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        held = read_lock(os.path.dirname(self.path))
        if held is not None and not self.force:
            hint = ("  Wait for it, or clear the lock with --force-unlock if "
                    "you know it is dead.")
            if not held.is_local and held.age > LOCK_STALE_SECONDS:
                hint = ("  Its heartbeat is %s old, so its owner is probably "
                        "gone — but a lock held by another host is never "
                        "reaped automatically.\n  Clear it with --force-unlock "
                        "once you are sure." % fmt_duration(held.age))
            raise SystemExit("%s\n%s\n  (%s)"
                             % (held.describe(), hint, self.path))
        if held is not None and self.force:
            print("--force-unlock: taking over the lock from %s (pid %d)"
                  % (held.host, held.pid))
        self._write()
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        return self

    def _write(self):
        with open(self.path, "w") as f:
            f.write("%s %d %s %s %s\n"
                    % (socket.gethostname(), os.getpid(), now_iso(),
                       self.mode, self.port if self.port else "-"))

    def _heartbeat(self):
        while not self._stop.wait(LOCK_HEARTBEAT_SECONDS):
            try:
                os.utime(self.path, None)
            except OSError:
                return

    def __exit__(self, *_exc):
        self._stop.set()
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


def refuse_if_remote_sweep(out_root, what):
    """Guard the direct-database commands against being run on a host that is
    not the one holding the sweep.

    Putting the db in the shared directory is sound exactly to the extent that
    one process at a time opens it, so a `status` on a worker box must not
    quietly open the shared file. It fails loudly with the command that does
    work — which is better than reading the db even where reading would be
    safe, since live workers, their `-j`, their rates and the in-flight leases
    are not in the schema and never will be."""
    held = read_lock(out_root)
    if held is None or held.is_local:
        return
    lines = [held.describe()]
    if held.mode == "distributed" and held.port:
        lines.append("  Ask the coordinator instead:  ./fuzzrun.py %s "
                     "--master %s:%d" % (what, held.host, held.port))
    else:
        lines.append("  That host's sweep owns this database; run `%s` there."
                     % what)
    lines.append("  (--force-unlock clears the lock if you are sure it is "
                 "dead.)")
    raise SystemExit("\n".join(lines))


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

def nice_prefix(nice, idle_io):
    pre = []
    nice = int(nice or 0)
    if nice and shutil.which("nice"):
        pre += ["nice", "-n", str(nice)]
    if idle_io and shutil.which("ionice"):
        pre += ["ionice", "-c3"]
    return pre


def gen_cmd(params, jobs, base, count, out_dir, max_shrinks,
            nice=19, idle_io=True):
    """The generator invocation for one chunk.

    Takes the lane's *parameters* rather than a `Lane`, because a distributed
    worker builds this command too and holds no config: everything it needs
    arrives in the assignment frame."""
    cmd = nice_prefix(nice, idle_io) + [
        sys.executable, GEN,
        "-n", str(count), "--base-seed", str(base),
        "-j", str(jobs), "--max-depth", str(params["max_depth"]),
        "--out", out_dir, "--max-shrinks", str(max_shrinks)]
    if params.get("no_comments"):
        cmd.append("--no-comments")
    else:
        cmd += ["--comment-rate", "%g" % params["comment_rate"]]
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
                db.execute("INSERT OR IGNORE INTO failure_seed (failure_id,"
                           " seed, chunk_id) VALUES (?,?,?)",
                           (fid, meta["seed"], chunk_id))
            else:
                fid = row["id"]
                # **Count the hit only if this (failure, seed) pair is new.**
                # A range can legitimately be swept twice — a chunk reissued
                # after a lease expiry and then completed by both workers, or a
                # range re-swept after an abort — and a hit count that grows on
                # the re-sweep would describe the scheduler rather than the
                # bug. The seed table is the record; `hits` follows it.
                cur = db.execute(
                    "INSERT OR IGNORE INTO failure_seed (failure_id, seed,"
                    " chunk_id) VALUES (?,?,?)",
                    (fid, meta["seed"], chunk_id))
                if cur.rowcount:
                    db.execute("UPDATE failure SET hits=hits+1, last_seen=? "
                               "WHERE id=?", (now_iso(), fid))
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


def size_chunk(rate, prev_n, target_seconds, bootstrap):
    """How many seeds to put in the next chunk.

    Shared by both transports. The measured rate is per lane in single-host
    mode and per (worker, lane) in distributed mode — a 4-core laptop and a
    32-core box cannot share an estimate — but the arithmetic is the same, and
    so is the cap: never leap on one fast chunk."""
    if not rate:
        return bootstrap
    n = int(rate * target_seconds)
    if prev_n:
        n = min(n, prev_n * 3)
    return max(10, n)


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


def check_app_is_current(args):
    """A long sweep of a stale binary tests the wrong code."""
    if not os.path.exists(APP):
        raise SystemExit("app not found: %s\n  (cd ../../gren-format && "
                         "./build.sh)" % APP)
    if args.allow_stale_app:
        return
    if newest_source_mtime() > os.path.getmtime(APP):
        raise SystemExit(
            "the built app is older than the formatter sources.\n"
            "  A long sweep of a stale binary tests the wrong code.\n"
            "  Rebuild:  cd ../../gren-format && ./build.sh\n"
            "  Or sweep the old build anyway: --allow-stale-app")


def cmd_run(args):
    with SweepLock(args.out, mode="local", force=args.force_unlock):
        return run_sweep(args)


def run_sweep(args):
    global STOP
    defaults, lanes, _dist = load_config(args.config)
    if args.lane:
        wanted = set(args.lane)
        unknown = wanted - {ln.name for ln in lanes}
        if unknown:
            raise SystemExit("no such lane(s): %s" % ", ".join(sorted(unknown)))
        for ln in lanes:
            ln.enabled = ln.name in wanted
    if not any(ln.enabled for ln in lanes):
        raise SystemExit("no enabled lanes")

    check_app_is_current(args)

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
        " status, app_build, mode, coordinator_host) "
        "VALUES (?,?,?,'running',?,'local',?)",
        (gen["id"], now_iso(), total_seconds, build, socket.gethostname()))
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
        prev = db.execute(
            "SELECT seed_to - seed_from + 1 AS n FROM chunk WHERE lane_id=?"
            " AND status='complete' ORDER BY id DESC LIMIT 1",
            (lane.id,)).fetchone()
        n = size_chunk(lane.rate, prev["n"] if prev else None, target, bootstrap)
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
        cmd = gen_cmd(lane.params, lane.jobs, base, n, chunk_dir, max_shrinks,
                      defaults.get("nice", 19), defaults.get("idle_io", True))
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
    elapsed = total_seconds - max(0, deadline - time.time())
    finish_session(db, args.out, gen, session_id, status, elapsed, totals,
                   lanes, max_shrinks)
    return 1 if totals["new"] else 0


def finish_session(db, out_root, gen, session_id, status, elapsed, totals,
                   lanes, max_shrinks, extra=()):
    """Close the session row, print the summary, and file it under
    `fuzzrun-out/sessions/`. Shared by both transports so the two cannot report
    the same sweep differently."""
    db.execute("UPDATE session SET ended_at=?, status=? WHERE id=?",
               (now_iso(), status, session_id))
    db.commit()
    lines = ["", "session %d %s — %s of sweeping, %d chunks"
             % (session_id, status, fmt_duration(elapsed), totals["chunks"]),
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
    lines.extend(extra)
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
        ahead = db.execute(
            "SELECT COALESCE(SUM(seed_to-seed_from+1),0) s FROM chunk WHERE "
            "lane_id=? AND status='complete' AND seed_from>=?",
            (lane.id, row["cursor"])).fetchone()["s"]
        lines.append("  %-16s %s covered (next seed %s)%s"
                     % (lane.name,
                        human_int(row["cursor"] - lane.base_seed),
                        human_int(row["cursor"]),
                        "  +%s done ahead of cursor" % human_int(ahead)
                        if ahead else ""))
    out = "\n".join(lines)
    print(out)
    sdir = os.path.join(out_root, "sessions")
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "session-%04d.txt" % session_id), "w") as f:
        f.write(out + "\n")


# ───────────────────────────── distributed: protocol ──────────────────────

class FrameError(Exception):
    pass


def send_frame(sock, obj):
    """One length-prefixed JSON frame: a decimal byte count, a newline, then
    exactly that many bytes.

    Not NDJSON. The frames are small — artifacts stay on the shared filesystem
    and only their *path* travels — but a length prefix costs nothing and
    removes every escaping question in advance."""
    data = json.dumps(obj).encode()
    if len(data) > MAX_FRAME_BYTES:
        raise FrameError("frame too large (%d bytes)" % len(data))
    sock.sendall(b"%d\n" % len(data) + data)


def recv_frame(rf):
    """Read one frame from a buffered reader. None at a clean end of stream."""
    header = rf.readline()
    if not header:
        return None
    try:
        n = int(header.strip())
    except ValueError:
        raise FrameError("bad frame header %r" % header[:40])
    if n < 0 or n > MAX_FRAME_BYTES:
        raise FrameError("frame length out of range: %d" % n)
    body = rf.read(n)
    if body is None or len(body) != n:
        raise FrameError("truncated frame (%d of %d bytes)"
                         % (0 if body is None else len(body), n))
    try:
        return json.loads(body)
    except ValueError as e:
        raise FrameError("bad frame payload: %s" % e)


def token_file(out_root):
    return os.path.join(out_root, "fuzzrun.token")


def load_token(out_root, create=False, override=None):
    """The shared secret in the `hello` frame.

    It is a LAN and this is not TLS, but a port that accepts "here are my
    results" should not be anonymous. The token lives in the shared directory
    beside the database, so a worker started from that directory needs no
    option at all — and a worker started from a *local clone* gets a different
    one, which is the mistake the handshake exists to catch."""
    if override:
        return override
    env = os.environ.get("FUZZRUN_TOKEN")
    if env:
        return env.strip()
    path = token_file(out_root)
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    if not create:
        raise SystemExit(
            "no token at %s\n"
            "  The coordinator writes it when it starts. Point --out at the "
            "shared\n  directory, or pass --token / $FUZZRUN_TOKEN." % path)
    tok = secrets.token_hex(16)
    os.makedirs(out_root, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    return tok


def parse_master(spec):
    if ":" not in spec:
        raise SystemExit("--master wants host:port (got %r)" % spec)
    host, _, port = spec.rpartition(":")
    if not port.isdigit():
        raise SystemExit("--master wants host:port (got %r)" % spec)
    return host, int(port)


# ───────────────────────────── distributed: ranges ────────────────────────
#
# A lane's coverage is the contiguous prefix [base_seed, cursor). With one
# worker that is free — one chunk at a time, so the cursor is a true high-water
# mark. With four workers it is not: worker B can finish [200,300) while A is
# still grinding [100,200), and advancing to 300 would record 100 seeds nobody
# swept as covered, with nothing anywhere reporting it.
#
# So the cursor advances only to the LOW-WATER MARK, and these three functions
# are the whole of the arithmetic that keeps it honest.

def merge_ranges(ranges):
    """Inclusive (from, to) pairs, merged and sorted."""
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def subtract_ranges(span, covered):
    """`span` (one inclusive pair) minus `covered` (inclusive pairs)."""
    a, b = span
    if b < a:
        return []
    out, pos = [], a
    for c, d in merge_ranges(covered):
        if d < pos:
            continue
        if c > b:
            break
        if c > pos:
            out.append((pos, min(b, c - 1)))
        pos = max(pos, d + 1)
        if pos > b:
            break
    if pos <= b:
        out.append((pos, b))
    return out


class LaneState:
    """The scheduler's live view of one lane.

    `cursor` is coverage and is written back to the db. `alloc_next` is where
    the next *unseen* range starts — it runs ahead of the cursor while chunks
    are in flight, and is not coverage. `pending` holds ranges below
    `alloc_next` that nobody has completed: gaps left by a reissued or aborted
    chunk, or by a master that died. They are handed out before new ground, so
    a hole is never left behind for a later session to discover."""

    def __init__(self, lane):
        self.lane = lane
        self.cursor = lane.cursor
        self.alloc_next = lane.cursor
        self.pending = []
        self.done_ahead = []

    def absorb(self):
        """Pull the cursor up over every completed range that now touches it."""
        moved = True
        while moved:
            moved, keep = False, []
            for a, b in self.done_ahead:
                if b < self.cursor:
                    continue                       # already inside the prefix
                if a <= self.cursor:
                    self.cursor, moved = b + 1, True
                else:
                    keep.append((a, b))
            self.done_ahead = keep

    def complete(self, a, b):
        self.done_ahead = merge_ranges(self.done_ahead + [(a, b)])
        self.pending = [r for span in self.pending
                        for r in subtract_ranges(span, [(a, b)])]
        self.absorb()

    def requeue(self, a, b):
        if b >= self.cursor:
            self.pending = merge_ranges(self.pending + [(max(a, self.cursor),
                                                        b)])

    def take(self, n):
        """The next range to assign: a pending gap first, then new ground."""
        while self.pending:
            a, b = self.pending[0]
            size = min(n, b - a + 1)
            if size == b - a + 1:
                self.pending.pop(0)
            else:
                self.pending[0] = (a + size, b)
            return a, size
        a = self.alloc_next
        self.alloc_next = a + n
        return a, n

    def ahead(self):
        return sum(b - a + 1 for a, b in self.done_ahead)


# ───────────────────────────── distributed: coordinator ───────────────────

class WorkerState:
    def __init__(self, wid, host, jobs, addr):
        self.id, self.host, self.jobs, self.addr = wid, host, jobs, addr
        self.rates = {}            # lane name -> seeds/sec, measured HERE
        self.last_n = {}           # lane name -> last chunk size
        self.timeouts = {}         # lane name -> consecutive timeouts
        self.disabled_lanes = set()
        self.inflight = set()
        self.chunks = 0
        self.seeds = 0
        self.connected_at = time.time()
        self.label = "%s#%d" % (host, wid)


class Coordinator:
    """The master. It coordinates and does no sweeping of its own.

    The one machine that has to stay up for twelve hours is not the one to load
    with `node` processes, and a coordinator competing with its own workers for
    CPU makes every rate estimate it keeps a lie.

    It is also the only process that may write the database, which is what
    makes putting that database on the shared filesystem sound. Workers are
    stateless: an assignment carries the lane's parameters and the scheduling
    niceties, so a worker reads no config, opens no database and holds no
    cursor."""

    def __init__(self, args, defaults, lanes, dist, db, gen, session_id,
                 token, total_seconds):
        self.args, self.db, self.gen = args, db, gen
        self.session_id, self.token = session_id, token
        self.lanes = lanes
        self.lane_state = {ln.name: LaneState(ln) for ln in lanes}
        self.defaults = defaults
        self.chunk_seconds = int(float(defaults.get("chunk_minutes", 10)) * 60)
        self.bootstrap = int(defaults.get("bootstrap_seeds", 40))
        self.max_shrinks = int(defaults.get("max_shrinks_per_chunk", 20))
        self.nice = defaults.get("nice", 19)
        self.idle_io = defaults.get("idle_io", True)
        self.lease_seconds = float(dist.get("lease_seconds", 120))
        self.drain_grace = float(dist.get("drain_grace_minutes", 35)) * 60
        self.build = app_build_id()
        self.gen_hash = sha1_file(GEN)
        self.lock = threading.RLock()
        self.workers = {}
        self.inflight = {}
        self.chunks_run = {}
        self.disabled = set()
        self.draining = False
        self.drain_reason = None
        self.drain_deadline = None
        self.total_seconds = total_seconds
        self.started = time.time()
        self.deadline = self.started + total_seconds
        self.floor = min(60, max(10, self.chunk_seconds // 2))
        self.totals = {"seeds": 0, "finds": 0, "new": 0, "quarantine": 0,
                       "unshrunk": 0, "chunks": 0}
        self.next_worker_seq = 0
        for ln in lanes:
            self.chunks_run[ln.name] = 0
            self._recover(self.lane_state[ln.name])

    # ---- coverage bookkeeping ------------------------------------------

    def _recover(self, ls):
        """Rebuild the cursor / allocation state from the database.

        A master that died mid-sweep leaves `leased` chunks behind and can
        leave completed chunks sitting above the cursor. Both are recovered
        here rather than papered over: the completed ones are absorbed, and
        everything else below the allocation high-water mark goes back on the
        pending queue to be swept again."""
        rows = self.db.execute(
            "SELECT seed_from, seed_to, status FROM chunk WHERE lane_id=?",
            (ls.lane.id,)).fetchall()
        if not rows:
            return
        alloc, complete = ls.cursor, []
        for r in rows:
            alloc = max(alloc, r["seed_to"] + 1)
            if r["status"] == "complete" and r["seed_to"] >= ls.cursor:
                complete.append((r["seed_from"], r["seed_to"]))
        ls.alloc_next = alloc
        ls.done_ahead = merge_ranges(complete)
        ls.absorb()
        ls.pending = subtract_ranges((ls.cursor, ls.alloc_next - 1),
                                     ls.done_ahead)
        if ls.pending:
            print("  %-16s %s seed(s) left uncovered by an earlier session — "
                  "re-sweeping them first"
                  % (ls.lane.name,
                     human_int(sum(b - a + 1 for a, b in ls.pending))))
        self._persist_cursor(ls)

    def _persist_cursor(self, ls):
        if ls.cursor != ls.lane.cursor:
            ls.lane.cursor = ls.cursor
        self.db.execute("UPDATE lane SET cursor=? WHERE id=?",
                        (ls.cursor, ls.lane.id))

    # ---- handshake ------------------------------------------------------

    def token_ok(self, msg):
        return hmac.compare_digest(str(msg.get("token", "")), self.token)

    def register(self, msg, addr):
        """Admit a worker, or refuse it and say which hash differed.

        With one shared directory "the worker has the same code" is true by
        construction, so this is a cheap assertion rather than provisioning —
        but it still catches a worker started from a local clone, and NFS
        attribute caching serving a stale `gen-random.py` or `app` for up to a
        minute after a rebuild on another host. Either one poisons the whole
        session's coverage claim: a different generator means the same seeds
        mean different modules, and a different build means it tested other
        code."""
        host = str(msg.get("host") or addr[0])
        jobs = int(msg.get("jobs") or 1)
        theirs_gen = str(msg.get("gen_hash", ""))
        theirs_app = str(msg.get("app_build", ""))
        if int(msg.get("protocol", 0)) != PROTOCOL_VERSION:
            return None, ("protocol version %s, coordinator speaks %d — the "
                          "worker is running a different fuzzrun.py"
                          % (msg.get("protocol"), PROTOCOL_VERSION))
        if theirs_gen != self.gen_hash:
            return None, ("gen-random.py differs (worker %s, coordinator %s). "
                          "Its seeds would generate different modules, so its "
                          "results could not be recorded against them. Check "
                          "the worker is running from the shared directory; "
                          "if you just rebuilt, wait out the NFS attribute "
                          "cache and retry."
                          % (theirs_gen[:12] or "?", self.gen_hash[:12]))
        if theirs_app != self.build:
            return None, ("app build differs (worker %s, coordinator %s). It "
                          "would be testing different code. Check the worker "
                          "is running from the shared directory; if you just "
                          "rebuilt, wait out the NFS attribute cache and retry."
                          % (theirs_app[:12] or "?", self.build))
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO worker (session_id, host, addr, jobs, gen_hash,"
                " app_build, connected_at, last_seen, state)"
                " VALUES (?,?,?,?,?,?,?,?,'live')",
                (self.session_id, host, "%s:%d" % addr, jobs, theirs_gen,
                 theirs_app, now_iso(), now_iso()))
            wid = cur.lastrowid
            w = WorkerState(wid, host, jobs, addr)
            # Seed the rate estimates from this host's last visit, so a worker
            # that reconnects does not re-bootstrap from 40 seeds.
            for r in self.db.execute(
                    "SELECT l.name, wr.seeds_per_sec FROM worker_rate wr"
                    " JOIN worker wk ON wk.id=wr.worker_id"
                    " JOIN lane l ON l.id=wr.lane_id"
                    " WHERE wk.host=? AND wk.jobs=? AND wk.id<>?"
                    " ORDER BY wk.id DESC", (host, jobs, wid)).fetchall():
                w.rates.setdefault(r["name"], r["seeds_per_sec"])
            self.workers[wid] = w
            self.db.commit()
        print("[%s] worker %s joined (-j %d, from %s:%d)%s"
              % (datetime.now().strftime("%H:%M:%S"), w.label, jobs,
                 addr[0], addr[1],
                 "" if not w.rates else "  known rates: " + ", ".join(
                     "%s %.1f/s" % (k, v) for k, v in sorted(w.rates.items())
                     if v)), flush=True)
        return w, None

    def detach(self, worker, why):
        with self.lock:
            self.db.execute("UPDATE worker SET state='gone', disconnected_at=?"
                            " WHERE id=?", (now_iso(), worker.id))
            self.workers.pop(worker.id, None)
            for cid in list(worker.inflight):
                self._reissue(cid, why)
            self.db.commit()
        print("[%s] worker %s left (%s)"
              % (datetime.now().strftime("%H:%M:%S"), worker.label, why),
              flush=True)

    # ---- scheduling -----------------------------------------------------

    def _make_assignment(self, worker, remaining):
        lane = pick_lane(self.lanes, self.chunks_run,
                         self.disabled | worker.disabled_lanes)
        if lane is None:
            return None
        ls = self.lane_state[lane.name]
        target = min(self.chunk_seconds, max(30, int(remaining)))
        n = size_chunk(worker.rates.get(lane.name), worker.last_n.get(lane.name),
                       target, self.bootstrap)
        base, n = ls.take(n)
        cur = self.db.execute(
            "INSERT INTO chunk (session_id, lane_id, seed_from, seed_to,"
            " started_at, app_build, status, worker_id, leased_at,"
            " last_heartbeat) VALUES (?,?,?,?,?,?,'leased',?,?,?)",
            (self.session_id, lane.id, base, base + n - 1, now_iso(),
             self.build, worker.id, now_iso(), now_iso()))
        chunk_id = cur.lastrowid
        self.db.commit()
        # Per-chunk scratch under the SHARED store. The worker points
        # `gen-random --out` here and reports the path; the master then calls
        # ingest_chunk on it in place, unchanged. That is what a shared
        # filesystem buys — the distributed mode adds a way to *schedule*
        # chunks, not a second way to *record* them.
        out_dir = os.path.join(self.args.out, "tmp",
                               "chunk-%d-%s" % (chunk_id, worker.label))
        self.chunks_run[lane.name] = self.chunks_run.get(lane.name, 0) + 1
        worker.inflight.add(chunk_id)
        self.inflight[chunk_id] = {
            "lane": lane.name, "worker": worker.id, "from": base,
            "to": base + n - 1, "n": n, "started": time.time(),
            "heartbeat": time.time(), "out_dir": out_dir}
        print("[%s] %-16s seeds %s..%s (n=%s) -> %s  %s left"
              % (datetime.now().strftime("%H:%M:%S"), lane.name,
                 human_int(base), human_int(base + n - 1), human_int(n),
                 worker.label,
                 fmt_duration(max(0, self.deadline - time.time()))),
              flush=True)
        return {"chunk_id": chunk_id, "lane": lane.name,
                "params": lane.params, "seed_from": base, "count": n,
                "out_dir": out_dir, "nice": self.nice,
                "idle_io": bool(self.idle_io),
                "max_shrinks": self.max_shrinks,
                "hard_timeout": max(180, target * 3)}

    def handle_want(self, worker, msg):
        slots = max(1, min(int(msg.get("slots", 1)), 4))
        with self.lock:
            if self.draining or STOP:
                return {"type": "drain", "reason": self.drain_reason or "stop"}
            remaining = self.deadline - time.time()
            if remaining < self.floor:
                self._begin_drain("deadline")
                return {"type": "drain", "reason": "deadline"}
            out = []
            for _ in range(slots):
                a = self._make_assignment(worker, remaining)
                if a is None:
                    break
                out.append(a)
            if not out:
                return {"type": "wait", "seconds": 5}
            return {"type": "assign", "chunks": out}

    def handle_heartbeat(self, worker, msg):
        cid = int(msg.get("chunk_id", -1))
        with self.lock:
            info = self.inflight.get(cid)
            if info is not None and info["worker"] == worker.id:
                info["heartbeat"] = time.time()
                self.db.execute("UPDATE chunk SET last_heartbeat=? WHERE id=?",
                                (now_iso(), cid))
                self.db.commit()
            self.db.execute("UPDATE worker SET last_seen=? WHERE id=?",
                            (now_iso(), worker.id))
            return {"type": "ack", "drain": self.draining,
                    "known": info is not None}

    def handle_done(self, worker, msg):
        """Record a finished chunk. Idempotent on chunk id.

        A `done` retried after a network blip must not double-count, and a
        chunk reissued after a lease expiry may genuinely be completed by two
        workers — so a chunk already recorded `complete` is acknowledged and
        dropped, and the per-`(failure, seed)` recording in `ingest_chunk`
        keeps the hit counts exact either way."""
        cid = int(msg.get("chunk_id", -1))
        with self.lock:
            row = self.db.execute("SELECT * FROM chunk WHERE id=?",
                                  (cid,)).fetchone()
            if row is None:
                return self._ack("unknown chunk")
            if row["status"] == "complete":
                self._drop_inflight(worker, cid)
                return self._ack("already recorded")
            lane = self._lane_by_id(row["lane_id"])
            if lane is None:
                # A lane retired mid-session (a config change under a running
                # sweep). Record the chunk, count nothing: its parameters are
                # no longer the ones the lane claims.
                self._drop_inflight(worker, cid)
                self.db.execute("UPDATE chunk SET status='aborted:lane-gone',"
                                " ended_at=? WHERE id=?", (now_iso(), cid))
                self.db.commit()
                return self._ack("lane no longer active")
            ls = self.lane_state[lane.name]
            secs = float(msg.get("elapsed", 0.0) or 0.0)
            outcome = str(msg.get("outcome", "complete"))
            rc = msg.get("rc")
            n = row["seed_to"] - row["seed_from"] + 1
            info = self.inflight.get(cid) or {}
            out_dir = msg.get("out_dir") or info.get("out_dir")
            self._drop_inflight(worker, cid)

            if outcome != "complete" or rc not in (0, 1):
                why = outcome if outcome != "complete" else "exit %s" % rc
                self.db.execute(
                    "UPDATE chunk SET status=?, ended_at=?, seconds=? WHERE "
                    "id=?", ("aborted:%s" % why, now_iso(), secs, cid))
                if row["status"] != "reissued":
                    ls.requeue(row["seed_from"], row["seed_to"])
                self.db.commit()
                print("       chunk %d aborted on %s (%s) after %s — range "
                      "will be re-swept"
                      % (cid, worker.label, why, fmt_duration(secs)),
                      flush=True)
                if outcome == "timeout":
                    # Scoped to this worker: one pathological host is not a
                    # pathological lane.
                    worker.timeouts[lane.name] = \
                        worker.timeouts.get(lane.name, 0) + 1
                    prev = worker.rates.get(lane.name) or (n / max(secs, 1))
                    worker.rates[lane.name] = prev / 2
                    if worker.timeouts[lane.name] >= 2:
                        worker.disabled_lanes.add(lane.name)
                        print("       lane %r timed out twice on %s — that "
                              "worker stops taking it this session (seeds "
                              "%s.. look pathological; try ./gen-random.py "
                              "--seed %s)"
                              % (lane.name, worker.label,
                                 human_int(row["seed_from"]), row["seed_from"]),
                              flush=True)
                if out_dir:
                    shutil.rmtree(out_dir, ignore_errors=True)
                return self._ack("aborted")

            run_dir = msg.get("run_dir") or os.path.join(out_dir or "",
                                                         "run-000000")
            finds, newf, unshrunk, quar = ingest_chunk(
                self.db, self.gen, lane, cid, run_dir, self.args.out)
            clean = n - finds - quar
            self.db.execute(
                "UPDATE chunk SET status='complete', ended_at=?, seconds=?,"
                " clean=?, finds=?, quarantine=?, unshrunk=? WHERE id=?",
                (now_iso(), secs, clean, finds, quar, unshrunk, cid))
            worker.timeouts[lane.name] = 0
            rate = n / max(secs, 0.001)
            prev = worker.rates.get(lane.name)
            worker.rates[lane.name] = (rate if prev is None
                                       else 0.6 * rate + 0.4 * prev)
            worker.last_n[lane.name] = n
            self.db.execute(
                "INSERT INTO worker_rate (worker_id, lane_id, seeds_per_sec)"
                " VALUES (?,?,?) ON CONFLICT(worker_id, lane_id) DO UPDATE"
                " SET seeds_per_sec=excluded.seeds_per_sec",
                (worker.id, lane.id, worker.rates[lane.name]))
            # The lane-level rate stays a whole-fleet average, so a single-host
            # `run` after a distributed session still starts from something
            # measured.
            lrate = self.db.execute("SELECT seeds_per_sec FROM lane WHERE "
                                    "id=?", (lane.id,)).fetchone()[0]
            lrate = rate if lrate is None else 0.6 * rate + 0.4 * lrate
            self.db.execute("UPDATE lane SET seeds_per_sec=? WHERE id=?",
                            (lrate, lane.id))
            ls.complete(row["seed_from"], row["seed_to"])
            self._persist_cursor(ls)
            self.db.commit()
            if out_dir:
                shutil.rmtree(out_dir, ignore_errors=True)
            worker.chunks += 1
            worker.seeds += n
            for k, v in (("seeds", n), ("finds", finds), ("new", newf),
                         ("quarantine", quar), ("unshrunk", unshrunk),
                         ("chunks", 1)):
                self.totals[k] += v
            note = "%s clean" % human_int(clean)
            if finds:
                note += ", %d find(s), %d new" % (finds, newf)
            if unshrunk:
                note += ", %d unshrunk" % unshrunk
            if quar:
                note += ", %d QUARANTINE" % quar
            ahead = ls.ahead()
            print("       %s in %s (%.1f seeds/s) from %s%s"
                  % (note, fmt_duration(secs), n / max(secs, 0.001),
                     worker.label,
                     "  [%s done ahead of cursor]" % human_int(ahead)
                     if ahead else ""), flush=True)
            return self._ack("recorded")

    def _sweep_scratch(self):
        """Remove this session's leftover per-chunk scratch directories.

        An ingested chunk deletes its own; what is left belongs to a chunk that
        was reissued or was still running when the drain grace expired. A
        SIGKILLed worker's `gen-random.py` outlives it (it runs in its own
        session, deliberately), so one of these may still be being written to —
        which is harmless, since nothing will ever read it."""
        root = os.path.join(self.args.out, "tmp")
        mine = {r["id"] for r in self.db.execute(
            "SELECT id FROM chunk WHERE session_id=?", (self.session_id,))}
        removed = 0
        try:
            names = os.listdir(root)
        except OSError:
            return 0
        for name in names:
            if not name.startswith("chunk-"):
                continue
            bits = name.split("-")
            if len(bits) < 3 or not bits[1].isdigit():
                continue
            if int(bits[1]) in mine:
                path = os.path.join(root, name)
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
        return removed

    def _ack(self, note):
        """Every acknowledgement carries the drain flag.

        Without it a worker that has just reported its final chunk would ask
        for more work in the same moment the master decides it is finished, and
        get an EOF instead of an answer — a clean end of session that looks
        like a lost coordinator."""
        return {"type": "ack", "note": note, "drain": self.draining}

    def _lane_by_id(self, lane_id):
        for ln in self.lanes:
            if ln.id == lane_id:
                return ln
        return None

    def _drop_inflight(self, worker, cid):
        self.inflight.pop(cid, None)
        worker.inflight.discard(cid)

    def _reissue(self, cid, why):
        """Take a chunk back off a worker. Its range is NOT counted."""
        info = self.inflight.pop(cid, None)
        row = self.db.execute("SELECT * FROM chunk WHERE id=?",
                              (cid,)).fetchone()
        if row is None or row["status"] == "complete":
            return
        self.db.execute("UPDATE chunk SET status='reissued', ended_at=? "
                        "WHERE id=?", (now_iso(), cid))
        lane = self._lane_by_id(row["lane_id"])
        if lane is not None:
            self.lane_state[lane.name].requeue(row["seed_from"],
                                               row["seed_to"])
        w = self.workers.get(info["worker"]) if info else None
        if w is not None:
            w.inflight.discard(cid)
        print("[%s] chunk %d (%s %s..%s) reissued: %s"
              % (datetime.now().strftime("%H:%M:%S"), cid,
                 lane.name if lane else "?", human_int(row["seed_from"]),
                 human_int(row["seed_to"]), why), flush=True)

    def expire_leases(self):
        now = time.time()
        with self.lock:
            for cid, info in list(self.inflight.items()):
                if now - info["heartbeat"] > self.lease_seconds:
                    self._reissue(cid, "no heartbeat for %s"
                                  % fmt_duration(now - info["heartbeat"]))
            self.db.commit()

    def _begin_drain(self, reason):
        if self.draining:
            return
        self.draining = True
        self.drain_reason = reason
        self.drain_deadline = time.time() + self.drain_grace
        print("\n[%s] draining (%s) — no new chunks; waiting up to %s for the "
              "%d in flight"
              % (datetime.now().strftime("%H:%M:%S"), reason,
                 fmt_duration(self.drain_grace), len(self.inflight)),
              flush=True)

    # ---- reports --------------------------------------------------------

    def live_text(self):
        with self.lock:
            lines = ["", "coordinator on %s:%d — session %d, %s of %s elapsed%s"
                     % (socket.gethostname(), self.port, self.session_id,
                        fmt_duration(time.time() - self.started),
                        fmt_duration(self.total_seconds),
                        "  DRAINING (%s)" % self.drain_reason
                        if self.draining else "")]
            if not self.workers:
                lines.append("  no workers attached")
            for w in sorted(self.workers.values(), key=lambda x: x.id):
                rates = ", ".join("%s %.1f/s" % (k, v)
                                  for k, v in sorted(w.rates.items()) if v)
                lines.append("  %-20s -j %-3d %s chunks, %s seeds%s"
                             % (w.label, w.jobs, human_int(w.chunks),
                                human_int(w.seeds),
                                "  (%s)" % rates if rates else ""))
                if w.disabled_lanes:
                    lines.append("      not taking: %s"
                                 % ", ".join(sorted(w.disabled_lanes)))
            if self.inflight:
                lines.append("  in flight:")
                for cid, i in sorted(self.inflight.items()):
                    wk = self.workers.get(i["worker"])
                    lines.append("    #%-5d %-16s %s..%s  %s on %s"
                                 % (cid, i["lane"], human_int(i["from"]),
                                    human_int(i["to"]),
                                    fmt_duration(time.time() - i["started"]),
                                    wk.label if wk else "?"))
            for ln in self.lanes:
                ls = self.lane_state[ln.name]
                if ls.pending:
                    lines.append("  %-16s %s seed(s) queued for re-sweep"
                                 % (ln.name,
                                    human_int(sum(b - a + 1
                                                  for a, b in ls.pending))))
            return "\n".join(lines)

    def remote_report(self, msg):
        kind = msg.get("type")
        with self.lock:
            if kind == "failures":
                return failures_text(self.db, msg.get("state", "open"),
                                     msg.get("bucket"),
                                     bool(msg.get("all_generations")),
                                     bool(msg.get("verbose")),
                                     int(msg.get("lines", 30)))
            return status_text(self.db, self.args.out) + "\n" + self.live_text()

    # ---- connection handling --------------------------------------------

    def serve_connection(self, sock, addr):
        sock.settimeout(300)
        rf = sock.makefile("rb")
        worker = None
        why = "connection closed"
        try:
            first = recv_frame(rf)
            if first is None:
                return
            if not self.token_ok(first):
                send_frame(sock, {"type": "refuse",
                                  "reason": "bad or missing token"})
                return
            kind = first.get("type")
            if kind in ("status", "failures"):
                send_frame(sock, {"type": "text",
                                  "text": self.remote_report(first)})
                return
            if kind != "hello":
                send_frame(sock, {"type": "refuse",
                                  "reason": "expected hello, got %r" % kind})
                return
            worker, reason = self.register(first, addr)
            if worker is None:
                send_frame(sock, {"type": "refuse", "reason": reason})
                print("[%s] REFUSED a worker from %s:%d — %s"
                      % (datetime.now().strftime("%H:%M:%S"), addr[0], addr[1],
                         reason), flush=True)
                return
            send_frame(sock, {"type": "welcome", "worker_id": worker.id,
                              "session": self.session_id,
                              "lease_seconds": self.lease_seconds,
                              "generation": self.gen["id"]})
            while True:
                msg = recv_frame(rf)
                if msg is None:
                    break
                t = msg.get("type")
                if t == "want":
                    send_frame(sock, self.handle_want(worker, msg))
                elif t == "heartbeat":
                    send_frame(sock, self.handle_heartbeat(worker, msg))
                elif t == "done":
                    send_frame(sock, self.handle_done(worker, msg))
                elif t == "bye":
                    send_frame(sock, {"type": "bye"})
                    why = "said goodbye"
                    break
                else:
                    send_frame(sock, {"type": "refuse",
                                      "reason": "unknown frame %r" % t})
                    why = "protocol error"
                    break
        except (FrameError, OSError, ValueError) as e:
            why = "%s: %s" % (type(e).__name__, e)
        finally:
            try:
                rf.close()
            except OSError:
                pass
            if worker is not None:
                self.detach(worker, why)

    # ---- the session ----------------------------------------------------

    def serve(self, bind, port):
        self.port = port
        srv = _CoordServer((bind, port), _CoordHandler)
        srv.coord = self
        ip, bound_port = srv.server_address[0], srv.server_address[1]
        self.port = bound_port
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        print("fuzzrun coordinate: generation %d (%s), app %s"
              % (self.gen["id"], self.gen["gen_hash"][:12], self.build))
        print("         listening on %s:%d (%s)" % (ip, bound_port,
                                                    socket.gethostname()))
        if _is_loopback(ip):
            print("         !! that is a LOOPBACK address — workers on other "
                  "hosts cannot reach it.\n"
                  "            Set [distributed] bind = \"<this host's LAN "
                  "address>\" in fuzzrun.toml,\n"
                  "            or pass --bind. (Common on Debian/Ubuntu, where "
                  "/etc/hosts maps the\n"
                  "            hostname to 127.0.1.1.)")
        print("         %s budget, ~%s chunks, lanes: %s"
              % (fmt_duration(self.total_seconds),
                 fmt_duration(self.chunk_seconds),
                 ", ".join("%s(w%g)" % (ln.name, ln.weight)
                           for ln in self.lanes if ln.enabled)))
        print("         ends at %s"
              % datetime.fromtimestamp(self.deadline).strftime("%H:%M:%S"))
        print("         start workers:  ./fuzzrun.py worker --master %s:%d "
              "-j 12" % (socket.gethostname(), bound_port))
        print("         watch it:       ./fuzzrun.py status --master %s:%d\n"
              % (socket.gethostname(), bound_port), flush=True)

        last_report = time.time()
        while True:
            time.sleep(1)
            self.expire_leases()
            if STOP and not self.draining:
                self._begin_drain("interrupted")
            if not self.draining and time.time() > self.deadline - self.floor:
                self._begin_drain("deadline")
            if self.draining:
                with self.lock:
                    idle = not self.inflight
                if idle:
                    break
                if time.time() > self.drain_deadline:
                    with self.lock:
                        stuck = list(self.inflight)
                        for cid in stuck:
                            self._reissue(cid, "drain grace expired")
                        self.db.commit()
                    print("       %d chunk(s) were still running when the "
                          "drain grace expired — their ranges stay uncovered "
                          "and are re-swept next session" % len(stuck))
                    break
            if time.time() - last_report >= 300:
                last_report = time.time()
                print(self.live_text(), flush=True)

        # Let the workers hang up on their own. They have all been told to
        # drain by now, so this is a second or two, and it is the difference
        # between a worker exiting 0 and a worker reporting a lost coordinator.
        goodbye = time.time() + 15
        while self.workers and time.time() < goodbye:
            time.sleep(0.5)
        srv.shutdown()
        status = "interrupted" if STOP else "done"
        extra = []
        left = self._sweep_scratch()
        if left:
            extra.append("  scratch:       %d leftover chunk director(ies) "
                         "removed (a reissued or drained chunk leaves one)"
                         % left)
        with self.lock:
            hosts = self.db.execute(
                "SELECT host, jobs, COUNT(*) c FROM worker WHERE session_id=? "
                "GROUP BY host, jobs", (self.session_id,)).fetchall()
        if hosts:
            extra.append("  workers:       " + ", ".join(
                "%s (-j %d)%s" % (h["host"], h["jobs"],
                                  " ×%d" % h["c"] if h["c"] > 1 else "")
                for h in hosts))
        finish_session(self.db, self.args.out, self.gen, self.session_id,
                       status, time.time() - self.started, self.totals,
                       self.lanes, self.max_shrinks, extra)
        return 1 if self.totals["new"] else 0


class _CoordServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _CoordHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.coord.serve_connection(self.request, self.client_address)


def _is_loopback(ip):
    try:
        import ipaddress
        return ipaddress.ip_address(ip).is_loopback
    except (ImportError, ValueError):
        return ip.startswith("127.")


def cmd_coordinate(args):
    defaults, lanes, dist = load_config(args.config)
    if args.lane:
        wanted = set(args.lane)
        unknown = wanted - {ln.name for ln in lanes}
        if unknown:
            raise SystemExit("no such lane(s): %s" % ", ".join(sorted(unknown)))
        for ln in lanes:
            ln.enabled = ln.name in wanted
    if not any(ln.enabled for ln in lanes):
        raise SystemExit("no enabled lanes")
    check_app_is_current(args)
    total_seconds = parse_duration(args.for_)
    port = args.port if args.port is not None else int(dist.get("port", 9999))
    bind = args.bind or dist.get("bind") or socket.gethostname()

    global STOP
    STOP = False

    with SweepLock(args.out, mode="distributed", port=port,
                   force=args.force_unlock):
        token = load_token(args.out, create=True, override=args.token)
        # check_same_thread=False: connection handlers run on their own
        # threads, and every one of them takes self.lock before touching it.
        # The master is still the only PROCESS that writes the store.
        db = open_db(args.db, check_same_thread=False)
        gen, fresh = ensure_generation(db, args.yes)
        lanes = sync_lanes(db, gen, lanes, args.yes)
        os.makedirs(os.path.join(args.out, "failures"), exist_ok=True)
        os.makedirs(os.path.join(args.out, "tmp"), exist_ok=True)
        scur = db.execute(
            "INSERT INTO session (generation_id, started_at, requested_seconds,"
            " status, app_build, mode, coordinator_host, port)"
            " VALUES (?,?,?,'running',?,'distributed',?,?)",
            (gen["id"], now_iso(), total_seconds, app_build_id(),
             socket.gethostname(), port))
        session_id = scur.lastrowid
        db.commit()

        def on_signal(_signum, _frame):
            global STOP
            if STOP:
                return
            STOP = True
            print("\n[stopping — draining workers; in-flight ranges that do "
                  "not land are re-swept next session]", flush=True)

        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)

        if fresh:
            print("new generation — all lanes start at base_seed")
        coord = Coordinator(args, defaults, lanes, dist, db, gen, session_id,
                            token, total_seconds)
        try:
            return coord.serve(bind, port)
        except OSError as e:
            raise SystemExit("could not listen on %s:%d — %s" % (bind, port, e))


# ───────────────────────────── distributed: worker ────────────────────────

class ChunkRun:
    """One assignment, executed in a background thread.

    The worker's socket I/O all happens on its main loop, so the only thing
    crossing threads is this object's `done` flag."""

    def __init__(self, assign, jobs, log_dir):
        self.a = assign
        self.jobs = jobs
        self.log_path = os.path.join(log_dir, "chunk-%d.log" % assign["chunk_id"])
        self.done = False
        self.rc = None
        self.secs = 0.0
        self.outcome = "complete"
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        cmd = gen_cmd(self.a["params"], self.jobs, self.a["seed_from"],
                      self.a["count"], self.a["out_dir"], self.a["max_shrinks"],
                      self.a.get("nice", 19), self.a.get("idle_io", True))
        try:
            self.rc, self.secs, self.outcome = run_child(
                cmd, float(self.a.get("hard_timeout", 1800)), self.log_path)
        except Exception as e:                      # noqa: BLE001
            self.rc, self.outcome = -1, "error:%s" % type(e).__name__
            print("  chunk %d could not be run: %s" % (self.a["chunk_id"], e),
                  file=sys.stderr)
        finally:
            self.done = True

    def done_frame(self):
        return {"type": "done", "chunk_id": self.a["chunk_id"],
                "run_dir": os.path.join(self.a["out_dir"], "run-000000"),
                "out_dir": self.a["out_dir"], "elapsed": self.secs,
                "rc": self.rc, "outcome": self.outcome,
                "count": self.a["count"]}


def cmd_worker(args):
    """Pull work from a coordinator and run it. Holds no state of its own.

    Pull, not push: a worker can join late, leave whenever, and sit behind
    whatever the network does, and the master never needs to reach back."""
    global STOP
    STOP = False
    host, port = parse_master(args.master)
    token = load_token(args.out, create=False, override=args.token)
    if not os.path.exists(APP):
        raise SystemExit("app not found: %s\n  (cd ../../gren-format && "
                         "./build.sh)" % APP)
    if not os.path.exists(GEN):
        raise SystemExit("gen-random.py not found: %s" % GEN)
    log_dir = os.path.join(args.out, "tmp")
    os.makedirs(log_dir, exist_ok=True)

    def on_signal(_signum, _frame):
        global STOP
        if STOP:
            return
        STOP = True
        print("\n[stopping — telling the coordinator, and shutting the running "
              "chunk down]", flush=True)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # Wait for the coordinator rather than failing on a race. Starting four
    # workers by hand means starting them in some order, and "the master is
    # still coming up" should not cost a host its session. The wait is printed
    # rather than silent, so a typo in --master still looks like a typo.
    deadline = time.time() + max(0, args.wait)
    said = False
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=30)
            break
        except OSError as e:
            if time.time() >= deadline or STOP:
                raise SystemExit("cannot reach coordinator %s:%d — %s"
                                 % (host, port, e))
            if not said:
                said = True
                print("waiting for the coordinator at %s:%d (up to %s) …"
                      % (host, port, fmt_duration(args.wait)), flush=True)
            time.sleep(2)
    sock.settimeout(120)
    rf = sock.makefile("rb")
    me = socket.gethostname()
    send_frame(sock, {"type": "hello", "token": token, "host": me,
                      "jobs": args.jobs, "gen_hash": sha1_file(GEN),
                      "app_build": app_build_id(),
                      "protocol": PROTOCOL_VERSION})
    resp = recv_frame(rf)
    if resp is None:
        raise SystemExit("coordinator closed the connection during handshake")
    if resp.get("type") == "refuse":
        raise SystemExit("coordinator refused this worker:\n  %s"
                         % resp.get("reason", "?"))
    if resp.get("type") != "welcome":
        raise SystemExit("unexpected handshake reply: %r" % resp)
    print("worker %s: attached to %s:%d as #%d (session %d, generation %d), "
          "-j %d" % (me, host, port, resp.get("worker_id", -1),
                     resp.get("session", -1), resp.get("generation", -1),
                     args.jobs), flush=True)

    running = {}
    draining = False
    slots = max(1, args.slots)
    rc = 0
    try:
        while True:
            for cid, run in list(running.items()):
                if not run.done:
                    continue
                send_frame(sock, run.done_frame())
                ack = recv_frame(rf)
                if ack is None:
                    raise FrameError("coordinator closed while reporting "
                                     "chunk %d" % cid)
                print("  chunk %d %s in %s (%s)"
                      % (cid, run.outcome, fmt_duration(run.secs),
                         ack.get("note", "ack")), flush=True)
                if ack.get("drain") and not draining:
                    draining = True
                if run.outcome == "complete" and run.rc in (0, 1):
                    try:
                        os.remove(run.log_path)   # nothing to read; the master
                    except OSError:               # has the artifacts
                        pass
                del running[cid]

            if (STOP or draining) and not running:
                send_frame(sock, {"type": "bye"})
                recv_frame(rf)
                break

            if not STOP and not draining and len(running) < slots:
                send_frame(sock, {"type": "want",
                                  "slots": slots - len(running)})
                reply = recv_frame(rf)
                if reply is None:
                    raise FrameError("coordinator closed the connection")
                kind = reply.get("type")
                if kind == "assign":
                    for a in reply.get("chunks", []):
                        print("  chunk %d: %s seeds %s..%s (%s)"
                              % (a["chunk_id"], a["lane"],
                                 human_int(a["seed_from"]),
                                 human_int(a["seed_from"] + a["count"] - 1),
                                 human_int(a["count"])), flush=True)
                        running[a["chunk_id"]] = ChunkRun(a, args.jobs, log_dir)
                elif kind == "drain":
                    draining = True
                    print("  coordinator is draining (%s) — finishing what I "
                          "hold" % reply.get("reason", "?"), flush=True)
                elif kind == "refuse":
                    raise SystemExit("coordinator refused a request: %s"
                                     % reply.get("reason"))

            for cid, run in list(running.items()):
                if run.done:
                    continue
                # A heartbeat says "this chunk is still mine", nothing more:
                # gen-random reports no progress, and a seeds-done field that
                # always read 0 would be a number that lies.
                send_frame(sock, {"type": "heartbeat", "chunk_id": cid})
                ack = recv_frame(rf)
                if ack is None:
                    raise FrameError("coordinator closed during heartbeat")
                if ack.get("drain") and not draining:
                    draining = True
                    print("  coordinator is draining — will exit once chunk "
                          "%d lands" % cid, flush=True)
            time.sleep(5 if (running or draining or STOP) else 3)
    except (FrameError, OSError) as e:
        print("worker: lost the coordinator (%s)" % e, file=sys.stderr)
        rc = 1
        STOP = True
        for run in running.values():
            run.thread.join(timeout=60)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    print("worker %s: done" % me)
    return rc


def remote_text(args, kind, payload):
    """Ask a running coordinator, rather than opening the shared database.

    Better than reading the db even where reading it would be safe: live
    workers, their declared `-j`, their measured rates and the in-flight leases
    are not in the schema and never will be."""
    host, port = parse_master(args.master)
    token = load_token(args.out, create=False, override=args.token)
    try:
        with socket.create_connection((host, port), timeout=30) as s:
            rf = s.makefile("rb")
            send_frame(s, dict(payload, type=kind, token=token))
            resp = recv_frame(rf)
    except OSError as e:
        raise SystemExit("cannot reach coordinator %s:%d — %s" % (host, port, e))
    if resp is None:
        raise SystemExit("coordinator closed the connection without replying")
    if resp.get("type") == "refuse":
        raise SystemExit("coordinator refused: %s" % resp.get("reason"))
    print(resp.get("text", ""))
    return 0


# ───────────────────────────── status / failures ──────────────────────────

def status_text(db, out_root):
    """The `status` report as a string.

    A string rather than a pile of `print`s because the coordinator serves the
    same report over its port — `status --master hostA:9999` has to be the same
    answer as `status`, not a second implementation of it."""
    out = []
    def p(s=""):
        out.append(s)
    gen = current_generation(db)
    if gen is None:
        return "no sweeps recorded yet — ./fuzzrun.py run --for 30m"
    live = sha1_file(GEN)
    p("generation %d  %s  (started %s)"
      % (gen["id"], gen["gen_hash"][:12], gen["created_at"]))
    if live != gen["gen_hash"]:
        p("  !! gen-random.py is now %s — the next run starts a new "
          "generation" % live[:12])
    p("  app build %s%s" % (app_build_id(),
                            "" if os.path.exists(APP) else "  (MISSING)"))
    p()
    lanes = db.execute(
        "SELECT * FROM lane WHERE generation_id=? AND retired=0 ORDER BY id",
        (gen["id"],)).fetchall()
    p("%-16s %12s %12s %10s  %s"
      % ("lane", "covered", "next seed", "seeds/s", "settings"))
    for ln in lanes:
        pj = json.loads(ln["params_json"])
        desc = "depth %d, %s" % (
            pj["max_depth"],
            "no-comments" if pj["no_comments"]
            else "comments %.2f" % pj["comment_rate"])
        # Seeds completed but sitting ahead of the cursor — a distributed
        # sweep's laggard chunk holds them back. NOT coverage, and never to be
        # added to it; reported for the same reason aborted chunks are, so the
        # re-sweeping is visible rather than mysterious.
        ahead = db.execute(
            "SELECT COALESCE(SUM(seed_to-seed_from+1),0) s FROM chunk "
            "WHERE lane_id=? AND status='complete' AND seed_from>=?",
            (ln["id"], ln["cursor"])).fetchone()["s"]
        p("%-16s %12s %12s %10s  %s%s"
          % (ln["name"], human_int(ln["cursor"] - ln["base_seed"]),
             human_int(ln["cursor"]),
             "%.1f" % ln["seeds_per_sec"] if ln["seeds_per_sec"] else "-",
             desc,
             "   +%s done ahead of cursor" % human_int(ahead) if ahead else ""))
    tot = db.execute(
        "SELECT COALESCE(SUM(seed_to-seed_from+1),0) s, COALESCE(SUM(seconds),0)"
        " t, COUNT(*) c FROM chunk WHERE status='complete' AND lane_id IN "
        "(SELECT id FROM lane WHERE generation_id=?)", (gen["id"],)).fetchone()
    p("\n%s seeds over %s of CPU-wall in %d chunks"
      % (human_int(tot["s"]), fmt_duration(tot["t"]), tot["c"]))
    ab = db.execute(
        "SELECT COUNT(*) c FROM chunk WHERE status LIKE 'aborted%' AND lane_id "
        "IN (SELECT id FROM lane WHERE generation_id=?)", (gen["id"],)).fetchone()
    if ab["c"]:
        p("%d aborted chunk(s) — their ranges were not counted as covered"
          % ab["c"])
    re_n = db.execute(
        "SELECT COUNT(*) c FROM chunk WHERE status='reissued' AND lane_id "
        "IN (SELECT id FROM lane WHERE generation_id=?)", (gen["id"],)).fetchone()
    if re_n["c"]:
        p("%d reissued chunk(s) — a worker's lease expired and the range went "
          "back on the queue" % re_n["c"])
    q = db.execute(
        "SELECT COALESCE(SUM(quarantine),0) q FROM chunk WHERE lane_id IN "
        "(SELECT id FROM lane WHERE generation_id=?)", (gen["id"],)).fetchone()
    if q["q"]:
        # This is a generator problem, not a formatter find — but it undermines
        # every other verdict in the generation, so it belongs in `status`.
        p("QUARANTINE: %s module(s) the generator emitted did not parse — "
          "its\n  crash/idempotency verdicts are only trustworthy while "
          "this is 0.\n  Samples: %s"
          % (human_int(q["q"]),
             os.path.join(out_root, "quarantine", gen["gen_hash"][:12])))
    rows = db.execute(
        "SELECT state, bucket, COUNT(*) c, SUM(hits) h FROM failure WHERE "
        "generation_id=? GROUP BY state, bucket ORDER BY state, c DESC",
        (gen["id"],)).fetchall()
    if rows:
        p("\nfailures (this generation):")
        for r in rows:
            p("  %-14s %-16s %d distinct, %d hits"
              % (r["state"], r["bucket"], r["c"], r["h"]))
    else:
        p("\nno failures recorded in this generation")
    stale = db.execute(
        "SELECT COUNT(*) c FROM failure WHERE state='stale-grammar'").fetchone()
    if stale["c"]:
        p("  (%d failure(s) from earlier generations are stale-grammar)"
          % stale["c"])
    s = db.execute("SELECT * FROM session ORDER BY id DESC LIMIT 3").fetchall()
    if s:
        p("\nrecent sessions:")
        for r in s:
            mode = r["mode"] or "local"
            where = ("  [%s on %s]" % (mode, r["coordinator_host"])
                     if mode == "distributed" else "")
            p("  #%d %s  %s -> %s  (%s requested)%s"
              % (r["id"], r["status"], r["started_at"],
                 r["ended_at"] or "…", fmt_duration(r["requested_seconds"]),
                 where))
    return "\n".join(out)


def failures_text(db, state="open", bucket=None, all_generations=False,
                  verbose=False, lines=30):
    out = []
    gen = current_generation(db)
    if gen is None:
        return "nothing recorded yet"
    q = "SELECT f.*, l.name lane FROM failure f JOIN lane l ON l.id=f.lane_id"
    where, params = [], []
    if not all_generations:
        where.append("f.generation_id=?")
        params.append(gen["id"])
    if state != "all":
        where.append("f.state=?")
        params.append(state)
    if bucket:
        where.append("f.bucket=?")
        params.append(bucket)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY f.state, f.bucket, f.hits DESC"
    rows = db.execute(q, params).fetchall()
    if not rows:
        what = "failures" if state == "all" else "%s failures" % state
        if bucket:
            what = "%s %s" % (state if state != "all" else "",
                              bucket) + " failures"
        scope = ("in any generation" if all_generations
                 else "in generation %d" % gen["id"])
        return "no %s %s" % (what.strip(), scope)
    for r in rows:
        out.append("#%-4d %-15s %-16s seed %-10d hits %-5d %s"
                   % (r["id"], r["bucket"], r["lane"], r["first_seed"],
                      r["hits"], r["state"]))
        out.append("      %s" % (r["message"] or "").strip()[:110])
        out.append("      repro: %s" % r["repro"])
        if not r["shrunk"]:
            out.append("      NOT MINIMIZED (shrink cap) — input.min.gren is "
                       "the full module")
        out.append("      %s" % r["artifact_dir"])
        if verbose:
            rp = os.path.join(HERE, r["artifact_dir"] or "", "report.txt")
            if os.path.isfile(rp):
                with open(rp) as f:
                    body = f.read().splitlines()
                for line in body[:lines]:
                    out.append("      | %s" % line)
                if len(body) > lines:
                    out.append("      | … %d more lines in report.txt"
                               % (len(body) - lines))
        out.append("")
    out.append("%d failure(s)" % len(rows))
    return "\n".join(out)


def cmd_status(args):
    if args.master:
        return remote_text(args, "status", {})
    refuse_if_remote_sweep(args.out, "status")
    print(status_text(open_db(args.db), args.out))
    return 0


def cmd_failures(args):
    if args.master:
        return remote_text(args, "failures",
                           {"state": args.state, "bucket": args.bucket,
                            "all_generations": args.all_generations,
                            "verbose": args.verbose, "lines": args.lines})
    refuse_if_remote_sweep(args.out, "failures")
    print(failures_text(open_db(args.db), args.state, args.bucket,
                        args.all_generations, args.verbose, args.lines))
    return 0


# Which artifact files a bucket's `--full` export carries beyond the two every
# failure gets (`input.min.gren`, `report.txt`). `report.txt` already holds the
# precomputed diff for the buckets that have one, so these are the raw sides of
# it — bulk that is worth having only when the diff is not enough.
EXPORT_EXTRAS = {
    "non-idempotent": ["formatted.gren", "formatted2.gren"],
    "sort-order": ["permuted.gren", "formatted.gren", "permuted.formatted.gren"],
    "comment-loss": ["formatted.gren"],
    "predicate-lie": ["formatted.gren"],
    "rui-not-fixpoint": ["rui.gren", "rui2.gren"],
    "rui-crash": ["rui.gren"],
    "rui-ast-mismatch": ["rui.gren"],
    "rui-non-idempotent": ["rui.gren"],
    "rui-comment-order": ["rui.gren"],
    "stranded-operator": ["formatted.gren"],
    "spontaneous-break": ["formatted.gren"],
    "break-ignored": ["unbroken.gren", "formatted.gren"],
}


def check_hint(bucket):
    """The command that re-tests THIS bucket against the current build.

    Bucket-aware on purpose, because the obvious answer is wrong for two of
    them. `--show` exits 0 on a **comment-loss** find — a dropped comment is
    AST-equivalent and its output is its own fixed point, which is exactly why
    `gen-random.py` needs a comment-multiset oracle at all — so a bundle that
    said "run --show" would read as "already fixed". A **sort-order** find is
    about a PAIR of inputs and cannot be re-tested from one file at all."""
    if bucket == "predicate-lie":
        return "node ../../gren-format/app --audit-predicates input.min.gren"
    if bucket.startswith("rui-"):
        # Plain --show exits 0 on every one of these: the ordinary path passed
        # before this oracle ran at all. The flag is the whole finding.
        return ("node ../../gren-format/app --remove-unused-imports --show "
                "input.min.gren")
    if bucket == "comment-loss":
        return ("--show exits 0 on this class. Compare the comment multisets:\n"
                "#          node ../../gren-format/app --pre-context input.min.gren\n"
                "#          node ../../gren-format/app --show input.min.gren > f.gren\n"
                "#          node ../../gren-format/app --pre-context f.gren\n"
                "#          (report.txt already lists what went missing)")
    if bucket == "sort-order":
        return ("needs BOTH inputs — format input.min.gren and permuted.gren "
                "and diff\n#          (export --full carries the permuted twin; "
                "report.txt has the diff)")
    if bucket in ("stranded-operator", "spontaneous-break"):
        # The whole point of the layout oracles is that these are STABLE:
        # `--show` exits 0 on every one of them, so the obvious hint would read
        # as "already fixed" the way it would for comment-loss.
        return ("--show exits 0 on this class — the layout is wrong but stable.\n"
                "#          node ../../gren-format/app --show input.min.gren "
                "> f.gren\n"
                "#          then look at the row / declaration report.txt names")
    if bucket == "break-ignored":
        return ("needs BOTH inputs — input.min.gren writes an author break and "
                "unbroken.gren\n#          does not; they format to the same "
                "bytes, which is the finding\n"
                "#          (export --full carries unbroken.gren)")
    return "node ../../gren-format/app --show input.min.gren"


def _export_file(out, path, name, max_lines):
    """One file section: a header naming exactly how many lines follow, then
    those lines verbatim.

    **The count is what makes the bundle parseable, not the delimiter.** `--`
    opens a comment in Gren, so a `.gren` payload can legally contain a line
    starting with `-----` and a reader that scanned for the next delimiter
    would cut the file short. A reader takes the `shown=` number and reads
    exactly that many lines."""
    if not os.path.isfile(path):
        out.append("----- %s  [MISSING from the artifact directory]" % name)
        return
    with open(path, errors="replace") as f:
        body = f.read().splitlines()
    shown = body[:max_lines]
    out.append("----- %s  shown=%d of %d lines%s"
               % (name, len(shown), len(body),
                  "  TRUNCATED (raise --lines)" if len(body) > len(shown)
                  else ""))
    out.extend(shown)


def cmd_export(args):
    """Bundle failures into one self-contained text blob to hand to somebody
    who cannot see this machine's `fuzzrun-out/`.

    **The unit that travels is the `.gren` file, not the seed.** A seed
    reproduces only against the same `gen-random.py` AND the same lane
    parameters (`write_report` spells out why a bare `--seed` usually replays
    clean), so it is useless to a reader on another checkout. `input.min.gren`
    is just a Gren file: it needs no generator, no seed and no generation match
    to reproduce. The repro command is still printed, for a reader who does have
    this generation.

    Plain text rather than a tarball so the bundle can be pasted into a chat or
    an issue, and so a reader can reconstruct the sources from it by hand."""
    refuse_if_remote_sweep(args.out, "export")
    db = open_db(args.db)
    gen = current_generation(db)
    if gen is None:
        print("nothing recorded yet")
        return 0
    q = "SELECT f.*, l.name lane FROM failure f JOIN lane l ON l.id=f.lane_id"
    where, params = [], []
    if args.id:
        where.append("f.id=?")
        params.append(args.id)
    else:
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
    q += " ORDER BY f.bucket, f.hits DESC"
    rows = db.execute(q, params).fetchall()
    if not rows:
        print("no matching failures", file=sys.stderr)
        return 0

    out = [
        "fuzzrun failure export",
        "  exported     %s from %s" % (now_iso(), socket.gethostname()),
        "  generation   %d  %s" % (gen["id"], gen["gen_hash"][:12]),
        "  app build    %s" % app_build_id(),
        "  failures     %d (%s)" % (len(rows),
                                    "id %d" % args.id if args.id
                                    else "state=%s" % args.state),
        "",
        "# Each failure below carries its own minimized Gren source. To look at",
        "# one: save the `input.min.gren` section to a file and run the `check`",
        "# command against it. No generator, seed or generation match needed --",
        "# the .gren file IS the reproducer.",
        "#",
        "# Parsing this: a `----- <name>  shown=N of M lines` header is followed",
        "# by EXACTLY N lines of that file, verbatim. Read the count; do not scan",
        "# for the next delimiter -- `--` opens a comment in Gren, so a payload",
        "# can legally contain a line starting with `-----`.",
        "",
    ]
    truncated = 0
    for r in rows:
        adir = os.path.join(HERE, r["artifact_dir"] or "")
        out.append("=" * 72)
        out.append("===== FAILURE %d  %s  lane=%s  hits=%d  state=%s"
                   % (r["id"], r["bucket"], r["lane"], r["hits"], r["state"]))
        out.append("      first seed  %d" % r["first_seed"])
        out.append("      message     %s" % (r["message"] or "").strip())
        out.append("      repro       %s" % r["repro"])
        out.append("      check       %s" % check_hint(r["bucket"]))
        out.append("      app build   %s" % (r["first_app_build"] or "?"))
        if not r["shrunk"]:
            out.append("      NOT MINIMIZED (shrink cap was hit) --"
                       " input.min.gren is the full module")
        seeds = db.execute("SELECT seed FROM failure_seed WHERE failure_id=?"
                           " ORDER BY seed LIMIT 8", (r["id"],)).fetchall()
        if seeds:
            out.append("      seeds       %s%s"
                       % (", ".join(str(s["seed"]) for s in seeds),
                          " …" if r["hits"] > len(seeds) else ""))
        out.append("")
        names = ["input.min.gren", "report.txt"]
        if args.full:
            # The UNMINIMIZED module is not redundant: one seed can carry two
            # bugs and the shrinker keeps only the one it was minimizing
            # towards, so a fix verified against input.min.gren alone can leave
            # the second live (2026-08-09, fuzzrun seed 10035748).
            names.insert(1, "input.gren")
            names += EXPORT_EXTRAS.get(r["bucket"], [])
        before = len(out)
        for name in names:
            _export_file(out, os.path.join(adir, name), name, args.lines)
            out.append("")
        truncated += sum(1 for ln in out[before:] if ln.startswith("----- …"))
    out.append("=" * 72)
    out.append("end of export -- %d failure(s)%s"
               % (len(rows),
                  ", %d file section(s) truncated" % truncated if truncated
                  else ""))
    text = "\n".join(out) + "\n"
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
        print("wrote %s (%d failure(s), %s)"
              % (args.output, len(rows), human_int(len(text)) + " bytes"),
              file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_resweep(args):
    # `resweep` writes (UPDATE failure SET state='fixed'), so it takes the lock
    # like every other writer. Locally that used to be merely sloppy; with the
    # database on a shared filesystem, a resweep fired off during a twelve-hour
    # sweep is precisely the two-writer case the lock exists to prevent.
    with SweepLock(args.out, mode="resweep", force=args.force_unlock):
        return resweep(args)


def resweep(args):
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
    with SweepLock(args.out, mode="reset", force=args.force_unlock):
        return reset(args)


def reset(args):
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
    r.add_argument("--force-unlock", action="store_true",
                   help="take over a lock held by a sweep you know is dead")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("coordinate",
                       help="run the master of a multi-host sweep (does no "
                            "sweeping of its own)")
    c.add_argument("--for", dest="for_", default="1h", metavar="DURATION",
                   help="2h, 90m, 1h30m (a bare number means minutes)")
    c.add_argument("--port", type=int,
                   help="listen here (default: [distributed] port, 9999)")
    c.add_argument("--bind",
                   help="listen on this address (default: this host's name — "
                        "deliberately not 0.0.0.0)")
    c.add_argument("--token", help="shared secret (default: the one in "
                                   "fuzzrun-out/fuzzrun.token, created if "
                                   "absent)")
    c.add_argument("--lane", action="append",
                   help="restrict to this lane (repeatable)")
    c.add_argument("-y", "--yes", action="store_true",
                   help="answer generation/lane reset prompts with yes")
    c.add_argument("--allow-stale-app", action="store_true",
                   help="sweep even though the app is older than the sources")
    c.add_argument("--force-unlock", action="store_true",
                   help="take over a lock held by a sweep you know is dead")
    c.set_defaults(func=cmd_coordinate)

    k = sub.add_parser("worker",
                       help="pull work from a coordinator and run it")
    k.add_argument("--master", required=True, metavar="HOST:PORT")
    k.add_argument("-j", "--jobs", type=int, default=6,
                   help="heavy jobs this host may run (gen-random -j)")
    k.add_argument("--slots", type=int, default=1,
                   help="chunks to hold at once (default 1: -j already sets "
                        "this host's parallelism)")
    k.add_argument("--token", help="shared secret, if not readable from the "
                                   "shared directory")
    k.add_argument("--wait", type=int, default=60, metavar="SECONDS",
                   help="how long to wait for the coordinator to come up "
                        "(default 60)")
    k.set_defaults(func=cmd_worker)

    s = sub.add_parser("status", help="cursors, coverage, failure counts")
    s.add_argument("--master", metavar="HOST:PORT",
                   help="ask a running coordinator instead of opening the db")
    s.add_argument("--token")
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
    f.add_argument("--master", metavar="HOST:PORT",
                   help="ask a running coordinator instead of opening the db")
    f.add_argument("--token")
    f.set_defaults(func=cmd_failures)

    e = sub.add_parser("export",
                       help="bundle failures (with their .gren sources) as "
                            "one text blob to send elsewhere")
    e.add_argument("--id", type=int, help="export just this failure id")
    e.add_argument("--state", default="open",
                   choices=["open", "fixed", "stale-grammar", "all"])
    e.add_argument("--bucket", choices=BUCKETS)
    e.add_argument("--all-generations", action="store_true")
    e.add_argument("--full", action="store_true",
                   help="also include the unminimized input.gren and the "
                        "bucket's raw output files")
    e.add_argument("--lines", type=int, default=200,
                   help="max lines per file section (default 200); "
                        "truncation is always marked")
    e.add_argument("-o", "--output", help="write here instead of stdout")
    e.set_defaults(func=cmd_export)

    w = sub.add_parser("resweep",
                       help="re-test open failures against the current build")
    w.add_argument("--id", type=int, help="just this failure id")
    w.add_argument("-j", "--jobs", type=int, default=6)
    w.add_argument("--force-unlock", action="store_true",
                   help="take over a lock held by a sweep you know is dead")
    w.set_defaults(func=cmd_resweep)

    t = sub.add_parser("reset", help="restart a lane, or start a generation")
    t.add_argument("--lane", help="reset just this lane's cursor")
    t.add_argument("-y", "--yes", action="store_true")
    t.add_argument("--force-unlock", action="store_true",
                   help="take over a lock held by a sweep you know is dead")
    t.set_defaults(func=cmd_reset)

    i = sub.add_parser("init", help="write a starter fuzzrun.toml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
