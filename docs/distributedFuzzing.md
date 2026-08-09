# Distributed sweeps — design

**Status: design, not implemented.** This is the plan for a `fuzzrun.py` mode
that spreads one sweep across several hosts. Nothing described here exists yet;
`fuzzrun.py run` is still the single-host runner
[`fuzzTesting.md`](fuzzTesting.md) documents, and that mode is not going away —
see [Single-host mode is untouched](#single-host-mode-is-untouched).

Read [`fuzzTesting.md`](fuzzTesting.md) first. This document assumes its
vocabulary: lanes, cursors, generations, chunks, the dedup store.

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

### How it is meant to be invoked

Everything lives in the one shared directory — config, database, artifacts — so
no path options are needed on any host:

```bash
# on whichever host is free
cd /nfs/…/gren-format-lib/tests
./fuzzrun.py coordinate --for 2h --port 9999 --yes

# on each worker host
cd /nfs/…/gren-format-lib/tests
./fuzzrun.py worker --master hostA:9999 -j 12

# from anywhere, while it runs
./fuzzrun.py status --master hostA:9999
```

`-j` is per worker, so hosts of different sizes need no coordination beyond
telling each one its own number.

---

## The crux: parallel workers break the contiguous-prefix invariant

Everything else here is plumbing. This is the part that needs a real decision.

A lane's coverage is `[base_seed, cursor)` — a **prefix, never a set with
holes**. `fuzzTesting.md` is emphatic about why: a coverage number you cannot
trust is worse than no number at all, and a silent hole could hide a bug for
ever. Today the invariant is free, because the sweep is serial:

```python
base = lane.cursor                    # fuzzrun.py:717
...
lane.cursor = base + n                # fuzzrun.py:779
```

One chunk at a time, so the cursor is always a true high-water mark, and an
aborted chunk simply does not advance it.

With four workers that breaks on the first overlap. Worker B finishes
`[200,300)` while worker A is still grinding `[100,200)`. Advance the cursor to
300 and 100 seeds nobody swept are now recorded as covered — and nothing
anywhere reports it.

**The master advances the cursor only to the low-water mark**: the first seed of
the oldest still-in-flight chunk. Chunks that finished ahead of that stay in the
`chunk` table with `status='done'` and are absorbed the moment the laggard
lands. If A dies, its range is reissued and B's completion is already banked.

```
lane cursor ─────┐
                 ▼
base ──────── 100 ─────── 200 ─────── 300 ────────
   covered    │ in flight │  done,    │
  (contiguous)│ (worker A)│  ahead of │
              │           │ the cursor│  (worker B)
```

`status` prints both numbers, labelled:

```
dense-comments    covered 71,835   next 10,071,835   +1,200 done ahead of cursor
```

The second number is not coverage and must never be added to the first. It is
reported for the same reason aborted chunks are reported separately today: the
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

### Hazard 1 — `SweepLock` is cross-host unsafe, and fails *open*

```python
pid = int(f.read().split()[0])
os.kill(pid, 0)             # asks whether THIS host has that pid
```

The lock file holds a bare pid (`fuzzrun.py:324`). A master on host A holds it.
Start a second master on host B: it reads A's pid, asks whether *host B* has a
process with that number, almost certainly finds none, concludes **stale**,
deletes the lock, and starts. Two masters then hand out overlapping seeds and
both write one sqlite file.

That is the worst outcome this system has available, it is silently reachable,
and the guard that exists to prevent it is what walks into it.

The lock must record `hostname pid timestamp`, and the `os.kill` liveness test
must apply **only when the hostname matches**. A lock held by another host is
never auto-reaped: the master heartbeats it (touches the mtime) while running, a
reader can see how stale it is, and clearing it takes an explicit
`--force-unlock`. A dead remote master stays recoverable; a live one stays
protected.

### Hazard 2 — SQLite WAL does not work over NFS

`open_db` sets `PRAGMA journal_mode=WAL` (`fuzzrun.py:317`) so that `status` can
run from another terminal while a sweep writes. WAL needs an mmap'd `-shm`
shared-memory file, which SQLite documents as unsupported on network
filesystems.

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

So:

- `journal_mode=TRUNCATE`, not `DELETE`. The journal file is reused and
  truncated rather than created and unlinked every transaction, which is fewer
  NFS metadata round-trips for the same guarantee.
- `synchronous=FULL` stays. The db is tiny and written rarely; there is nothing
  to buy by trading durability.
- **Unconditionally**, not "detect a network filesystem and switch". One code
  path, no environment-dependent behaviour to reason about, and the local-disk
  case loses nothing it can measure.
- **Read the pragma back and assert it.** `open_db` executes the pragma today
  and never checks the result, so if this directory is already NFS then SQLite
  may have been quietly refusing the WAL switch all along. The journal mode
  should be a known fact, not a hoped-for one.

#### What this makes mandatory

The NFS risk is **concurrency**, not the journal mode. Putting the db on the
shared filesystem is sound exactly to the extent that one process at a time
opens it — so the three guards below stop being hygiene and become the thing the
decision rests on. All three land with it, not after it.

1. **The hostname-aware lock ships in the same change** (hazard 1 above). With
   the db on local disk, two masters is a confusing split brain: two separate
   databases, nothing lost. With the db on NFS it is two writers on one SQLite
   file over a filesystem whose locking SQLite warns about — corruption of the
   record itself. The same bug, an order of magnitude more expensive.

2. **Every db-writing command takes the lock, not just `run`.** `SweepLock` is
   held by `cmd_run` alone (`fuzzrun.py:616`); `cmd_resweep` and `cmd_reset`
   both write — `UPDATE failure SET state='fixed'`, cursor resets — with no lock
   at all. Locally that is sloppy. On NFS, a `resweep` fired off during a
   twelve-hour sweep is precisely the two-writer case.

3. **Direct db access from a non-coordinator host is refused, with a pointer.**
   `fuzzrun.py status` on a worker box must not silently open the shared file; it
   should say *"a sweep is running on hostA — use `status --master
   hostA:9999`"*. That preserves the single-accessor property the decision rests
   on, and fails loudly rather than returning a possibly-stale read.

**Cross-host `status` and `failures` therefore still go through the port:**

```bash
fuzzrun.py status --master hostA:9999
```

which is better than reading the db even where reading it would be safe — live
workers, their declared `-j`, per-worker rates and in-flight leases are not in
the schema and never will be.

Two documentation lines go stale with this and must change:
[`fuzzTesting.md`](fuzzTesting.md)'s *"the database is in WAL mode so readers do
not block on the writer"*, and `open_db`'s own comment saying the same.

### A third thing to watch, not a hazard

Every worker runs `node …/gren-format/app` from NFS, thousands of times per
chunk. The page cache should absorb that after the first read, but if measured
throughput comes in well below a local run, the escape hatch is copying `app` to
each worker's local disk. The handshake hash below is what keeps that honest — a
stale copy is refused rather than quietly swept with.

---

## The handshake still checks hashes

With one shared directory, "the worker has the same code" is true by
construction, so the hash exchange stops being provisioning and becomes a cheap
assertion. It is still worth its two sha1s, because it catches two things a
shared directory does not prevent:

- a worker started by mistake from a **local clone** instead of the NFS path;
- **NFS attribute caching** serving a worker a stale `gen-random.py` or `app`
  for up to `acregmax` (commonly 60 s) after a rebuild on another host.

A worker whose `gen-random.py` differs generates *different modules for the same
seeds*, so its results would be recorded against seeds that do not mean that. A
worker on a different `app` build tests different code. Either one poisons the
whole session's coverage claim, so the master **refuses** the worker and names
which hash differs. This is the distributed twin of the existing stale-app guard,
and it is as unbudgeable.

---

## Schema changes

Additive; nothing existing changes meaning.

| table | change | why |
|---|---|---|
| `worker` (new) | `id, session_id, host, jobs, gen_hash, app_build, connected_at, last_seen, disconnected_at, state` | who is attached, and the rate estimates hang off it |
| `chunk` | `+ worker_id, leased_at, last_heartbeat` | reissue on lease expiry; attribute a find to a host |
| `chunk.status` | new values `leased`, `reissued` | `done` / `aborted:*` keep their meaning |
| `worker_rate` (new) | `worker_id, lane_id, seeds_per_sec` | a 4-core laptop and a 32-core box cannot share an estimate |
| `session` | `+ mode ('local'\|'distributed'), coordinator_host, port` | `status` on an old db must still read |

`lane.cursor` keeps its type and its meaning — the contiguous prefix. It simply
stops being computed as `base + n` and starts being computed as the low-water
mark over in-flight chunks.

---

## Protocol

TCP, length-prefixed JSON frames (a decimal byte count, newline, then the
payload). Not NDJSON: the frames are small now that artifacts stay on disk, but a
length prefix costs nothing and removes every escaping question in advance.

Pull-only. The master never opens a connection.

| frame | direction | carries |
|---|---|---|
| `hello` | W→M | token, host, `jobs`, `gen_hash`, `app_build` |
| `welcome` / `refuse` | M→W | worker id, or the reason and which hash differed |
| `want` | W→M | free slots |
| `assign` | M→W | chunk id, lane name **and its full params**, `seed_from`, `count`, `out_dir`, `nice`/`ionice`, `max_shrinks` |
| `heartbeat` | W→M | chunk id, seeds done so far |
| `done` | W→M | chunk id, run dir, elapsed seconds, child exit code, outcome |
| `drain` | M→W | stop asking for work; finish what you hold |
| `bye` | W→M / M→W | clean shutdown either way |

**Workers are stateless.** The assignment carries the lane's parameters and the
scheduling niceties, so a worker reads no `fuzzrun.toml`, opens no database, and
holds no cursor. It knows how to run one command and report one path. Everything
that has to be *remembered* lives on the master, which is the only process that
may write the db.

**Timestamps are the master's.** Workers report elapsed seconds, never wall
times, so clock skew across hosts cannot reach the record.

**Completion is idempotent on chunk id.** A `done` frame retried after a network
blip must not double-count hits, and a chunk reissued after a lease expiry may
genuinely be completed twice by two workers; recording per `(chunk_id, seed)`
keeps both cases exact.

**Authentication** is a shared token in the `hello` frame plus an explicit bind
address. It is a LAN and this is not TLS, but a port that accepts "here are my
results" should not be anonymous, and binding to a named interface rather than
`0.0.0.0` should be the default.

---

## Scheduling

The master keeps the existing lane logic — weighted round-robin over enabled
lanes (`pick_lane`), whichever lane is furthest behind its share. Workers are
lane-agnostic; a fast host must not distort the lane weights just by asking for
work more often.

Chunk sizing moves from per-lane to **per `(worker, lane)`**. A brand-new worker
gets `bootstrap_seeds`, and its measured rate grows from there, still capped at
3× its own previous chunk so one fast chunk cannot cause a wild overshoot. The
existing `chunk_minutes` target is per chunk, so a 32-core host simply gets
proportionally more seeds in the same wall-clock.

The deadline rule generalises unchanged: **a chunk is never killed to meet the
deadline.** The master stops issuing when less than `floor` remains
(`min(60, max(10, chunk_seconds // 2))`, `fuzzrun.py:695`), sends `drain`, and
waits for in-flight chunks. A chunk still running when the drain grace expires is
simply not counted — its range stays uncovered and is re-swept next session,
which is the same trade the serial runner already makes.

---

## Failure modes

| what happens | what the master does |
|---|---|
| worker dies mid-chunk | lease expires (no heartbeat) → chunk `reissued`, range not counted, cursor unmoved |
| worker's network drops, then returns | its `done` is accepted if the chunk is still open; a duplicate is idempotent |
| worker reports a chunk twice | second report ignored on chunk id |
| two workers complete a reissued range | coverage idempotent; hits deduped per `(chunk_id, seed)` |
| worker hash mismatch | refused at handshake, named, worker exits non-zero |
| chunk times out on a worker | `aborted:timeout`, same lane-rate halving and two-strikes lane disable as today (`fuzzrun.py:750`) — but scoped per worker, since one pathological host is not a pathological lane |
| master Ctrl-C | `drain` to all workers, record partial state, release the lock |
| master dies hard | lock heartbeat goes stale; workers see the socket close and exit; next master needs `--force-unlock` |
| second master started on another host | refused by the hostname-aware lock (**hazard 1**) |

---

## Single-host mode is untouched

`fuzzrun.py run --for 2h` keeps working exactly as it does today, over the same
in-process code path. The distributed mode is a second **transport**, not a
second **runner**: the lane picker, chunk sizer, deadline logic and
`ingest_chunk` are shared, and only the "how does a chunk get executed" step
differs.

Deliberately **not** done: reimplementing single-host as "distributed with one
loopback worker". It is tidier on paper and it puts the one mode that works today
behind a socket, a lock protocol and a lease timer. The gain is a little less
code; the risk is the whole existing gate.

---

## Testing plan

1. **Loopback first.** A master on `127.0.0.1` and a worker on the same host
   exercises the entire protocol — handshake, assign, heartbeat, lease expiry,
   drain — before a second machine is involved.
2. **Kill a worker mid-chunk** and confirm the range is re-swept and the cursor
   did not move. The low-water mark is the thing most worth an explicit test,
   because when it is wrong it is wrong *silently*.
3. **Start a worker with a deliberately edited `gen-random.py`** and confirm the
   handshake refuses it. A guard that has never been seen to fire is not known to
   work — the same reason the predicate audit is proved by breaking what it
   watches (`GENERATOR.md`).
4. **Two masters, two hosts, one store** — must refuse.
5. **A 5-minute real sweep** across the hosts, then compare: seeds swept should
   be close to the sum of the hosts' single-host rates, and `status` should show
   a contiguous cursor with nothing stranded ahead of it once the session drains.

---

## Not in v1

- **Master-side shrink suppression.** Four hosts hitting one bug all shrink it
  independently before the master can dedupe. The assignment already carries
  `max_shrinks`, so the master can lower it when a run starts drowning — but the
  simple version under-merges rather than hides, which is the safe direction and
  the one already documented.
- **Shipping the generator and app to workers.** Unnecessary with NFS; the
  handshake hash is the check that would make it safe if it is ever wanted.
- **Master-side artifact GC** for the per-chunk scratch dirs. v1 deletes each
  after ingest; a crashed master will leave some behind.
- **Resweep across workers.** `resweep` stays single-host; it is short, and it
  answers one question against one build.
