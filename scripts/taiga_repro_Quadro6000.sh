#!/usr/bin/env bash
# =============================================================================
# taiga_repro_Quadro6000.sh — reproduce Taiga-S1 from scratch on a dual Quadro RTX 6000
# (Turing, sm_75, 2 x 24 GB) workstation, Ubuntu 24.04 or 26.04, driver 595-open
# Version: 2026.10.02.3
#
# Same pipeline as the Spark/mochi scripts; differences:
#   - FreeCAD from your PPA matching the release: noble -> bleedingedge/noble-bleed,
#     resolute -> bleedingedge/resolute-bleed (discovery + worker launcher as on mochi)
#   - PyTorch build is chosen by *testing*: cu130, then cu128, then cu126, keeping
#     the first one whose kernels actually run on every GPU (Turing support differs
#     between builds; the pipeline is plain FP32, so no bf16/Ampere requirement)
#   - GPU=<n> pins a run to one GPU (CUDA_VISIBLE_DEVICES)
#   - "pair": two independent seeds in parallel, one per GPU, CPU workers split
#
# Stages:  setup data train export eval calib | all | pair | uninstall
# Usage:   ./taiga_repro_Quadro6000.sh setup              # interactive (sudo for apt)
#          ./taiga_repro_Quadro6000.sh pair               # seeds 2 and 12 on GPU 0 / 1
#          PAIR_SEEDS="2 22" ./taiga_repro_Quadro6000.sh pair
#          GPU=1 SEED=12 ./taiga_repro_Quadro6000.sh data train export eval calib
#
# The model is 1.2M params, so splitting one training run across both GPUs buys
# nothing; one seed per GPU gives you seed variance in the same wall time.
# Concurrent runs are safe: the shared test set and the published-model baseline
# are generated once under a lock, and each run reads its own linked copy of the
# test shards (featurization caches are written next to the shards).
#
# Tunables (env vars):
#   WORK=~/taiga  SEED=2  GPU=  DATA_WORKERS=8  WORKERS=nproc-2  REPO_REF=a6e81d3
#   PAIR_SEEDS="2 12"    PPA=(auto by release)   FREECAD_PKG=freecad
#   TORCH_INDEX=...      force one PyTorch wheel index (skips probing)
#   FREECAD_PYTHON / FREECAD_LIB   set both to skip FreeCAD discovery
# =============================================================================
set -euo pipefail

WORK=${WORK:-$HOME/taiga}
SEED=${SEED:-2}
DATA_WORKERS=${DATA_WORKERS:-8}
WORKERS=${WORKERS:-$(( $(nproc) > 4 ? $(nproc) - 2 : 2 ))}
REPO_URL=${REPO_URL:-https://github.com/shhivv/taiga-s1.git}
REPO_REF=${REPO_REF:-a6e81d3}
# shellcheck disable=SC1091
. /etc/os-release
case ${VERSION_CODENAME:-} in
  noble)    DEFAULT_PPA=ppa:bleedingedge/noble-bleed ;;
  resolute) DEFAULT_PPA=ppa:bleedingedge/resolute-bleed ;;
  *)        DEFAULT_PPA=ppa:bleedingedge/resolute-bleed ;;
esac
PPA=${PPA:-$DEFAULT_PPA}
PAIR_SEEDS=${PAIR_SEEDS:-"2 12"}
GPU=${GPU:-}
[[ -n $GPU ]] && export CUDA_VISIBLE_DEVICES=$GPU
FREECAD_PKG=${FREECAD_PKG:-freecad}
TEST_SEED=3
TEST_WORKERS=8          # fixed so the test set is identical across machines
SUITES="iid comp comp2 comp3 len len2 len3"

REPO=$WORK/taiga-s1
VENV=$REPO/.venv
PY=$VENV/bin/python
FCWRAP=$WORK/bin/freecad-python
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
gpu_probe() {  # torch imports, sees every GPU, and real kernels run on each
  "$PY" - <<'EOF' 2>&1 | sed 's/^/  /'
import subprocess, sys
try:
    import torch
except ImportError:
    sys.exit(1)
want = 0
try:
    want = len(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout.strip().splitlines())
except FileNotFoundError:
    pass
print("torch", torch.__version__, "| CUDA build", torch.version.cuda, "| arch list", torch.cuda.get_arch_list())
if want == 0:
    sys.exit(0)  # CPU-only machine
n = torch.cuda.device_count()
if n == 0:
    print("cuda not available"); sys.exit(1)
for i in range(n):
    d = torch.device("cuda", i)
    try:
        x = torch.randn(512, 512, device=d)
        y = torch.nn.functional.softmax(x @ x.T, dim=-1)
        layer = torch.nn.TransformerEncoderLayer(128, 4, 384, batch_first=True).to(d)
        z = layer(torch.randn(8, 64, 128, device=d)); z.sum().backward()
        torch.cuda.synchronize(d)
        assert torch.isfinite(y).all() and torch.isfinite(z).all()
        print(f"  cuda:{i} {torch.cuda.get_device_name(i)} sm_{''.join(map(str, torch.cuda.get_device_capability(i)))}: OK")
    except Exception as e:
        print(f"  cuda:{i}: FAILED ({type(e).__name__}: {e})"); sys.exit(1)
EOF
  return "${PIPESTATUS[0]}"
}

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
    if ! grep -rqs "${PPA#ppa:}" /etc/apt/sources.list /etc/apt/sources.list.d/; then
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
# Generated by taiga_repro_Quadro6000.sh — FreeCAD worker interpreter
export QT_QPA_PLATFORM=offscreen
export PYTHONPATH="\${PYTHONPATH:+\$PYTHONPATH:}${libdir}${mods}"
exec $sysp "\$@"
EOF
  chmod +x "$FCWRAP"
  FREECAD_PYTHON=$FCWRAP
  FREECAD_LIB=$libdir
}

stage_setup() {
  case ${VERSION_CODENAME:-} in noble|resolute) log "Ubuntu $VERSION_CODENAME -> $PPA" ;;
    *) log "Note: untested release ${VERSION_CODENAME:-unknown}; using $PPA" ;; esac
  for c in git curl; do command -v $c >/dev/null || die "missing '$c' (sudo apt install $c)"; done

  command -v uv >/dev/null || { log "Installing uv"; curl -LsSf https://astral.sh/uv/install.sh | sh; mark uv; }

  if [[ ! -d $REPO/.git ]]; then log "Cloning $REPO_URL"; git clone "$REPO_URL" "$REPO"; fi
  git -C "$REPO" fetch -q origin
  git -C "$REPO" checkout -q "$REPO_REF"
  log "Repo at $(git -C "$REPO" rev-parse --short HEAD)"

  # PyTorch: keep the first build whose kernels actually run on every visible GPU
  [[ -x $PY ]] || uv venv --python 3.11 "$VENV"
  local cands=() idx ok=""
  if [[ -n ${TORCH_INDEX:-} ]]; then cands=("$TORCH_INDEX")
  elif command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then
    log "GPUs:"; nvidia-smi --query-gpu=index,name,compute_cap,memory.total,driver_version --format=csv,noheader
    local drv; drv=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 | cut -d. -f1)
    (( drv >= 580 )) && cands+=(https://download.pytorch.org/whl/cu130)
    (( drv >= 570 )) && cands+=(https://download.pytorch.org/whl/cu128)
    cands+=(https://download.pytorch.org/whl/cu126)
  else
    cands=(https://download.pytorch.org/whl/cpu); log "No NVIDIA GPU found; using CPU PyTorch"
  fi
  for idx in "${cands[@]}"; do
    if gpu_probe; then ok="existing install"; log "Existing torch works on all GPUs"; break; fi
    log "Trying PyTorch from $idx"
    uv pip install --python "$PY" --reinstall-package torch --index-url "$idx" torch || continue
    if gpu_probe; then ok=$idx; break; fi
  done
  [[ -n $ok ]] || die "no PyTorch build ran on these GPUs (tried: ${cands[*]})"
  log "Using PyTorch from $ok"
  uv pip install --python "$PY" -e "${REPO}[dev]"   # torch already satisfied; not replaced

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
  local train=data/gen_train_seed${SEED} test=data/gen_test
  if compgen -G "$train/*.jsonl.gz" >/dev/null; then
    log "Training data exists in $train — skipping (delete it to regenerate)"
  else
    timed datagen_train "$PY" -m freecad_s1.datagen --out "$train" \
      --episodes 4000 8000 12000 --workers "$DATA_WORKERS" --seed "$SEED"
  fi
  mkdir -p data
  exec 9>"data/.gen_test.lock"; flock 9            # one generator; other runs wait
  if [[ -f $test/.complete ]]; then
    log "Test data exists in $test — skipping"
  else
    rm -rf "$test"
    timed datagen_test "$PY" -m freecad_s1.datagen --out "$test" \
      --episodes 300 600 900 --workers "$TEST_WORKERS" --seed "$TEST_SEED"
    touch "$test/.complete"
  fi
  exec 9>&-
  # per-run view of the test shards (hard links), so featurization caches don't collide
  local mine=data/gen_test_seed${SEED}
  mkdir -p "$mine"; ln -f "$test"/*.jsonl.gz "$mine"/
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
  mkdir -p "$ref"
  exec 8>"$ref/.lock"; flock 8                       # baseline computed once, shared
  if [[ ! -f $ref/eval_perturb.json ]]; then   # published model, once per machine
    timed eval_ref "$PY" -m freecad_s1.evaluate --ckpt shhivv/taiga-s1 --data data/gen_test \
      --episodes 100 --suites $SUITES --workers "$WORKERS" --out "$ref/eval.json"
    timed eval_ref_perturb "$PY" -m freecad_s1.evaluate --ckpt shhivv/taiga-s1 \
      --episodes 100 --perturb 0.2 --suites $SUITES --workers "$WORKERS" --out "$ref/eval_perturb.json"
  fi
  exec 8>&-
  timed eval "$PY" -m freecad_s1.evaluate --ckpt "$RUN/hf" --data "data/gen_test_seed${SEED}" \
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

# ----------------------------------------------------------------------------- pair
stage_pair() {
  [[ -f $ENVFILE ]] || die "run setup first (it needs sudo, so run it interactively)"
  local seeds ngpu w i=0 s pids=() rc=0
  read -r -a seeds <<<"$PAIR_SEEDS"
  ngpu=$(nvidia-smi -L 2>/dev/null | wc -l)
  (( ngpu >= ${#seeds[@]} )) || die "${#seeds[@]} seeds but only $ngpu GPU(s)"
  w=$(( WORKERS / ${#seeds[@]} )); (( w >= 2 )) || w=2
  log "Launching seeds ${seeds[*]} on GPUs 0..$(( ${#seeds[@]} - 1 )), $w FreeCAD workers each"
  for s in "${seeds[@]}"; do
    SEED=$s GPU=$i WORKERS=$w "$0" data train export eval calib > "$LOGDIR/pair_seed${s}.log" 2>&1 &
    pids+=($!); log "  seed $s -> GPU $i (pid $!, log $LOGDIR/pair_seed${s}.log)"
    i=$(( i + 1 ))
  done
  for i in "${!pids[@]}"; do
    if wait "${pids[$i]}"; then log "seed ${seeds[$i]} finished"
    else log "seed ${seeds[$i]} FAILED — see $LOGDIR/pair_seed${seeds[$i]}.log"; rc=1; fi
  done
  (( rc == 0 )) || die "at least one run failed"
  cd "$REPO"
  for s in "${seeds[@]}"; do log "Seed $s"; "$PY" scripts/summarize.py "runs/seed$s/eval.json" "runs/seed$s/eval_perturb.json"; done
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
  "script_version": "2026.10.02.3", "script": "taiga_repro_Quadro6000.sh", "seed": $SEED, "gpu": "${GPU:-all}", "data_workers": $DATA_WORKERS, "workers": $WORKERS,
  "repo_commit": sh("git -C '$REPO' rev-parse HEAD"), "host": platform.node(), "arch": platform.machine(),
  "python": sys.version.split()[0], "torch": torch.__version__, "cuda": torch.version.cuda,
  "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
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
  declare -F "stage_$s" >/dev/null || die "unknown stage '$s' (setup data train export eval calib all pair uninstall)"
  "stage_$s"
done
# shellcheck disable=SC1090
[[ -f $ENVFILE ]] && source "$ENVFILE"
[[ " ${stages[*]} " == *" pair "* ]] || write_manifest   # pair children write their own
log "Done. Timings: $LOGDIR/timings_seed${SEED}.tsv | outputs: $RUN"
