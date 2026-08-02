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

# Architecture invariant (docs/commentHandling.md): no Render/* code may read a
# source row/position to make a layout or comment-placement decision.
python3 "$(dirname "$(realpath "$0")")/check-render-invariant.py" || exit 1

# The divergence catalogue and its fixture suite must stay 1:1 (see
# check-divergence-index.py). Checked here so drift is named, rather than
# surfacing as a missing-file error inside the suite.
python3 "$(dirname "$(realpath "$0")")/check-divergence-index.py" || exit 1

pushd ..
devbox run build_test

popd
node app "$@"
