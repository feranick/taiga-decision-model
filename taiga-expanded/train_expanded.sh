#!/bin/bash
# train_expanded.sh — run the training scripts (../training) on the expanded model: upstream at
# the patches' BASE commit with the patch series applied, in its own WORK folder, with the
# expanded evaluation suites added. BASELINE=1 runs the same upstream commit unpatched
# (the reference for "no regression on the original suites").
# Version: 2026.10.05.2
#
# Usage:   ./train_expanded.sh <dgx|5060ti|quadro> <stages...>
#   e.g.   ./train_expanded.sh 5060ti setup
#          DATA_SEED=2 ./train_expanded.sh 5060ti sweep
#          BASELINE=1 ./train_expanded.sh 5060ti setup            # unpatched upstream at the same commit
# Env:     WORK (default ~/taiga-expanded, or ~/taiga-head with BASELINE=1); every variable of the
#          training scripts (SEED, SEEDS, EPOCHS, DATA_SCALE, DETERMINISTIC, ...) passes through.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
machine=$(tr "[:upper:]" "[:lower:]" <<<"${1:-}"); shift || true   # dgx, DGX, ... all work
case $machine in
  dgx) script=taiga_repro_DGX.sh ;;
  5060ti) script=taiga_repro_5060ti.sh ;;
  quadro) script=taiga_repro_Quadro6000.sh ;;
  *) echo "usage: $0 <dgx|5060ti|quadro> <stages...>"; exit 1 ;;
esac
[[ -f $HERE/patches/BASE ]] || { echo "error: $HERE/patches/BASE missing"; exit 1; }
export REPO_REF=${REPO_REF:-$(cut -c1-12 "$HERE/patches/BASE")}
EXT_SUITES="side"   # suites added by the patch series (keep in sync with freecad_s1/ext/sampler.SPLITS)
if [[ ${BASELINE:-0} == 1 ]]; then
  export WORK=${WORK:-$HOME/taiga-head} PATCHES=""
else
  export WORK=${WORK:-$HOME/taiga-expanded} PATCHES=$HERE/patches
fi
export SUITES=${SUITES:-"iid comp comp2 comp3 len len2 len3 len4 len5 len6"}
[[ ${BASELINE:-0} == 1 ]] || SUITES="$SUITES $EXT_SUITES"
echo "taiga-expanded | upstream $REPO_REF | WORK=$WORK | patches: ${PATCHES:-none} | suites: $SUITES"
exec bash "$REPO/training/$script" "$@"
