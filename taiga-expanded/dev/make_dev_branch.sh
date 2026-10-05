#!/bin/bash
# make_dev_branch.sh — create (or reset) the development clone: upstream at BASE_REF with
# the current patch series applied as commits on branch BRANCH. Edit and commit there,
# then run export_patches.sh.
# Version: 2026.10.05.1
# Env: DEV_DIR (default ~/taiga/taiga-expanded-dev), BASE_REF, UPSTREAM_URL, BRANCH
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

if [[ ! -d $DEV_DIR/.git ]]; then
  git clone -q "$UPSTREAM_URL" "$DEV_DIR"
fi
git -C "$DEV_DIR" fetch -q origin
if [[ -n $(git -C "$DEV_DIR" status --porcelain --untracked-files=no) ]]; then
  die "$DEV_DIR has uncommitted changes; commit (and export) or discard them first"
fi
git_id "$DEV_DIR"
git -C "$DEV_DIR" checkout -q -B "$BRANCH" "$BASE_REF"
shopt -s nullglob
patches=("$PATCH_DIR"/*.patch)
if (( ${#patches[@]} )); then
  git -C "$DEV_DIR" am -q --3way "${patches[@]}" || { git -C "$DEV_DIR" am --abort; die "patches do not apply to $BASE_REF"; }
fi
echo "dev branch $BRANCH at $DEV_DIR: $(git -C "$DEV_DIR" rev-parse --short "$BASE_REF") + ${#patches[@]} patch(es)"
git -C "$DEV_DIR" log --oneline "$BASE_REF..$BRANCH"
