# taiga-decision-model

Tools to train, evaluate and use [Taiga-S1](https://github.com/shhivv/biome-s1), a small decision model for FreeCAD from the Biome-S1 model family, across different operating systems, FreeCAD versions and GPUs.

## What Taiga-S1 is

Taiga-S1 is a 1.2M-parameter transformer that builds parts in FreeCAD PartDesign one command at a time. At each step it reads the live FreeCAD state (feature tree, selection, sketch constraints, workbench) and scores the commands that are currently valid. It runs in about 1 ms per decision on a CPU, with no LLM, no vision model and no screenshots.

It is meant as the fast "System 1" layer of a CAD agent:

```
requirement ──► planner (e.g. an LLM) ──► goal: ordered features + dimensions ──► Taiga-S1 ──► FreeCAD commands ──► part (.FCStd)
```

The planner decides *what* to build; Taiga-S1 decides *how*, command by command. Dimensions come from the goal, not from the model.

## Approach

- **One model per platform.** Training data is generated in headless FreeCAD, so each OS and FreeCAD version gets its own data and model. Runs are not meant to match upstream exactly. Every run records its script, repo commit, OS, FreeCAD, PyTorch, CUDA, driver and GPU versions in `manifest.json`.
- **Upstream pipeline, pinned.** Training and evaluation use upstream's own code at commit `a6e81d3`, with the flags from its `train_final.sh`.
- **Same yardstick everywhere.** Every machine evaluates on the same test goals (seed 3), and also evaluates the published `shhivv/taiga-s1` on its own FreeCAD as a per-machine baseline.

## Repository structure

| Folder | Contents | Documentation |
|---|---|---|
| [`training/`](training/) | Setup, data generation, training, evaluation and calibration, one script per machine; multi-seed sweeps and variance analysis | [`README_training.md`](training/README_training.md) |
| [`build/`](build/) | Building parts with a trained model (inference) and example goals | [`README_build_part.md`](build/README_build_part.md) |
| [`taiga-expanded/`](taiga-expanded/) | Extends Taiga-S1's vocabulary (side faces, curved outlines, revolve, …) as a patch series on a pinned upstream commit; trains with the scripts in `training/` | [`README.md`](taiga-expanded/README.md) |

More folders will be added as the project grows.

```
taiga-decision-model/
├── README.md                     this file
├── training/
│   ├── README_training.md        stages, environment variables, outputs, variance studies, known issues
│   ├── taiga_aggregate.py        distribution of results across runs (used by sweep/aggregate)
│   ├── taiga_bench_size.py       time per decision at larger model sizes (random weights, CPU/GPU)
│   ├── taiga_run.py              runs upstream training/evaluation with FreeCAD worker-crash recovery
│   ├── taiga_repro_DGX.sh        NVIDIA DGX Spark, Ubuntu 24.04 (noble)
│   ├── taiga_repro_5060ti.sh     PowerSpec G467 (RTX 5060 Ti), Ubuntu 26.04 (resolute)
│   └── taiga_repro_Quadro6000.sh Dell Precision 7920 (2 × Quadro RTX 6000), Ubuntu 26.04 (resolute)
├── build/
│   ├── README_build_part.md      goal format, feature types, output, GUI demo
│   ├── taiga_build_part.py       builds parts headless and saves .FCStd files
│   ├── flange/
│   │   └── example_goals.json        simple example goals (bracket plate, spacer, bolt-circle disc)
│   ├── engine/
│   │   ├── example_goals_engine.json single-cylinder engine parts kit
│   │   ├── assembly_engine.json      assembly spec for the engine kit
│   │   └── build_engine.sh           checks, builds and assembles the engine kit
│   ├── pump/
│   │   ├── example_goals_pump.json   parts kit for a Victor Pumps S 80 self-priming pump (from the brochure)
│   │   ├── assembly_pump.json        assembly spec for the pump kit
│   │   ├── build_pump.sh             checks, builds and assembles the pump kit
│   │   └── README.md                 where each dimension comes from
│   ├── pump_s80_reference_CAD/        reference geometry for a future Taiga model (not built by Taiga)
│   │   ├── pump_s80.py               parametric model of the real S 80 pump end (curved casing, volute, impeller)
│   │   ├── build_pump_s80.sh         generates the STEP parts and assembles them in FreeCAD
│   │   ├── README.md                 internal layout, design rules, dimensions
│   │   └── images/                   3D view and cross-sections
│   └── taiga_assemble.py         places built parts into one assembly (.FCStd + .step)
└── taiga-expanded/
    ├── README.md                 design, status of each primitive, workflow
    ├── patches/                  git patch series against upstream (+ BASE commit)
    ├── dev/                      make the dev branch, export and check the patches
    ├── eval/                     S 80 goals; builds them (teacher, models) and scores them against the reference CAD
    ├── datasets/                 plan: public CAD build histories (DeepCAD) as training and test goals
    └── train_expanded.sh         trains the patched (or baseline) model with the training scripts
```

## Tested platforms

| Machine | OS | FreeCAD | GPU | Script |
|---|---|---|---|---|
| NVIDIA DGX Spark (aarch64) | DGX OS / Ubuntu 24.04 (noble) | 1.1.x (Qt5 build) from `ppa:bleedingedge/noble-spark-bleed` | GB10, CUDA 13 | `training/taiga_repro_DGX.sh` |
| PowerSpec G467 | Ubuntu 26.04 (resolute) | 1.1.x from `ppa:bleedingedge/resolute-bleed` | RTX 5060 Ti (Blackwell) | `training/taiga_repro_5060ti.sh` |
| Dell Precision 7920 Tower | Ubuntu 26.04 (resolute) | 1.1.x from `ppa:bleedingedge/resolute-bleed` | 2 × Quadro RTX 6000 (Turing) | `training/taiga_repro_Quadro6000.sh` |

The model is small, so the GPU is not the bottleneck: most of the time goes into the FreeCAD steps, which run on the CPU.

## Getting started

1. **Train a model** on your machine (example: PowerSpec G467):
   ```bash
   cd training
   ./taiga_repro_5060ti.sh setup                     # interactive (sudo)
   ./taiga_repro_5060ti.sh data train export eval calib
   ```
   See [`training/README_training.md`](training/README_training.md) for the other machines, running detached and the outputs.

2. **Build a part** with it:
   ```bash
   cd ~/taiga/taiga-s1 && source ~/taiga/freecad.env
   .venv/bin/python <repo>/build/taiga_build_part.py --model runs/seed2/hf \
       --goals showcase/goals.json --name flange --out parts
   ```
   `<repo>` is the folder of this repository. See [`build/README_build_part.md`](build/README_build_part.md) for writing your own goals.

## Versioning

Scripts use `YYYY.MM.DD.x` versioning. Each script's version is in its header, in the version table of its folder's README and, for training runs, in `manifest.json`.

## Credits

Taiga-S1, its training pipeline and the published weights are by Shiv Shanmugam ([shhivv/biome-s1](https://github.com/shhivv/biome-s1), formerly `shhivv/taiga-s1`, MIT license; weights at [huggingface.co/shhivv/taiga-s1](https://huggingface.co/shhivv/taiga-s1)). The repository now also hosts Mesa-S1, a sibling model that operates FreeCAD's interface; the scripts here cover Taiga-S1 only. This repository contains only setup, training and usage scripts around it.
