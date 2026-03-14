#!/bin/bash

set -e

gren make Main --output=run-tests
node run-tests
