#!/bin/bash
# package_release.sh — package production models as one release file for GitHub:
#   taiga-models-<label>-<version>.tar.gz   models/<name>/hf, registry.json, MANIFEST.json, README.txt
#   taiga-models-<label>-<version>.tar.gz.sha256
# MANIFEST.json records each file's SHA-256 and the patch series the models need (count and the
# fingerprint a work folder keeps in patches.sha), so build/deploy_prod.sh can check both.
# Version: 2026.10.24.1
#
# Usage:  build/package_release.sh MODELS_TAR LABEL VERSION N_PATCHES [OUT_DIR]
#   MODELS_TAR  a tar with models/<name>/hf, as made for deploy_prod.sh (harness/README.md)
#   LABEL       e.g. v5;  VERSION e.g. 2026.10.24.1;  N_PATCHES e.g. 22 (patches 0001-0022)
#   OUT_DIR     default: the current folder
# e.g.    build/package_release.sh ~/Desktop/taiga_prod_v5.tar v5 2026.10.24.1 22 ~/Desktop
set -euo pipefail
die() { echo "error: $*" >&2; exit 1; }
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
(( $# >= 4 )) || die "usage: $0 MODELS_TAR LABEL VERSION N_PATCHES [OUT_DIR]"
src=$1; label=$2; version=$3; n=$4; out=$(cd "${5:-.}" && pwd)
[[ -f $src ]] || die "no $src"
command -v python3 >/dev/null || die "python3 needed"

patches=("$REPO"/taiga-expanded/patches/*.patch)
(( n >= 1 && n <= ${#patches[@]} )) || die "N_PATCHES must be 1..${#patches[@]}"
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
series=$(cat "${patches[@]:0:n}" | sha256 | cut -c1-12)
last=$(basename "${patches[n - 1]}")
base=$(cat "$REPO/taiga-expanded/patches/BASE" 2>/dev/null | head -1 || true)

name=taiga-models-$label-$version
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/$name"
tar xf "$src" -C "$tmp/$name"
compgen -G "$tmp/$name/models/*/hf" >/dev/null || die "$src has no models/<name>/hf"
cp "$REPO/build/registry_prod.json" "$tmp/$name/registry.json"

python3 - "$tmp/$name" "$label" "$version" "$n" "$series" "$last" "$base" <<'EOF'
import hashlib, json, os, sys
root, label, version, n, series, last, base = sys.argv[1:]
reg = json.load(open(os.path.join(root, "registry.json")))
names = sorted(os.listdir(os.path.join(root, "models")))
listed = {os.path.basename(os.path.dirname(m["path"].rstrip("/"))) for m in reg["models"]}
missing = listed - set(names)
if missing:
    sys.exit(f"error: registry_prod.json names models not in the tar: {sorted(missing)}")
files = {}
for d, _, fs in os.walk(os.path.join(root, "models")):
    for f in sorted(fs):
        p = os.path.join(d, f)
        files[os.path.relpath(p, root)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
man = {"name": f"taiga-models-{label}-{version}", "label": label, "version": version,
       "models": [{k: m[k] for k in ("name", "role", "params", "measured") if k in m} for m in reg["models"]],
       "patches": {"count": int(n), "last": last, "series_sha": series, "upstream_base": base},
       "freecad": sorted({m.get("freecad", "") for m in reg["models"]}),
       "files": files}
json.dump(man, open(os.path.join(root, "MANIFEST.json"), "w"), indent=2)
print(f"manifest: {len(files)} files, patches 1-{n} ({last}), series {series}")
EOF

cat > "$tmp/$name/README.txt" <<EOF
Taiga production models $label ($version), for the taiga-decision-model repository.
They run only in a work folder set up with patches 0001-$(printf %04d "$n") (fingerprint $series in
the work folder's patches.sha) and FreeCAD 1.1.3. Install on a Linux machine:
    bash build/deploy_prod.sh $name.tar.gz ~/taiga-expanded-$(printf %04d "$n")
The full steps for a new machine: docs/README_install.md in the repository.
EOF

COPYFILE_DISABLE=1 tar czf "$out/$name.tar.gz" -C "$tmp" "$name"   # COPYFILE_DISABLE: no macOS ._ files
(cd "$out" && sha256 "$name.tar.gz" > "$name.tar.gz.sha256")
echo "wrote $out/$name.tar.gz ($(du -h "$out/$name.tar.gz" | cut -f1)) and .sha256"

python3 - "$tmp/$name/MANIFEST.json" > "$out/$name.notes.md" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1]))
p = m["patches"]
print(f"Production models **{m['label']}** ({m['version']}) for Taiga: taiga-expanded patches 0001-{p['count']:04d}, "
      f"FreeCAD {', '.join(m['freecad'])}.\n")
for x in m["models"]:
    print(f"- `{x['name']}` ({x['role']}, {x.get('params', '')}): {x.get('measured', '')}")
print(f"\nWork folder fingerprint (`patches.sha`): `{p['series_sha']}` (patches 0001-{p['count']:04d}, last `{p['last']}`).")
print("\nInstall on a new machine: `docs/README_install.md`. Check the download against the `.sha256` file.")
PYEOF
echo "release notes: $out/$name.notes.md"
echo "publish:  gh release create models-$version \"$out/$name.tar.gz\" \"$out/$name.tar.gz.sha256\" \\"
echo "            --title \"Taiga models $label ($version)\" --notes-file \"$out/$name.notes.md\""
