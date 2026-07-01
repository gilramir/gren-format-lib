#!/bin/bash

pushd ..
devbox run build_test

popd
node app "$@"
