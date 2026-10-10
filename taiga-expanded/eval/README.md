# S 80 evaluation

Rebuilds the Victor S 80 pump end with Taiga-S1 goals in the taiga-expanded vocabulary and scores every part against the reference CAD in [`../../build/pump_s80_reference_CAD`](../../build/pump_s80_reference_CAD) (volumetric IoU, part by part).

Two questions, answered separately:

1. **Can the vocabulary express the part?** The scripted teacher (the expert that labels the training data) builds each goal: no model involved. Its IoU with the reference is the best any model can reach with these goals.
2. **Does the model build what the goal says?** Each trained model builds the same goals; its IoU with the teacher's build measures the model alone, and its IoU with the reference the end result. With several models (the seeds of a sweep), each part gets a mean ± sd.

## Files

| File | What it is |
|---|---|
| `make_s80_goals.py` | Generates the goals from the reference script's own dimensions (imports `pump_s80.py`, so it needs OCP: run it with `~/taiga/taiga-s1/.venv/bin/python`) |
| `s80_goals.json` | The goals, one per part, for `../../build/taiga_build_part.py` (generated) |
| `s80_eval.json` | Which goal(s) build each reference part, and where they go in pump coordinates (generated) |
| `eval_s80.py` | Scores build folders against the reference (FreeCAD's Python) |
| `voxel_iou.py` | IoU on a voxel grid (numpy), used by `eval_s80.py` |
| `run_s80.sh` | Runs everything: check, teacher builds, model builds, scoring |

## Run

After `../train_expanded.sh <machine> setup` (the teacher needs only the patched code, so this works before any model is trained):

```bash
cd taiga-expanded/eval
./run_s80.sh check              # can every goal be built? (fast, nothing saved)
./run_s80.sh teacher            # teacher builds -> ~/taiga-expanded/s80/teacher
./run_s80.sh eval               # score them against the reference
```

Once the sweep has exported its models (`~/taiga-expanded/taiga-s1/runs/data2/seed*/hf`):

```bash
./run_s80.sh models             # every seed's model builds every goal -> ~/taiga-expanded/s80/seed<N>
./run_s80.sh eval               # teacher and all seeds, with mean ± sd over the seeds
```

Model builds use `taiga_build_part.py`'s loop guard (when the model is back in a state it already acted from and repeats the same action, its most likely untried action is taken instead). To score the models without it: `OUT=~/taiga-expanded/s80_noguard BUILD_ARGS=--no-loop-guard ./run_s80.sh models` (copy or link `s80/teacher` into that folder for the comparison with the teacher). The first model builds (2026-10-06) were made before the guard existed.

`./run_s80.sh models <runs folder>` takes another experiment's runs (e.g. `.../runs/x2_data2`); use a different `OUT` for each experiment. The reference STEP files are read from `~/taiga/taiga-s1/parts/pump_s80` (written by `build_pump_s80.sh`) and generated there if missing. That needs OpenCASCADE's Python bindings (OCP 7.9): the variance-study venv `~/taiga/taiga-s1/.venv` if it has them, otherwise a small separate venv `~/taiga-expanded/ocp-venv`, created on first use (the training venv is left alone). `REF`, `REF_PY`, `WORK` and `OUT` override the defaults.

## Outputs (in `~/taiga-expanded/s80`)

- `teacher/`, `seed<N>/`: the built parts (`<goal>.FCStd`, `.step`) and a log per folder
- `eval_s80.json`, `eval_s80.log`: the scores. Per part and build folder: IoU with the reference, coverage (share of the reference volume the build fills), excess (built volume outside the reference, as a share of the reference volume), volumes. Per goal and model: IoU with the teacher's build. A volume-weighted IoU for the whole pump.
- `s80_overlay_<label>.FCStd`: the reference (grey, transparent) and the build (orange), assembled in pump coordinates

## The goals

| Part | Goal | Features | How |
|---|---|---|---|
| Shaft | `s80_shaft` | 3 | Stepped profile revolved about X; two keyways as pockets on XY datum planes |
| Bearings | `s80_bearing` (placed twice) | 1 | Ring revolved about X |
| Mechanical seal | `s80_mechanical_seal` | 1 | Stepped ring revolved about X |
| Wear plate | `s80_wear_plate` | 3 | Ring revolved about X; hole on its +X face, polar pattern ×6 about X |
| Impeller | `s80_impeller` | 4 | Hub and shroud revolved about X; spline blade on the shroud face, polar ×3 about X; keyway on a YZ datum plane |
| Bearing bracket | `s80_bearing_bracket` | 8 | Flange, hollow lantern and bearing housing in one revolved profile; seal windows through both sides; flange holes ×4 about X; foot web and plate on XY datum planes; foot holes, mirrored |
| Priming cover | `s80_priming_cover` | 5 | Original vocabulary only: base cylinder, cap, holes ×4, handle |
| Check valve | `s80_check_valve` | 2 | Weight plate; flap and hinge tab as one curved outline |
| Inspection cover | `s80_inspection_cover` | 6 | Spigot; rounded plate outline; two rows of 3 holes |
| Casing | `s80_casing` | 52 | Rounded body outline; feet (webs and plates on datum planes, repeated); suction chamber, separation chamber, volute spiral, duct and impeller eye as pockets on datum planes inside the body; rear hub, seal bore, tapped holes; suction neck, flange, bore and 8 bolt holes; discharge neck, flange, bore and 4 holes on top; priming boss; inspection opening and tapped holes; drain boss and hole; foot bolt holes |
| Casing (core) | `s80_casing_core` | 24 | The casing without its small bolt and tapped holes (and their patterns) |

The casing's 52 features are far beyond anything in training (at most 5 features; the longest test suite has 17), which is why `s80_casing_core` exists: if a model fails the casing but builds the core, the problem is length, not vocabulary.

Goal frames: parts turned about the shaft use pump coordinates (shaft along X through the origin). The casing, inspection cover and check valve are built with their main extrusion along Z, as every Taiga base feature is, and rotated into place (120° about (1, 1, 1)). The priming cover is built upright and moved.

**Teacher scores** (spark DGX, FreeCAD 1.1.3, 8 patches, 2026-10-05): every goal builds, and the teacher matches its own target exactly (IoU 1.0000 in the build check). Against the reference:

| Part | IoU | Note |
|---|---|---|
| Casing | 0.9986 | 52 features, 267 steps; 11 min (2.5 s per FreeCAD step on the full part) |
| Casing core | 0.9883 | without bolt and tapped holes; 155 steps, 95 s |
| Impeller | 0.9986 | spline blades (FreeCAD's interpolated B-spline vs the reference's approximated one) |
| Wear plate, covers, check valve, bearing bracket, shaft, bearings, seal | 1.0000 | |
| Whole pump (volume-weighted) | 0.9989 | |

So the vocabulary expresses the S 80 to within 0.2 % of its volume; anything a model loses beyond that is the model's. The casing's FreeCAD steps get slow as the part grows (each step re-reads the whole shape), which matters for model builds: 5 seeds take about an hour for the casing alone.

**Model scores, patch 0009** (Spark2, 5 seeds, patches 0001–0009, 2× data, 8 epochs, loop guard on, 2026-10-08). IoU with the reference:

| Part | Features | Seeds at IoU ≥ 0.999 | Mean ± sd | Step-1 models (0001–0008, 4 epochs) |
|---|---|---|---|---|
| Wear plate, inspection cover, priming cover, check valve, shaft, mechanical seal | 1–8 | 5 / 5 | 1.000 | failed on XY datum planes and overhangs (cover caps, check-valve flap, shaft keyways) |
| Bearings | 1 (revolve far from the origin) | 3 / 5 | 0.60 ± 0.55 | |
| Impeller | 3 (revolve, spline blade, polar pattern) | 2 / 5 | 0.40 ± 0.55 | |
| Bearing bracket | 13 | 0 / 5 | 0.55 ± 0.16 | every seed runs out of steps (106) |
| Casing core / casing | 24 / 52 | 0 / 5 | 0.57 ± 0.27 / 0.48 ± 0.28 | |
| Whole pump (volume-weighted) | | | 0.33–0.76 per seed | |

- Patch 0009 fixed what it was made for: every part whose failure was an XY datum plane or an overhang now builds exactly on every seed.
- The bearings fail on two seeds although the goal is a single revolve: its profile sits 211–228 mm along the shaft, far from the origin compared with its 17 mm width. The revolve samplers put profiles near the origin, so this is a position the model has not seen (positions are divided by the part's scale, so the offset looks like 12× the part).
- The bearing bracket (13 features) exhausts its step budget on every seed and the casing parts collapse after a few dozen steps, with the loop guard stepping in about every other step: long goals, the limit already seen in `len6`.

**Same models, fixed loop guard** (`taiga_build_part.py` 2026.10.08.1: a step that worked and was then undone by the model is redone, and Undo is blocked after it; Spark2, 2026-10-08, `OUT=~/taiga-expanded-0009/s80_g2`):

| Part | Seeds at IoU ≥ 0.999 | Mean ± sd | Before the fix |
|---|---|---|---|
| Six short parts (as above) | 5 / 5 | 1.000 | 5 / 5 |
| Bearings | **5 / 5** | 1.000 | 3 / 5, 0.60 |
| Impeller | **3 / 5** | 0.91 ± 0.12 | 2 / 5, 0.40 |
| Bearing bracket | 0 / 5 | **0.994 ± 0.002** | 0.55 ± 0.16 |
| Casing core / casing | 0 / 5 | 0.56 ± 0.29 / 0.58 ± 0.30 | 0.57 / 0.48 |
| Whole pump (volume-weighted) | | 0.26–0.84 per seed (0.84 on three seeds) | 0.33–0.76 |

- **The guard's teardowns were most of the short-part losses.** The bearings now build on every seed; the impeller on three, and on the other two the model runs out of steps (54) after part of the blade pattern (IoU 0.78).
- **The bracket is now almost right on every seed** (0.99 against the reference), where it lost half its volume before. A step-by-step build (seed 2) shows why it is not exact: the first 42 steps (revolve, window, flange holes and their pattern, the foot on two XY datum planes) match the teacher, then the two holes on the foot's **underside** (`Face-Z`) are put on `Face+Y` instead. The missing Ø18 holes in the 16 mm plate are exactly the 8 cm³ excess. No training family puts features on a bottom face, so this is a coverage gap, like the XY datum planes before patch 0009. The build's own check scores 0 on three seeds because those builds ran out of steps in the middle of the recovery (an open sketch); the exported STEP only shows the body as it stood.
- **The guard cannot tell a right step from a wrong one.** The model correctly undid the hole on the wrong face; the guard took it for a spurious Undo (FreeCAD had accepted the step), redid it and blocked Undo, and the model flailed until the step budget ran out.

**Guard variants on the same models** (Spark2, 2026-10-08): no guard (`--no-loop-guard`), the redo exception limited to once per part (`--guard-redos 1`), and without a limit (`s80_g2` above). Seeds exact (IoU ≥ 0.999) / mean IoU against the reference:

| Part | No guard | Redo once per part | Redo without limit |
|---|---|---|---|
| Six short parts | 5 / 5 | 5 / 5 | 5 / 5 |
| Bearings | 3 / 5 (two loop until out of steps) | 5 / 5 | 5 / 5 |
| Impeller | 3 / 5, 0.91 | 3 / 5, 0.60 | 3 / 5, 0.91 |
| Bearing bracket | 0 / 5, 0.79 | 0 / 5, 0.73 | 0 / 5, **0.99** |
| Casing core / casing | 0 / 5, 0.48 / 0.49 | 0 / 5, 0.66 / 0.57 | 0 / 5, 0.56 / 0.58 |

- **Without a guard the model loops:** every failure ends out of steps, repeating a dead end (22 steps on a single revolve, the full 540 on the casing). Breaking repeated dead ends is needed.
- **The two guard variants build exactly the same parts.** Without a limit, failed builds keep more of their correct steps (the bracket complete except the two bottom holes); with the limit, the old teardown comes back after the first redo. `taiga_build_part.py` 2026.10.08.3 therefore sets no limit by default (`--guard-redos -1`; a number limits it).
- **What is left is training, not the guard:** the bracket's bottom-face holes (next patch), the impeller's blade pattern on two seeds (patch 0010's `turned` family) and the casing's length and input limits (`../README.md`, primitive 8).
- **The casing parts are unchanged:** they fail on length and on the model's input limits (`../README.md`, primitive 8), not on the guard.

## Limits of the vocabulary found on the way

- **Patterns only about the origin axes.** The suction flange's 8 bolt holes are on a circle around the port axis (z = 110), not the shaft axis, so they are written as 4 holes, each mirrored. A polar pattern about a feature's own axis would make it one hole and one pattern.
- **No pattern of a pattern.** The discharge flange's 2 × 2 holes are two mirrored pairs, and the foot holes two repeated pairs.
- **Pads start at the sketch plane.** The discharge neck is wider than the flat top of the casing; the reference neck fills down to the rounded corners, the Taiga neck starts at the top face (a few cm³).
- **Internal cavities** are pockets on datum planes inside the body, extruded both ways. That works for the 2.5D chambers of this model; real cast cavities with rounded, varying sections would need sweeps and lofts (Phase 2).
- **Long goals.** A real casing is ~50 features; the models are trained on ≤ 5.

## Versions

| File | Version |
|---|---|
| `make_s80_goals.py` | 2026.10.05.2 |
| `eval_s80.py` | 2026.10.10.1 |
| `voxel_iou.py` | 2026.10.06.1 |
| `run_s80.sh` | 2026.10.10.1 |
