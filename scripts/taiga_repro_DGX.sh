#!/usr/bin/env bash
# =============================================================================
# taiga_repro_DGX.sh — reproduce Taiga-S1 from scratch on an NVIDIA DGX Spark
# Version: 2026.10.02.3
#
# Pipeline (mirrors upstream scripts/train_final.sh + final_eval.sh):
#   setup   : uv + Python 3.11 venv, PyTorch (CUDA 13, aarch64), FreeCAD via
#             conda-forge (micromamba), clone + install shhivv/taiga-s1, tests
#   data    : synthetic datagen in headless FreeCAD (train 4k/8k/12k, test 300/600/900)
#   train   : SFT (4 epochs) + 2 DAgger rounds, all generalization tricks on
#   export  : checkpoint -> HF layout (model.safetensors + config.json)
#   eval    : your model AND the published shhivv/taiga-s1 on identical suites,
#             clean and with 20% injected random actions
#   calib   : fit softmax temperature on held-out on-policy states
#   all     : everything above, in order
#   uninstall: remove everything setup created (keeps results unless PURGE=1)
#
# Usage:
#   ./taiga_repro_DGX.sh all                 # full run, seed 2 (upstream's seed)
#   SEED=12 ./taiga_repro_DGX.sh all         # independent replicate (e.g. 2nd Spark)
#   ./taiga_repro_DGX.sh train eval          # run selected stages
#
# Using both Sparks: run the full pipeline on each with a different SEED.
# The model is 1.2M params, so multi-node training buys nothing; two seeds
# instead give you seed-to-seed variance, which matters for the ablations.
# The test set (seed 3, 8 workers) is identical on both machines.
#
# Tunables (env vars):
#   WORK=~/taiga        working directory (repo, venv, FreeCAD env, data, runs)
#   SEED=2              training-data + model seed (upstream used 2)
#   DATA_WORKERS=8      datagen processes; 8 reproduces upstream's exact shards
#                       (episode RNG = seed*1000 + shard). Raise to ~18 for speed.
#   WORKERS=18          FreeCAD workers for DAgger / eval / calibration
#   REPO_REF=a6e81d3    upstream commit to pin (verified 2026-10-02)
#   FREECAD_SPEC="freecad>=1.0"   conda-forge spec (upstream evaluated on 1.1)
#   FREECAD_PYTHON / FREECAD_LIB  set both to use an existing FreeCAD instead
#   TORCH_INDEX=https://download.pytorch.org/whl/cu130
# =============================================================================
set -euo pipefail

WORK=${WORK:-$HOME/taiga}
SEED=${SEED:-2}
DATA_WORKERS=${DATA_WORKERS:-8}
WORKERS=${WORKERS:-$(( $(nproc) > 4 ? $(nproc) - 2 : 2 ))}
REPO_URL=${REPO_URL:-https://github.com/shhivv/taiga-s1.git}
REPO_REF=${REPO_REF:-a6e81d3}
FREECAD_SPEC=${FREECAD_SPEC:-"freecad>=1.0"}
TORCH_INDEX=${TORCH_INDEX:-https://download.pytorch.org/whl/cu130}
TEST_SEED=3
FREECAD_PKG=${FREECAD_PKG:-freecad}
PPA=${PPA:-}
TEST_WORKERS=8          # fixed so the test set is identical across machines
SUITES="iid comp comp2 comp3 len len2 len3"

REPO=$WORK/taiga-s1
VENV=$REPO/.venv
PY=$VENV/bin/python
FCENV=$WORK/fcenv
MAMBA=$WORK/bin/micromamba
RUN=$REPO/runs/seed${SEED}
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
  "$@" 2>&1 | tee -a "$LOGDIR/${name}_seed${SEED}.log"
  local rc=${PIPESTATUS[0]}
  set -e
  [[ $rc -eq 0 ]] || die "$name failed (exit $rc) — see $LOGDIR/${name}_seed${SEED}.log"
  printf '%s\t%s\t%ds\n' "$(date -Is)" "$name" $((SECONDS - t0)) >> "$LOGDIR/timings_seed${SEED}.tsv"
}
# shellcheck disable=SC1090
load_fc() { [[ -f $ENVFILE ]] && source "$ENVFILE"; [[ -n ${FREECAD_PYTHON:-} && -n ${FREECAD_LIB:-} ]] || die "FreeCAD not configured; run setup"; }

# ----------------------------------------------------------------------------- setup
stage_setup() {
  [[ $(uname -m) == aarch64 ]] || log "Note: not aarch64 ($(uname -m)); script targets DGX Spark but should still work"
  for c in git curl tar bzip2; do command -v $c >/dev/null || die "missing '$c' (sudo apt install $c)"; done

  # uv + Python 3.11
  command -v uv >/dev/null || { log "Installing uv"; curl -LsSf https://astral.sh/uv/install.sh | sh; mark uv; }

  # Repo, pinned
  if [[ ! -d $REPO/.git ]]; then
    log "Cloning $REPO_URL"; git clone "$REPO_URL" "$REPO"
  fi
  git -C "$REPO" fetch -q origin
  git -C "$REPO" checkout -q "$REPO_REF"
  log "Repo at $(git -C "$REPO" rev-parse --short HEAD)"

  # Venv: torch first (CUDA 13 aarch64 wheels for GB10 / sm_121), then the package
  [[ -x $PY ]] || uv venv --python 3.11 "$VENV"
  if ! "$PY" -c "import torch" 2>/dev/null; then
    log "Installing PyTorch from $TORCH_INDEX"
    uv pip install --python "$PY" --index-url "$TORCH_INDEX" torch \
      || { log "CUDA wheel failed; falling back to PyPI torch"; uv pip install --python "$PY" torch; }
  fi
  uv pip install --python "$PY" -e "${REPO}[dev]"
  "$PY" - <<'EOF'
import torch
print("torch", torch.__version__, "| CUDA build", torch.version.cuda, "| cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0), "capability", torch.cuda.get_device_capability(0))
    x = torch.randn(256, 256, device="cuda"); print("matmul ok:", float((x @ x).sum()) == float((x @ x).sum()))
else:
    print("WARNING: training will run on CPU (fine for a 1.2M-param model, just slower)")
EOF

  # FreeCAD (headless) from conda-forge, unless the user supplied one
  if [[ -z ${FREECAD_PYTHON:-} || -z ${FREECAD_LIB:-} ]]; then
    if [[ ! -x $MAMBA ]]; then
      log "Installing micromamba"
      local arch; arch=$([[ $(uname -m) == aarch64 ]] && echo linux-aarch64 || echo linux-64)
      curl -Ls "https://micro.mamba.pm/api/micromamba/$arch/latest" | tar -xj -C "$WORK" bin/micromamba
    fi
    if [[ ! -d $FCENV ]]; then
      log "Creating FreeCAD env ($FREECAD_SPEC) — takes a few minutes"
      MAMBA_ROOT_PREFIX=$WORK/mamba "$MAMBA" create -y -p "$FCENV" -c conda-forge "$FREECAD_SPEC"
    fi
    local so; so=$(find "$FCENV" -name 'FreeCAD.so' -path '*lib*' 2>/dev/null | head -1)
    [[ -n $so ]] || die "FreeCAD.so not found under $FCENV"
    FREECAD_PYTHON=$FCENV/bin/python
    FREECAD_LIB=$(dirname "$so")
  fi
  printf 'export FREECAD_PYTHON=%q\nexport FREECAD_LIB=%q\n' "$FREECAD_PYTHON" "$FREECAD_LIB" > "$ENVFILE"
  load_fc

  log "Checking FreeCAD import (Part, Sketcher, PartDesign)"
  PYTHONPATH=$FREECAD_LIB "$FREECAD_PYTHON" -c \
    "import FreeCAD, Part, Sketcher; FreeCAD.newDocument('t').addObject('PartDesign::Body','B'); print('FreeCAD', '.'.join(FreeCAD.Version()[:3]))" \
    || die "FreeCAD import failed"

  # Upstream tests silently SKIP the FreeCAD ones if FreeCAD isn't found, so we checked above first.
  cd "$REPO"
  timed tests "$PY" -m pytest -q
}

# ----------------------------------------------------------------------------- data
stage_data() {
  load_fc; cd "$REPO"
  local train=data/gen_train_seed${SEED} test=data/gen_test
  if compgen -G "$train/*.jsonl.gz" >/dev/null; then
    log "Training data exists in $train — skipping (delete it to regenerate)"
  else
    timed datagen_train "$PY" -m freecad_s1.datagen --out "$train" \
      --episodes 4000 8000 12000 --workers "$DATA_WORKERS" --seed "$SEED"
  fi
  if compgen -G "$test/*.jsonl.gz" >/dev/null; then
    log "Test data exists in $test — skipping"
  else
    timed datagen_test "$PY" -m freecad_s1.datagen --out "$test" \
      --episodes 300 600 900 --workers "$TEST_WORKERS" --seed "$TEST_SEED"
  fi
  "$PY" - "$train" <<'EOF'
import gzip, sys, pathlib
n = sum(1 for p in pathlib.Path(sys.argv[1]).glob("*.jsonl.gz") for l in gzip.open(p, "rt") if '"state"' in l)
print(f"labeled states: {n:,}  (upstream: ~590k)")
EOF
}

# ----------------------------------------------------------------------------- train
stage_train() {
  load_fc; cd "$REPO"
  mkdir -p "$RUN"
  # Same flags as upstream train_final.sh, plus EXTRA="--type-dropout 0.15" from the README.
  timed train "$PY" -m freecad_s1.train_sft --data "data/gen_train_seed${SEED}" --out "$RUN" \
    --epochs 4 --pos-mode rand --ordinal --invariant-numerics --modular --pointer "done" \
    --index-eval identity --type-dropout 0.15 \
    --dagger-rounds 2 --dagger-episodes 400 --dagger-epochs 2 --dagger-workers "$WORKERS" \
    --seed "$SEED" --device auto
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
stage_eval() {
  load_fc; cd "$REPO"
  [[ -d $RUN/hf ]] || die "no exported model at $RUN/hf; run export"
  local ref=runs/reference_hf
  if [[ ! -f $ref/eval.json ]]; then   # published model, once per machine
    mkdir -p "$ref"
    timed eval_ref "$PY" -m freecad_s1.evaluate --ckpt shhivv/taiga-s1 --data data/gen_test \
      --episodes 100 --suites $SUITES --workers "$WORKERS" --out "$ref/eval.json"
    timed eval_ref_perturb "$PY" -m freecad_s1.evaluate --ckpt shhivv/taiga-s1 \
      --episodes 100 --perturb 0.2 --suites $SUITES --workers "$WORKERS" --out "$ref/eval_perturb.json"
  fi
  timed eval "$PY" -m freecad_s1.evaluate --ckpt "$RUN/hf" --data data/gen_test \
    --episodes 100 --suites $SUITES --workers "$WORKERS" --out "$RUN/eval.json"
  timed eval_perturb "$PY" -m freecad_s1.evaluate --ckpt "$RUN/hf" \
    --episodes 100 --perturb 0.2 --suites $SUITES --workers "$WORKERS" --out "$RUN/eval_perturb.json"

  log "Published shhivv/taiga-s1"; "$PY" scripts/summarize.py "$ref/eval.json" "$ref/eval_perturb.json"
  log "Your model (seed $SEED)";   "$PY" scripts/summarize.py "$RUN/eval.json" "$RUN/eval_perturb.json"
}

# ----------------------------------------------------------------------------- calib
stage_calib() {
  load_fc; cd "$REPO"
  timed calibrate bash -c "'$PY' scripts/calibrate.py --model '$RUN/hf' --workers $WORKERS > '$RUN/calibration.json'"
  cat "$RUN/calibration.json"
  log "Temperature written into $RUN/hf/config.json"
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
  "script_version": "2026.10.02.3", "script": "taiga_repro_DGX.sh", "seed": $SEED, "data_workers": $DATA_WORKERS, "workers": $WORKERS,
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
  declare -F "stage_$s" >/dev/null || die "unknown stage '$s' (setup data train export eval calib all uninstall)"
  "stage_$s"
done
# shellcheck disable=SC1090
[[ -f $ENVFILE ]] && source "$ENVFILE"
write_manifest
log "Done. Timings: $LOGDIR/timings_seed${SEED}.tsv | outputs: $RUN"
