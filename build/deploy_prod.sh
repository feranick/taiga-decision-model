#!/bin/bash
# deploy_prod.sh — install the production models on this machine, in ~/taiga_prod:
#   models/<name>/hf     the models (from MODELS_TAR)
#   registry.json        build/registry_prod.json (paths into models/)
#   work -> WORK         the work folder the models were trained with
# then install "mcp" into the work folder's venv and test the harness (models, one build).
# Version: 2026.10.20.1
#
# Usage:  build/deploy_prod.sh MODELS_TAR [WORK]
#   MODELS_TAR  a tar with models/<name>/hf (see harness/README.md, "Production on every machine")
#   WORK        default ~/taiga-expanded-0021 (v4: patches 0001-0021)
# The MCP client then runs, over ssh:
#   TAIGA_WORK=~/taiga_prod/work TAIGA_REGISTRY=~/taiga_prod/registry.json \
#     ~/taiga_prod/work/taiga-s1/.venv/bin/python ~/Software/taiga-decision-model/harness/taiga_mcp.py
# A later model only changes ~/taiga_prod (rerun with its tar and work folder); the client config stays.
set -euo pipefail
die() { echo "error: $*" >&2; exit 1; }
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tarball=${1:-}; work=${2:-$HOME/taiga-expanded-0021}
[[ -f $tarball ]] || die "usage: $0 MODELS_TAR [WORK]"
PY=$work/taiga-s1/.venv/bin/python
[[ -x $PY ]] || die "no work folder at $work; set it up first: cd $REPO/taiga-expanded && WORK=$work ./train_expanded.sh <machine> setup"
P=$HOME/taiga_prod
mkdir -p "$P"

new=$(mktemp -d "$P/.models.XXXXXX")
tar xf "$tarball" -C "$new"
[[ -d $new/models ]] && compgen -G "$new/models/*/hf" >/dev/null || { rm -rf "$new"; die "$tarball has no models/<name>/hf"; }
rm -rf "$P/models.old"
[[ -d $P/models ]] && mv "$P/models" "$P/models.old"
mv "$new/models" "$P/models"; rmdir "$new"
ln -sfn "$work" "$P/work"
cp "$REPO/build/registry_prod.json" "$P/registry.json"
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
"$PY" "$H" build "$REPO/build/flange/example_goals.json" --chain main:guided,teacher --no-step \
  | grep -E '"(built|accepted)"' | paste - - | sed 's/^ *//'
rm -rf "$P/models.old"
echo "deployed: $P"
