#!/usr/bin/env bash
# =============================================================================
# taiga_repro_5060ti.sh — reproduce Taiga-S1 from scratch on Ubuntu 26.04
# (resolute), host "mochi", with FreeCAD 1.1.x from ppa:bleedingedge/resolute-bleed
# Version: 2026.10.05.1
#
# Same pipeline as taiga_repro_DGX.sh (DGX Spark); only setup differs:
#   - FreeCAD from your PPA via apt (system Python), no conda
#   - FreeCAD lib dir, matching system interpreter and (if needed) Mod dirs are
#     discovered from the installed packages and wrapped in a small launcher,
#     so the torch venv never sees FreeCAD's paths
#   - PyTorch wheel chosen by hardware: cu130 (driver >= 580) or cu128 for the
#     RTX 5060 Ti (Blackwell, sm_120); CPU if no NVIDIA GPU (training a 1.2M-param model on CPU is fine)
#
# Stages:  setup data train export eval calib | all | sweep | aggregate | uninstall
# Usage:   ./taiga_repro_5060ti.sh all
#          SEED=12 ./taiga_repro_5060ti.sh train eval
#
# FreeCAD 1.1.x matches the version upstream evaluated on, so SEED=2 with
# DATA_WORKERS=8 is the best chance of regenerating the author's exact data.
#
# Tunables (env vars):
#   WORK=~/taiga  SEED=2  DATA_WORKERS=8  WORKERS=nproc-2  REPO_REF=a6e81d3
#   PPA=ppa:bleedingedge/resolute-bleed   FREECAD_PKG=freecad
#   TORCH_INDEX=...      override the auto-chosen PyTorch wheel index
#   FREECAD_PYTHON / FREECAD_LIB   set both to skip discovery entirely
# setup uses sudo for apt (PPA + FreeCAD); everything else runs as your user.
#
# Run-to-run variance (see README_training.md, "Variance studies"):
#   sweep      train + evaluate every seed in SEEDS, then aggregate
#   aggregate  distribution (mean, sd, min, max) over the runs of an experiment
#   SEEDS="2 12 22 32 42"  SWEEP_STAGES="data train export eval"  SWEEP_FORCE=0
#   DATA_SEED=   unset: training data follows SEED; set: every run uses that data
#   DATA_SCALE=1 multiplies the 4000/8000/12000 training episodes
#   EPOCHS=4  DAGGER_ROUNDS=2  DAGGER_EPISODES=400  DAGGER_EPOCHS=2
#   DETERMINISTIC=0  1: deterministic PyTorch (fails on non-deterministic ops),
#                    warn: only warn; DAgger workers pinned to DAGGER_WORKERS=8
#   EXP=         experiment name; default derived from the settings above
#                (empty for the defaults, so plain runs stay in runs/seed<N>)
# =============================================================================
set -euo pipefail

WORK=${WORK:-$HOME/taiga}
SEED=${SEED:-2}
DATA_SEED=${DATA_SEED:-}
DATA_SCALE=${DATA_SCALE:-1}
EPOCHS=${EPOCHS:-4}
DAGGER_ROUNDS=${DAGGER_ROUNDS:-2}
DAGGER_EPISODES=${DAGGER_EPISODES:-400}
DAGGER_EPOCHS=${DAGGER_EPOCHS:-2}
DETERMINISTIC=${DETERMINISTIC:-0}; export DETERMINISTIC
SEEDS=${SEEDS:-"2 12 22 32 42"}
SWEEP_STAGES=${SWEEP_STAGES:-"data train export eval"}
DATA_WORKERS=${DATA_WORKERS:-8}
WORKERS=${WORKERS:-$(( $(nproc) > 4 ? $(nproc) - 2 : 2 ))}
REPO_URL=${REPO_URL:-https://github.com/shhivv/biome-s1.git}   # formerly shhivv/taiga-s1
REPO_REF=${REPO_REF:-a6e81d3}
PPA=${PPA:-ppa:bleedingedge/resolute-bleed}
FREECAD_PKG=${FREECAD_PKG:-freecad}
TEST_SEED=3
TEST_WORKERS=8          # fixed so the test set is identical across machines
SUITES="iid comp comp2 comp3 len len2 len3 len4 len5 len6"   # len4-6: 13/15/17-feature stress suites

REPO=$WORK/taiga-s1
VENV=$REPO/.venv
PY=$VENV/bin/python
FCWRAP=$WORK/bin/freecad-python
exp_tag() {  # experiment name from non-default settings; empty for the defaults
  local t=()
  [[ $EPOCHS != 4 ]] && t+=("e$EPOCHS")
  [[ "$DAGGER_ROUNDS/$DAGGER_EPISODES/$DAGGER_EPOCHS" != 2/400/2 ]] && t+=("dg${DAGGER_ROUNDS}x${DAGGER_EPISODES}x${DAGGER_EPOCHS}")
  [[ $DATA_SCALE != 1 ]] && t+=("x$DATA_SCALE")
  [[ -n $DATA_SEED ]] && t+=("data$DATA_SEED")
  [[ $DETERMINISTIC != 0 ]] && t+=("det")
  local IFS=_; echo "${t[*]}"
}
EXP=${EXP:-$(exp_tag)}
GROUP=$REPO/runs${EXP:+/$EXP}
RUN=$GROUP/seed${SEED}
TRAIN_DATA=data/gen_train_seed${DATA_SEED:-$SEED}; [[ $DATA_SCALE == 1 ]] || TRAIN_DATA+=_x$DATA_SCALE
LOGTAG=${EXP:+${EXP}_}seed${SEED}
SELF=$(realpath "$0")
AGG=$(dirname "$SELF")/taiga_aggregate.py
RUNNER=$(dirname "$SELF")/taiga_run.py
LOGDIR=$WORK/logs
ENVFILE=$WORK/freecad.env
MARKER=$WORK/.installed_by_taiga

[[ " $* " == *" uninstall "* ]] || mkdir -p "$WORK/bin" "$LOGDIR"
export PATH="$WORK/bin:$HOME/.local/bin:$PATH"
export QT_QPA_PLATFORM=offscreen   # headless; FreeCAD App never needs a display

log()  { printf '\n[%s] \033[1m%s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '\n\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
timed() {  # timed <name> <cmd...>   — logs to file and records wall time
  local name=$1; shift; local t0=$SECONDS
  log "$name"
  set +e
  "$@" 2>&1 | tee -a "$LOGDIR/${name}_${LOGTAG}.log"
  local rc=${PIPESTATUS[0]}
  set -e
  [[ $rc -eq 0 ]] || die "$name failed (exit $rc) — see $LOGDIR/${name}_${LOGTAG}.log"
  printf '%s\t%s\t%ds\n' "$(date -Is)" "$name" $((SECONDS - t0)) >> "$LOGDIR/timings_${LOGTAG}.tsv"
}
# shellcheck disable=SC1090
load_fc() {
  [[ -f $ENVFILE ]] && source "$ENVFILE"
  [[ -n ${FREECAD_PYTHON:-} && -n ${FREECAD_LIB:-} ]] || die "FreeCAD not configured; run setup"
  [[ -f $RUNNER ]] || die "taiga_run.py not found next to this script ($RUNNER)"
}

# ----------------------------------------------------------------------------- setup
fc_check() {  # fc_check <python> <lib>  — can it build a PartDesign body headless?
  PYTHONPATH=$2 "$1" - <<'EOF' 2>&1
import FreeCAD, Part, Sketcher
doc = FreeCAD.newDocument("t")
body = doc.addObject("PartDesign::Body", "B")
sk = body.newObject("Sketcher::SketchObject", "S")
doc.recompute()
print("FreeCAD", ".".join(FreeCAD.Version()[:3]), "OK")
EOF
}

install_freecad() {
  local SUDO=""; [[ $EUID -ne 0 ]] && SUDO=sudo
  if dpkg-query -W -f='${Status}' "$FREECAD_PKG" 2>/dev/null | grep -q "install ok installed"; then
    log "$FREECAD_PKG already installed"
  else
    if ! grep -rqs "bleedingedge/resolute-bleed" /etc/apt/sources.list /etc/apt/sources.list.d/; then
      log "Adding $PPA"
      command -v add-apt-repository >/dev/null || { $SUDO apt-get update; $SUDO apt-get install -y software-properties-common; }
      $SUDO add-apt-repository -y "$PPA"; mark ppa
    fi
    log "Installing $FREECAD_PKG"
    $SUDO apt-get update
    $SUDO apt-get install -y "$FREECAD_PKG"; mark freecad_pkg
  fi
  local ver origin
  ver=$(dpkg-query -W -f='${Version}' "$FREECAD_PKG")
  origin=$(apt-cache policy "$FREECAD_PKG" | grep -A1 '^ \*\*\*' | tail -1 | xargs || true)
  log "Installed $FREECAD_PKG $ver  (from: ${origin:-unknown})"
  [[ $ver == *1.1* ]] || log "WARNING: expected FreeCAD 1.1.x; got $ver"
  [[ $origin == *bleedingedge* ]] || log "WARNING: package does not appear to come from $PPA"
}

discover_freecad() {
  # All files shipped by installed freecad* packages (Debian splits FreeCAD across several)
  local pkgs files so libdir pyver sysp mods=""
  pkgs=$(dpkg-query -W -f='${binary:Package}\n' '*freecad*' 2>/dev/null | tr '\n' ' ' || true)
  # shellcheck disable=SC2086
  files=$(dpkg -L $pkgs 2>/dev/null || true)
  so=$(grep -E '/FreeCAD\.so$' <<<"$files" | head -1 || true)
  [[ -n $so ]] || so=$(find /usr/lib /usr/local/lib /opt -name FreeCAD.so 2>/dev/null | head -1 || true)
  [[ -n $so ]] || die "FreeCAD.so not found; is $FREECAD_PKG installed?"
  libdir=$(dirname "$so")

  # The interpreter must be the Python FreeCAD.so was linked against
  pyver=$(ldd "$so" | grep -oE 'libpython3\.[0-9]+' | head -1 | sed 's/libpython//' || true)
  sysp=/usr/bin/python${pyver:-3}
  [[ -x $sysp ]] || sysp=/usr/bin/python3
  log "FreeCAD lib: $libdir | interpreter: $sysp (linked: python${pyver:-?})"

  local out
  out=$(fc_check "$sysp" "$libdir" || true); echo "$out"
  if [[ $out != *" OK"* ]]; then
    # Packaged builds sometimes need the Mod dirs explicitly (as in the Debian/Android port)
    local moddirs d
    moddirs=$(grep -E '/Mod/PartDesign$' <<<"$files" | xargs -r -n1 dirname | sort -u || true)
    [[ -n $moddirs ]] || moddirs=$(find /usr/lib /usr/share /usr/local -type d -path '*/Mod/PartDesign' \
                                   -exec dirname {} \; 2>/dev/null | sort -u || true)
    [[ -n $moddirs ]] || die "FreeCAD import failed and no Mod directories found"
    while read -r d; do
      [[ -n $d ]] || continue
      mods="$mods:$d"
      while read -r sub; do mods="$mods:$sub"; done < <(find "$d" -mindepth 1 -maxdepth 1 -type d)
    done <<<"$moddirs"
    log "Retrying with Mod dirs on PYTHONPATH"
    out=$(fc_check "$sysp" "$libdir$mods" || true); echo "$out"
    [[ $out == *" OK"* ]] || die "FreeCAD still fails headless; see output above"
  fi

  # Launcher: the worker processes get FreeCAD's paths, the torch venv never does.
  cat > "$FCWRAP" <<EOF
#!/bin/sh
# Generated by taiga_repro_5060ti.sh — FreeCAD worker interpreter
export QT_QPA_PLATFORM=offscreen
export PYTHONPATH="\${PYTHONPATH:+\$PYTHONPATH:}${libdir}${mods}"
exec $sysp "\$@"
EOF
  chmod +x "$FCWRAP"
  FREECAD_PYTHON=$FCWRAP
  FREECAD_LIB=$libdir
}

stage_setup() {
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ ${VERSION_CODENAME:-} == resolute ]] || log "Note: expected Ubuntu resolute, found ${VERSION_CODENAME:-unknown}"
  for c in git curl; do command -v $c >/dev/null || die "missing '$c' (sudo apt install $c)"; done

  command -v uv >/dev/null || { log "Installing uv"; curl -LsSf https://astral.sh/uv/install.sh | sh; mark uv; }

  if [[ ! -d $REPO/.git ]]; then log "Cloning $REPO_URL"; git clone "$REPO_URL" "$REPO"; fi
  git -C "$REPO" remote set-url origin "$REPO_URL"   # existing clones: follow the repo rename
  git -C "$REPO" fetch -q origin
  git -C "$REPO" checkout -q "$REPO_REF"
  log "Repo at $(git -C "$REPO" rev-parse --short HEAD)"

  # PyTorch: pick wheels by hardware unless overridden
  if [[ -z ${TORCH_INDEX:-} ]]; then
    if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then
      # RTX 50-series (Blackwell, sm_120) needs cu128+; cu130 wheels need driver >= 580
      local drv; drv=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 | cut -d. -f1)
      if (( drv >= 580 )); then TORCH_INDEX=https://download.pytorch.org/whl/cu130
      elif (( drv >= 570 )); then TORCH_INDEX=https://download.pytorch.org/whl/cu128
      else die "NVIDIA driver $drv is too old for Blackwell GPUs; install driver >= 570 (580+ preferred)"; fi
      log "NVIDIA GPU: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1) -> $TORCH_INDEX"
    else
      TORCH_INDEX=https://download.pytorch.org/whl/cpu
      log "No NVIDIA GPU found; using CPU PyTorch"
    fi
  fi
  [[ -x $PY ]] || uv venv --python 3.11 "$VENV"
  if ! "$PY" -c "import torch" 2>/dev/null; then
    log "Installing PyTorch from $TORCH_INDEX"
    uv pip install --python "$PY" --index-url "$TORCH_INDEX" torch \
      || { log "Index install failed; falling back to PyPI torch"; uv pip install --python "$PY" torch; }
  fi
  uv pip install --python "$PY" -e "${REPO}[dev]"
  "$PY" - <<'EOF'
import torch
print("torch", torch.__version__, "| CUDA build", torch.version.cuda, "| cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0), "capability", torch.cuda.get_device_capability(0))
    x = torch.randn(256, 256, device="cuda"); torch.cuda.synchronize(); print("matmul ok:", bool(torch.isfinite(x @ x).all()))
    arch = "sm_%d%d" % torch.cuda.get_device_capability(0)
    assert arch in torch.cuda.get_arch_list(), f"{arch} not in this torch build {torch.cuda.get_arch_list()}"
EOF
  if command -v nvidia-smi >/dev/null && ! "$PY" -c "import torch,sys; sys.exit(not torch.cuda.is_available())"; then
    die "NVIDIA GPU present but torch can't use it; reinstall torch with a matching TORCH_INDEX"
  fi

  if [[ -z ${FREECAD_PYTHON:-} || -z ${FREECAD_LIB:-} ]]; then
    install_freecad
    discover_freecad
  fi
  printf 'export FREECAD_PYTHON=%q\nexport FREECAD_LIB=%q\n' "$FREECAD_PYTHON" "$FREECAD_LIB" > "$ENVFILE"
  load_fc

  # End-to-end check through the repo's own worker path, then the test suite
  # (upstream tests silently SKIP FreeCAD ones if not found, hence the check first).
  cd "$REPO"
  "$PY" -c "from freecad_s1.runtime.fcenv import freecad_python; print('repo sees FreeCAD at', freecad_python())"
  timed tests "$PY" -m pytest -q
}

# ----------------------------------------------------------------------------- data
stage_data() {
  load_fc; cd "$REPO"
  local train=$TRAIN_DATA test=data/gen_test eps
  [[ $DATA_SCALE =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "DATA_SCALE must be a number (got '$DATA_SCALE')"
  eps=$(awk -v s="$DATA_SCALE" 'BEGIN { printf "%d %d %d", 4000*s+0.5, 8000*s+0.5, 12000*s+0.5 }')
  mkdir -p data
  exec 7>"data/.$(basename "$train").lock"; flock 7   # runs sharing DATA_SEED generate it once
  if compgen -G "$train/*.jsonl.gz" >/dev/null && [[ ! -f $train/.incomplete ]]; then
    log "Training data exists in $train — skipping (delete it to regenerate)"
  else
    rm -rf "$train"; mkdir -p "$train"; touch "$train/.incomplete"
    # shellcheck disable=SC2086
    timed datagen_train "$PY" -m freecad_s1.datagen --out "$train" \
      --episodes $eps --workers "$DATA_WORKERS" --seed "${DATA_SEED:-$SEED}"
    rm -f "$train/.incomplete"
  fi
  exec 7>&-
  if compgen -G "$test/*.jsonl.gz" >/dev/null; then
    log "Test data exists in $test — skipping"
  else
    timed datagen_test "$PY" -m freecad_s1.datagen --out "$test" \
      --episodes 300 600 900 --workers "$TEST_WORKERS" --seed "$TEST_SEED"
  fi
  "$PY" - "$train" <<'EOF'
import gzip, sys, pathlib
n = sum(1 for p in pathlib.Path(sys.argv[1]).glob("*.jsonl.gz") for l in gzip.open(p, "rt") if '"state"' in l)
print(f"labeled states: {n:,}  (upstream: ~590k at DATA_SCALE=1)")
EOF
}

# ----------------------------------------------------------------------------- train
stage_train() {
  load_fc; cd "$REPO"
  mkdir -p "$RUN"
  [[ -d $TRAIN_DATA ]] || die "no training data at $TRAIN_DATA; run data"
  local dw=${DAGGER_WORKERS:-$WORKERS}
  if [[ $DETERMINISTIC != 0 ]]; then
    # Deterministic kernels + fixed cuBLAS workspace; DAgger collection order depends on the
    # worker count, so it is pinned. Bit-identical results hold only on the same GPU/driver/torch.
    export CUBLAS_WORKSPACE_CONFIG=:4096:8 PYTHONHASHSEED=0
    dw=${DAGGER_WORKERS:-8}
    log "Deterministic training (DETERMINISTIC=$DETERMINISTIC), DAgger workers pinned to $dw"
  fi
  log "Run $RUN | data $TRAIN_DATA | epochs $EPOCHS | DAgger ${DAGGER_ROUNDS}x${DAGGER_EPISODES}, ${DAGGER_EPOCHS} epochs"
  # Upstream train_final.sh flags, plus EXTRA="--type-dropout 0.15" from the README; budgets are tunable.
  : > "$RUN/crashes_train.jsonl"   # 0 lines = no crash
  timed train env S1_CRASH_LOG="$RUN/crashes_train.jsonl" "$PY" "$RUNNER" freecad_s1.train_sft \
    --data "$TRAIN_DATA" --out "$RUN" \
    --epochs "$EPOCHS" --pos-mode rand --ordinal --invariant-numerics --modular --pointer "done" \
    --index-eval identity --type-dropout 0.15 \
    --dagger-rounds "$DAGGER_ROUNDS" --dagger-episodes "$DAGGER_EPISODES" --dagger-epochs "$DAGGER_EPOCHS" \
    --dagger-workers "$dw" --seed "$SEED" --device auto
  cp "$LOGDIR/train_${LOGTAG}.log" "$RUN/train.log"   # read by taiga_aggregate.py
}

# ----------------------------------------------------------------------------- export
stage_export() {
  cd "$REPO"
  [[ -f $RUN/last.pt ]] || die "no checkpoint at $RUN/last.pt; run train"
  timed export "$PY" -c "from freecad_s1.model.net import load_checkpoint, save_pretrained; \
save_pretrained('$RUN/hf', load_checkpoint('$RUN/last.pt'))"
  "$PY" -c "import json; c=json.load(open('$RUN/hf/config.json')); print({k:c[k] for k in list(c)[:20]})"
}

# ----------------------------------------------------------------------------- eval
ref_complete() {  # ref_complete <dir> — published-model baseline exists and covers every suite in $SUITES
  [[ -f $1/eval.json && -f $1/eval_perturb.json ]] || return 1
  "$PY" - "$1" $SUITES <<'EOF'
import json, sys
ref, suites = sys.argv[1], set(sys.argv[2:])
for f in ("eval.json", "eval_perturb.json"):
    have = {k.split("-")[0] for k in json.load(open(f"{ref}/{f}")).get("episodes", {})}
    if not suites <= have:
        print(f"baseline {f} lacks suites {sorted(suites - have)}; re-evaluating the published model")
        sys.exit(1)
EOF
}

stage_eval() {
  load_fc; cd "$REPO"
  [[ -d $RUN/hf ]] || die "no exported model at $RUN/hf; run export"
  local ref=runs/reference_hf
  if ! ref_complete "$ref"; then   # published model, once per machine
    mkdir -p "$ref"
    : > "$ref/crashes_eval.jsonl"; : > "$ref/crashes_eval_perturb.jsonl"
    timed eval_ref env S1_CRASH_LOG="$ref/crashes_eval.jsonl" "$PY" "$RUNNER" freecad_s1.evaluate --ckpt shhivv/taiga-s1 --data data/gen_test \
      --episodes 100 --suites $SUITES --workers "$WORKERS" --out "$ref/eval.json"
    timed eval_ref_perturb env S1_CRASH_LOG="$ref/crashes_eval_perturb.jsonl" "$PY" "$RUNNER" freecad_s1.evaluate --ckpt shhivv/taiga-s1 \
      --episodes 100 --perturb 0.2 --suites $SUITES --workers "$WORKERS" --out "$ref/eval_perturb.json"
  fi
  : > "$RUN/crashes_eval.jsonl"; : > "$RUN/crashes_eval_perturb.jsonl"
  timed eval env S1_CRASH_LOG="$RUN/crashes_eval.jsonl" "$PY" "$RUNNER" freecad_s1.evaluate --ckpt "$RUN/hf" --data data/gen_test \
    --episodes 100 --suites $SUITES --workers "$WORKERS" --out "$RUN/eval.json"
  timed eval_perturb env S1_CRASH_LOG="$RUN/crashes_eval_perturb.jsonl" "$PY" "$RUNNER" freecad_s1.evaluate --ckpt "$RUN/hf" \
    --episodes 100 --perturb 0.2 --suites $SUITES --workers "$WORKERS" --out "$RUN/eval_perturb.json"

  log "Published shhivv/taiga-s1"; "$PY" scripts/summarize.py "$ref/eval.json" "$ref/eval_perturb.json"
  log "Your model (${EXP:+$EXP, }seed $SEED)";   "$PY" scripts/summarize.py "$RUN/eval.json" "$RUN/eval_perturb.json"
  local c; c=$(cat "$RUN"/crashes_*.jsonl 2>/dev/null | wc -l || true)
  (( c == 0 )) || log "NOTE: $c FreeCAD worker crash(es) recovered in this run — see $RUN/crashes_*.jsonl"
}

# ----------------------------------------------------------------------------- calib
stage_calib() {
  load_fc; cd "$REPO"
  timed calibrate bash -c "S1_CRASH_LOG='$RUN/crashes_calib.jsonl' '$PY' '$RUNNER' scripts/calibrate.py --model '$RUN/hf' --workers $WORKERS > '$RUN/calibration.json'"
  cat "$RUN/calibration.json"
  log "Temperature written into $RUN/hf/config.json"
}

# ----------------------------------------------------------------------------- sweep
# Train and evaluate every seed in SEEDS with the same settings, one after another,
# then report the distribution. Seeds already evaluated are skipped (SWEEP_FORCE=1 redoes them).
stage_sweep() {
  [[ -f $ENVFILE ]] || die "run setup first (it needs sudo, so run it interactively)"
  local s rc=0
  log "Sweep ${EXP:-<default settings>}: seeds $SEEDS | stages $SWEEP_STAGES | runs in $GROUP"
  for s in $SEEDS; do
    if [[ -f $GROUP/seed$s/eval_perturb.json && ${SWEEP_FORCE:-0} != 1 ]]; then
      log "seed $s already evaluated — skipping"; continue
    fi
    # shellcheck disable=SC2086
    SEED=$s EXP=$EXP "$SELF" $SWEEP_STAGES || { log "seed $s FAILED"; rc=1; }
  done
  stage_aggregate
  (( rc == 0 )) || die "at least one seed failed"
}

# ----------------------------------------------------------------------------- aggregate
stage_aggregate() {
  [[ -f $AGG ]] || die "taiga_aggregate.py not found next to this script ($AGG)"
  cd "$REPO"
  "$PY" "$AGG" "$GROUP"
}

# ----------------------------------------------------------------------------- uninstall
# Removes only what this script created. Trained models, evals and logs are kept
# in $WORK/results-<timestamp>/ unless PURGE=1. System packages (FreeCAD, PPA) and
# uv are removed only if the marker file shows this script installed them;
# REMOVE_FREECAD=0 keeps FreeCAD regardless. UNINSTALL_YES=1 skips the prompt.
mark() { mkdir -p "$WORK"; grep -qsx "$1=1" "$MARKER" || echo "$1=1" >> "$MARKER"; }
marked() { grep -qsx "$1=1" "$MARKER"; }

stage_uninstall() {
  local purge=${PURGE:-0} hf=${HF_HOME:-$HOME/.cache/huggingface}/hub/models--shhivv--taiga-s1
  local items=() p keep="" sys=()
  # Safety: never operate on / or $HOME, and only on a directory this script populated
  [[ $(realpath -m "$WORK") != / && $(realpath -m "$WORK") != "$(realpath -m "$HOME")" ]] \
    || die "refusing to uninstall: WORK=$WORK is / or your home directory"
  [[ -d $REPO || -f $MARKER || -f $ENVFILE ]] || { log "Nothing installed at $WORK"; exit 0; }
  if pgrep -f freecad_s1 >/dev/null; then
    pgrep -af freecad_s1 | sed 's/^/  /'
    die "Taiga processes still running (above); stop them (or the tmux session) first"
  fi

  for p in "$REPO" "$WORK/fcenv" "$WORK/mamba" "$WORK/bin/micromamba" "$WORK/bin/freecad-python" "$ENVFILE" "$LOGDIR" "$hf"; do
    [[ -e $p ]] && items+=("$p")
  done
  if (( purge == 0 )) && { [[ -d $REPO/runs ]] || [[ -d $LOGDIR ]]; }; then
    keep=$WORK/results-$(date +%Y%m%d-%H%M%S)
  fi
  marked uv && sys+=("uv (~/.local/bin/uv, uvx; ~/.cache/uv; ~/.local/share/uv incl. its Python 3.11)")
  if [[ ${REMOVE_FREECAD:-1} == 1 ]]; then
    marked freecad_pkg && sys+=("apt package: $FREECAD_PKG")
    marked ppa && sys+=("apt source: $PPA")
  fi

  log "Uninstall plan for $WORK"
  for p in "${items[@]}"; do printf '  remove  %-8s %s\n' "$(du -sh "$p" 2>/dev/null | cut -f1)" "$p"; done
  for p in "${sys[@]}"; do printf '  remove  %s\n' "$p"; done
  if [[ -n $keep ]]; then echo "  keep    trained models, evals, manifests and logs -> $keep   (PURGE=1 to delete)"
  else echo "  results: DELETED (PURGE=1)"; fi
  if [[ ${UNINSTALL_YES:-0} != 1 ]]; then
    [[ -t 0 ]] || die "not a terminal; set UNINSTALL_YES=1 to confirm"
    read -r -p "Proceed? [y/N] " p; [[ $p == [yY]* ]] || { log "Aborted"; exit 0; }
  fi

  if [[ -n $keep ]]; then
    mkdir -p "$keep"
    [[ -d $REPO/runs ]] && mv "$REPO/runs" "$keep/runs"
    [[ -d $LOGDIR ]] && mv "$LOGDIR" "$keep/logs"
  fi
  for p in "${items[@]}"; do rm -rf -- "$p"; done
  rm -f -- "$WORK"/bin/freecad-python
  rmdir "$WORK/bin" 2>/dev/null || true

  if marked uv; then
    log "Removing uv"
    uv cache clean >/dev/null 2>&1 || true
    rm -rf -- "$HOME/.local/share/uv" "$HOME/.cache/uv"
    rm -f -- "$HOME/.local/bin/uv" "$HOME/.local/bin/uvx"
  fi
  if (( ${#sys[@]} )) && { marked freecad_pkg || marked ppa; } && [[ ${REMOVE_FREECAD:-1} == 1 ]]; then
    local SUDO=""; [[ $EUID -ne 0 ]] && SUDO=sudo
    if marked freecad_pkg; then log "Removing $FREECAD_PKG"; $SUDO apt-get remove -y "$FREECAD_PKG"; fi
    if marked ppa; then log "Removing $PPA"; $SUDO add-apt-repository --remove -y "$PPA"; $SUDO apt-get update -qq || true; fi
    log "Leftover dependencies can be reviewed with: sudo apt autoremove --dry-run"
  fi
  rm -f -- "$MARKER"
  rmdir "$WORK" 2>/dev/null || true
  log "Uninstalled.${keep:+ Results kept in $keep}"
  exit 0
}

# ----------------------------------------------------------------------------- manifest
write_manifest() {
  [[ -x $PY ]] || return 0
  mkdir -p "$RUN"
  "$PY" - "$RUN/manifest.json" <<EOF
import json, platform, subprocess, sys, torch
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
    except Exception: return None
json.dump({
  "script_version": "2026.10.05.1", "script": "taiga_repro_5060ti.sh", "seed": $SEED, "exp": "$EXP", "data_seed": ${DATA_SEED:-$SEED}, "data_scale": $DATA_SCALE, "epochs": $EPOCHS,
  "dagger_rounds": $DAGGER_ROUNDS, "dagger_episodes": $DAGGER_EPISODES, "dagger_epochs": $DAGGER_EPOCHS, "deterministic": "$DETERMINISTIC",
  "data_workers": $DATA_WORKERS, "workers": $WORKERS,
  "repo_commit": sh("git -C '$REPO' rev-parse HEAD"), "host": platform.node(), "arch": platform.machine(),
  "python": sys.version.split()[0], "torch": torch.__version__, "cuda": torch.version.cuda,
  "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
  "freecad": sh("PYTHONPATH='${FREECAD_LIB:-}' '${FREECAD_PYTHON:-false}' -c \"import FreeCAD;print('.'.join(FreeCAD.Version()[:3]))\""),
  "driver": sh("nvidia-smi --query-gpu=driver_version --format=csv,noheader"),
}, open(sys.argv[1], "w"), indent=2)
EOF
}

# ----------------------------------------------------------------------------- main
stages=("$@"); [[ ${#stages[@]} -gt 0 ]] || stages=(all)
[[ " ${stages[*]} " != *" uninstall "* || ${#stages[@]} -eq 1 ]] || die "run uninstall on its own"
[[ ${stages[0]} == all ]] && stages=(setup data train export eval calib)
log "Taiga-S1 repro | WORK=$WORK SEED=$SEED DATA_WORKERS=$DATA_WORKERS WORKERS=$WORKERS | stages: ${stages[*]}"
for s in "${stages[@]}"; do
  declare -F "stage_$s" >/dev/null || die "unknown stage '$s' (setup data train export eval calib all sweep aggregate uninstall)"
  "stage_$s"
done
# shellcheck disable=SC1090
[[ -f $ENVFILE ]] && source "$ENVFILE"
case " ${stages[*]} " in *" sweep "*|*" aggregate "*) ;; *) write_manifest ;; esac   # sweep children write their own
log "Done. Timings: $LOGDIR/timings_${LOGTAG}.tsv | outputs: $RUN"
