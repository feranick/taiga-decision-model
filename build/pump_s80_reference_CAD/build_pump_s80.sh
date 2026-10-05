#!/bin/bash
# build_pump_s80.sh — generate the parametric S 80 pump (STEP parts) and assemble it in FreeCAD.
# Version: 2026.10.05.3
#
# Usage:   ./build_pump_s80.sh            Env: OUT (default parts/pump_s80), WORK (default ~/taiga),
#                                              BASE_TAIGA (default $WORK/taiga-s1)
# Needs OpenCASCADE's Python bindings in the upstream venv (installed on first run):
#   uv pip install --python ~/taiga/taiga-s1/.venv/bin/python 'cadquery-ocp>=7.9,<8'
set -uo pipefail

WORK=${WORK:-$HOME/taiga}
BASE_TAIGA=${BASE_TAIGA:-$WORK/taiga-s1}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)   # this folder, whatever it is called
REPO=$(cd "$HERE/../.." && pwd)
OUT=${OUT:-parts/pump_s80}
PY=$BASE_TAIGA/.venv/bin/python

# shellcheck disable=SC1091
source "$WORK/freecad.env" || { echo "error: $WORK/freecad.env not found; run the training setup first"; exit 1; }
cd "$BASE_TAIGA" || { echo "error: $BASE_TAIGA not found"; exit 1; }

# The model is tested with OCP 7.9 (OpenCASCADE 7.9, the same kernel generation as FreeCAD 1.1).
# OCP 8.x renamed classes the script uses, so anything other than 7.9 is replaced.
ocp=$("$PY" -c "import OCP; print(OCP.__version__)" 2>/dev/null || true)
if [[ $ocp != 7.9.* ]]; then
  echo "== OCP ${ocp:-not installed}; installing cadquery-ocp 7.9 into $BASE_TAIGA/.venv"
  uv pip install --python "$PY" "cadquery-ocp>=7.9,<8" || { echo "error: could not install cadquery-ocp 7.9"; exit 1; }
  ocp=$("$PY" -c "import OCP; print(OCP.__version__)" 2>/dev/null || true)
  [[ $ocp == 7.9.* ]] || { echo "error: OCP is still ${ocp:-missing}; another package provides it:"; uv pip list --python "$PY" | grep -i ocp; exit 1; }
fi

echo "== Generating the S 80 parts"
"$PY" "$HERE/pump_s80.py" --out "$OUT" --cutaway --preview || exit 1

echo "== Assembling in FreeCAD"
"$FREECAD_PYTHON" "$REPO/build/taiga_assemble.py" --parts "$OUT" --spec "$OUT/pump_s80.json" || exit 1
"$FREECAD_PYTHON" "$REPO/build/taiga_assemble.py" --parts "$OUT" --spec "$OUT/pump_s80.json" --explode 1 || exit 1

echo "Open with: freecad $(realpath -m "$OUT")/pump_s80.FCStd   (or pump_s80_exploded.FCStd; casing_cutaway.step for the cut casing)"
