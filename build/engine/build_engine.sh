#!/bin/bash
# build_engine.sh — check, build and assemble the engine kit with a trained Taiga-S1 model.
# Version: 2026.10.05.1
#
# Usage:   ./build_engine.sh [model]          (default model: runs/seed2/hf)
# Env:     MODEL, OUT (default parts/engine), WORK (default ~/taiga),
#          BASE_TAIGA (default $WORK/taiga-s1)
# Relative MODEL and OUT paths are relative to BASE_TAIGA.
set -uo pipefail

WORK=${WORK:-$HOME/taiga}
BASE_TAIGA=${BASE_TAIGA:-$WORK/taiga-s1}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)   # this repository, wherever it is
KIT=$REPO/build/engine
MODEL=${1:-${MODEL:-runs/seed2/hf}}
OUT=${OUT:-parts/engine}
PY=$BASE_TAIGA/.venv/bin/python

# shellcheck disable=SC1091
source "$WORK/freecad.env" || { echo "error: $WORK/freecad.env not found; run the training setup first"; exit 1; }
cd "$BASE_TAIGA" || { echo "error: $BASE_TAIGA not found"; exit 1; }

echo "== Checking goals"
"$PY" "$REPO/build/taiga_build_part.py" --goals "$KIT/example_goals_engine.json" --check \
  || { echo "error: some goals can't be built (INFEASIBLE above); fix the goal file first"; exit 1; }

echo "== Building parts with $MODEL"
"$PY" "$REPO/build/taiga_build_part.py" --model "$MODEL" --goals "$KIT/example_goals_engine.json" --out "$OUT" \
  || echo "WARNING: some parts were not built correctly; assembling what was built"

echo "== Assembling"
"$FREECAD_PYTHON" "$REPO/build/taiga_assemble.py" --parts "$OUT" --spec "$KIT/assembly_engine.json" || exit 1
"$FREECAD_PYTHON" "$REPO/build/taiga_assemble.py" --parts "$OUT" --spec "$KIT/assembly_engine.json" --explode 1 || exit 1

echo "Open with: freecad $(realpath -m "$OUT")/engine_assembly.FCStd   (or engine_assembly_exploded.FCStd)"
