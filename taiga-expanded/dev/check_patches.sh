#!/bin/bash
# check_patches.sh — apply the patch series to a fresh clone of upstream at BASE_REF and run
# the tests. Uses the upstream venv's Python for the tests; FreeCAD tests run when FreeCAD
# is configured (source ~/taiga/freecad.env first), otherwise they are skipped.
# Version: 2026.10.05.1
# Env: PY (default ~/taiga/taiga-s1/.venv/bin/python), KEEP=1 keeps the temporary clone
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"
PY=${PY:-$HOME/taiga/taiga-s1/.venv/bin/python}

tmp=$(mktemp -d)
[[ ${KEEP:-0} == 1 ]] || trap 'rm -rf "$tmp"' EXIT
git clone -q "$UPSTREAM_URL" "$tmp/up"
git -C "$tmp/up" checkout -q "$BASE_REF"
git_id "$tmp/up"
if [[ -f $PATCH_DIR/BASE && $(git -C "$tmp/up" rev-parse HEAD) != $(cat "$PATCH_DIR/BASE") ]]; then
  die "patches were exported against $(cat "$PATCH_DIR/BASE"), not $BASE_REF"
fi
shopt -s nullglob
patches=("$PATCH_DIR"/*.patch)
git -C "$tmp/up" am -q "${patches[@]}" || die "patch series does not apply cleanly to $BASE_REF"
echo "applied ${#patches[@]} patch(es) to $(git -C "$tmp/up" rev-parse --short "$BASE_REF")"
cd "$tmp/up"
PYTHONPATH="$tmp/up${PYTHONPATH:+:$PYTHONPATH}" "$PY" -m pytest -q tests
echo "patch series OK${KEEP:+ (clone kept at $tmp/up)}"
