#!/bin/bash
#
# End-to-end coverage
#
# Builds a sourcemapped test app, runs
# it under V8 coverage, and joins the V8 data against a fresh index of the
# project's sources into coverage.json (`join` indexes the --src project
# itself). Writes intermediates under out/.
#
set -e

if ! command -v gren-coverage-node >/dev/null; then
  echo "!! gren-coverage-node not found on PATH — install it via npm" >&2
  exit 1
fi

ROOT=$(pwd)   # clean absolute path (no ..) for report SF/file
OUT="${ROOT}/out"
COVDIR="${OUT}/v8cov"

mkdir -p "${OUT}"
rm -rf "${COVDIR}" && mkdir -p "${COVDIR}"

echo "==> building the test harness with sourcemaps and running"
# Build Main and run it, via devbox
test_rc=0
devbox run -- cov >/dev/null || test_rc=$?

if [ "${test_rc}" -ne 0 ]; then
  echo "!! build or tests exited ${test_rc} — coverage below reflects a failing/partial run"
fi

echo "==> joining"
# `join` indexes ${ROOT} (the denominator) itself, then shells out to
# gren-coverage.js (the one irreducibly-JS step) for the sourcemap/V8 decode.
# Absolute --src keeps the report's file paths clean (no ..). Its stdout summary
# is suppressed here; the "wrote ..." note goes to stderr.
gren-coverage-node join \
  --app "${ROOT}/cov-app" \
  --cov "${COVDIR}" \
  --src "${ROOT}"/.. \
  --out "${OUT}/coverage.json" >/dev/null

echo "==> rendering lcov -> ${OUT}/coverage.lcov"
gren-coverage-node render lcov "${OUT}/coverage.json" > "${OUT}/coverage.lcov"

# Generate HTML
if command -v genhtml >/dev/null; then
  echo "==> genhtml -> ${OUT}/html/index.html"
  genhtml "${OUT}/coverage.lcov" -o "${OUT}/html" --branch-coverage >/dev/null
else
  echo "==> genhtml not found on PATH — skipping HTML report (install the lcov package for one)"
fi


# Terminal report (the four-state view). genhtml the lcov for a browsable one:
gren-coverage-node render text "${OUT}/coverage.json"

# Propagate the test result so CI still fails on a failing suite.
exit "${test_rc}"
