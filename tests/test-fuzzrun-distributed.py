#!/usr/bin/env python3
"""Tests for `fuzzrun.py`'s distributed mode.

Everything here is a guard that is silent when it works, so each one is proved
by making it fire. The five checks are the testing plan from
`docs/distributedFuzzing.md`:

1. **Range arithmetic** — the low-water mark, in isolation. When it is wrong it
   is wrong *silently*, which is the whole reason a lane's coverage is a prefix
   and not a set.
2. **Loopback** — a coordinator and two workers on 127.0.0.1 exercise the whole
   protocol (handshake, assign, heartbeat, drain, bye) before a second machine
   is involved. Asserts the cursor is a true prefix: every seed below it was
   swept by some chunk.
3. **A worker killed mid-chunk** — the cursor must not move past its range
   while a faster worker races ahead, and the range must be re-swept.
4. **A worker with a different `gen-random.py`** — refused at the handshake,
   naming the hash. Run from a *fake local clone*, which is the mistake the
   check exists to catch.
5. **A second coordinator against one store** — refused by the hostname-aware
   lock; and a direct `status` on a non-coordinator host refused with a pointer
   to `--master`.

This is not part of `run-tests.sh`: it binds sockets, spawns real sweeps and
takes a couple of minutes. Run it by hand after touching the transport.

    ./test-fuzzrun-distributed.py           # all of it
    ./test-fuzzrun-distributed.py -k lease  # one test by substring
"""

import argparse
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
FUZZRUN = os.path.join(HERE, "fuzzrun.py")
GEN = os.path.join(HERE, "gen-random.py")
APP = os.path.join(HERE, "..", "..", "gren-format", "app")


def load_fuzzrun():
    spec = importlib.util.spec_from_file_location("fuzzrun_mod", FUZZRUN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fr = load_fuzzrun()

CONFIG = """\
[defaults]
jobs = 4
nice = 19
idle_io = true
chunk_minutes = %(chunk_minutes)s
bootstrap_seeds = %(bootstrap)d
max_shrinks_per_chunk = 5

[distributed]
port = %(port)d
bind = "127.0.0.1"
lease_seconds = %(lease)d
drain_grace_minutes = 2

[lanes.tiny]
max_depth = 4
comment_rate = 0.3
base_seed = %(base)d
weight = 1
"""


class Fail(Exception):
    pass


def check(cond, what):
    if not cond:
        raise Fail(what)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Store:
    """A scratch shared directory: config, db and artifacts, as on NFS."""

    def __init__(self, base=10**6, bootstrap=40, chunk_minutes=1, lease=30):
        self.dir = tempfile.mkdtemp(prefix="fuzzrun-test-")
        self.db = os.path.join(self.dir, "fuzzrun.db")
        self.out = os.path.join(self.dir, "out")
        self.config = os.path.join(self.dir, "fuzzrun.toml")
        self.port = free_port()
        with open(self.config, "w") as f:
            f.write(CONFIG % {"port": self.port, "base": base,
                              "bootstrap": bootstrap, "lease": lease,
                              "chunk_minutes": chunk_minutes})

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def globals(self):
        return ["--db", self.db, "--out", self.out, "--config", self.config]

    def sql(self, q, params=()):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            return db.execute(q, params).fetchall()
        finally:
            db.close()

    def chunks(self):
        try:
            return self.sql("SELECT * FROM chunk ORDER BY id")
        except sqlite3.OperationalError:
            return []

    def cursor(self):
        rows = self.sql("SELECT cursor, base_seed FROM lane WHERE retired=0")
        return rows[0]["cursor"] if rows else None


def spawn(store, *argv, out=None):
    log = open(out, "wb") if out else subprocess.DEVNULL
    return subprocess.Popen([sys.executable, FUZZRUN] + store.globals()
                            + list(argv),
                            stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)


def run(store, *argv, timeout=120):
    p = subprocess.run([sys.executable, FUZZRUN] + store.globals() + list(argv),
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def wait_for_port(port, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
            return
        except OSError:
            time.sleep(0.3)
    raise Fail("coordinator never listened on port %d" % port)


def wait_for(predicate, seconds, what):
    end = time.time() + seconds
    while time.time() < end:
        got = predicate()
        if got:
            return got
        time.sleep(0.4)
    raise Fail("timed out after %ds waiting for %s" % (seconds, what))


def kill_tree(proc, marker=None):
    """Kill a worker, and any gen-random it orphaned.

    A worker's child runs in its own session (so Ctrl-C reaches the runner
    only), which means SIGKILLing the worker leaves the generator running. A
    real sweep just re-issues the range and the orphan writes to a scratch
    directory nobody reads; a test should not leave one burning CPU."""
    try:
        proc.kill()
        proc.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if marker:
        subprocess.run(["pkill", "-9", "-f", marker], capture_output=True)


def tail(path, n=25):
    try:
        with open(path, errors="replace") as f:
            return "\n    | ".join([""] + f.read().splitlines()[-n:])
    except OSError:
        return " (no log)"


def contiguous_coverage(store):
    """Every seed below the cursor was actually swept by a completed chunk."""
    rows = [r for r in store.chunks() if r["status"] == "complete"]
    lane = store.sql("SELECT cursor, base_seed FROM lane WHERE retired=0")[0]
    covered = fr.merge_ranges([(r["seed_from"], r["seed_to"]) for r in rows])
    holes = fr.subtract_ranges((lane["base_seed"], lane["cursor"] - 1), covered)
    return holes


# ───────────────────────────── the tests ──────────────────────────────────

def test_range_arithmetic():
    """The low-water mark, with no sockets involved."""
    check(fr.merge_ranges([(1, 5), (6, 9), (20, 22)]) == [(1, 9), (20, 22)],
          "merge_ranges should join adjacent ranges")
    check(fr.subtract_ranges((1, 10), [(3, 4), (8, 20)]) == [(1, 2), (5, 7)],
          "subtract_ranges")
    check(fr.subtract_ranges((1, 10), []) == [(1, 10)], "subtract nothing")
    check(fr.subtract_ranges((5, 4), [(1, 9)]) == [], "empty span")

    class L:                                   # a Lane stand-in
        name, id, base_seed, cursor = "l", 1, 100, 100

    ls = fr.LaneState(L())
    a1, n1 = ls.take(100)                      # worker A: [100,200)
    a2, n2 = ls.take(100)                      # worker B: [200,300)
    check((a1, n1, a2, n2) == (100, 100, 200, 100), "sequential allocation")

    ls.complete(a2, a2 + n2 - 1)               # B finishes FIRST
    check(ls.cursor == 100,
          "the cursor must NOT move while an older chunk is in flight "
          "(got %d)" % ls.cursor)
    check(ls.ahead() == 100,
          "B's 100 seeds are done but ahead of the cursor (got %d)"
          % ls.ahead())

    ls.complete(a1, a1 + n1 - 1)               # A lands: both absorb at once
    check(ls.cursor == 300, "cursor absorbs both (got %d)" % ls.cursor)
    check(ls.ahead() == 0, "nothing left ahead")

    # A dies instead: its range goes back on the queue and is handed out again
    # before any new ground.
    ls2 = fr.LaneState(L())
    a1, n1 = ls2.take(100)
    a2, n2 = ls2.take(100)
    ls2.complete(a2, a2 + n2 - 1)
    ls2.requeue(a1, a1 + n1 - 1)
    a3, n3 = ls2.take(100)
    check((a3, n3) == (a1, n1), "a reissued range is handed out again first")
    check(ls2.cursor == 100, "still no coverage until it lands")
    ls2.complete(a3, a3 + n3 - 1)
    check(ls2.cursor == 300, "and then the whole prefix absorbs")

    # Completing the same range twice (a reissued chunk finished by two
    # workers) must be idempotent, not double-count.
    ls2.complete(a3, a3 + n3 - 1)
    check(ls2.cursor == 300, "duplicate completion is idempotent")


def test_loopback():
    """Two workers, one coordinator, a whole session end to end."""
    st = Store(base=2 * 10**6, bootstrap=12, chunk_minutes=1)
    master_log = os.path.join(st.dir, "master.log")
    try:
        m = spawn(st, "coordinate", "--for", "80s", "-y", out=master_log)
        wait_for_port(st.port)
        w1 = spawn(st, "worker", "--master", "127.0.0.1:%d" % st.port, "-j", "4",
                   out=os.path.join(st.dir, "w1.log"))
        w2 = spawn(st, "worker", "--master", "127.0.0.1:%d" % st.port, "-j", "2",
                   out=os.path.join(st.dir, "w2.log"))
        rc = m.wait(timeout=240)
        check(rc == 0, "coordinator exited %d (see %s)" % (rc, master_log))
        for name, w in (("w1", w1), ("w2", w2)):
            # A clean end of session must look clean from the worker's side
            # too: a worker that asks for work in the moment the master decides
            # it is finished should be told to drain, not handed an EOF.
            wrc = w.wait(timeout=60)
            check(wrc == 0, "worker %s exited %d — see %s"
                  % (name, wrc, os.path.join(st.dir, "%s.log" % name)))

        done = [r for r in st.chunks() if r["status"] == "complete"]
        check(len(done) >= 3, "expected several completed chunks, got %d"
              % len(done))
        workers = st.sql("SELECT * FROM worker")
        check(len(workers) == 2, "both workers should be registered, got %d"
              % len(workers))
        check(all(w["state"] == "gone" for w in workers),
              "workers should be marked gone at the end")
        check(len({r["worker_id"] for r in done}) == 2,
              "both workers should have completed chunks")

        holes = contiguous_coverage(st)
        check(not holes, "coverage below the cursor has holes: %s" % (holes,))
        swept = sum(r["seed_to"] - r["seed_from"] + 1 for r in done)
        lane = st.sql("SELECT * FROM lane WHERE retired=0")[0]
        check(lane["cursor"] - lane["base_seed"] == swept,
              "cursor says %d covered, chunks swept %d — with everything "
              "landed these must agree"
              % (lane["cursor"] - lane["base_seed"], swept))

        rates = st.sql("SELECT * FROM worker_rate")
        check(len(rates) == 2, "a rate per (worker, lane), got %d" % len(rates))
        check(len({r["seeds_per_sec"] for r in rates}) == 2,
              "a -j 4 host and a -j 2 host must not share a rate estimate")
    finally:
        for p in (m, w1, w2):
            kill_tree(p, marker=st.dir)
        st.cleanup()


def test_lease_and_low_water():
    """Kill a worker mid-chunk. The cursor must not move past its range, and
    the range must come back."""
    st = Store(base=3 * 10**6, bootstrap=60, chunk_minutes=1, lease=20)
    master_log = os.path.join(st.dir, "master.log")
    slow = fast = None
    try:
        # The budget has to outlast the whole test: the coordinator stops
        # issuing half a chunk before its deadline, so a session that expires
        # mid-test would look exactly like a range that never came back.
        m = spawn(st, "coordinate", "--for", "8m", "-y", out=master_log)
        wait_for_port(st.port)
        # -j 1 against -j 8: the slow worker holds one chunk while the fast one
        # runs several past it.
        slow = spawn(st, "worker", "--master", "127.0.0.1:%d" % st.port,
                     "-j", "1", out=os.path.join(st.dir, "slow.log"))
        held = wait_for(
            lambda: next((r for r in st.chunks() if r["status"] == "leased"),
                         None),
            60, "the slow worker to take a chunk")
        fast = spawn(st, "worker", "--master", "127.0.0.1:%d" % st.port,
                     "-j", "8", out=os.path.join(st.dir, "fast.log"))
        ahead = wait_for(
            lambda: [r for r in st.chunks()
                     if r["status"] == "complete"
                     and r["seed_from"] > held["seed_from"]],
            90, "the fast worker to finish a chunk past the slow one's")

        # THE assertion. Chunks are done above it, and the cursor has not moved.
        cur = st.cursor()
        check(cur <= held["seed_from"],
              "cursor advanced to %d past an in-flight chunk starting at %d — "
              "%d seeds would be recorded as covered that nobody swept"
              % (cur, held["seed_from"], cur - held["seed_from"]))
        check(sum(r["seed_to"] - r["seed_from"] + 1 for r in ahead) > 0,
              "expected completed-but-not-counted seeds")

        # Kill the worker and only ITS orphan. The marker has to be that
        # chunk's scratch directory, not the store: every process in this test
        # — the coordinator included — carries the store path in its argv.
        kill_tree(slow, marker=os.path.join(st.out, "tmp",
                                            "chunk-%d-" % held["id"]))
        slow = None
        reissued = wait_for(
            lambda: st.sql("SELECT * FROM chunk WHERE id=? AND status IN "
                           "('reissued','complete')", (held["id"],)),
            90, "the dead worker's chunk to be reissued")
        check(reissued[0]["status"] == "reissued",
              "chunk %d should be reissued, is %r"
              % (held["id"], reissued[0]["status"]))

        # And the fast worker sweeps it, which lets the cursor catch up.
        try:
            wait_for(lambda: st.cursor() > held["seed_to"], 150,
                     "the reissued range to be re-swept and the cursor to "
                     "absorb")
        except Fail as e:
            raise Fail("%s\n    master log:%s" % (e, tail(master_log)))
        check(not contiguous_coverage(st), "coverage has holes after recovery")
    finally:
        for p in (m, slow, fast):
            if p:
                kill_tree(p, marker=st.dir)
        st.cleanup()


def test_master_restart_recovers():
    """Kill the coordinator mid-chunk. The next one must re-sweep the range
    that was in flight, not step over it.

    This is the path that decides whether a crashed session leaves a silent
    hole: on startup the master rebuilds cursor / allocation / pending from the
    `chunk` table, and a range that was `leased` when it died belongs on the
    pending queue."""
    st = Store(base=6 * 10**6, bootstrap=40, chunk_minutes=1, lease=20)
    log1 = os.path.join(st.dir, "master1.log")
    log2 = os.path.join(st.dir, "master2.log")
    m1 = m2 = w = None
    try:
        m1 = spawn(st, "coordinate", "--for", "8m", "-y", out=log1)
        wait_for_port(st.port)
        w = spawn(st, "worker", "--master", "127.0.0.1:%d" % st.port, "-j", "6",
                  out=os.path.join(st.dir, "w.log"))
        wait_for(lambda: len([r for r in st.chunks()
                              if r["status"] == "complete"]) >= 1,
                 90, "the first chunk to land")
        held = wait_for(
            lambda: next((r for r in st.chunks() if r["status"] == "leased"),
                         None),
            60, "a chunk to be in flight")
        cursor_before = st.cursor()
        check(cursor_before == held["seed_from"],
              "with everything before it landed, the cursor should sit at the "
              "in-flight chunk's first seed (%d vs %d)"
              % (cursor_before, held["seed_from"]))

        # Both die hard. No drain, no bye, no chance to tidy up.
        kill_tree(m1, marker=None)
        m1 = None
        kill_tree(w, marker=os.path.join(st.out, "tmp", "chunk-"))
        w = None
        check(st.cursor() == cursor_before,
              "the cursor must not have moved over the in-flight range")

        # The dead master's lock names THIS host with a dead pid, so it is
        # reclaimed without --force-unlock. (A lock from another host never is
        # — that is test_two_masters_refused.)
        m2 = spawn(st, "coordinate", "--for", "4m", "-y", out=log2)
        wait_for_port(st.port)
        w = spawn(st, "worker", "--master", "127.0.0.1:%d" % st.port, "-j", "6",
                  out=os.path.join(st.dir, "w2.log"))
        try:
            wait_for(lambda: st.cursor() > held["seed_to"], 150,
                     "the abandoned range to be re-swept")
        except Fail as e:
            raise Fail("%s\n    master log:%s" % (e, tail(log2)))
        covered = [r for r in st.chunks()
                   if r["status"] == "complete"
                   and r["seed_from"] <= held["seed_from"] <= r["seed_to"]]
        check(covered, "no completed chunk covers the abandoned range %d..%d"
              % (held["seed_from"], held["seed_to"]))
        check(not contiguous_coverage(st),
              "coverage has holes after a master restart")
    finally:
        for p in (m1, m2, w):
            if p:
                kill_tree(p, marker=st.dir)
        st.cleanup()


def test_hash_mismatch_refused():
    """A worker whose generator differs is refused, and told which hash.

    Run from a fake local clone — the same directory layout, a copied
    `fuzzrun.py`, a symlinked `app` and an EDITED `gen-random.py` — because
    "started from a local clone instead of the NFS path" is exactly the mistake
    a shared directory does not prevent."""
    st = Store(base=4 * 10**6, bootstrap=8, chunk_minutes=1)
    clone = tempfile.mkdtemp(prefix="fuzzrun-clone-")
    m = None
    try:
        ctests = os.path.join(clone, "gren-format-lib", "tests")
        capp = os.path.join(clone, "gren-format")
        os.makedirs(ctests)
        os.makedirs(capp)
        shutil.copy2(FUZZRUN, ctests)
        os.symlink(os.path.abspath(APP), os.path.join(capp, "app"))
        with open(GEN) as f:
            src = f.read()
        with open(os.path.join(ctests, "gen-random.py"), "w") as f:
            f.write(src + "\n# a one-line grammar change is a new generation\n")

        m = spawn(st, "coordinate", "--for", "60s", "-y",
                  out=os.path.join(st.dir, "master.log"))
        wait_for_port(st.port)
        wait_for(lambda: os.path.exists(fr.token_file(st.out)), 30,
                 "the coordinator to write its token")
        p = subprocess.run(
            [sys.executable, os.path.join(ctests, "fuzzrun.py"),
             "--out", st.out, "worker",
             "--master", "127.0.0.1:%d" % st.port, "-j", "2"],
            capture_output=True, text=True, timeout=60)
        check(p.returncode != 0,
              "a worker with a different gen-random.py must exit non-zero")
        check("gen-random.py differs" in (p.stdout + p.stderr),
              "the refusal must name which hash differed, got:\n%s"
              % (p.stdout + p.stderr))

        # …and the same clone with the RIGHT generator is admitted, so the
        # check is not just "anything from elsewhere is refused".
        shutil.copy2(GEN, os.path.join(ctests, "gen-random.py"))
        ok = subprocess.Popen(
            [sys.executable, os.path.join(ctests, "fuzzrun.py"),
             "--out", st.out, "worker",
             "--master", "127.0.0.1:%d" % st.port, "-j", "2"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
        try:
            wait_for(lambda: st.sql("SELECT * FROM worker"), 40,
                     "the matching-hash worker to be admitted")
        finally:
            kill_tree(ok, marker=st.dir)
    finally:
        if m:
            kill_tree(m, marker=st.dir)
        shutil.rmtree(clone, ignore_errors=True)
        st.cleanup()


def test_two_masters_refused():
    """A lock held by another host is never reaped, and the direct-db commands
    say where to look instead.

    The other host is simulated by writing its lock file, because the bug this
    guards is precisely that the old code asked *this* kernel about *that*
    host's pid. `os.getpid()` is a live pid here, so a lock naming another host
    with a live-looking pid is the exact shape that used to be reaped."""
    st = Store(base=5 * 10**6)
    try:
        os.makedirs(st.out, exist_ok=True)
        with open(fr.lock_path(st.out), "w") as f:
            f.write("otherhost %d %s distributed 9999\n"
                    % (os.getpid(), fr.now_iso()))

        rc, out = run(st, "coordinate", "--for", "30s", "-y")
        check(rc != 0, "a second coordinator must be refused")
        check("otherhost" in out,
              "the refusal must name the holding host, got:\n%s" % out)

        rc, out = run(st, "run", "--for", "30s", "-y")
        check(rc != 0, "a single-host run must be refused too")

        rc, out = run(st, "status")
        check(rc != 0, "direct db access from a non-coordinator host must be "
                       "refused, got:\n%s" % out)
        check("--master otherhost:9999" in out,
              "the refusal must point at the coordinator, got:\n%s" % out)

        rc, out = run(st, "resweep")
        check(rc != 0, "resweep writes, so it must take the lock too")

        # --force-unlock is the only way through, and it is explicit.
        rc, out = run(st, "reset", "-y", "--force-unlock")
        check(rc == 0, "--force-unlock should take over, got:\n%s" % out)
    finally:
        st.cleanup()


TESTS = [
    ("range-arithmetic", test_range_arithmetic),
    ("loopback", test_loopback),
    ("lease-low-water", test_lease_and_low_water),
    ("master-restart", test_master_restart_recovers),
    ("hash-mismatch", test_hash_mismatch_refused),
    ("two-masters", test_two_masters_refused),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.
                                 RawDescriptionHelpFormatter)
    ap.add_argument("-k", metavar="SUBSTRING", help="run only matching tests")
    args = ap.parse_args()
    if not os.path.exists(APP):
        raise SystemExit("build the app first: cd ../../gren-format && "
                         "./build.sh")
    failed = 0
    for name, fn in TESTS:
        if args.k and args.k not in name:
            continue
        sys.stdout.write("%-20s " % name)
        sys.stdout.flush()
        t0 = time.time()
        try:
            fn()
        except Fail as e:
            failed += 1
            print("FAIL  (%.0fs)\n    %s" % (time.time() - t0, e))
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            print("ERROR (%.0fs)\n    %s: %s"
                  % (time.time() - t0, type(e).__name__, e))
        else:
            print("ok    (%.0fs)" % (time.time() - t0))
    print("\n%d failed" % failed if failed else "\nall passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
