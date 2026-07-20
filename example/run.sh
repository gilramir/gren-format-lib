#!/bin/bash

echo "Gren Orginal --------------"
cat Divergence20.gren
echo "Gren Formatted --------------"
../../gren-format/gren-format.sh --show Divergence20.gren

echo

echo "Elm Original --------------"
cat Divergence20.elm
echo "Elm Formatted --------------"
elm-format --stdin --elm-version=0.19 < Divergence20.elm


echo "Gren Prebroken Orginal --------------"
cat Divergence20-prebroken.gren
echo "Gren Prebroken Formatted --------------"
../../gren-format/gren-format.sh --show Divergence20-prebroken.gren

echo

echo "Elm Prebroken Original --------------"
cat Divergence20-prebroken.elm
echo "Elm Prebroken Formatted --------------"
elm-format --stdin --elm-version=0.19 < Divergence20-prebroken.elm
