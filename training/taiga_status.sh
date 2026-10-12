#!/bin/bash
# taiga_status.sh — what is running and how far it is, on this machine (read-only).
# Version: 2026.10.12.5
#
# Usage: ~/Software/taiga-decision-model/training/taiga_status.sh
# Looks at every ~/taiga-expanded* work folder: setup tests, sweeps (seeds finished, current
# stage, aggregate), S 80 builds and scoring, DeepCAD builds and scoring; plus tmux sessions.
shopt -s nullglob

say() { printf '%s\n' "$*"; }
newest() { local f best=""; for f in "$@"; do [[ -z $best || $f -nt $best ]] && best=$f; done; printf '%s' "$best"; }
count_lines() { local c; c=$(grep -cE "$1" "$2" 2>/dev/null); echo "${c:-0}"; }   # grep -c prints 0 itself (exit 1)

say "== $(hostname)  $(date '+%Y-%m-%d %H:%M')"
say "tmux: $(tmux ls 2>/dev/null | cut -d: -f1 | tr '\n' ' ' || true)"
[[ -z $(tmux ls 2>/dev/null) ]] && say "      (no tmux sessions)"

for W in "$HOME"/taiga-expanded*; do
  [[ -d $W/taiga-s1 || -d $W/deepcad_test || -d $W/deepcad_train ]] || continue
  say ""
  say "-- $W"
  # setup tests
  t=$(newest "$W"/logs/tests_*.log)
  if [[ -n $t ]]; then
    r=$(grep -E '[0-9]+ (passed|failed)' "$t" | tail -n 1)
    say "   setup tests: ${r:-running}"
  fi
  # sweeps: one line per experiment folder
  for G in "$W"/taiga-s1/runs/*/; do
    G=${G%/}; exp=$(basename "$G")
    [[ $exp == reference_hf || $exp == seed* ]] && continue
    done_files=("$G"/seed*/eval_perturb.json); n_done=${#done_files[@]}
    line="   sweep $exp: $n_done/5 seeds finished"
    if (( n_done < 5 )); then
      last=$(newest "$W"/logs/sweep_"${exp}"_seed*.log "$W"/logs/sweep_"${exp}".log)
      [[ -n $last ]] && line+=" | now: $(grep -a -E '^\[[0-9:]+\]|^\[(sft|dagger)' "$last" | tail -n 1 | cut -c1-80)"
    fi
    say "$line"
  done
  # sweeps still generating data (no run folder yet)
  declare -A seen=()
  for L in "$W"/logs/sweep_*.log; do   # sweep_<exp>_seed<N>.log (parallel seeds) or sweep_<exp>.log
    exp=$(basename "$L" .log); exp=${exp#sweep_}; exp=${exp%_seed[0-9]*}
    [[ -n ${seen[$exp]:-} || -d $W/taiga-s1/runs/$exp ]] && continue
    seen[$exp]=1
    now=$(grep -a -E '^\[[0-9:]+\]' "$(newest "$W"/logs/sweep_"${exp}"_seed*.log "$W"/logs/sweep_"${exp}".log)" | tail -n 1 | cut -c1-80)
    say "   sweep $exp: starting | now: ${now:-?}"
  done
  unset seen
  # S 80
  for S in "$W"/s80*/; do
    S=${S%/}
    built=0; for f in "$S"/seed*.log; do built=$((built + $(count_lines '^s80_\w+: (SUCCESS|FAIL|CRASH)' "$f"))); done
    if [[ -f $S/eval_s80.log ]] && grep -q "^whole pump" "$S/eval_s80.log"; then
      say "   $(basename "$S"): scored | whole pump: $(grep '^whole pump' "$S/eval_s80.log" | cut -c37- | tr -s ' ')"
    else
      say "   $(basename "$S"): $built builds done (11 per model)"
    fi
  done
  # DeepCAD
  for D in "$W"/deepcad_*/; do
    D=${D%/}; name=$(basename "$D")
    if [[ -f $D/verify.log && ! -f $D/eval.log ]]; then
      say "   $name: verify $(grep -c ' teacher ' "$D/verify.log") parts$( [[ -f $D/goals_${name#deepcad_}_verified.json ]] && echo ' (done)')"
    fi
    nb=0; for f in "$D"/seed*.log; do nb=$((nb + $(count_lines '^dc_[0-9_]+: (SUCCESS|FAIL|CRASH)' "$f"))); done
    (( nb > 0 )) && say "   $name: $nb model builds"
    if [[ -f $D/eval.log ]]; then
      p1=$(grep -v 'vs teacher' "$D/eval.log" | grep -c ' teacher ')
      p2=$(( $(grep -c 'vs teacher' "$D/eval.log") / 5 ))
      say "   $name scoring: phase 1 $p1, phase 2 $p2 (of the goal count)$(grep -q '^Share of' "$D/eval.log" && echo ' | done')"
    fi
  done
done

# aggregates written by the sweep commands
for A in "$HOME"/agg_*.txt; do
  say ""
  say "aggregate $(basename "$A"): $(grep -E '^  len6-L9' "$A" | head -n 1 | tr -s ' ' | cut -c1-60)"
done
