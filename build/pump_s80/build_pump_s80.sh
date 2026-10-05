#!/bin/bash
# build_pump_s80.sh — generate the parametric S 80 pump (STEP parts) and assemble it in FreeCAD.
# Version: 2026.10.05.1
#
# Usage:   ./build_pump_s80.sh            Env: OUT (default parts/pump_s80), WORK (default ~/taiga),
#                                              BASE_TAIGA (default $WORK/taiga-s1)
# Needs OpenCASCADE's Python bindings in the upstream venv (installed on first run):
#   uv pip install --python ~/taiga/taiga-s1/.venv/bin/python cadquery-ocp
set -uo pipefail

WORK=${WORK:-$HOME/taiga}
BASE_TAIGA=${BASE_TAIGA:-$WORK/taiga-s1}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${OUT:-parts/pump_s80}
PY=$BASE_TAIGA/.venv/bin/python

# shellcheck disable=SC1091
source "$WORK/freecad.env" || { echo "error: $WORK/freecad.env not found; run the training setup first"; exit 1; }
cd "$BASE_TAIGA" || { echo "error: $BASE_TAIGA not found"; exit 1; }

if ! "$PY" -c "import OCP" 2>/dev/null; then
  echo "== Installing cadquery-ocp (OpenCASCADE bindings) into $BASE_TAIGA/.venv"
  uv pip install --python "$PY" cadquery-ocp || { echo "error: could not install cadquery-ocp"; exit 1; }
fi

echo "== Generating the S 80 parts"
"$PY" "$REPO/build/pump_s80/pump_s80.py" --out "$OUT" --cutaway --preview || exit 1

echo "== Assembling in FreeCAD"
"$FREECAD_PYTHON" "$REPO/build/taiga_assemble.py" --parts "$OUT" --spec "$OUT/pump_s80.json" || exit 1
"$FREECAD_PYTHON" "$REPO/build/taiga_assemble.py" --parts "$OUT" --spec "$OUT/pump_s80.json" --explode 1 || exit 1

echo "Open with: freecad $(realpath -m "$OUT")/pump_s80.FCStd   (or pump_s80_exploded.FCStd; casing_cutaway.step for the cut casing)"
