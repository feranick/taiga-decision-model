#!/bin/bash
# run_deepcad.sh — DeepCAD build histories as Taiga goals: download, convert, verify, score models.
# Version: 2026.10.06.4
#
# Usage:  ./run_deepcad.sh download          DeepCAD's data (cad_json + split) into $DATA
#         ./run_deepcad.sh convert           JSON -> goals + reference STEP files        -> $OUT
#         ./run_deepcad.sh teacher           the scripted teacher builds every goal      -> $OUT/teacher
#         ./run_deepcad.sh verify            score the teacher builds against the originals and keep
#                                            the goals that match (IoU >= $MIN_IOU)     -> goals_<subset>_verified.json
#         ./run_deepcad.sh diagnose          why the goals the teacher can't build fail (first rejected feature)
#         ./run_deepcad.sh models [RUNS]     every model in RUNS/seed*/hf builds the verified goals -> $OUT/seed<N>
#         ./run_deepcad.sh eval              score teacher and model builds (verified goals only)
# Env:    WORK (~/taiga-expanded), DATA (~/deepcad), SUBSET (test), LIMIT (300 models; 0 = all),
#         MAX_FEATURES (60), MIN_IOU (0.99), OUT ($WORK/deepcad_$SUBSET), REF_PY (Python with OCP 7.9:
#         $WORK/ocp-venv/bin/python, as created by ../eval/run_s80.sh), BUILD_ARGS (extra options for
#         taiga_build_part.py in "models", e.g. --no-loop-guard)
# License: DeepCAD's models come from public Onshape documents (see README.md); check before use
#         beyond research.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-$HOME/taiga-expanded}
DATA=${DATA:-$HOME/deepcad}
SUBSET=${SUBSET:-test}
LIMIT=${LIMIT:-300}
MAX_FEATURES=${MAX_FEATURES:-60}
MIN_IOU=${MIN_IOU:-0.99}
OUT=${OUT:-$WORK/deepcad_$SUBSET}
REF_PY=${REF_PY:-$WORK/ocp-venv/bin/python}
PY=$WORK/taiga-s1/.venv/bin/python
BUILD=$REPO/build/taiga_build_part.py
URL=${URL:-http://www.cs.columbia.edu/cg/deepcad/data.tar}

die() { echo "error: $*" >&2; exit 1; }
log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

need_work() {
  # shellcheck disable=SC1091
  source "$WORK/freecad.env" 2>/dev/null || die "$WORK/freecad.env not found; run ../train_expanded.sh <machine> setup first"
  [[ -x $PY ]] || die "no venv at $PY"
  cd "$WORK/taiga-s1" || die "$WORK/taiga-s1 not found"
}

find_data() {  # cad_json folder and split file, wherever the archive put them
  CAD_JSON=$(find "$DATA" -maxdepth 4 -type d -name cad_json 2>/dev/null | head -n 1)
  SPLIT=$(find "$DATA" -maxdepth 4 -type f -name train_val_test_split.json 2>/dev/null | head -n 1)
}

stage_download() {
  mkdir -p "$DATA"
  find_data
  if [[ -n $CAD_JSON && -n $SPLIT ]]; then log "DeepCAD data already in $DATA: $CAD_JSON"; return 0; fi
  if [[ ! -f $DATA/data.tar ]]; then
    log "downloading $URL -> $DATA"
    curl -L --fail -o "$DATA/data.tar" "$URL" || die "download failed (the README of github.com/rundiwu/DeepCAD has a backup link)"
  fi
  log "extracting $DATA/data.tar"
  tar -xf "$DATA/data.tar" -C "$DATA" || die "extract failed"
  # the JSON files are in a nested archive (data/cad_json.tar.gz); the quantized vectors (cad_vec) aren't needed
  for a in $(find "$DATA" -maxdepth 4 -name 'cad_json*' \( -name '*.tar' -o -name '*.tar.gz' -o -name '*.tgz' -o -name '*.zip' \)); do
    log "extracting nested archive $a"
    case $a in *.zip) unzip -q -o "$a" -d "$(dirname "$a")" ;; *) tar -xf "$a" -C "$(dirname "$a")" ;; esac
  done
  find_data
  if [[ -z $CAD_JSON || -z $SPLIT ]]; then
    echo "archive contents (top levels):"; tar -tf "$DATA/data.tar" | awk -F/ 'NF>1{print $1"/"$2} NF==1{print $1}' | sort | uniq -c | head -n 20
    die "no cad_json folder or train_val_test_split.json found under $DATA"
  fi
  log "cad_json: $CAD_JSON ($(find "$CAD_JSON" -name '*.json' | wc -l) files), split: $SPLIT"
}

stage_convert() {
  "$REF_PY" -c "import OCP, numpy" 2>/dev/null || die "$REF_PY lacks OCP or numpy (run ../eval/run_s80.sh eval once to create ocp-venv, or set REF_PY)"
  find_data
  [[ -n $CAD_JSON && -n $SPLIT ]] || die "no DeepCAD data in $DATA (run: $0 download)"
  log "convert $SUBSET (limit $LIMIT, at most $MAX_FEATURES features) -> $OUT"
  "$REF_PY" "$HERE/convert_deepcad.py" --data "$CAD_JSON" --split "$SPLIT" \
    --subset "$SUBSET" --out "$OUT" --refs --max-features "$MAX_FEATURES" --limit "$LIMIT" 2>&1 \
    | grep -v -e '^\*\*\*' -e 'Statistics on Transfer' -e 'Transfer Mode' -e 'Transferring Shape' -e 'WorkSession' -e 'Step File Name' \
    | tee "$OUT/convert.log"
}

stage_teacher() {
  need_work
  log "teacher builds -> $OUT/teacher"
  "$PY" "$BUILD" --goals "$OUT/goals_$SUBSET.json" --teacher --quiet --out "$OUT/teacher" 2>&1 | tee "$OUT/teacher.log"
}

stage_verify() {
  need_work
  log "teacher builds vs the original models"
  "$FREECAD_PYTHON" "$REPO/taiga-expanded/eval/eval_s80.py" --spec "$OUT/eval_$SUBSET.json" --ref "$OUT/refs" \
    --built "teacher=$OUT/teacher" --out "$OUT/verify_$SUBSET.json" 2>&1 | tail -n 5 | tee "$OUT/verify.log"
  "$PY" - "$OUT" "$SUBSET" "$MIN_IOU" <<'PYEOF'
import json, sys
from pathlib import Path
out, sub, min_iou = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
res = json.loads((out / f"verify_{sub}.json").read_text())["parts"]
goals = json.loads((out / f"goals_{sub}.json").read_text())
spec = json.loads((out / f"eval_{sub}.json").read_text())
keep = {k for k, v in res.items() if v["teacher"].get("iou", 0.0) >= min_iou}
(out / f"goals_{sub}_verified.json").write_text(json.dumps({k: g for k, g in goals.items() if k in keep}, separators=(",", ":")) + "\n")
spec["parts"] = [p for p in spec["parts"] if p["part"] in keep]
(out / f"eval_{sub}_verified.json").write_text(json.dumps(spec, indent=1) + "\n")
lens = sorted(len(goals[k]["features"]) for k in keep)
print(f"verified: {len(keep)} of {len(res)} goals match their original (IoU >= {min_iou}); "
      f"features per goal: median {lens[len(lens) // 2] if lens else 0}, max {lens[-1] if lens else 0}")
PYEOF
}

stage_diagnose() {
  need_work
  local names
  names=$(grep -a INFEASIBLE "$OUT/teacher.log" | awk '{print $1}' | sed 's/:$//' | sort -u)
  [[ -n $names ]] || { log "no infeasible goals in $OUT/teacher.log"; return 0; }
  log "diagnosing $(wc -w <<<"$names") goals"
  # shellcheck disable=SC2086
  PYTHONPATH="$FREECAD_LIB:$WORK/taiga-s1" "$FREECAD_PYTHON" "$HERE/diagnose_goals.py" "$OUT/goals_$SUBSET.json" $names 2>&1 \
    | grep -v -e '^\s*$' | tee "$OUT/diagnose.log"
}

stage_models() {
  need_work
  local runs=${1:-$WORK/taiga-s1/runs/data2} n=0 goals=$OUT/goals_${SUBSET}_verified.json
  [[ -f $goals ]] || die "no verified goals ($goals); run teacher and verify first"
  for hf in "$runs"/seed*/hf; do
    [[ -d $hf ]] || continue
    local label; label=$(basename "$(dirname "$hf")")
    log "model $hf -> $OUT/$label"
    # shellcheck disable=SC2086
    "$PY" "$BUILD" --model "$hf" --goals "$goals" --quiet ${BUILD_ARGS:-} --out "$OUT/$label" 2>&1 | tail -n 4 | tee "$OUT/$label.log"
    n=$((n + 1))
  done
  (( n > 0 )) || die "no exported models in $runs/seed*/hf"
}

stage_eval() {
  need_work
  local built=("teacher=$OUT/teacher")
  for d in "$OUT"/seed*/; do [[ -d $d ]] && built+=("$(basename "$d")=${d%/}"); done
  log "eval: ${built[*]}"
  "$FREECAD_PYTHON" "$REPO/taiga-expanded/eval/eval_s80.py" --spec "$OUT/eval_${SUBSET}_verified.json" --ref "$OUT/refs" \
    --built "${built[@]}" --teacher "$OUT/teacher" --out "$OUT/eval_${SUBSET}_models.json" > "$OUT/eval.log" 2>&1 \
    || die "eval_s80.py failed, see $OUT/eval.log"
  "$PY" - "$OUT/eval_${SUBSET}_models.json" "$OUT/goals_${SUBSET}_verified.json" "$MIN_IOU" <<'PYEOF'
import json, math, sys
res = json.loads(open(sys.argv[1]).read())
goals, thr = json.loads(open(sys.argv[2]).read()), float(sys.argv[3])
labels = list(res["builds"])
models = [lb for lb in labels if lb != "teacher"]
bucket = lambda n: "1-5" if n <= 5 else "6-10" if n <= 10 else "11-20" if n <= 20 else "21+"
rows = {}
for part, r in res["parts"].items():
    b = bucket(len(goals[part]["features"]))
    for lb in labels:
        rows.setdefault((b, lb), []).append(r[lb].get("iou", 0.0))
def stat(xs):
    m = sum(xs) / len(xs)
    return m, (math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0)
print(f"\nShare of models built to IoU >= {thr} with the original (mean IoU in brackets)")
print(f"{'features':10s} {'goals':>6s}" + "".join(f"{lb:>18s}" for lb in labels) + ("   models mean ± sd" if len(models) > 1 else ""))
for b in ("1-5", "6-10", "11-20", "21+", "all"):
    per = {lb: (sum((v for (bb, l), v in rows.items() if l == lb and (b == "all" or bb == b)), [])) for lb in labels}
    n = len(per[labels[0]])
    if not n:
        continue
    ok = {lb: sum(x >= thr for x in per[lb]) / n for lb in labels}
    line = f"{b:10s} {n:6d}" + "".join(f"{ok[lb]:9.3f} ({sum(per[lb]) / n:5.3f})" for lb in labels)
    if len(models) > 1:
        m, s = stat([ok[lb] for lb in models])
        line += f"   {m:.3f} ± {s:.3f}"
    print(line)
PYEOF
}

mkdir -p "$OUT"
stage=${1:-}; shift || true
case $stage in
  download) stage_download ;;
  convert) stage_convert ;;
  teacher) stage_teacher ;;
  verify) stage_verify ;;
  diagnose) stage_diagnose ;;
  models) stage_models "$@" ;;
  eval) stage_eval ;;
  *) sed -n '3,21p' "$0"; exit 1 ;;
esac
