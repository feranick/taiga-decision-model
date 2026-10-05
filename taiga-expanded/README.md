# taiga-expanded

Extends Taiga-S1's vocabulary so it can build real parts like the Victor S 80 pump (`../build/pump_s80_reference_CAD`). The changes are kept as a **git patch series** on a pinned upstream commit, not as a fork. Training, sweeps and evaluation reuse the scripts in `../training`.

## Status

| # | Primitive | State |
|---|---|---|
| 1 | **Features on any planar face** (±X, ±Y, ±Z): holes, pockets, bosses on side faces; new evaluation suite `side` | Patch 0001. All 38 tests pass, including the FreeCAD ones (DGX, FreeCAD 1.1.3, 2026-10-05) |
| 2 | **Features on origin planes with an offset**, extruded symmetrically: cross bores (e.g. a piston-pin bore through a cylinder wall), through windows, cross pins and lugs; new evaluation suite `plane` | Patch 0002. Pure-Python tests pass; FreeCAD tests run during `setup` |
| 3 | **Curved outlines**: closed profiles of lines, arcs and splines, drawn by one command (`Sketcher_CreateProfile`) and fixed by `Sketcher_ConstrainBlock`; outline bases, bosses and pockets on any face or plane; new evaluation suite `outline` | Patch 0003. Pure-Python tests pass; FreeCAD tests run during `setup` |
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
  - New commands and goal kinds are appended to the vocabularies, so upstream ids keep their values. From patch 0003 the vocabulary is larger, so the published `shhivv/taiga-s1` weights no longer fit the patched code. `train_expanded.sh` therefore skips the published-model evaluation (`REF_EVAL=0`); evaluate it on the baseline (`BASELINE=1`) instead.
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

### Primitive 2: features on origin planes with an offset

A goal feature with `off` is sketched on the origin plane normal to (nx, ny, nz), shifted by `off` along that axis, and extruded **symmetrically** on both sides of the plane:

```json
{"kind": "hole", "params": {"r": 4, "x": 0, "y": 0, "z": 15, "nx": 0, "ny": 1, "nz": 0, "off": 0}}
```

- **Where:** `Select:Plane:XZ` here (normal Y). `off` shifts the sketch along Y, and the centre (x, y, z) lies on the shifted plane.
- **What it gives, by kind:**
  - `hole`: a cross bore through the whole part, e.g. a piston-pin or crank bore through a cylinder wall.
  - `pocket_rect`, deeper than the part: a through window.
  - `boss_cyl` / `boss_box`, longer than the part: a cross pin or lugs sticking out on both sides.
- **No new commands:** the model chooses the plane. The runtime applies the offset (`AttachmentOffset`, with the sign taken from the plane's own normal) and the symmetric extrusion (`Midplane`, or `SideType` where FreeCAD has it).
- **New training goals:** box or cylinder bases with 1–2 datum-plane features, optionally a top feature from the original vocabulary and a top dressup. Training draws the expanded goals from the `side` and `plane` samplers at random.
- **New evaluation suite `plane`:** level 3, on its own seeds.

### Primitive 3: curved outlines

New kinds `profile_base`, `profile_boss` and `profile_pocket` carry a closed outline:

```json
{"kind": "profile_pocket", "params": {"depth": 4, "outline": {
  "start": [-10, -3], "segs": [["L", 10, -3], ["A", 13, 0, 10, 3], ["L", -10, 3], ["A", -13, 0, -10, -3]]}}}
```

- **Segments:** `["L", u, v]` is a line to (u, v), `["A", um, vm, u, v]` an arc through (um, vm) to (u, v), and `["S", [[u, v], ...]]` a spline through points. The last point closes the outline at `start`.
- **Coordinates:** (u, v) are global in-plane coordinates of the sketch plane. On the top face and the XY plane, u = X and v = Y. Normal to X, u = Y and v = Z. Normal to Y, u = X and v = Z.
- **Where:** with `nx`/`ny`/`nz` the outline goes on a side face, and with `off` on a datum plane (primitives 1 and 2). `profile_pocket` without `depth` cuts through all.
- **One command per outline:** `Sketcher_CreateProfile` draws the whole outline, and `Sketcher_ConstrainBlock` fixes it. The model decides when and on which face; the runtime takes the points from the goal. The model sees only a summary: the number of lines, arcs and splines, and the outline's size.
- **Arcs** are converted to FreeCAD's counter-clockwise sketch arcs.
- **Splines** are sketch B-splines. If a FreeCAD build has trouble with them, `TAIGA_SPLINE=polyline` draws them as short lines instead.
- **Mistakes:** if the model chooses `CreateProfile` for an intent without an outline, a default outline is drawn so there is something to undo, as upstream does for other wrong commands.
- **New training goals:** outline bases (rounded rectangle, slot, polygon, D shape, spline blob) with profile bosses, pockets and holes on top, polar patterns of profile features (blade or slot rings), curved windows on side faces, and top dressups.
- **New evaluation suite `outline`:** level 3.

This is what the S 80's curved casing outline, its blades, the covers and the wear-plate cutter need, apart from the revolve parts (primitive 4).

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
| `train_expanded.sh` | 2026.10.05.4 |
| `dev/*.sh` | 2026.10.05.1 (`export_patches.sh` 2026.10.05.2) |
| `patches/` | 0001 (features on any planar face), 0002 (features on origin planes with an offset), 0003 (curved outlines), against upstream `4a31bcf` |
