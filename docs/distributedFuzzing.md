# Distributed sweeps

**Status: implemented 2026-08-09.** `fuzzrun.py coordinate` and
`fuzzrun.py worker` spread one sweep across several hosts.
[`fuzzTesting.md`](fuzzTesting.md) is the operating manual — start there for how
to run one. This document is why it is shaped the way it is: the decisions, the
hazards a shared filesystem introduces, and the one invariant that is not free
once more than one machine is sweeping.

Single-host `fuzzrun.py run` is unchanged and is not going away — see
[Single-host mode is untouched](#single-host-mode-is-untouched).

This document assumes `fuzzTesting.md`'s vocabulary: lanes, cursors,
generations, chunks, the dedup store.

---

## What it is for

`gen-random.py` finds bugs at a rate roughly proportional to seeds swept, and
seeds are embarrassingly parallel. One host with 16 cores manages 8–14 seeds per
second; four hosts should manage four times that, and the twelve-hour sweeps that
find the six-feature conjunctions are exactly where that multiplier is worth
having.

The shape, decided 2026-08-09:

- **One master, started with a time budget** (`--for 5m` while testing, `2h`,
  later `12h`). It coordinates and **does no work of its own** — no local jobs,
  not even optionally. The one machine that must stay up for twelve hours is not
  the one to load with `node` processes, and a coordinator that competes with its
  own workers for CPU makes every rate estimate it keeps a lie.
- **Workers started by hand** on 2–4 other hosts, each told how many heavy jobs
  it may run (`-j`). They **connect out** to the master and pull work. Pull, not
  push: a worker can join late, leave whenever, and sit behind whatever the
  network does, and the master never needs to reach back.
- **Every host shares one NFS directory**, so all of them run the same
  `fuzzrun.py`, the same `gen-random.py` and the same built `gren-format/app` by
  construction.

### How it is invoked

Everything lives in the one shared directory — config, database, artifacts — so
no path options are needed on any host:

```bash
# on whichever host is free
cd /nfs/…/gren-format-lib/tests
./fuzzrun.py coordinate --for 2h --yes

# on each worker host
cd /nfs/…/gren-format-lib/tests
./fuzzrun.py worker --master hostA:9999 -j 12

# from anywhere, while it runs
./fuzzrun.py status --master hostA:9999
```

`-j` is per worker, so hosts of different sizes need no coordination beyond
telling each one its own number. The port comes from `[distributed] port` in
`fuzzrun.toml` (9999) unless `--port` overrides it, and the coordinator prints
the two commands above, with its own hostname filled in, as it starts.

**Order does not matter.** A worker started before its master waits for it (up
to `--wait`, default 60s) and says so, rather than failing on a race — starting
four hosts by hand means starting them in some order.

---

## The crux: parallel workers break the contiguous-prefix invariant

Everything else here is plumbing. This is the part that needed a real decision.

A lane's coverage is `[base_seed, cursor)` — a **prefix, never a set with
holes**. `fuzzTesting.md` is emphatic about why: a coverage number you cannot
trust is worse than no number at all, and a silent hole could hide a bug for
ever. With one worker the invariant is free, because the sweep is serial: one
chunk at a time, so the cursor is always a true high-water mark, and an aborted
chunk simply does not advance it.

With four workers that breaks on the first overlap. Worker B finishes
`[200,300)` while worker A is still grinding `[100,200)`. Advance the cursor to
300 and 100 seeds nobody swept are now recorded as covered — and nothing
anywhere reports it.

**The master advances the cursor only to the low-water mark**: the first seed of
the oldest still-in-flight chunk. Chunks that finished ahead of that stay in the
`chunk` table with `status='complete'` and are absorbed the moment the laggard
lands. If A dies, its range is reissued and B's completion is already banked.

```
lane cursor ─────┐
                 ▼
base ──────── 100 ─────── 200 ─────── 300 ────────
   covered    │ in flight │  done,    │
  (contiguous)│ (worker A)│  ahead of │
              │           │ the cursor│  (worker B)
```

All of that arithmetic lives in one small class, `LaneState`, and three
functions beside it (`merge_ranges`, `subtract_ranges`, `LaneState.absorb`).
`cursor` is coverage and is written back to the database; `alloc_next` is where
the next *unseen* range starts and runs ahead of the cursor while chunks are in
flight; `pending` holds ranges below `alloc_next` that nobody completed — gaps
left by a reissued or aborted chunk, or by a master that died — and they are
handed out **before** new ground, so a hole is never left behind for a later
session to discover. A master starting up rebuilds all three from the `chunk`
table (`Coordinator._recover`), which is what makes a crashed session
recoverable rather than a source of silent holes.

`status` prints both numbers, labelled:

```
dense-comments    covered 71,835   next 10,071,835   +1,200 done ahead of cursor
```

The second number is not coverage and must never be added to the first. It is
reported for the same reason aborted chunks are reported separately: the
re-sweeping is then visible rather than mysterious.

---

## What NFS buys, and what it costs

### It removes the artifact transport entirely

The obvious distributed design ships each failure's artifacts to the master —
`input.gren`, `input.min.gren`, `report.txt`, `meta.json` and the per-bucket
extras — because the dedup key is the *minimized source* and only the master can
compare it across hosts. That is a real chunk of protocol: a file map, size caps,
escaping.

A shared filesystem deletes all of it. The worker points `gen-random.py --out` at
a per-chunk scratch directory under the shared store
(`fuzzrun-out/tmp/chunk-<id>-<worker>/`) and, when the child exits, reports **the
path**. The master then calls

```python
ingest_chunk(db, gen, lane, chunk_id, run_dir, out_root)
```

on it **in place, unchanged**. Dedup keying, `shutil.copytree` into
`fuzzrun-out/failures/<hash>/`, quarantine sampling, `_store_path` — none of it
learns that a network exists. That is the seam worth protecting: the distributed
mode adds a way to *schedule* chunks, not a second way to *record* them.

This leans on NFS close-to-open consistency, which is the guarantee it actually
provides: what one client writes and closes is visible to another client that
opens afterwards. The child process has exited before the completion frame is
sent, so every file is closed. The ordering is not a race we are hoping wins.

### Hazard 1 — `SweepLock` was cross-host unsafe, and failed *open*

```python
pid = int(f.read().split()[0])
os.kill(pid, 0)             # asks whether THIS host has that pid
```

The lock file held a bare pid. A master on host A holds it. Start a second
master on host B: it reads A's pid, asks whether *host B* has a process with
that number, almost certainly finds none, concludes **stale**, deletes the lock,
and starts. Two masters then hand out overlapping seeds and both write one
sqlite file.

That is the worst outcome this system has available, it was silently reachable,
and the guard that existed to prevent it is what walked into it.

The lock now records `hostname pid timestamp mode port`, and the `os.kill`
liveness test applies **only when the hostname matches**. A lock held by another
host is never auto-reaped: the owner heartbeats it (touches the mtime) every 30
seconds while running, a reader can see how stale it is, and clearing it takes an
explicit `--force-unlock`. A dead remote master stays recoverable; a live one
stays protected.

### Hazard 2 — SQLite WAL does not work over NFS

`open_db` used to set `PRAGMA journal_mode=WAL` so that `status` could run from
another terminal while a sweep wrote. WAL needs an mmap'd `-shm` shared-memory
file, which SQLite documents as unsupported on network filesystems.

**Decision (2026-08-09, the user's): the database stays on NFS beside the
artifacts, and WAL is dropped.** The alternative — db on the coordinator's local
disk via the existing `--db` global — was written up here first and rejected for
a reason that outweighs the tidiness: with the db in the shared directory, the
master can be started from **whichever host is free**, because coverage state
follows the directory instead of being pinned to one machine. That is the
difference between "ssh to the coordinator" and "ssh anywhere", and the whole
point of this mode is that it is easy to run.

**Dropping WAL costs almost nothing here, and that is measurable rather than
hopeful.** WAL's benefit scales with write frequency; this database is written
roughly **once per chunk** — once every ten minutes, for milliseconds. The
contention it avoids is a collision a reader would have to land inside that
window to hit, and `busy_timeout=30000` (already set, same line) covers it.

So, in `open_db`:

- `journal_mode=TRUNCATE`, not `DELETE`. The journal file is reused and
  truncated rather than created and unlinked every transaction, which is fewer
  NFS metadata round-trips for the same guarantee.
- `synchronous=FULL` stays. The db is tiny and written rarely; there is nothing
  to buy by trading durability.
- **Unconditionally**, not "detect a network filesystem and switch". One code
  path, no environment-dependent behaviour to reason about, and the local-disk
  case loses nothing it can measure.
- **The pragma is read back and asserted.** The old code executed it and never
  checked the result, so if this directory were already NFS then SQLite may have
  been quietly refusing the WAL switch all along. The journal mode is a known
  fact now, not a hoped-for one.

#### What this made mandatory

The NFS risk is **concurrency**, not the journal mode. Putting the db on the
shared filesystem is sound exactly to the extent that one process at a time
opens it — so the three guards below stopped being hygiene and became the thing
the decision rests on. All three landed with it, not after it.

1. **The hostname-aware lock shipped in the same change** (hazard 1 above). With
   the db on local disk, two masters is a confusing split brain: two separate
   databases, nothing lost. With the db on NFS it is two writers on one SQLite
   file over a filesystem whose locking SQLite warns about — corruption of the
   record itself. The same bug, an order of magnitude more expensive.

2. **Every db-writing command takes the lock, not just `run`.** `SweepLock` was
   held by `cmd_run` alone; `cmd_resweep` and `cmd_reset` both write —
   `UPDATE failure SET state='fixed'`, cursor resets — and had no lock at all.
   Locally that was sloppy. On NFS, a `resweep` fired off during a twelve-hour
   sweep is precisely the two-writer case.

3. **Direct db access from a non-coordinator host is refused, with a pointer.**
   `fuzzrun.py status` on a worker box must not silently open the shared file;
   it says *"a distributed sweep is running on host hostA … Ask the coordinator
   instead: ./fuzzrun.py status --master hostA:9999"*. That preserves the
   single-accessor property the decision rests on, and fails loudly rather than
   returning a possibly-stale read. (`refuse_if_remote_sweep`, on `status`,
   `failures` and `export`.)

**Cross-host `status` and `failures` therefore go through the port**, which is
better than reading the db even where reading it would be safe — live workers,
their declared `-j`, per-worker rates and in-flight leases are not in the schema
and never will be. The coordinator answers with `status_text()` plus a live
section; both commands are one implementation serving two transports, so
`status` and `status --master` cannot drift into different answers.

---

## The handshake still checks hashes

With one shared directory, "the worker has the same code" is true by
construction, so the hash exchange is not provisioning but a cheap assertion. It
is still worth its two sha1s, because it catches two things a shared directory
does not prevent:

- a worker started by mistake from a **local clone** instead of the NFS path;
- **NFS attribute caching** serving a worker a stale `gen-random.py` or `app`
  for up to `acregmax` (commonly 60 s) after a rebuild on another host.

A worker whose `gen-random.py` differs generates *different modules for the same
seeds*, so its results would be recorded against seeds that do not mean that. A
worker on a different `app` build tests different code. Either one poisons the
whole session's coverage claim, so the master **refuses** the worker, names which
hash differed, and the worker exits non-zero. This is the distributed twin of the
existing stale-app guard, and it is as unbudgeable.

The test for it runs a worker out of a **fake local clone** — same directory
layout, copied `fuzzrun.py`, symlinked `app`, edited `gen-random.py` — and then
runs the same clone again with the generator restored, because a guard that
refuses everything reads exactly like one that refuses the right thing.

---

## Schema

Additive; nothing existing changed meaning, and `status` on a database written
before this still reads. `migrate_db` adds the columns to an existing store on
first open.

| table | change | why |
|---|---|---|
| `worker` (new) | `id, session_id, host, addr, jobs, gen_hash, app_build, connected_at, last_seen, disconnected_at, state` | who is attached, and the rate estimates hang off it |
| `chunk` | `+ worker_id, leased_at, last_heartbeat` | reissue on lease expiry; attribute a find to a host |
| `chunk.status` | new values `leased`, `reissued` | `complete` / `aborted:*` keep their meaning |
| `worker_rate` (new) | `worker_id, lane_id, seeds_per_sec` | a 4-core laptop and a 32-core box cannot share an estimate |
| `session` | `+ mode ('local'\|'distributed'), coordinator_host, port` | `status` on an old db must still read |

`lane.cursor` keeps its type and its meaning — the contiguous prefix. It simply
stops being computed as `base + n` and starts being computed as the low-water
mark over in-flight chunks.

A reconnecting worker does not re-bootstrap: its rates are seeded from the most
recent `worker` row with the same host **and** the same `-j`, since a host that
comes back with a different job count is not the same measurement.

---

## Protocol

TCP, length-prefixed JSON frames (a decimal byte count, newline, then the
payload). Not NDJSON: the frames are small now that artifacts stay on disk, but a
length prefix costs nothing and removes every escaping question in advance.

Pull-only. The master never opens a connection.

| frame | direction | carries |
|---|---|---|
| `hello` | W→M | token, host, `jobs`, `gen_hash`, `app_build`, protocol version |
| `welcome` / `refuse` | M→W | worker id, or the reason and which hash differed |
| `want` | W→M | free slots |
| `assign` | M→W | chunk id, lane name **and its full params**, `seed_from`, `count`, `out_dir`, `nice`/`idle_io`, `max_shrinks`, `hard_timeout` |
| `wait` | M→W | nothing to hand out yet; ask again in N seconds |
| `heartbeat` | W→M | chunk id — a claim that it is still mine. Deliberately *not* seeds-done: `gen-random.py` reports no progress, and a field that always read 0 would be a number that lies |
| `done` | W→M | chunk id, run dir, elapsed seconds, child exit code, outcome |
| `ack` | M→W | a note, and the drain flag |
| `drain` | M→W | stop asking for work; finish what you hold |
| `bye` | W→M / M→W | clean shutdown either way |
| `status` / `failures` | C→M | a filter; the master replies with rendered text |

A worker's socket I/O all happens on its main loop — strict request, then
response — so there is no reply-matching to get wrong; the chunk itself runs in
a background thread and the only thing crossing threads is a `done` flag.

**Every acknowledgement carries the drain flag**, and that is load-bearing
rather than tidy. Without it a worker that has just reported its final chunk
asks for more work in the same moment the master decides it is finished, and
gets an EOF instead of an answer — a clean end of session that looks, from the
worker's side, like a lost coordinator. The master also waits up to 15 seconds
for its workers to hang up before closing the socket. The loopback test asserts
the workers exit **0**, which is what keeps both of those honest.

**Workers are stateless.** The assignment carries the lane's parameters and the
scheduling niceties, so a worker reads no `fuzzrun.toml`, opens no database, and
holds no cursor. It knows how to run one command and report one path. Everything
that has to be *remembered* lives on the master, which is the only process that
may write the db.

**Timestamps are the master's.** Workers report elapsed seconds, never wall
times, so clock skew across hosts cannot reach the record.

**Completion is idempotent on chunk id.** A `done` frame retried after a network
blip must not double-count hits, and a chunk reissued after a lease expiry may
genuinely be completed twice by two workers. A chunk already recorded `complete`
is acknowledged and dropped; and `ingest_chunk` now bumps a failure's `hits`
only when the `(failure_id, seed)` row it inserts is **new**, so a re-swept range
cannot inflate a hit count. (That also fixes the same over-count in single-host
mode, where a range re-swept after an abort had always double-counted.)

**Authentication** is a shared token in the `hello` frame plus an explicit bind
address. It is a LAN and this is not TLS, but a port that accepts "here are my
results" should not be anonymous. The token lives in `fuzzrun-out/fuzzrun.token`
(mode 0600), written by the coordinator when it starts — so a worker on the
shared directory needs no option at all, and a worker started from a local clone
gets a different one. `--token` / `$FUZZRUN_TOKEN` override it.

**The bind address defaults to this host's name, not `0.0.0.0`.** If that
resolves to a loopback address the coordinator says so in capitals at startup
and names the fix, because Debian and Ubuntu map the hostname to 127.0.1.1 in
`/etc/hosts` by default and a coordinator nobody can reach otherwise looks like
a network problem.

---

## Scheduling

The master keeps the existing lane logic — weighted round-robin over enabled
lanes (`pick_lane`), whichever lane is furthest behind its share. Workers are
lane-agnostic; a fast host must not distort the lane weights just by asking for
work more often.

Chunk sizing moves from per-lane to **per `(worker, lane)`** via the shared
`size_chunk`. A brand-new worker gets `bootstrap_seeds`, and its measured rate
grows from there, still capped at 3× its own previous chunk so one fast chunk
cannot cause a wild overshoot. The existing `chunk_minutes` target is per chunk,
so a 32-core host simply gets proportionally more seeds in the same wall-clock.
The lane-level rate is still maintained as a whole-fleet average, so a
single-host `run` after a distributed session starts from something measured.

**A worker holds one chunk at a time.** Its `-j` is passed straight to
`gen-random -j`, which is where its parallelism belongs; running two chunks at
once would just oversubscribe the same cores. `--slots` raises it for a host
where that is genuinely wanted, and the `want` frame has carried a slot count
from the start so the master needs no change.

The deadline rule generalises unchanged: **a chunk is never killed to meet the
deadline.** The master stops issuing when less than `floor` remains
(`min(60, max(10, chunk_seconds // 2))`), sends `drain`, and waits for in-flight
chunks. A chunk still running when the drain grace expires
(`drain_grace_minutes`, 35 by default) is simply not counted — its range stays
uncovered and is re-swept next session, which is the same trade the serial runner
already makes.

---

## Failure modes

| what happens | what the master does |
|---|---|
| worker dies mid-chunk | its socket closes → the chunk is reissued at once, range not counted, cursor unmoved |
| worker is SIGKILLed and its socket somehow stays open | lease expires (no heartbeat for `lease_seconds`) → same path |
| worker's network drops, then returns | its `done` is accepted if the chunk is not already `complete`; a duplicate is idempotent |
| worker reports a chunk twice | second report acknowledged and ignored, on chunk id |
| two workers complete a reissued range | coverage idempotent; hits deduped per `(failure_id, seed)` |
| worker hash mismatch | refused at handshake, named, worker exits non-zero |
| chunk times out on a worker | `aborted:timeout`, same rate halving and two-strikes lane disable as the serial runner — but scoped **per worker**, since one pathological host is not a pathological lane |
| worker started before the master | it waits (`--wait`, default 60s) and says so |
| master Ctrl-C | `drain` to all workers, record partial state, release the lock |
| master dies hard | lock heartbeat goes stale; workers see the socket close and exit non-zero; the next master needs `--force-unlock`, and recovers the uncovered ranges from the `chunk` table |
| second master started on another host | refused by the hostname-aware lock (**hazard 1**) |

---

## Single-host mode is untouched

`fuzzrun.py run --for 2h` works exactly as it did, over the same in-process code
path. The distributed mode is a second **transport**, not a second **runner**:
`pick_lane`, `size_chunk`, the deadline logic, `gen_cmd`, `run_child`,
`ingest_chunk` and `finish_session` are shared, and only the "how does a chunk
get executed" step differs.

Four things did change under `run`, all of them shared code and all of them
improvements it wanted anyway: the lock is hostname-aware and heartbeats;
`gen_cmd` takes a lane's *parameters* rather than a `Lane` (a worker holds no
config); chunk sizing moved into `size_chunk`; and a failure's `hits` now counts
`(failure, seed)` pairs rather than ingests, which stops a re-swept range from
inflating a count.

Deliberately **not** done: reimplementing single-host as "distributed with one
loopback worker". It is tidier on paper and it puts the one mode that works today
behind a socket, a lock protocol and a lease timer. The gain is a little less
code; the risk is the whole existing gate.

---

## Testing

`tests/test-fuzzrun-distributed.py` — not part of `run-tests.sh` (it binds
sockets and spawns real sweeps; it takes a few minutes), run by hand after
touching the transport. Every check here is a guard that is silent when it
works, so each is proved by making it fire.

```bash
cd gren-format-lib/tests
./test-fuzzrun-distributed.py            # all six
./test-fuzzrun-distributed.py -k lease   # one, by substring
```

1. **Range arithmetic** — the low-water mark with no sockets involved: B
   finishes first and the cursor must *not* move; both absorb when A lands; a
   requeued range is handed out again before new ground; completing one twice is
   idempotent. This is the thing most worth an explicit test, because when it is
   wrong it is wrong *silently*.
2. **Loopback** — a coordinator and two workers (`-j 4` and `-j 2`) on
   127.0.0.1 exercise the whole protocol. Asserts both workers completed chunks,
   both are marked `gone` at the end, the cursor is a true prefix (every seed
   below it covered by a completed chunk), and the two hosts did **not** share a
   rate estimate.
3. **Kill a worker mid-chunk** — a `-j 1` worker holds a chunk while a `-j 8`
   worker races past it; asserts the cursor has not advanced past the in-flight
   range, then SIGKILLs the slow worker and asserts the chunk is `reissued`, is
   re-swept, and the cursor catches up with no holes.
4. **Kill the coordinator mid-chunk** — the next one must re-sweep the range
   that was in flight rather than step over it. This is `_recover`'s only
   coverage, and it is the path that decides whether a crashed session leaves a
   silent hole. It also pins the *other* half of the lock rule: a lock left by
   a dead process **on this host** is still reclaimed without `--force-unlock`.
5. **A worker with a different `gen-random.py`** — refused, naming the hash;
   then the same clone with the generator restored is admitted.
6. **Two masters, one store** — the second is refused, and `run`, `status` and
   `resweep` are all refused too, with `status` printing the `--master` pointer.
   `--force-unlock` is the only way through.

Still worth doing by hand on the real hosts, since no test can stand in for it:
**a real sweep across the machines**, comparing seeds swept against the sum of
the hosts' single-host rates, and checking `status` shows a contiguous cursor
with nothing stranded ahead of it once the session drains.

---

## Not in v1

- **Master-side shrink suppression.** Four hosts hitting one bug all shrink it
  independently before the master can dedupe. The assignment already carries
  `max_shrinks`, so the master can lower it when a run starts drowning — but the
  simple version under-merges rather than hides, which is the safe direction and
  the one already documented.
- **Shipping the generator and app to workers.** Unnecessary with NFS; the
  handshake hash is the check that would make it safe if it is ever wanted.
- **Master-side artifact GC** for the per-chunk scratch dirs. Each is deleted
  after ingest, so the only leftovers are from a crashed master — or from a
  worker that was SIGKILLed, since its `gen-random.py` child runs in its own
  session (deliberately, so Ctrl-C reaches the runner only) and outlives it. A
  worker asked to stop politely shuts its child down.
- **Resweep across workers.** `resweep` stays single-host; it is short, and it
  answers one question against one build.
