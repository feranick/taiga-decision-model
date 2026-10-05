# taiga-expanded

Extends Taiga-S1's vocabulary so it can build real parts like the Victor S 80 pump (`../build/pump_s80_reference_CAD`). The changes are kept as a **git patch series** on a pinned upstream commit, not as a fork. Training, sweeps and evaluation reuse the scripts in `../training`.

## Status

| # | Primitive | State |
|---|---|---|
| 1 | **Features on any planar face** (±X, ±Y, ±Z): holes, pockets, bosses on side faces; new evaluation suite `side` | Patch 0001. All 38 tests pass, including the FreeCAD ones (DGX, FreeCAD 1.1.3, 2026-10-05) |
| 2 | Sketches on origin planes with an offset (datum planes inside the part) | Planned |
| 3 | Curved outlines: arc, slot, closed polyline, spline (drawn as one command from the goal's data) | Planned |
| 4 | Pad/pocket of a given depth from any face; revolve and groove with a profile | Planned |
| 5 | Patterns of any feature (bosses included); fillet/chamfer on chosen edges | Planned |
| 6 | Training: size range up to ~400 mm; perturbed DAgger (`--dagger-perturb`); new suites per primitive | Planned |

Out of scope for now (Phase 2): sweep, loft, helix, multi-body parts, assemblies.

## Layout

```
taiga-expanded/
├── README.md            this file
├── patches/             the patch series (git format-patch) + BASE (upstream commit it applies to)
├── dev/                 make_dev_branch.sh, export_patches.sh, check_patches.sh (+ config.sh)
├── train_expanded.sh    runs ../training/taiga_repro_<machine>.sh on the patched (or baseline) code
├── goals/               test goals for the new features (planned)
└── eval/                part-by-part comparison with the S 80 reference (planned)
```

## Design

- **Base:** upstream `shhivv/biome-s1` at `4a31bcf` (current HEAD when this started). It includes perturbed DAgger rollouts and failure histories in `evaluate.py`.
- **Small footprint in upstream files:** new code lives in `freecad_s1/ext/`. Upstream files only get short hooks marked `taiga-expanded`, so the series rebases easily onto a newer upstream. Patch 0001 adds 19 lines (and changes 4) in 5 upstream files; everything else (431 lines, incl. tests) is in new files.
- **The model still chooses commands; the runtime fills in numbers.** New geometry comes from the goal, not from the model. A curved outline (primitive 3) will be one command, "draw the goal's profile". The model learns when and where to use it, not the coordinates.
- **Original behaviour preserved:**
  - Goals without the new parameters featurize exactly as before: the new parameters go in an extra block that is all zeros for original goals.
  - The original evaluation suites (`iid`, `comp*`, `len*`) produce exactly the same goals for the same seeds.
  - Training mixes the expanded goals in: by default 35 % of training goals use the new features, set by `TAIGA_EXT_FRACTION`. The rest come from the original sampler.
- **Separate work folders:** the expanded model uses `~/taiga-expanded`, and the unpatched baseline at the same commit uses `~/taiga-head`. Neither touches your ongoing variance study in `~/taiga`. The training scripts refuse to run when the code in a work folder doesn't match `PATCHES`.

### Primitive 1: features on any planar face

A goal feature can carry a support normal and a global centre:

```json
{"kind": "hole", "params": {"r": 4, "x": 20, "y": 0, "z": 12, "nx": 1, "ny": 0, "nz": 0}}
```

- **Where:** the sketch goes on the largest planar face whose outward normal is (nx, ny, nz), here `Select:Face+X`, instead of the top face.
- **Centre:** (x, y, z) is in global coordinates. The coordinate along the normal comes from the face itself.
- **Rectangle sizes:** `w` runs along the first in-plane axis (X, then Y, then Z) and `d` along the second.
- **Kinds:** all sketched top-face kinds work this way: `hole`, `hole_std`, `pocket_rect`, `boss_cyl`, `boss_box`.
- **New training goals:** box bases with 1–2 side features, optionally a top feature from the original vocabulary, a mirrored side boss or pocket, and a top dressup.
- **New evaluation suite `side`:** level 3, on its own seeds.

Changes to upstream files: the teacher picks the side face (`expert.py`), the runtime maps the profile onto it (`runtime/session.py`), the goal encoding adds the extra block (`model/featurize.py`), and `goals.py`/`evaluate.py` add the new split and suite.

## Workflow

### Develop

```bash
cd taiga-expanded/dev
./make_dev_branch.sh                 # ~/taiga/taiga-expanded-dev: upstream at BASE + current patches, branch "expanded"
# edit and commit in ~/taiga/taiga-expanded-dev, run its tests ...
./export_patches.sh                  # rewrite ../patches from the branch's commits
./check_patches.sh                   # fresh clone + git am + full test suite
```

For `check_patches.sh`, run `source ~/taiga/freecad.env` first to include the FreeCAD tests (`tests/test_ext_runtime.py`, `scripts/smoke_ext.py`). Without it they are skipped.

### Train and evaluate

```bash
./train_expanded.sh 5060ti setup                                # ~/taiga-expanded: clone, apply patches, venv, FreeCAD, tests
DATA_SEED=2 ./train_expanded.sh 5060ti sweep                    # expanded model, 5 seeds, suites incl. "side"
BASELINE=1 ./train_expanded.sh 5060ti setup                     # ~/taiga-head: same upstream commit, no patches
BASELINE=1 DATA_SEED=2 ./train_expanded.sh 5060ti sweep         # baseline for the no-regression check
```

Compare the original suites between the two with `../training/taiga_aggregate.py` (mean ± sd over seeds), e.g. `taiga_aggregate.py ~/taiga-head/taiga-s1/runs/data2 ~/taiga-expanded/taiga-s1/runs/data2`.

## Success criteria

1. **No regression:** on the original suites, the expanded model's distribution over seeds is not worse than the baseline's at the same commit.
2. **New features:** high clean and perturbed success on each new suite.
3. **Real parts:** S 80 parts rebuilt with Taiga goals, scored by IoU against `../build/pump_s80_reference_CAD` (planned in `eval/`).

## Versions

| File | Version |
|---|---|
| `train_expanded.sh` | 2026.10.05.2 |
| `dev/*.sh` | 2026.10.05.1 |
| `patches/` | 0001 (features on any planar face), against upstream `4a31bcf` |
