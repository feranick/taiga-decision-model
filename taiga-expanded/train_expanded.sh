#!/bin/bash
# train_expanded.sh — run the training scripts (../training) on the expanded model: upstream at
# the patches' BASE commit with the patch series applied, in its own WORK folder, with the
# expanded evaluation suites added. BASELINE=1 runs the same upstream commit unpatched
# (the reference for "no regression on the original suites").
# Version: 2026.10.13.2
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
EXT_SUITES="side plane outline revolve pattern edges large large_ext datum_z overhang turned mixed mixed_long"   # suites added by the patch series (keep in sync with freecad_s1/ext/sampler.SPLITS)
if [[ ${BASELINE:-0} == 1 ]]; then
  export WORK=${WORK:-$HOME/taiga-head} PATCHES=""
else
  # the patches extend the vocabulary, so the published weights only make sense on the baseline
  export WORK=${WORK:-$HOME/taiga-expanded} PATCHES=$HERE/patches REF_EVAL=${REF_EVAL:-0}
fi
# A WORK set up with an earlier, shorter series keeps working after new patches are added: its
# series is the first n patches of patches/ (patches are only ever appended), so those n are used.
if [[ ${BASELINE:-0} != 1 && -f $WORK/patches.sha ]]; then
  all=("$HERE"/patches/*.patch); want=$(cat "$WORK/patches.sha"); n_ok=""
  for ((n = ${#all[@]}; n >= 1; n--)); do
    [[ $(cat "${all[@]:0:n}" | sha256sum | cut -c1-12) == "$want" ]] && { n_ok=$n; break; }
  done
  if [[ -n $n_ok && $n_ok -lt ${#all[@]} ]]; then
    # one folder per series, named by its fingerprint, built aside and moved into place in one step:
    # runs started together (or still running) never see it half-written or deleted
    PATCHES=$WORK/patches_$want
    if [[ ! -d $PATCHES ]]; then
      tmp=$(mktemp -d "$WORK/.patches.XXXXXX")
      cp "${all[@]:0:n_ok}" "$HERE/patches/BASE" "$tmp"/
      mv -T "$tmp" "$PATCHES" 2>/dev/null || rm -rf "$tmp"   # another run got there first: use its copy
    fi
    echo "note: $WORK was set up with patches 1-$n_ok of ${#all[@]}; using those (a newer series needs a new WORK)"
  fi
fi
# patch 0016 adds mixed_at / mixed_at_long: only for a WORK that has them (or is not set up yet),
# so work folders made with an earlier series keep evaluating as before
ev=$WORK/taiga-s1/freecad_s1/evaluate.py
if [[ ${BASELINE:-0} != 1 ]] && { [[ ! -f $ev ]] || grep -q '"mixed_at"' "$ev"; }; then
  EXT_SUITES+=" mixed_at mixed_at_long"
fi
export SUITES=${SUITES:-"iid comp comp2 comp3 len len2 len3 len4 len5 len6"}
[[ ${BASELINE:-0} == 1 ]] || SUITES="$SUITES $EXT_SUITES"
echo "taiga-expanded | upstream $REPO_REF | WORK=$WORK | patches: ${PATCHES:-none} | suites: $SUITES"
exec bash "$REPO/training/$script" "$@"
