#!/bin/bash

set -e
COMPILER_DIR=$(realpath ../../compiler)
export GREN_BIN="$COMPILER_DIR"/gren
node "$COMPILER_DIR"/app make Main --output=run-tests

node run-tests
