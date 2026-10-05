#!/bin/bash
# export_patches.sh — write the dev branch's commits (BASE_REF..BRANCH) as the patch series
# in ../patches (replacing the old series), plus BASE (the upstream commit they apply to).
# Version: 2026.10.05.2
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

[[ -d $DEV_DIR/.git ]] || die "no dev clone at $DEV_DIR; run make_dev_branch.sh"
[[ -z $(git -C "$DEV_DIR" status --porcelain --untracked-files=no) ]] || die "commit your changes in $DEV_DIR first"
n=$(git -C "$DEV_DIR" rev-list --count "$BASE_REF..$BRANCH")
(( n > 0 )) || die "no commits on $BRANCH beyond $BASE_REF"
rm -f "$PATCH_DIR"/*.patch
git -C "$DEV_DIR" format-patch -q -N --zero-commit --no-signature -o "$PATCH_DIR" "$BASE_REF..$BRANCH"
git -C "$DEV_DIR" rev-parse "$BASE_REF" > "$PATCH_DIR/BASE"
echo "exported $n patch(es) to $PATCH_DIR:"
ls -1 "$PATCH_DIR"/*.patch | xargs -n1 basename
