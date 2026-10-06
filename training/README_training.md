# Taiga-S1 reproduction scripts

Scripts to set up and retrain [Taiga-S1](https://github.com/shhivv/biome-s1) from scratch. Taiga-S1 is a 1.2M-parameter model that predicts the next FreeCAD PartDesign command. Each script sets up the environment, generates synthetic data in headless FreeCAD, trains the model, and evaluates it against the published `shhivv/taiga-s1`. They all use upstream's own pipeline, pinned to commit `a6e81d3`, with the flags from upstream's `train_final.sh`.

Upstream was renamed from `shhivv/taiga-s1` to [`shhivv/biome-s1`](https://github.com/shhivv/biome-s1) when it became a model family (Taiga-S1 plus Mesa-S1, which operates FreeCAD's interface). The scripts clone the new URL and repoint existing clones on `setup`. The pin stays at `a6e81d3`, the last commit before the Mesa-S1 work: later commits change shared training and featurization code, so moving the pin would make new runs incomparable with existing ones. The published Taiga-S1 weights are still `shhivv/taiga-s1` on Hugging Face.

| Script | Machine | CPU / RAM | OS | FreeCAD source | GPU |
|---|---|---|---|---|---|
| `taiga_repro_DGX.sh` | NVIDIA DGX Spark (aarch64) | GB10 Grace, 20 Arm cores / 128 GB unified | DGX OS / Ubuntu 24.04 (noble) | `ppa:bleedingedge/noble-spark-bleed` (1.1.x, Qt5 build) | GB10, CUDA 13 |
| `taiga_repro_5060ti.sh` | PowerSpec G467 | Intel Core i7-8700K @ 3.70 GHz (12 cores) / 40 GB | Ubuntu 26.04 (resolute) | `ppa:bleedingedge/resolute-bleed` (1.1.x) | RTX 5060 Ti (Blackwell, sm_120) |
| `taiga_repro_Quadro6000.sh` | Dell Precision 7920 Tower | Intel Xeon Gold 6230 @ 2.10 GHz (40 cores) / 64 GB | Ubuntu 26.04 (resolute) | `ppa:bleedingedge/resolute-bleed` (1.1.x) | 2 × Quadro RTX 6000 (Turing, sm_75) |

To build parts with a trained model (inference), see [build/README_build_part.md](../build/README_build_part.md).

All three scripts use the same stages, environment variables and output layout. The only differences are in `setup`, plus the `pair` stage, which only the Quadro script has.

---

## Prerequisites

- `git` and `curl`.
- An NVIDIA driver on the GPU machines. The PowerSpec G467 and the Dell Precision 7920 use `nvidia-drivers-595-open`.
  - **RTX 5060 Ti (PowerSpec G467):** needs driver ≥ 570, and ≥ 580 for the CUDA 13 PyTorch build.
  - **Quadro RTX 6000 (Dell Precision 7920):** works with any recent driver; the script picks a PyTorch build that runs on the card.
- `sudo` access on all three machines. It's used only by `setup` (to add the PPA and install FreeCAD) and by `uninstall`.
- About 20 GB of free disk space in the working directory (default `~/taiga`).

The scripts install everything else themselves: uv, Python 3.11, PyTorch, the upstream repo and FreeCAD.

---

## Quick start

`setup` must run interactively on all machines because it may prompt for your sudo password. The long stages should run detached; see [Running detached](#running-detached).

### DGX Spark
```bash
chmod +x taiga_repro_DGX.sh
./taiga_repro_DGX.sh setup                # interactive (sudo)
./taiga_repro_DGX.sh data train export eval calib
SEED=12 ./taiga_repro_DGX.sh data train export eval calib   # on the second Spark: independent replicate
```
The DGX script is specific to Ubuntu 24.04 (noble) and always installs FreeCAD 1.1.x (a Qt5 build of 1.1.3) from `ppa:bleedingedge/noble-spark-bleed`. It ignores `FREECAD_PYTHON`/`FREECAD_LIB`, doesn't use conda, and refuses FreeCAD development builds (see [Known issues](#known-issues)).

### PowerSpec G467 (RTX 5060 Ti)
```bash
chmod +x taiga_repro_5060ti.sh
./taiga_repro_5060ti.sh setup             # interactive (sudo)
./taiga_repro_5060ti.sh data train export eval calib
```

### Dell Precision 7920 Tower (2 × Quadro RTX 6000)
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
| `data` | Generates synthetic training data (4k/8k/12k episodes × `DATA_SCALE`, about 590k labeled states at scale 1) and the test set (300/600/900 episodes, seed 3). Skips generation if the data already exists. |
| `train` | Supervised training for `EPOCHS` (4) epochs, then `DAGGER_ROUNDS` (2) DAgger rounds, with all of upstream's generalization options enabled. `DETERMINISTIC=1` makes it reproducible. |
| `export` | Converts the checkpoint to Hugging Face format (`model.safetensors` + `config.json`). |
| `eval` | Evaluates your model **and** the published `shhivv/taiga-s1` on the same suites, once normally (`eval.json`) and once with 20% random actions injected (`eval_perturb.json`). Suites: `iid` (1–5 features, like training), `comp comp2 comp3` (feature combinations held out of training), `len len2 len3` (6–7, 8–9 and 11 features) and the stress suites `len4 len5 len6` (13, 15 and 17 features). |
| `calib` | Fits a softmax temperature on held-out states and writes it into the exported `config.json`. |
| `all` | Runs `setup data train export eval calib` in that order. |
| `pair` | *(Quadro only)* Runs two seeds in parallel, one per GPU, splitting the CPU workers between them. Requires `setup` to have been run first. |
| `sweep` | Trains and evaluates every seed in `SEEDS` with the same settings, then runs `aggregate`. Seeds already evaluated are skipped. On the Quadro, one seed per GPU runs in parallel. See [Variance studies](#variance-studies). |
| `aggregate` | Reports the distribution of results over all runs of the current experiment (mean, sd, min, max per suite) plus per-run training diagnostics. |
| `uninstall` | Removes what the script installed; see [Uninstall](#uninstall). Must be run on its own. |

---

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `WORK` | `~/taiga` | Working directory for the repo, venv, data, runs and logs |
| `SEED` | `2` | Seed for the model and, unless `DATA_SEED` is set, the training data (upstream used 2) |
| `DATA_SEED` | unset | Fixes the training data for every run (e.g. `2`), so runs differ only in the model seed |
| `DATA_SCALE` | `1` | Multiplies the number of training episodes (e.g. `0.5`, `2`) |
| `EPOCHS` | `4` | Supervised training epochs |
| `DAGGER_ROUNDS`, `DAGGER_EPISODES`, `DAGGER_EPOCHS` | `2`, `400`, `2` | DAgger rounds, episodes per level per round, training epochs per round |
| `DAGGER_PERTURB`, `DAGGER_PERTURB_FRAC` | `0`, `0.5` | `> 0`: perturbed DAgger. In that fraction of the rollout batches, each step takes a random off-plan command with this probability, so the model learns to notice and repair mistakes (upstream `--dagger-perturb`; upstream's Mesa-S1 uses `0.2`). Needs a `REPO_REF` that has it, e.g. `4a31bcf`; the pinned `a6e81d3` does not. |
| `TAIGA_EXT_FRACTION`, `TAIGA_SIZE_AUG`, `TAIGA_SIZE_MAX` | unset | `taiga-expanded` only: share of expanded training goals (0.35), share of training goals scaled up (0), largest scale factor (5). See `../taiga-expanded/README.md`. Recorded in `manifest.json` and added to the data and experiment names. |
| `DETERMINISTIC` | `0` | `1`: deterministic PyTorch (stops on a non-deterministic operation); `warn`: only warns. Pins DAgger workers to `DAGGER_WORKERS` (8). |
| `EXP` | derived | Experiment name; runs go to `runs/<EXP>/seed<N>`. Derived from the settings that differ from the defaults (e.g. `e8_x2_data2`); empty for the defaults, so plain runs stay in `runs/seed<N>`. |
| `SEEDS` | `"2 12 22 32 42"` | Seeds used by `sweep` |
| `SWEEP_STAGES` | `"data train export eval"` | Stages `sweep` runs for each seed (add `calib` if needed) |
| `SWEEP_FORCE` | `0` | `1`: `sweep` also redoes seeds that are already evaluated |
| `SUITES` | all 10 suites | Evaluation suites to run (`taiga-expanded` adds its own) |
| `PATCHES` | unset | Folder with a git patch series applied on top of `REPO_REF` during `setup` (used by `../taiga-expanded/train_expanded.sh`). Use a separate `WORK`: the scripts refuse to run when the code in `WORK` doesn't match `PATCHES`. |
| `REF_EVAL` | `1` | `0` skips evaluating the published `shhivv/taiga-s1` model in `eval` (needed when patches change the vocabulary, as `taiga-expanded` does from patch 0003) |
| `DATA_WORKERS` | `8` | Number of data-generation processes. Keep 8 to regenerate upstream's exact shards; raise it for speed. |
| `WORKERS` | `nproc − 2` | Number of FreeCAD workers for DAgger, eval and calibration |
| `REPO_REF` | `a6e81d3` | Upstream commit to pin |
| `TORCH_INDEX` | auto | Forces a specific PyTorch wheel index (e.g. `https://download.pytorch.org/whl/cu128`) |
| `FREECAD_PYTHON`, `FREECAD_LIB` | auto | *(5060ti, Quadro)* Set both to use an existing FreeCAD and skip installing or detecting one. Ignored by the DGX script. |
| `PPA`, `FREECAD_PKG` | per script, `freecad` | Which PPA and package to install (`noble-spark-bleed` on the DGX, `resolute-bleed` on the others) |
| `FREECAD_EXPECT` | `1.1` | *(DGX)* FreeCAD series expected from the PPA; setup warns if the installed version differs |
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
# DGX / PowerSpec G467
nohup ./taiga_repro_DGX.sh data train export eval calib > ~/taiga/logs/run.log 2>&1 &
nohup ./taiga_repro_5060ti.sh data train export eval calib > ~/taiga/logs/run.log 2>&1 &

# Dell Precision 7920: both seeds
nohup ./taiga_repro_Quadro6000.sh pair > ~/taiga/logs/pair.log 2>&1 &

# Dell Precision 7920: one seed on one GPU
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
│   │   ├── gen_train_seed<N>[_x<S>]/ training shards (data seed, optional DATA_SCALE)
│   │   ├── gen_test/                 shared test set (seed 3)
│   │   └── gen_test_seed<N>/         (Quadro) per-run hard links to the test shards
│   └── runs/
│       ├── <EXP>/                    one folder per experiment (non-default settings)
│       │   ├── seed<N>/              same layout as below
│       │   └── sweep_summary.json    distribution over the runs (from aggregate)
│       ├── seed<N>/
│       │   ├── last.pt               trained checkpoint
│       │   ├── hf/                   exported model (+ calibrated temperature)
│       │   ├── eval.json             normal evaluation
│       │   ├── eval_perturb.json     evaluation with 20% random actions
│       │   ├── calibration.json      ECE / NLL before and after temperature scaling
│       │   ├── train.log             training log (validation curve, DAgger rollouts)
│       │   ├── crashes_*.jsonl       FreeCAD worker crashes recovered per phase (empty = none)
│       │   └── manifest.json         settings and versions: experiment, seeds, budgets, script, repo commit, torch, CUDA, FreeCAD, driver, GPU
│       └── reference_hf/             published shhivv/taiga-s1 evaluated on this machine
├── logs/
│   ├── <stage>_[<EXP>_]seed<N>.log
│   ├── sweep_[<EXP>_]seed<N>.log     (Quadro) full output of each sweep run
│   └── timings_[<EXP>_]seed<N>.tsv   wall time per stage
├── freecad.env                       detected FreeCAD paths
├── bin/                              freecad-python launcher (FreeCAD worker interpreter)
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

- **Always removed:** the repo, venv, datasets, the FreeCAD launcher, any conda FreeCAD environment left by older DGX script versions, the logs, and the cached Hugging Face weights.
- **Kept by default:** trained models, evals, manifests and logs, which are moved to `~/taiga/results-<timestamp>/`.
- **Removed only if this script installed them** (according to `.installed_by_taiga`): uv, the FreeCAD apt package and the PPA. Anything that was already present stays. The uninstaller does not run `apt autoremove`; review leftovers with `sudo apt autoremove --dry-run`.
- **Refuses to run** if `WORK` is `/` or your home directory, or if any Taiga processes are still running.

Only installs made by these script versions are recorded. Anything installed by earlier versions has to be removed by hand.

---

## Notes and troubleshooting

- **Each platform gets its own model.** Runs aren't meant to match upstream: each OS and FreeCAD version (noble with 1.1.x built on Qt5, resolute with 1.1.x on Qt6) produces its own data and model. Compare models only within the same platform, using the FreeCAD and OS versions recorded in `manifest.json`. The `reference_hf` evaluation shows how the published model performs on each machine's FreeCAD.
- **"FreeCAD import failed".** Setup creates a PartDesign Body and a Sketch headless before running the tests. Upstream's tests silently *skip* when FreeCAD is missing, so a passing pytest alone proves nothing. If automatic detection fails, set `FREECAD_PYTHON` and `FREECAD_LIB` yourself.
- **"GPU present but torch can't use it"** (5060ti) or **"no PyTorch build ran on these GPUs"** (Quadro). Check the driver version with `nvidia-smi`, then force a different build with `TORCH_INDEX`, for example `cu128` or `cu126`.
- **The GPU is not the bottleneck.** The model is small, so most of the time goes into the FreeCAD steps (data generation, DAgger and evaluation), which run on the CPU. Upstream reports about 25 minutes of training on an Apple M-series Mac.
- **Restarting a run.**
  - `data` skips generation if complete shards exist. An interrupted generation is detected and redone. Runs sharing a `DATA_SEED` generate the data once.
  - The Quadro script marks the test set complete only after a full generation.
  - `train` does not resume from a checkpoint; it starts over.
  - The published-model baseline is computed once per machine and then reused. If it lacks any suite in the current suite list (e.g. after the stress suites were added), `eval` evaluates the published model again.
  - To add the stress suites to an existing run, rerun only `eval` (e.g. `./taiga_repro_5060ti.sh eval`); data and training are not redone.
- **Using both GPUs.** Splitting a 1.2M-parameter model across GPUs gains nothing. Running one seed per GPU (`pair`), or one seed per Spark, measures seed-to-seed variance in the same wall time.

---

## Variance studies

Two training runs with the same seed, data and machine can produce noticeably different models, because GPU arithmetic is not deterministic. The tools below measure that spread and help find its cause, rather than picking the best run.

### What the tools do

- **`sweep`** trains and evaluates several seeds with identical settings. **`aggregate`** reports, for every suite, the mean, standard deviation, minimum and maximum over the runs, plus per-run diagnostics.
- **Clean results** use the same goals for every run and deterministic decisions, so their spread is purely model-to-model variance, not evaluation noise. **Perturbed results** also depend on the injected random actions; `aggregate` prints the binomial noise level (`noise sd`) for comparison.
- **Per-run diagnostics** come from each run's `train.log`: final SFT validation accuracy and NLL, how much the NLL still dropped in the last SFT epoch (a large drop means training had not converged), validation NLL after DAgger, DAgger rollout success, and a checkpoint hash.

### Suggested experiments

Run each as a sweep. The experiment name, and so its folder, is derived from the settings.

| Question | Command (5060 Ti shown) |
|---|---|
| Baseline spread, data and model both vary | `./taiga_repro_5060ti.sh sweep` |
| Spread from optimization alone (same data) | `DATA_SEED=2 ./taiga_repro_5060ti.sh sweep` |
| Is it under-trained? | `DATA_SEED=2 EPOCHS=8 ./taiga_repro_5060ti.sh sweep` |
| Is there too little data? | `DATA_SEED=2 DATA_SCALE=2 ./taiga_repro_5060ti.sh sweep` |
| Does more DAgger help? | `DATA_SEED=2 DAGGER_ROUNDS=4 ./taiga_repro_5060ti.sh sweep` |
| Does perturbed DAgger help? | `BASELINE=1 DATA_SEED=2 DAGGER_PERTURB=0.2 ../taiga-expanded/train_expanded.sh 5060ti sweep` (upstream `4a31bcf`, unpatched; compare with the same command without `DAGGER_PERTURB`) |
| Is a single run reproducible? | `DETERMINISTIC=1 EXP=det_a SEEDS=2 ./taiga_repro_5060ti.sh sweep`, then the same with `EXP=det_b`; compare `sha256sum runs/det_*/seed2/last.pt` |

Compare experiments side by side (mean ± sd over runs):

```bash
cd ~/taiga/taiga-s1
.venv/bin/python <repo>/training/taiga_aggregate.py runs/data2 runs/e8_data2 runs/x2_data2
```

How to read the outcome: if the spread with fixed data (`data2`) is about as large as the baseline, it comes from optimization, and more epochs or a different schedule are the levers to try. If fixed data removes most of it, the training sample matters, and more data (`DATA_SCALE`) should narrow it. The goal is a mean that rises while the spread shrinks; a higher best run alone is not.

### Notes

- With `DATA_SEED` unset, every seed generates its own training data, which takes about as long as a full `data` stage per seed. With `DATA_SEED` set, the data is generated once and shared.
- `DETERMINISTIC=1` gives bit-identical models only on the same GPU model, driver and PyTorch version. If training stops with an error about a non-deterministic operation, use `DETERMINISTIC=warn` to list such operations without stopping.
- The default settings keep using `runs/seed<N>`, so existing runs belong to the default experiment: `./taiga_repro_5060ti.sh aggregate` summarizes them.
- `aggregate` can be rerun at any time; it reads whatever runs exist in the experiment folder.

---

## Known issues

### `No module named 'PartDesign'` during setup (PPA FreeCAD builds, noble and resolute)

**Symptom.** `setup` prints a traceback right after the `FreeCAD lib:` line:

```
[12:11:52] FreeCAD lib: /usr/lib/freecad-python3/lib | interpreter: /usr/bin/python3.14 (linked: python3.14)
Traceback (most recent call last):
  File "<stdin>", line 3, in <module>
ModuleNotFoundError: <stdin>(3)<class 'ModuleNotFoundError'>: No module named 'PartDesign'
```

**Cause.** When FreeCAD is imported as a Python library, its home path is `/usr/lib/freecad-python3/`, so it looks for modules in `/usr/lib/freecad-python3/Mod`. The PPA packages (all of them: noble on the DGX, resolute on the 5060 Ti and Quadro) install them in `/usr/share/freecad/Mod` and don't ship a link between the two. The GUI finds the modules another way, so only headless and Python use is affected. `Part` and `Sketcher` still load because they are compiled modules in `/usr/lib/freecad-python3/lib`. PartDesign is a Python package under `Mod/`, so it doesn't.

**Impact.** The traceback comes from the first headless check (`fc_check`). The script then retries with the Mod directories on `PYTHONPATH` and writes them into the `~/taiga/bin/freecad-python` launcher. If the log continues with `Retrying with Mod dirs on PYTHONPATH` and then `FreeCAD <version> OK`, the run is valid. If the retry also fails, `setup` stops with `FreeCAD still fails headless`. Plain `python3` outside the launcher still can't load PartDesign.

**Permanent fix (in the packaging).** The link belongs in the `.deb`, so it comes with every install and upgrade. In the `debian/` folder shared by the PPA builds:

1. Find the binary package that owns the library folder: `dpkg -S /usr/lib/freecad-python3/lib`.
2. Add `debian/<that package>.links` (or a line to it, if it exists), without leading slashes; `dh_link` creates the symlink at build time:
   ```
   usr/share/freecad/Mod usr/lib/freecad-python3/Mod
   ```
3. Bump the version in each release's `debian/changelog` (e.g. `…~ppa2~noble1`, `…~ppa2~resolute1`) and upload. Only the packaging changes; FreeCAD's sources are not touched.
4. On each machine, remove the link made by hand **before** upgrading (dpkg refuses to overwrite a file it doesn't own), then upgrade and check that the package now owns the link:
   ```bash
   sudo rm /usr/lib/freecad-python3/Mod          # the symlink only; the modules stay in /usr/share/freecad/Mod
   sudo apt update && sudo apt install --only-upgrade freecad
   dpkg -S /usr/lib/freecad-python3/Mod          # should name the package
   ```

**Workaround (packages without the link).** Link the module directory to where FreeCAD expects it, once per machine. The `ls` check avoids overwriting an existing directory. A link made by hand is unknown to dpkg, so it has to be made again after a fresh install of the system or of FreeCAD.

```bash
ls /usr/lib/freecad-python3/Mod 2>/dev/null || sudo ln -s /usr/share/freecad/Mod /usr/lib/freecad-python3/Mod
```

Then rerun `setup` (e.g. `./taiga_repro_5060ti.sh setup`). On the 5060ti and Quadro scripts, leave `FREECAD_PYTHON` and `FREECAD_LIB` unset in your shell so discovery runs again; the DGX script always rediscovers. The first check now passes, and the launcher is regenerated without the extra Mod paths. Runs made before the fix don't need to be redone if the retry succeeded.

**Verify.** Both lines must print:

```bash
PYTHONPATH=/usr/lib/freecad-python3/lib python3 -c "
import FreeCAD, Part, Sketcher
d = FreeCAD.newDocument()
b = d.addObject('PartDesign::Body', 'Body')
print('OK', FreeCAD.Version()[:3], b.TypeId)
import PartDesign; print('PartDesign import OK')"
```

Then run upstream's FreeCAD integration tests through the launcher. They must **pass**, not skip, because they skip silently when FreeCAD is missing:

```bash
cd ~/taiga/taiga-s1
source ~/taiga/freecad.env
.venv/bin/python -m pytest -q tests/test_runtime.py
```

**Undo.** `.installed_by_taiga` doesn't record a link made by hand, so `uninstall` won't remove it. Remove it by hand, with no trailing slash, so only the link is deleted (also needed before installing packages that ship the link, see above):

```bash
sudo rm /usr/lib/freecad-python3/Mod
```

### FreeCAD development builds (26.x): `NameError: name 'random' is not defined`

**Symptom.** The FreeCAD import check passes, but `test_runtime.py` fails: `test_expert_rollouts_clean_and_noisy` with `KeyError: 'L1_noise0.0_n'` and `test_worker_protocol_roundtrip` with `FreeCAD worker exited (code 1)`. Running the scripts directly shows the real error, e.g. `NameError: name 'random' is not defined` in `scripts/smoke_expert.py` or `name 'json' is not defined` in `freecad_s1/runtime/worker.py`.

**Cause.** FreeCAD development builds use calendar versions (seen with `26.3.0`, commit `a4ce44d33`, which conda-forge installed for `freecad>=1.0`). Their `src/App/FreeCADInit.py` ends with a "clean global namespace" step that deletes every module imported in the calling script's `__main__`, except `FreeCAD`, `App`, `os`, `sys`, `traceback` and `inspect`. Any script that imports other modules before FreeCAD loses them.

**Fix.** Use the PPA release. The DGX script from version 2026.10.03.1 always installs FreeCAD from `ppa:bleedingedge/noble-spark-bleed` and stops if it finds a FreeCAD with a major version of 2 or higher. To switch an existing DGX installation from the old conda setup:

```bash
./taiga_repro_DGX.sh setup
```

`~/taiga/fcenv` and `~/taiga/mamba` are no longer used. Remove them to free space, or let `uninstall` do it.

**Verify.** `setup` logs `FreeCAD 1.1.x OK` and `test_runtime.py` passes: `16 passed`, no failures or skips.

---

### FreeCAD worker dies during evaluation: `FreeCAD worker exited (code 1)`

**Symptom.** `eval_perturb` (or, more rarely, DAgger or `eval`) stops with `freecad_s1.runtime.client.WorkerError: FreeCAD worker exited (code 1)`, raised from `vec.score(...)`. Nothing is printed by the worker.

**Cause.** A segmentation fault inside OpenCASCADE 7.9 (`BOPTools_AlgoTools3D::DoSplitSEAMOnFace`, in the boolean `common()` used to compute the IoU of the finished part), on unusual geometry left by an episode. Seen on resolute (OCCT 7.9) with seed 22's model under 20% injected random actions, in an episode where the model repeated `PartDesign_Pad` / `Std_Undo` dozens of times. FreeCAD's signal handler turns the segfault into a silent `exit(1)`, and upstream's lockstep `VecEnv` then aborts the whole evaluation.

**Fix.** From version 2026.10.04.3 the scripts run upstream's training, evaluation and calibration through `taiga_run.py`, which replaces a dead worker with a fresh one and counts only the affected episode as a failure (IoU 0 when scoring crashes; "unrecoverable" when a step crashes). Each crash is printed and recorded in the run's `crashes_*.jsonl`; `eval` reports the count, and `aggregate` shows it per run (`crashes t/e/p`). A crash is a real failure of the episode, so the results stay honest.

**Rerunning an affected run.** Only the evaluation needs to be redone, e.g. `SEED=22 GPU=0 ./taiga_repro_Quadro6000.sh eval`.

---

## Versions

Each script uses `YYYY.MM.DD.x` versioning. The version is in the script header and in each run's `manifest.json`.

| Script | Version |
|---|---|
| `taiga_repro_DGX.sh` | 2026.10.05.6 |
| `taiga_repro_5060ti.sh` | 2026.10.05.5 |
| `taiga_repro_Quadro6000.sh` | 2026.10.05.5 |
| `taiga_aggregate.py` | 2026.10.04.3 |
| `taiga_run.py` | 2026.10.04.1 |
