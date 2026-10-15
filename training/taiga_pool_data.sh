#!/bin/bash
# taiga_pool_data.sh — combine training-data folders (shards) into one folder to train on.
# Version: 2026.10.15.1
#
# Usage: taiga_pool_data.sh OUT_DIR SRC_DIR [SRC_DIR ...]
#   e.g. taiga_pool_data.sh ~/pool/prod1 ~/taiga-expanded-0015/taiga-s1/data/gen_train_seed21_x4_kh \
#                                        ~/incoming/spark1_seed22 ~/incoming/quadro_seed23
#
# Each SRC_DIR is a data folder written by the `data` stage (shardNNN.jsonl.gz). Generate the
# parts on several machines with different seeds, e.g. on each machine
#   DATA_SEED=<own seed> DATA_SCALE=<n> <same TAIGA_* settings> ./train_expanded.sh <machine> data
# copy the folders to the training machine, then pool them here. Every shard is checked
# (gzip -t) and linked (or copied, across file systems) as s<k>_shardNNN.jsonl.gz; pool.json
# records the sources and the number of labelled states. Train on the pool with
#   TRAIN_DATA_DIR=OUT_DIR ... ./train_expanded.sh <m> sweep
# Pool only data made with the same patch series and TAIGA_* settings (the data folder names
# carry those settings).
# FRACTION=0.5 takes only the first half of each source's shards (data-scaling runs).
# SPLIT=n writes each shard as n smaller ones, whole episodes dealt out in turn. Training loads
# shards in parallel and one loader process needs about 20 GB per million states in its shard, so
# the large shards of a machine with few generator workers (e.g. 560k states each on the 5060 Ti)
# ran it out of memory; aim for at most ~150k states per shard on a 40 GB machine.
set -euo pipefail
(( $# >= 2 )) || { sed -n '2,25p' "$0"; exit 1; }
frac=${FRACTION:-1}
split=${SPLIT:-1}
out=$1; shift
[[ -e $out ]] && { echo "error: $out exists; choose a new folder" >&2; exit 1; }
mkdir -p "$out"
k=0; total=0; shards=0
srcs_json=""
for src in "$@"; do
  src=$(realpath "$src")
  files=("$src"/*.jsonl.gz)
  [[ -e ${files[0]} ]] || { echo "error: no *.jsonl.gz shards in $src" >&2; rm -rf "$out"; exit 1; }
  [[ -f $src/.incomplete ]] && { echo "error: $src is marked incomplete (generation did not finish)" >&2; rm -rf "$out"; exit 1; }
  keep=$(awk -v n="${#files[@]}" -v f="$frac" 'BEGIN { k = int(n * f + 0.5); print (k < 1 ? 1 : (k > n ? n : k)) }')
  files=("${files[@]:0:keep}")
  n=0
  for f in "${files[@]}"; do
    gzip -t "$f" 2>/dev/null || { echo "error: damaged shard $f" >&2; rm -rf "$out"; exit 1; }
    dst="$out/s${k}_$(basename "$f")"
    if (( split > 1 )); then  # whole episodes (a "goal" record, then its states) dealt out in turn
      python3 - "$f" "${dst%.jsonl.gz}" "$split" <<'PYEOF'
import gzip, json, sys
src, stem, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
outs = [gzip.open(f"{stem}_p{i:02d}.jsonl.gz", "wt") for i in range(n)]
ep = -1
with gzip.open(src, "rt") as fh:
    for line in fh:
        if "goal" in json.loads(line):  # an episode starts with its goal record
            ep += 1
        outs[max(ep, 0) % n].write(line)
for o in outs:
    o.close()
PYEOF
    else
      ln "$f" "$dst" 2>/dev/null || cp "$f" "$dst"
    fi
    c=$(zcat "$f" | grep -c '"state"' || true)
    n=$((n + c)); shards=$((shards + 1))
  done
  echo "source $k: $src  (${#files[@]} shards, $n states)"
  srcs_json+="${srcs_json:+, }{\"dir\": \"$src\", \"shards\": ${#files[@]}, \"states\": $n}"
  total=$((total + n)); k=$((k + 1))
done
nout=$(find "$out" -maxdepth 1 -name '*.jsonl.gz' | wc -l)
printf '{"created": "%s", "host": "%s", "shards": %d, "states": %d, "sources": [%s]}\n' \
  "$(date -Is)" "$(hostname)" "$nout" "$total" "$srcs_json" > "$out/pool.json"
echo "pooled $shards shards ($nout after SPLIT=$split), $total labelled states -> $out"
