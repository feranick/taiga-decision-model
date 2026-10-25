# Installing the production model on a new machine

The production models are published as a GitHub release of this repository, one file per release:

| File | What it is |
|---|---|
| `taiga-models-<label>-<version>.tar.gz` | The models (`models/<name>/hf`), their registry (`registry.json`), `MANIFEST.json` (SHA-256 of every file, the patch series the models need) and a short `README.txt` |
| `taiga-models-<label>-<version>.tar.gz.sha256` | The checksum of the download |

Current release: **v5, `models-2026.10.24.1`**: `small-v5` (main) and `small-v5-s22` (fallback), 1.2M parameters each, for taiga-expanded patches 0001-0022 and FreeCAD 1.1.3 (results: `../taiga-expanded/eval/README.md`, v5).

A model runs only with the code it was trained with: a **work folder** set up with exactly its patch series (v5: 0001-0022, fingerprint `e7c48c21861a`). The steps below set one up, install the release into `~/taiga_prod` and test it. About 30 minutes, most of it the setup's tests.

## Requirements

- Linux, one of the tested platforms (`../README.md`, Tested platforms): Ubuntu 24.04 on an NVIDIA DGX Spark (`dgx`), or Ubuntu 26.04 on x86-64 (`5060ti`, `quadro`; the two differ only in their GPU setup). The models run on the CPU for building parts, so a GPU is not needed here.
- `git`, `curl`, `sudo` (the setup installs FreeCAD 1.1.3 from a PPA), about 5 GB of disk.
- For a client on another computer (e.g. the Claude desktop app): ssh access with a key.

## 1. Clone the repository

```bash
git clone https://github.com/feranick/taiga-decision-model.git ~/Software/taiga-decision-model
```

The MCP command in step 5 expects this path; elsewhere, adjust it there.

## 2. Set up the work folder for the release's patch series

The repository may already hold newer patches than the release needs. A work folder keeps the series recorded in its `patches.sha`, so write the release's fingerprint first, then set up (interactive: it may ask for the sudo password):

```bash
cd ~/Software/taiga-decision-model/taiga-expanded
W=~/taiga-expanded-0022; mkdir -p $W
ls patches/*.patch | head -22 | xargs cat | sha256sum | cut -c1-12 > $W/patches.sha
cat $W/patches.sha                                   # must print e7c48c21861a (v5)
WORK=$W bash ./train_expanded.sh dgx setup            # or 5060ti / quadro, see Requirements
```

The setup ends with **130 passed, 1 skipped** for 0001-0022 (with newer patches in the repository it also says `set up with patches 1-22 of N`). For another release, use its patch count instead of 22 and its fingerprint (in the release notes and in `MANIFEST.json`).

## 3. Download the release

```bash
V=2026.10.24.1; F=taiga-models-v5-$V.tar.gz
cd ~ && curl -LO https://github.com/feranick/taiga-decision-model/releases/download/models-$V/$F \
     && curl -LO https://github.com/feranick/taiga-decision-model/releases/download/models-$V/$F.sha256
sha256sum -c $F.sha256                               # F: OK
```

If the repository is private, download with the GitHub CLI instead: `gh release download models-$V -R feranick/taiga-decision-model`.

## 4. Install and test

```bash
bash ~/Software/taiga-decision-model/build/deploy_prod.sh ~/$F ~/taiga-expanded-0022
```

`deploy_prod.sh` checks every file against `MANIFEST.json` and the work folder's `patches.sha` against the release (it stops if they differ), installs `~/taiga_prod` (`models/`, `registry.json`, `work` → the work folder), adds `mcp` to the work folder's venv, and tests: both models `"available": true`, and three test parts each `"built": true` with `"accepted": "main:guided"`. A warning after the test means the model did not build every part (most often a work folder with another patch series).

## 5. Use it

**From an MCP client** (e.g. the Claude desktop app on another computer), in its MCP configuration:

```json
{"mcpServers": {"taiga": {"command": "ssh", "args": ["<user>@<host>",
  "TAIGA_WORK=~/taiga_prod/work TAIGA_REGISTRY=~/taiga_prod/registry.json ~/taiga_prod/work/taiga-s1/.venv/bin/python ~/Software/taiga-decision-model/harness/taiga_mcp.py"]}}}
```

Then `list_models` shows `small-v5`; the workflow is in `../harness/README.md`.

**From the command line** on the machine itself:

```bash
export TAIGA_WORK=~/taiga_prod/work TAIGA_REGISTRY=~/taiga_prod/registry.json
PY=$TAIGA_WORK/taiga-s1/.venv/bin/python; H=~/Software/taiga-decision-model/harness/taiga_harness.py
$PY $H build my_goals.json          # the scripted teacher first, then the model; files in ~/taiga_parts
```

## Updating and rolling back

A new release: set up its work folder (step 2, with its patch count) if its series differs, then step 4 with its file. The MCP configuration stays the same, since it points at `~/taiga_prod`; restart the client. To go back, run step 4 with the earlier release and its work folder (v4: `models-…` for patches 0001-0021, work folder `~/taiga-expanded-0021`).

## Making a release (maintainers)

From a models tar as made for `deploy_prod.sh` (`../harness/README.md`, Production on every machine) and `build/registry_prod.json` describing the same models:

```bash
cd ~/Desktop/taiga-decision-model
bash build/package_release.sh ~/Desktop/taiga_prod_v5.tar v5 2026.10.24.1 22 ~/Desktop
gh release create models-2026.10.24.1 ~/Desktop/taiga-models-v5-2026.10.24.1.tar.gz \
  ~/Desktop/taiga-models-v5-2026.10.24.1.tar.gz.sha256 \
  --title "Taiga models v5 (2026.10.24.1)" --notes-file ~/Desktop/taiga-models-v5-2026.10.24.1.notes.md
```

`package_release.sh` writes the release file, its checksum and the release notes (models, results, patch series and fingerprint); it stops if `registry_prod.json` names a model the tar does not have. Without the GitHub CLI, create the release on github.com (Releases, Draft a new release, tag `models-<version>`) and attach the two files and the notes.

| Script | Version |
|---|---|
| `build/package_release.sh` | 2026.10.24.1 |
| `build/deploy_prod.sh` | 2026.10.24.2 (also takes a release file: checks it against its `MANIFEST.json` and the work folder's patch series; the registry from the release) |
