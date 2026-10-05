# Shared settings for the taiga-expanded dev scripts (sourced, not run).
UPSTREAM_URL=${UPSTREAM_URL:-https://github.com/shhivv/biome-s1.git}   # formerly shhivv/taiga-s1
BASE_REF=${BASE_REF:-4a31bcf}                                          # pinned upstream commit the patches apply to
BRANCH=${BRANCH:-expanded}
DEV_DIR=${DEV_DIR:-$HOME/taiga/taiga-expanded-dev}                     # working clone for development
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PATCH_DIR=${PATCH_DIR:-$(cd "$HERE/../patches" && pwd)}
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
git_id() { git -C "$1" config user.email >/dev/null || git -C "$1" config user.email "taiga-expanded@local"
           git -C "$1" config user.name >/dev/null || git -C "$1" config user.name "taiga-expanded"; }
