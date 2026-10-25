#!/bin/bash
# deploy_prod.sh — install the production models on this machine, in ~/taiga_prod:
#   models/<name>/hf     the models (from MODELS_TAR)
#   registry.json        build/registry_prod.json (paths into models/)
#   work -> WORK         the work folder the models were trained with
# then install "mcp" into the work folder's venv and test the harness (models, one build).
# Version: 2026.10.24.2
#
# Usage:  build/deploy_prod.sh MODELS_TAR [WORK]
#   MODELS_TAR  a tar with models/<name>/hf, or a release .tar.gz (build/package_release.sh: its files and
#               the work folder's patch series are checked against its MANIFEST.json)
#   WORK        default ~/taiga-expanded-0022 (v5: patches 0001-0022)
# The MCP client then runs, over ssh:
#   TAIGA_WORK=~/taiga_prod/work TAIGA_REGISTRY=~/taiga_prod/registry.json \
#     ~/taiga_prod/work/taiga-s1/.venv/bin/python ~/Software/taiga-decision-model/harness/taiga_mcp.py
# A later model only changes ~/taiga_prod (rerun with its tar and work folder); the client config stays.
set -euo pipefail
die() { echo "error: $*" >&2; exit 1; }
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tarball=${1:-}; work=${2:-$HOME/taiga-expanded-0022}
[[ -f $tarball ]] || die "usage: $0 MODELS_TAR [WORK]"
PY=$work/taiga-s1/.venv/bin/python
[[ -x $PY ]] || die "no work folder at $work; set it up first: cd $REPO/taiga-expanded && WORK=$work ./train_expanded.sh <machine> setup"
P=$HOME/taiga_prod
mkdir -p "$P"

new=$(mktemp -d "$P/.models.XXXXXX")
tar xf "$tarball" -C "$new"   # a plain tar, or a release .tar.gz (build/package_release.sh)
top=$new
[[ -d $top/models ]] || top=$(dirname "$(compgen -G "$new/*/models" | head -1)" 2>/dev/null || echo "$new")
[[ -d $top/models ]] && compgen -G "$top/models/*/hf" >/dev/null || { rm -rf "$new"; die "$tarball has no models/<name>/hf"; }
if [[ -f $top/MANIFEST.json ]]; then   # a release: check the files and the work folder's patch series
  "$PY" - "$top" "$work" <<'PYEOF' || { rm -rf "$new"; exit 1; }
import hashlib, json, os, sys
top, work = sys.argv[1:]
m = json.load(open(os.path.join(top, "MANIFEST.json")))
bad = [f for f, h in m["files"].items() if hashlib.sha256(open(os.path.join(top, f), "rb").read()).hexdigest() != h]
if bad:
    sys.exit(f"error: damaged files in the release: {bad}")
want = m["patches"]["series_sha"]
shafile = os.path.join(work, "patches.sha")
have = open(shafile).read().strip() if os.path.exists(shafile) else None
if have != want:
    sys.exit(f"error: {work} has patch series {have}, the models need {want} (patches 0001-{m['patches']['count']:04d});"
             " set up a work folder for them (docs/README_install.md)")
print(f"release {m['name']}: {len(m['files'])} files verified, patch series {want} matches {work}")
PYEOF
fi
rm -rf "$P/models.old"
[[ -d $P/models ]] && mv "$P/models" "$P/models.old"
mv "$top/models" "$P/models"
if [[ -f $top/registry.json ]]; then cp "$top/registry.json" "$P/registry.json"; else cp "$REPO/build/registry_prod.json" "$P/registry.json"; fi
rm -rf "$new"
ln -sfn "$work" "$P/work"
echo "models: $(ls "$P/models" | tr '\n' ' ')| work: $work"

if ! "$PY" -c "import mcp" 2>/dev/null; then
  if [[ -x $HOME/.local/bin/uv ]]; then "$HOME/.local/bin/uv" pip install -q --python "$PY" "mcp>=1.2"
  else "$PY" -m pip install -q "mcp>=1.2"; fi
fi
"$PY" -c "import mcp" || die "could not install mcp into $PY"

export TAIGA_WORK=$P/work TAIGA_REGISTRY=$P/registry.json
H=$REPO/harness/taiga_harness.py
"$PY" "$H" models | grep -E '"(name|available)"'
echo "test build (the model first):"
out=$("$PY" "$H" build "$REPO/build/flange/example_goals.json" --chain main:guided,teacher --no-step)
grep -E '"(built|accepted)"' <<<"$out" | paste - - | sed 's/^ *//'
if grep -q '"accepted": "teacher"' <<<"$out" || grep -q '"result": "CRASH"' <<<"$out"; then
  echo "warning: the model did not build every test part (see the newest ~/taiga_parts/build_*/runtime.log)."
  echo "  Most often WORK has another patch series than the model (v5: patches 0001-0022, 130 setup tests):"
  echo "  the goal kinds and commands then differ and the model crashes."
fi
rm -rf "$P/models.old"
echo "deployed: $P"
