#!/bin/bash

# ./run-tests.sh              run the effectful test suite
# ./run-tests.sh --coverage   run it under code coverage instead, producing a
#                             four-state report (hit / never-called / eliminated
#                             / absent) plus out/coverage.lcov, via the sibling
#                             gren-coverage-node tool.

if [ "${1:-}" = "--coverage" ]; then
  shift
  COV="$(dirname "$(realpath "$0")")/../../gren-coverage-node/run-coverage.sh"
  if [ ! -x "${COV}" ]; then
    echo "coverage needs the gren-coverage-node sibling repo at ${COV}" >&2
    exit 1
  fi
  exec "${COV}" "$@"
fi

# (The architecture invariant that used to be checked here -- no Render/* code
# may read a source row/position to decide layout or comment placement -- is now
# enforced by the Gren compiler. `Formatter.RenderTree` hands the render layer a
# `RenderNode`/`RenderShape` pair with no positions on them at all, so a row read
# under src/Formatter/Render/ does not typecheck. check-render-invariant.py was
# deleted; see docs/testing.md.)

# The divergence catalogue and its fixture suite must stay 1:1 (see
# check-divergence-index.py). Checked here so drift is named, rather than
# surfacing as a missing-file error inside the suite.
python3 "$(dirname "$(realpath "$0")")/check-divergence-index.py" || exit 1

# A failed build must NOT fall through to `node app` — the app from the previous
# build is still sitting there, so running it reports a green for the code as it
# was BEFORE the edit that broke the compile.
pushd ..
devbox run build_test || {
  popd
  echo "run-tests.sh: build failed — not running the previously-built app" >&2
  exit 1
}

popd
node app "$@"
