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

pushd ..
devbox run build_test

popd
node app "$@"
