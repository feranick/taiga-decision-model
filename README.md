# Taiga-S1 reproduction scripts

Scripts to set up and retrain [Taiga-S1](https://github.com/shhivv/taiga-s1) from scratch. Taiga-S1 is a 1.2M-parameter model that predicts the next FreeCAD PartDesign command. Each script sets up the environment, generates synthetic data in headless FreeCAD, trains the model, and evaluates it against the published `shhivv/taiga-s1`. They all use upstream's own pipeline, pinned to commit `a6e81d3`, with the flags from upstream's `train_final.sh`.

| Script | Machine | OS | FreeCAD source | GPU |
|---|---|---|---|---|
| `taiga_repro_DGX.sh` | NVIDIA DGX Spark (aarch64) | DGX OS / Ubuntu 24.04 | conda-forge via micromamba (or your own build) | GB10, CUDA 13 |
| `taiga_repro_5060ti.sh` | mochi | Ubuntu 26.04 (resolute) | `ppa:bleedingedge/resolute-bleed` (1.1.x) | RTX 5060 Ti (Blackwell, sm_120) |
| `taiga_repro_Quadro6000.sh` | dual Quadro RTX 6000 workstation | Ubuntu 24.04 or 26.04 | `ppa:bleedingedge/noble-bleed` or `resolute-bleed`, picked automatically | 2 × Quadro RTX 6000 (Turing, sm_75) |

All three scripts use the same stages, environment variables and output layout. The only differences are in `setup`, plus the `pair` stage, which only the Quadro script has.

---

## Prerequisites

- `git` and `curl` (and `tar` and `bzip2` on the DGX).
- An NVIDIA driver on the GPU machines. Mochi and the Quadro machine are tested with `nvidia-drivers-595-open`.
  - **RTX 5060 Ti:** needs driver ≥ 570, and ≥ 580 for the CUDA 13 PyTorch build.
  - **Quadro:** works with any recent driver; the script picks a PyTorch build that runs on the card.
- `sudo` access on mochi and the Quadro machine. It's used only by `setup` (to add the PPA and install FreeCAD) and by `uninstall`.
- About 20 GB of free disk space in the working directory (default `~/taiga`).

The scripts install everything else themselves: uv, Python 3.11, PyTorch, the upstream repo and FreeCAD.

---

## Quick start

`setup` must run interactively because it may prompt for your sudo password. The long stages should run detached; see [Running detached](#running-detached).

### DGX Spark
```bash
chmod +x taiga_repro_DGX.sh
./taiga_repro_DGX.sh all                  # seed 2 (upstream's seed)
SEED=12 ./taiga_repro_DGX.sh all          # on the second Spark: independent replicate
```
To use an existing FreeCAD instead of conda, such as your `noble-spark-bleed` 1.0.3 build, set both of these variables:
```bash
FREECAD_PYTHON=/usr/bin/python3 FREECAD_LIB=/usr/lib/freecad-python3/lib ./taiga_repro_DGX.sh all
```

### mochi (RTX 5060 Ti)
```bash
chmod +x taiga_repro_5060ti.sh
./taiga_repro_5060ti.sh setup             # interactive (sudo)
./taiga_repro_5060ti.sh data train export eval calib
```

### Quadro RTX 6000 workstation
```bash
chmod +x taiga_repro_Quadro6000.sh
./taiga_repro_Quadro6000.sh setup         # interactive (sudo)
./taiga_repro_Quadro6000.sh pair          # seed 2 on GPU 0 + seed 12 on GPU 1, in parallel
```

---

## Stages

Run them one at a time or several in sequence, for example `./script.sh train export eval`.

| Stage | What it does |
|---|---|
| `setup` | Installs uv, a Python 3.11 venv and PyTorch; installs and checks FreeCAD; clones and pins the repo; runs the upstream tests. |
| `data` | Generates synthetic training data (4k/8k/12k episodes, about 590k labeled states) and the test set (300/600/900 episodes, seed 3). Skips generation if the data already exists. |
| `train` | Supervised training for 4 epochs, then 2 DAgger rounds, with all of upstream's generalization options enabled. |
| `export` | Converts the checkpoint to Hugging Face format (`model.safetensors` + `config.json`). |
| `eval` | Evaluates your model **and** the published `shhivv/taiga-s1` on the same suites (`iid comp comp2 comp3 len len2 len3`), once normally and once with 20% random actions injected. |
| `calib` | Fits a softmax temperature on held-out states and writes it into the exported `config.json`. |
| `all` | Runs `setup data train export eval calib` in that order. |
| `pair` | *(Quadro only)* Runs two seeds in parallel, one per GPU, splitting the CPU workers between them. Requires `setup` to have been run first. |
| `uninstall` | Removes what the script installed; see [Uninstall](#uninstall). Must be run on its own. |

---

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `WORK` | `~/taiga` | Working directory for the repo, venv, data, runs and logs |
| `SEED` | `2` | Seed for the training data and the model (upstream used 2) |
| `DATA_WORKERS` | `8` | Number of data-generation processes. Keep 8 to regenerate upstream's exact shards; raise it for speed. |
| `WORKERS` | `nproc − 2` | Number of FreeCAD workers for DAgger, eval and calibration |
| `REPO_REF` | `a6e81d3` | Upstream commit to pin |
| `TORCH_INDEX` | auto | Forces a specific PyTorch wheel index (e.g. `https://download.pytorch.org/whl/cu128`) |
| `FREECAD_PYTHON`, `FREECAD_LIB` | auto | Set both to use an existing FreeCAD and skip installing or detecting one |
| `FREECAD_SPEC` | `freecad>=1.0` | *(DGX)* conda-forge package spec for FreeCAD |
| `PPA`, `FREECAD_PKG` | auto, `freecad` | *(5060ti, Quadro)* Which PPA and package to install |
| `GPU` | all | *(Quadro)* Pins the run to one GPU (sets `CUDA_VISIBLE_DEVICES`) |
| `PAIR_SEEDS` | `"2 12"` | *(Quadro)* Seeds used by `pair` |
| `PURGE` | `0` | *(uninstall)* `1` also deletes trained models, evals and logs |
| `REMOVE_FREECAD` | `1` | *(uninstall)* `0` keeps FreeCAD and the PPA |
| `UNINSTALL_YES` | `0` | *(uninstall)* `1` skips the confirmation prompt; required when not in a terminal |

The test set always uses seed 3 with 8 workers, so every machine evaluates on the same goals.

---

## Running detached

Run `setup` interactively first. After that, use either of the following.

### tmux (recommended)
```bash
tmux new -s taiga
./taiga_repro_5060ti.sh data train export eval calib
# detach: Ctrl-b then d      reattach: tmux attach -t taiga
```

### nohup
```bash
# DGX / mochi
nohup ./taiga_repro_DGX.sh data train export eval calib > ~/taiga/logs/run.log 2>&1 &
nohup ./taiga_repro_5060ti.sh data train export eval calib > ~/taiga/logs/run.log 2>&1 &

# Quadro: both seeds
nohup ./taiga_repro_Quadro6000.sh pair > ~/taiga/logs/pair.log 2>&1 &

# Quadro: one seed on one GPU
GPU=1 SEED=12 nohup ./taiga_repro_Quadro6000.sh data train export eval calib > ~/taiga/logs/run_seed12.log 2>&1 &
```

### Monitoring
```bash
tail -f ~/taiga/logs/run.log               # or pair.log
tail -f ~/taiga/logs/pair_seed2.log        # Quadro pair: full output of each seed
tail -f ~/taiga/logs/train_seed2.log       # per-stage logs (all scripts)
cat ~/taiga/logs/timings_seed*.tsv         # wall time per stage
nvidia-smi -l 5                            # GPU usage
```

### Stopping
```bash
pkill -f taiga_repro_; pkill -f freecad_s1
```

---

## Outputs

```
~/taiga/
├── taiga-s1/                         upstream repo (pinned) + .venv
│   ├── data/
│   │   ├── gen_train_seed<N>/        training shards
│   │   ├── gen_test/                 shared test set (seed 3)
│   │   └── gen_test_seed<N>/         (Quadro) per-run hard links to the test shards
│   └── runs/
│       ├── seed<N>/
│       │   ├── last.pt               trained checkpoint
│       │   ├── hf/                   exported model (+ calibrated temperature)
│       │   ├── eval.json             normal evaluation
│       │   ├── eval_perturb.json     evaluation with 20% random actions
│       │   ├── calibration.json      ECE / NLL before and after temperature scaling
│       │   └── manifest.json         versions: script, repo commit, torch, CUDA, FreeCAD, driver, GPU
│       └── reference_hf/             published shhivv/taiga-s1 evaluated on this machine
├── logs/
│   ├── <stage>_seed<N>.log
│   └── timings_seed<N>.tsv           wall time per stage
├── freecad.env                       detected FreeCAD paths
├── bin/                              micromamba (DGX) or freecad-python launcher (5060ti, Quadro)
├── fcenv/                            (DGX) conda FreeCAD environment
└── .installed_by_taiga               record of system changes, used by uninstall
```

To compare your model with the published one:
```bash
cd ~/taiga/taiga-s1
.venv/bin/python scripts/summarize.py runs/reference_hf/eval.json runs/reference_hf/eval_perturb.json
.venv/bin/python scripts/summarize.py runs/seed2/eval.json runs/seed2/eval_perturb.json
```

---

## Uninstall

```bash
./taiga_repro_5060ti.sh uninstall                   # shows the plan, asks for confirmation
PURGE=1 ./taiga_repro_5060ti.sh uninstall           # also deletes results
REMOVE_FREECAD=0 ./taiga_repro_5060ti.sh uninstall  # keeps FreeCAD and the PPA
UNINSTALL_YES=1 ./taiga_repro_5060ti.sh uninstall   # no prompt
```

- **Always removed:** the repo, venv, datasets, the conda FreeCAD environment (DGX), the FreeCAD launcher, the logs, and the cached Hugging Face weights.
- **Kept by default:** trained models, evals, manifests and logs, which are moved to `~/taiga/results-<timestamp>/`.
- **Removed only if this script installed them** (according to `.installed_by_taiga`): uv, the FreeCAD apt package and the PPA. Anything that was already present stays. The uninstaller does not run `apt autoremove`; review leftovers with `sudo apt autoremove --dry-run`.
- **Refuses to run** if `WORK` is `/` or your home directory, or if any Taiga processes are still running.

Only installs made by these script versions are recorded. Anything installed by earlier versions has to be removed by hand.

---

## Notes and troubleshooting

- **The FreeCAD version matters.** Upstream evaluated on FreeCAD 1.1. Mochi (1.1.3) is the closest match. With the defaults (`SEED=2`, `DATA_WORKERS=8`) it has the best chance of regenerating upstream's exact training data. Other versions give slightly different data and success rates. The `reference_hf` evaluation gives a baseline measured on the same machine.
- **"FreeCAD import failed".** Setup creates a PartDesign Body and a Sketch headless before running the tests. Upstream's tests silently *skip* when FreeCAD is missing, so a passing pytest alone proves nothing. If automatic detection fails, set `FREECAD_PYTHON` and `FREECAD_LIB` yourself.
- **"GPU present but torch can't use it"** (5060ti) or **"no PyTorch build ran on these GPUs"** (Quadro). Check the driver version with `nvidia-smi`, then force a different build with `TORCH_INDEX`, for example `cu128` or `cu126`.
- **The GPU is not the bottleneck.** The model is small, so most of the time goes into the FreeCAD steps (data generation, DAgger and evaluation), which run on the CPU. Upstream reports about 25 minutes of training on an Apple M-series Mac.
- **Restarting a run.**
  - `data` skips generation if shards exist. If data generation was interrupted, delete `data/gen_train_seed<N>` first.
  - The Quadro script marks the test set complete only after a full generation.
  - `train` does not resume from a checkpoint; it starts over.
  - The published-model baseline is computed once per machine and then reused.
- **Using both GPUs.** Splitting a 1.2M-parameter model across GPUs gains nothing. Running one seed per GPU (`pair`), or one seed per Spark, measures seed-to-seed variance in the same wall time.

---

## Versions

Each script uses `YYYY.MM.DD.x` versioning. The version is in the script header and in each run's `manifest.json`.

| Script | Version |
|---|---|
| `taiga_repro_DGX.sh` | 2026.10.02.3 |
| `taiga_repro_5060ti.sh` | 2026.10.02.5 |
| `taiga_repro_Quadro6000.sh` | 2026.10.02.3 |
