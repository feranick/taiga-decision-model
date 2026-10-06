#!/bin/bash
# run_s80.sh — S 80 evaluation of taiga-expanded: build the S 80 goals, score them against the reference CAD.
# Version: 2026.10.05.1
#
# Usage:  ./run_s80.sh check             check that every goal can be built (fast, nothing saved)
#         ./run_s80.sh teacher           build every goal with the scripted teacher   -> $OUT/teacher
#         ./run_s80.sh models [RUNS]     build with every model in RUNS/seed*/hf      -> $OUT/seed<N>
#                                        (default RUNS: $WORK/taiga-s1/runs/data2)
#         ./run_s80.sh eval              score every build folder in $OUT against the reference
#         ./run_s80.sh all [RUNS]        check, teacher, models, eval
# Env:    WORK  (default ~/taiga-expanded: the patched code, venv and FreeCAD paths from train_expanded.sh setup)
#         OUT   (default $WORK/s80)
#         REF   reference STEP folder (default ~/taiga/taiga-s1/parts/pump_s80, as written by
#               build/pump_s80_reference_CAD/build_pump_s80.sh); generated with REF_PY if missing
#         REF_PY Python with OCP 7.9 (default ~/taiga/taiga-s1/.venv/bin/python)
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-$HOME/taiga-expanded}
OUT=${OUT:-$WORK/s80}
REF=${REF:-$HOME/taiga/taiga-s1/parts/pump_s80}
REF_PY=${REF_PY:-$HOME/taiga/taiga-s1/.venv/bin/python}
PY=$WORK/taiga-s1/.venv/bin/python
BUILD=$REPO/build/taiga_build_part.py
GOALS=$HERE/s80_goals.json

die() { echo "error: $*" >&2; exit 1; }
log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

# shellcheck disable=SC1091
source "$WORK/freecad.env" 2>/dev/null || die "$WORK/freecad.env not found; run ../train_expanded.sh <machine> setup first"
[[ -x $PY ]] || die "no venv at $PY; run ../train_expanded.sh <machine> setup first"
mkdir -p "$OUT"
cd "$WORK/taiga-s1" || die "$WORK/taiga-s1 not found"

stage_check() {
  log "check: can every goal be built? ($GOALS)"
  "$PY" "$BUILD" --goals "$GOALS" --check
}

stage_teacher() {
  log "teacher builds -> $OUT/teacher"
  "$PY" "$BUILD" --goals "$GOALS" --teacher --quiet --out "$OUT/teacher" 2>&1 | tee "$OUT/teacher.log"
}

stage_models() {
  local runs=${1:-$WORK/taiga-s1/runs/data2} n=0
  for hf in "$runs"/seed*/hf; do
    [[ -d $hf ]] || continue
    local label; label=$(basename "$(dirname "$hf")")
    log "model $hf -> $OUT/$label"
    "$PY" "$BUILD" --model "$hf" --goals "$GOALS" --quiet --out "$OUT/$label" 2>&1 | tee "$OUT/$label.log"
    n=$((n + 1))
  done
  (( n > 0 )) || die "no exported models in $runs/seed*/hf (run the sweep, or pass the runs folder)"
}

ensure_ref() {
  [[ -f $REF/casing.step ]] && return 0
  log "reference STEP files not found in $REF: generating them with $REF_PY"
  "$REF_PY" -c "import OCP" 2>/dev/null || die "$REF_PY has no OCP; run build/pump_s80_reference_CAD/build_pump_s80.sh once, or set REF_PY"
  "$REF_PY" "$REPO/build/pump_s80_reference_CAD/pump_s80.py" --out "$REF" || die "pump_s80.py failed"
}

stage_eval() {
  ensure_ref
  local built=()
  [[ -d $OUT/teacher ]] && built+=("teacher=$OUT/teacher")
  for d in "$OUT"/seed*/; do [[ -d $d ]] && built+=("$(basename "$d")=${d%/}"); done
  (( ${#built[@]} > 0 )) || die "no builds in $OUT (run teacher and/or models first)"
  local t=(); [[ -d $OUT/teacher ]] && t=(--teacher "$OUT/teacher")
  log "eval: ${built[*]}"
  "$FREECAD_PYTHON" "$HERE/eval_s80.py" --ref "$REF" --built "${built[@]}" "${t[@]}" \
    --out "$OUT/eval_s80.json" --overlay 2>&1 | tee "$OUT/eval_s80.log"
}

stage=${1:-}; shift || true
case $stage in
  check) stage_check ;;
  teacher) stage_teacher ;;
  models) stage_models "$@" ;;
  eval) stage_eval ;;
  all) stage_check; stage_teacher; stage_models "$@"; stage_eval ;;
  *) sed -n '3,16p' "$0"; exit 1 ;;
esac
