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
| `eval_s80.py` | 2026.10.05.2 |
| `voxel_iou.py` | 2026.10.05.1 |
| `run_s80.sh` | 2026.10.05.2 |
