#!/bin/bash

set -e

../../gren.sh make Main --output=app

node app
