# Long fuzz sweeps (`fuzzrun.py`)

`gen-random.py` sweeps a range of seeds and exits. That is the right shape for a
gate you run after a change, but not for the thing it is actually good at:
grinding through hundreds of thousands of random modules over days, looking for
the bug that needs six features to co-occur.

`fuzzrun.py` is the coordinator for that. You give it a time budget — "fuzz for
two hours" — and it splits that into chunks, runs them under `nice`, advances a
seed cursor that survives across sessions, and records every failure with enough
information to reproduce it. It does not need Claude Code, or a terminal you keep
open, or a decision from you about which seeds you already covered last week.

This document is the operating manual. For what the generator itself checks —
its grammar, its oracles, its shrinker — see the `gen-random.py` section of
[testing.md](testing.md) and `GENERATOR.md`.

---

## Quick start

```bash
cd gren-format/ && ./build.sh          # sweeps test the built app, so build it first
cd ../gren-format-lib/tests

./fuzzrun.py run --for 2h              # sweep for two hours, then stop
./fuzzrun.py status                    # cursors, coverage, failure counts
./fuzzrun.py failures -v               # what was found, and how to reproduce it
```

To spread one sweep over several machines, see [Spreading a sweep across
hosts](#spreading-a-sweep-across-hosts).

Stop a sweep early with Ctrl-C; it shuts the current chunk down and records
where it got to. Start another whenever you like — `run` always resumes.

Everything lives in `gren-format-lib/tests/`:

| | |
|---|---|
| `fuzzrun.toml` | configuration — **tracked in git** |
| `fuzzrun.db` | sqlite state: cursors, chunks, failures (gitignored) |
| `fuzzrun-out/` | failure artifacts, session summaries, quarantine (gitignored) |

The `--db`, `--out` and `--config` options are global and go **before** the
subcommand: `./fuzzrun.py --config other.toml run --for 30m`.

---

## Configuration

A lane is one settings profile with its own seed cursor. The shipped
`fuzzrun.toml` defines three; add, remove, or disable them freely.

```toml
[defaults]
jobs = 6                    # gen-random -j per chunk; this host has 16 cores
nice = 19                   # scheduling niceness for the child
idle_io = true              # and idle I/O priority — both inherited by node
chunk_minutes = 10          # target wall-clock per chunk
bootstrap_seeds = 40        # first chunk of a lane, before its rate is known
max_shrinks_per_chunk = 20  # per-failure minimization cap

[lanes.dense-comments]
max_depth = 6
comment_rate = 0.7
base_seed = 10000000
weight = 3
```

Lane keys:

| key | meaning |
|---|---|
| `max_depth` | nesting bound passed to `gen-random.py --max-depth` |
| `comment_rate` | comment density, `0.0`–`1.0` |
| `no_comments` | `true` for structure only (ignores `comment_rate`) |
| `base_seed` | first seed of the lane; its coverage grows from here |
| `weight` | share of a session's chunks, relative to other enabled lanes |
| `enabled` | set `false` to park a lane without deleting it |
| `jobs` | override `defaults.jobs` for this lane |

**Changing `max_depth`, `comment_rate`, `no_comments` or `base_seed` changes what
the lane covers.** Its swept range would then describe settings it no longer
uses, so `fuzzrun` notices the change and offers to restart that lane from
`base_seed`. `weight`, `enabled` and `jobs` do not affect coverage and can change
at any time.

The shipped base seeds start at 10,000,000, clear of the by-hand sweeps that ran
up to roughly 9.7M. Lower them to re-cover that ground.

`./fuzzrun.py init` writes a starter config if you ever lose it (it refuses to
overwrite an existing one without `--force`).

---

## How a session spends its time

A session runs chunks back to back until the budget is gone:

1. **Pick a lane** — whichever enabled lane is furthest behind its weighted share
   of this session's chunks. A 3/2/1 weighting gives 3 chunks to the first lane
   for every 2 and 1 to the others.
2. **Size the chunk** — seeds per second is measured per lane and kept as a
   running average, so a chunk is sized to land near `chunk_minutes`. A brand-new
   lane starts at `bootstrap_seeds` and grows, capped at 3× its previous chunk so
   one unusually fast chunk cannot cause a wild overshoot.
3. **Run it** — `gen-random.py` under `nice -n 19 ionice -c3`, in its own process
   group. The niceness is inherited by every `node` worker it spawns, so a sweep
   stays out of the way of anyone else on the host.
4. **Record it** — failures are ingested, the cursor advances, the rate estimate
   updates.

Near the end of the budget the last chunk is sized to the time actually left.
**A chunk is never killed to meet the deadline**, so a session can overrun by a
few seconds. When less than half a chunk's worth of time remains (at most a
minute), the session stops rather than starting one too short to be worth its
startup.

Observed throughput on this host with `jobs = 6` is roughly 8–14 seeds per
second depending on depth, so a two-hour sweep covers on the order of 70,000
modules across all lanes.

### Duration format

`--for` accepts `2h`, `90m`, `45s`, `1h30m`. A bare number means minutes:
`--for 90` is an hour and a half. The default is `1h`.

---

## Coverage and cursors

A lane's coverage is always the contiguous range `[base_seed, cursor)` — a
prefix, never a set with holes. This is worth protecting, because a coverage
number you cannot trust is worse than no number at all, and it is what makes
"resume where I left off" a one-integer question.

The rule that keeps it true: **the cursor advances only when a chunk completes.**
If a chunk is interrupted, times out, or the generator exits unexpectedly, its
range is *not* counted, and the next session sweeps it again. Re-sweeping a few
thousand seeds costs minutes; a silent hole in the coverage could hide a bug
forever.

`status` reports aborted chunks separately so the re-sweeping is visible rather
than mysterious.

---

## Generations: when the grammar changes

A seed only means something in terms of the grammar that turns it into a module.
Change `gen-random.py` and seed 10,000,042 generates a different module than it
did yesterday — so every seed you have already swept no longer covers what the
records say it covers.

`fuzzrun` hashes `gen-random.py` and compares it on every run. When it changes,
it starts a **new generation**: every cursor resets to its `base_seed`, results
from the old generation stay in the database under the old hash, and any open
failures are marked `stale-grammar`. It explains all of this and asks before
doing it — `--yes` answers the prompt, which is what an unattended invocation
needs.

> **Promote any find you care about to a fixture *before* you change the
> grammar.** A `stale-grammar` failure cannot be re-tested: its seed no longer
> generates the module that failed, so re-running it proves nothing. The
> artifacts under `fuzzrun-out/failures/` survive, but the live repro does not.
> Use `./gen-random.py --promote SEED --name Foo` while it still reproduces.

You can also start a generation by hand with `./fuzzrun.py reset`, or restart a
single lane with `./fuzzrun.py reset --lane NAME`.

---

## What happens when it finds something

Findings come from `gen-random.py`'s own oracles and keep its buckets: `crash`,
`ast-mismatch`, `non-idempotent`, `comment-loss`, `sort-order`, `predicate-lie`,
`timeout`.

**Failures are deduplicated.** One formatter bug can fail hundreds of seeds; each
distinct failure is one row with a hit count, keyed on its bucket plus its
minimized source. A session that hits a single bug 400 times reports one entry
with 400 hits, not 400 things to read.

**Minimization is capped.** Shrinking a failure is expensive, and a sweep against
a broken build could otherwise spend its entire budget minimizing the same bug
over and over. Each chunk minimizes at most `max_shrinks_per_chunk` failures;
past that, failures are recorded unshrunk and deduplicated by their *full*
source. That under-merges — two hits of one bug can look like two findings —
which is the safe direction, since the alternative is hiding a second bug behind
the first. Unshrunk failures are flagged in `failures` output, counted in the
session summary, and their `report.txt` says so. The cap is never silent.

Artifacts for each distinct failure are copied to
`fuzzrun-out/failures/<hash>/`, holding what `gen-random.py` produced:
`report.txt` (repro command and a pre-computed diff), `input.gren`,
`input.min.gren`, and the per-bucket extras — `formatted.gren` and
`formatted2.gren` for a non-idempotent find, `permuted.gren` and both outputs for
a `sort-order` find.

### Quarantine

A generated module that does not parse is a **generator** bug, not a formatter
finding — but it matters, because a generator that emits illegal Gren is not
reliably telling the truth about anything else either. Quarantined modules are
counted per generation, sampled into `fuzzrun-out/quarantine/<hash>/`, and
reported by `status`. This number should be zero. If it isn't, fix the generator
before trusting the crash and idempotency findings around it.

---

## Re-testing after a fix

```bash
./fuzzrun.py resweep            # all open failures
./fuzzrun.py resweep --id 7     # just failure #7
```

`resweep` re-runs every seed recorded against each open failure, against the
currently built app, and closes the ones that now pass. A failure whose seeds
still fail stays open; if it now fails a *different* way, the output says so.

Two things it deliberately refuses to do:

- It will not run if `gen-random.py` has changed since the failures were
  recorded — the seeds no longer generate those modules, so a green result would
  be meaningless. It tells you to start a new generation instead.
- It does not shrink or write artifacts. It answers one question: does this still
  fail?

Rebuild the app first, or you will be re-testing the same binary that failed.

---

## Reading the output

`status` shows the current generation, each lane's coverage and next seed, total
seeds swept, failure counts by state, and recent sessions:

```
generation 1  1f342c815e80  (started 2026-07-25T11:52:33+00:00)
  app build acf7aae29b96

lane                  covered    next seed    seeds/s  settings
dense-comments          2,983   10,002,983       11.4  depth 6, comments 0.70
deep-structure            160   10,000,160        8.4  depth 8, no-comments
default-mix                40   10,000,040       11.1  depth 5, comments 0.25

3,183 seeds over 4m42s of CPU-wall in 8 chunks
```

`failures` groups findings by state and bucket, most-hit first. It defaults to
the open ones:

```bash
./fuzzrun.py failures                    # open failures
./fuzzrun.py failures -v                 # + the head of each report.txt
./fuzzrun.py failures --state all        # including fixed and stale-grammar
./fuzzrun.py failures --bucket crash     # one kind
```

Each entry carries the `./gen-random.py --seed …` command that reproduces it,
with the lane's settings already filled in — a repro that does not carry its own
`--max-depth` and `--comment-rate` replays at the defaults and usually comes back
clean, which is worse than no repro at all.

Every session also writes a summary to `fuzzrun-out/sessions/session-NNNN.txt`.

---

## Handing a failure to somebody else

`fuzzrun-out/` is on the machine that swept. When the person who has to look at
a bug cannot see that directory — another host, a colleague, an assistant —
`export` bundles what they need into one text blob:

```bash
./fuzzrun.py export                 # every open failure, to stdout
./fuzzrun.py export --id 7          # just one
./fuzzrun.py export -o bugs.txt     # to a file
./fuzzrun.py export --full          # + the unminimized input.gren and raw outputs
```

**Send the `.gren`, not the seed.** A seed reproduces only against the same
`gen-random.py` *and* the same lane parameters — that is what the generation
mechanism means, and it is why every `repro` line carries `--max-depth` and
`--comment-rate`. A reader on another checkout gets nothing from it.
`input.min.gren` is just a Gren file: no generator, no seed, no generation match,
just `node ../../gren-format/app --show` it. The bundle still prints the repro
command for a reader who does happen to have this generation.

Each failure carries its bucket, message, hit count, seeds, the artifact's app
build, `input.min.gren` and `report.txt` — which already holds the *precomputed*
diff (format¹ vs format² for a non-idempotent find, the missing/extra comment
list for comment-loss, both author orders for sort-order).

Three details that make it usable rather than merely present:

- **The `check` line is bucket-aware.** `--show` exits 0 on a **comment-loss**
  find — a dropped comment is AST-equivalent and its output is its own fixed
  point, which is the whole reason that oracle exists — so a bundle that said
  "run `--show`" would read as *already fixed*. That bucket gets the
  `--pre-context` comparison instead, `predicate-lie` gets `--audit-predicates`,
  and `sort-order` says outright that it needs both inputs.
- **Read the line count, not the delimiter.** Each section header is
  `----- <name>  shown=N of M lines`, and exactly N lines follow. `--` opens a
  comment in Gren, so a payload can legally contain a line starting with
  `-----`; a reader that scanned for the next delimiter would cut the file
  short. Verified by round-tripping a bundle back to files and diffing.
- **`--full` includes the unminimized `input.gren`**, which is not redundant:
  one seed can carry two bugs and the shrinker keeps only the one it was
  minimizing towards (2026-08-09, seed 10035748), so a fix verified against the
  minimized file alone can leave the second one live.

Truncation is always marked in the header, never silent; raise `--lines` (default
200 per section) to get the rest.

## Running it unattended

```bash
nohup ./fuzzrun.py run --for 6h --yes > /tmp/fuzz.log 2>&1 &
```

`--yes` is required for anything non-interactive: without a terminal to ask,
`fuzzrun` refuses to guess at a generation or lane reset rather than silently
discarding coverage.

`run` exits `0` when nothing new was found, `1` when it recorded a newly distinct
failure or refused to start. So a cron job can mail you only when something turns
up:

```
0 2 * * * cd /home/gram/prj/gren-format/gren-format-lib/tests && ./fuzzrun.py run --for 4h --yes
```

Two guards matter here, because both failure modes cost you the whole run:

- **A stale app.** `run` refuses to start if any formatter source file is newer
  than the built app — a six-hour sweep of a binary that predates your change
  tests the wrong code. Rebuild, or pass `--allow-stale-app` if sweeping the old
  build is what you meant.
- **A second sweep.** Two concurrent sweeps would hand out the same seeds and
  double-count coverage, so every command that writes — `run`, `coordinate`,
  `resweep`, `reset` — takes a lock (`fuzzrun-out/fuzzrun.lock`) and refuses to
  start alongside a live one. The lock records `hostname pid timestamp mode
  port`. A lock left by a killed process **on this host** is detected and
  reclaimed; one held by **another host** never is, because this machine cannot
  ask another machine's kernel about its pids. Its owner heartbeats it every 30
  seconds, so the message tells you how stale it is, and `--force-unlock` is the
  explicit way through.

You can run `status` and `failures` from another terminal while a sweep is going.
The database is not in WAL mode — WAL needs shared memory that network
filesystems do not provide, and the store is meant to be shareable — but it is
written about once every ten minutes, for milliseconds, and `busy_timeout=30000`
covers the collision. On a host that is *not* the one holding the sweep, those
commands refuse to open the database at all and point you at
`status --master`; see [Spreading a sweep across
hosts](#spreading-a-sweep-across-hosts).

---

## When something goes wrong

**A lane times out twice and gets disabled.** A chunk that runs past three times
its target is killed. If that happens twice to the same lane in one session, the
lane is dropped for the rest of the session and the message names the seed to
start looking at (`./gen-random.py --seed N`). The cursor never advanced, so
nothing was lost — but a genuinely pathological seed will stall that lane every
session until it is dealt with.

**The generator exits unexpectedly.** The lane is disabled for the session and
the child's log is kept at `fuzzrun-out/tmp/chunk-N.log`. This usually means
`gen-random.py` itself is broken, not the formatter.

**Everything is suddenly failing.** Check `status` for a quarantine count, and
check that the app is the build you think it is. A formatter that crashes on
everything produces thousands of hits of one bug; the dedup keeps that readable,
and the shrink cap keeps it from eating the budget.

---

## Spreading a sweep across hosts

Seeds are embarrassingly parallel, so four hosts sweep about four times as many
of them. One host coordinates and the others do the work:

```bash
# on whichever host is free — it coordinates and sweeps NOTHING itself
cd /nfs/…/gren-format-lib/tests
./fuzzrun.py coordinate --for 12h --yes

# on each of the other hosts
cd /nfs/…/gren-format-lib/tests
./fuzzrun.py worker --master hostA:9999 -j 12

# from anywhere, while it runs
./fuzzrun.py status --master hostA:9999
```

**Every host runs out of the same shared directory**, so the config, the
database, the artifacts, `gen-random.py` and the built `app` are the same by
construction. That is what makes a worker stateless: it reads no config, opens
no database, and holds no cursor — an assignment tells it everything, and it
reports back a path under the shared store. Start the coordinator from whichever
machine is free; coverage follows the directory, not the host.

`-j` is per worker, so a laptop and a 32-core box need no coordination beyond
their own numbers — chunk size is measured per (worker, lane), so each host gets
as many seeds as it can sweep in `chunk_minutes`. Order does not matter either: a
worker started before its coordinator waits for it.

The coordinator prints both commands with its own hostname filled in as it
starts. Settings live in `[distributed]` in `fuzzrun.toml` (port, bind address,
lease timeout, drain grace).

A few things worth knowing before the first run:

- **The port is authenticated** by a shared token in
  `fuzzrun-out/fuzzrun.token`, written when the coordinator starts. A worker in
  the shared directory picks it up with no options; `--token` /
  `$FUZZRUN_TOKEN` override it.
- **It binds to this host's name, not `0.0.0.0`.** If that resolves to a
  loopback address — the Debian/Ubuntu `/etc/hosts` default is `127.0.1.1` — no
  worker elsewhere can reach it, so the coordinator says so loudly at startup.
  Put the LAN address in `[distributed] bind` when that happens.
- **A worker whose `gen-random.py` or `app` differs is refused**, by name, at
  the handshake. Its seeds would mean different modules, or it would be testing
  different code; either poisons the whole session's coverage claim.
- **`status` and `failures` on a worker host refuse to open the database** and
  tell you to use `--master`. That preserves the one-writer property the shared
  database rests on — and the coordinator's answer is better anyway, since live
  workers, their rates and the in-flight leases are not in the schema.

Coverage stays a contiguous prefix, which with several workers is not free: the
cursor advances only to the **low-water mark**, the first seed of the oldest
chunk still in flight. A chunk that finishes ahead of a laggard is banked but not
counted, and `status` reports it separately:

```
dense-comments    covered 71,835   next 10,071,835   +1,200 done ahead of cursor
```

Never add that second number to the first. If a worker dies, its range goes back
on the queue and is handed out again before any new ground, so the prefix closes
up rather than growing a hole.

The design, the hazards a shared filesystem introduces, and what is deliberately
left out are in [distributedFuzzing.md](distributedFuzzing.md).

## Where the code lives

- **`tests/fuzzrun.py`** — the runner: config, scheduler, sqlite store,
  `run` / `coordinate` / `worker` / `status` / `failures` / `export` /
  `resweep` / `reset` / `init`.
- **`tests/test-fuzzrun-distributed.py`** — the distributed mode's tests
  (low-water mark, loopback, a killed worker, a refused handshake, two masters).
  Not part of `run-tests.sh`: it binds sockets and spawns real sweeps.
- **`tests/fuzzrun.toml`** — the lane definitions, and `[distributed]`.
- **`tests/gen-random.py`** — the generator being driven. Two of its flags exist
  for this: `--max-shrinks N` caps minimization per run, and
  `--seeds 1,2,3 --json` re-checks an explicit seed list with no shrinking and no
  artifacts, printing one JSON verdict per line — that is what `resweep` calls.
  Each failure directory also carries a `meta.json` so the runner never has to
  parse `report.txt`.
- **`GENERATOR.md`** — the generator's design: grammar, oracles, shrinking.
- **[testing.md](testing.md)** — every other gate in this repo.
