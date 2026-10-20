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
| `feature_check.py` | Which goal intents each build realized (a feature-level check next to IoU) |
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

**Patch-0013 models, upstream input limits** (5060 Ti, 2× data, 8 epochs, 5 seeds, loop guard on, 2026-10-11). Seeds exact / mean IoU against the reference:

| Part | 0013 | 0010, same budget (Quadro) |
|---|---|---|
| Six short parts, bearings, impeller | 5 / 5 | 5 / 5 |
| Bearing bracket | **4 / 5** (IoU 1.0000), 0.93 | 0 / 5, 0.99 on four seeds |
| Casing | 0 / 5, **0.81 ± 0.15** (0.83–0.90 on four seeds) | 0 / 5, 0.37 |
| Casing core | 0 / 5, 0.60 ± 0.31 | 0 / 5, 0.61 |
| Whole pump (volume-weighted) | **0.87–0.92 on four seeds** | 0.19–0.84 |

- **The bracket builds exactly** on four seeds: patch 0011's bottom-face features were the missing piece.
- **The casing gets further** (0.83–0.90 on four seeds against the earlier ceiling of 0.80) even with the 40-object input limit; it still fails, and the casing core (24 intents) is erratic. Whether the raised limits help is the Quadro run's question.

**Where the casing build goes wrong** (verbose builds, 2026-10-12): `long1` seed 32 (7M, DeepCAD goals) and `long2` seed 2 (matched ordinal and position tables) both build the casing correctly up to feature 18, the discharge neck on top. Feet, chambers on datum planes and the rear hub are right (spurious Undos after the feet's linear patterns are caught by the loop guard). For feature 18 the model selects `Plane:XY` where the teacher selects `Face+Z` (step 101 and step 95), and the build does not recover (`long2`: IoU 0.0 after 540 steps). Feature 18 is `boss_cyl` with `nz: 1` and `at` on the top face; the six features before it are pockets on XY datum planes, also `nz: 1` but with `off`. Training before patch 0016 never wrote a top-face feature with an explicit `nz: 1` (top features had no normal; `nz: 1` came only with `off`), so the model read it as a datum feature. A coverage gap, not ordinals or length. Two fixes:

- **Now, for existing models:** `taiga_build_part.py --canon-top` (2026.10.12.1) drops the explicit `+Z` normal from non-datum features, the form those models learned (the part is the same). For the S 80 run: `BUILD_ARGS=--canon-top OUT=$WORK/s80_canon ./run_s80.sh models <RUNS>`.
- **For production models:** patch 0016 trains on both forms, and on cavities on datum planes followed by stacks picked by `at` in the same goal (suites `mixed_at`, `mixed_at_long`).

**Patch-0014 models, raised input limits with matched tables** (`--max-nodes 160 --max-goal 64 --max-ord 72 --max-pos 256 --ord-table 28 --pos-table 96 --newest-nodes`; 2× data, 8 epochs, 5 seeds, loop guard on, 2026-10-13). Casing IoU per seed (seeds 12, 2, 22, 32, 42), plain goals and with `--canon-top`:

| Run | `len6` | Casing, plain | Casing, `--canon-top` | Casing core, `--canon-top` |
|---|---|---|---|---|
| `s0014`: 1.2M, synthetic goals only (Quadro) | 0.99 | 0.90 0.82 0.85 0.74 0.81 | 0.90 0.80 0.82 0.83 0.80 | 0.96 0.77 0.99 0.90 0.78 |
| `dc1`: 1.2M, DeepCAD goals at 25 % (Spark1) | 1.00 | 0.21 0.81 0.18 0.34 0.87 | 0.46 0.90 0.34 0.35 0.00¹ | **0.99 on all five** |
| `long2`: 7M, DeepCAD goals at 25 % (Spark2) | 0.99 | 0.21 0.00 0.80 0.81 0.63 | **0.97** 0.34 **0.93** 0.78 0.65 | **0.98–0.99 on all five** |

¹ FreeCAD worker hung for 6 hours on one step, killed: CRASH (since then `taiga_build_part.py --step-timeout`).

The nine other parts (impeller, wear plate, covers, check valve, bearing bracket, shaft, bearings, seal) are exact on all 15 models with and without `--canon-top`, except `s0014` seed 22's bracket (0.0, a failed build).

- **The matched tables fix length** at both sizes and with or without DeepCAD goals: `len6` 0.99–1.00 (the raised limits with the default tables had given 0.67–0.73).
- **DeepCAD goals make the casing core (24 intents) exact on every seed** once the flange is written in the learned form; without them it stalls between 0.77 and 0.99. They also make the plain casing worse: every sketch in a DeepCAD goal sits on a plane with an offset, which reinforces "`nz: 1` means a datum plane", so the explicit top flange is wrecked (excess up to 4×). `--canon-top` removes it; patch 0016 trains both meanings.
- **The full casing (52 intents) needs the larger model:** with `--canon-top`, 7M reaches 0.97 and 0.93 on two seeds and 0.74 ± 0.26 on average, 1.2M at most 0.90 and 0.41 ± 0.32 (0.51 without the hung build). The rest of the failures are the missing recovery: one wrong step (e.g. a skipped mirror at step 175) and the model never takes the teacher's Undo.

**Patch-0010 models** (Quadro, 2× data, 8 epochs, 5 seeds, 2026-10-10). Seeds exact / mean IoU against the reference:

| Part | With guard | Without guard |
|---|---|---|
| Six short parts | 5 / 5 | 5 / 5 |
| Bearings | 5 / 5 | **5 / 5** |
| Impeller | 5 / 5 | **5 / 5** |
| Bearing bracket | 0 / 5, 0.99 on four seeds | 0 / 5, 0.99 on three seeds |
| Casing core / casing | 0 / 5, 0.61 / 0.37 | 0 / 5, 0.49 / 0.49 |

The default-budget models (5060 Ti, 1× data, 4 epochs, 2026-10-10) give the same picture: six short parts, bearings and impeller exact on 5 / 5 with and without the guard; bracket 0.99 on every seed (one no-guard build missing); casing 0.72 ± 0.14 with the guard, 0.78 ± 0.03 without; whole pump 0.85 on three seeds either way.

- **The casing stops at the same point on most seeds and both budgets:** IoU 0.7969, coverage 0.8248 on eight of the ten guarded and unguarded 5060 Ti builds and on several Quadro builds. Identical results from different models suggest a hard limit rather than a learned mistake, most likely the 40 tree objects the model can see (about 19 sketch-based features; the casing has 52). Patch 0011's larger input limits are the test (Quadro run `s0012`).
- **The bearings and the impeller now build on every seed without the guard:** patch 0010's off-origin revolves and turned hubs fixed what the guard had been covering. The bracket still lacks its bottom-face holes (patch 0011), and the casing parts still fail on length and the input limits (patch 0011).
- **The build check was fooled once.** Seed 12's casing was reported `SUCCESS  IoU 46.9705` while it scores 0.0009 against the reference: a malformed solid with a negative volume made upstream's IoU formula exceed 1, and anything above 0.999 counts as a match. Fixed in `../../build/taiga_build_part.py` 2026.10.10.1 (an IoU outside [0, 1] is a failure) and, for the runtime itself (training, evaluation suites), in patch 0012.
- `eval_s80.py` 2026.10.10.1 also scores builds with no volume (open shells, loose faces) as 0; before, their triangles could still enclose voxels (e.g. IoU 0.28 with "vol 0.0").
- **The casing parts are unchanged:** they fail on length and on the model's input limits (`../README.md`, primitive 8), not on the guard.

**Patch-0016 pilots: 7M long model, patch 0016, `TAIGA_KEEP_HELDOUT=1`, DeepCAD goals at 25 %** (2× data, 8 epochs, 3 seeds, plain goals: no `--canon-top`, 2026-10-14). IoU per seed (seeds 12, 2, 22):

| Run | Casing | Casing core | The nine other parts | Whole pump |
|---|---|---|---|---|
| Control (Spark1) | 0.97 0.94 0.22 | 0.99 on all three | exact on all three | 0.98 0.95 0.28 |
| `DAGGER_PERTURB=0.2` (Spark2) | **0.973 0.966 0.973** | 0.99 on all three | exact on all three | **0.98 0.97 0.98** |

- **Patch 0016 fixes the flange:** the goals as written now build as well as the `--canon-top` builds of the patch-0014 models did at their best; the casing core is exact on every seed.
- **Perturbed DAgger makes the long part reliable:** all three casings at 0.97 (± 0.004), against one broken build in three without it, although the synthetic suites under perturbation show no difference (`len6` 0.85 against 0.86). Recovery on a real long part is what it buys.
- **The casing is not yet exact:** 0.973 against the teacher's 0.998, the same value on two seeds. The verbose build (seed 12, `DAGGER_PERTURB=0.2`) is correct for 176 steps; then, after the suction port's first bolt-hole pair (hole, mirror), it mirrors again where the teacher starts the next hole on `Face+Y`, and the teacher's Undos come too late to repair it. Training had one mirrored hole per flange; patch 0017 adds flanges with up to three mirrored pairs.

**Patch-0017 model, 4.8M states** (Quadro, 4 epochs, 1 seed, 2026-10-15): casing 0.974, every other part exact. The verbose build skips the suction port's second mirror (step 187, after 5 mirrors and 17 pockets), where the 0016 pilot had mirrored once too often after the first pair. More rim-pair training did not fix it: the model's own "already built?" pointer miscounts runs of near-identical intents deep in a long goal. `taiga_build_part.py --guided-progress` (2026.10.15.1) lets the runtime supply the active intent from the teacher's progress tracking, for complete goals; the model still picks every command.

**With `--guided-progress`, the whole S 80 builds exactly** (same 4.8M model, Quadro, 2026-10-15): casing 0.9983, the teacher's own score (it was 0.974 with the model's own pointer); casing core 0.9876 (the most a core can score against the full casing); impeller 0.9989 (the teacher's); every other part 1.0000; whole pump **0.9987**, identical to the teacher. With the active intent supplied, the model makes every one of the casing's several hundred command choices correctly; what limited it was only keeping count of the intents deep in a long goal.

**Feature-level check** (`feature_check.py`, `./run_s80.sh features`, 2026-10-15). IoU hides missing features: the 0.97 casing lacks about 15 intents. The teacher builds each goal once more and saves the part after every finished intent (`taiga_build_part.py --teacher --prefixes`); each intent's contribution to the final part is the region it added or removed that survives in the target, and a build realizes the intent if it agrees with the target on at least 90 % of that region (voxels, any build order). The result names the missing intents per build, e.g. `intents 37/52 … missing 35:mirror, 36:boss_cyl, …`, plus material outside the target ("excess"). Intents too small for the grid (e.g. 0.5 mm chamfers) are reported as "small", ones cut away later as "hidden".

First use (4.8M patch-0017 model, Quadro, 2026-10-15):

| Build | Casing IoU | Casing intents | Casing core | Bearing bracket |
|---|---|---|---|---|
| Own intent pointer | 0.974 | **34/52**: missing 35 (the suction port's second mirror) to 52 | 24/24 | 8/8 |
| `--guided-progress` | 0.9983 | **52/52**, no excess | 24/24 | 8/8 |

IoU 0.974 hid a third of the casing's features; the check names exactly the step the verbose build showed. All 52 intents are measurable at the default grid (none "small" or "hidden").

**1.2M against 7M on the same data** (4.8M states, patch 0017, `DAGGER_PERTURB=0.2`, 4 epochs, 1 seed each, Quadro, 2026-10-15):

| | 1.2M | 7M |
|---|---|---|
| S 80 whole pump, plain / guided | 0.9970 / 0.9987 | 0.9803 / 0.9987 |
| Casing intents, plain / guided | 49/52 / **52/52** | 34/52 / **52/52** |
| `len6` clean | 0.98 | 1.00 |
| Perturbed `len6` / `mixed_long` / `mixed_at_long` / `mixed_at` | **0.92 / 0.94 / 0.94** / 0.94 | 0.85 / 0.91 / 0.88 / 0.95 |

The small model is as good on the S 80 and better under perturbation; guided, both build the whole pump exactly.

**Data scaling, 7M** (patch 0017, `DAGGER_PERTURB=0.2`, 4 epochs, 1 seed each; 5060 Ti, Quadro, Spark2; 2026-10-15). Clean suites are 0.99–1.00 at every size:

| 20 % random actions | 2.25M states | 4.8M | 9.7M | 1.2M model at 4.8M |
|---|---|---|---|---|
| `len6` | 0.77 | 0.85 | 0.86 | **0.92** |
| `mixed_long` | 0.90 | 0.91 | **0.97** | 0.94 |
| `mixed_at_long` | 0.91 | 0.88 | **0.95** | 0.94 |
| `mixed_at` | 0.92 | 0.95 | **0.96** | 0.94 |
| S 80 whole pump, plain | 0.986 | 0.980 | 0.997 | 0.997 |

More data still helps the mixed suites from 4.8M to 9.7M (+0.03 to +0.07); `len6` levels off after 4.8M. The 1.2M model at 4.8M is already level with the 7M at 9.7M.

**v1, the production candidate** (1.2M, 9.7M states, patch 0017, DeepCAD goals 25 %, `TAIGA_KEEP_HELDOUT=1`, `DAGGER_PERTURB=0.2`, 4 epochs, 3 seeds; Spark2, 2026-10-16; `~/agg_prod_small10m.txt`):

| | seed 2 | seed 12 | seed 22 |
|---|---|---|---|
| Clean suites below 1.00 | `comp2` 0.93 | `comp3` 0.99, `len5` 0.96, `len6` 0.99 | `len4` 0.98, `len5` 0.99, `len6` 0.94 (0.75 without detours) |
| Perturbed `len6` / `mixed_long` / `mixed_at_long` | 0.96 / 0.91 / 0.97 | **0.97 / 0.94 / 0.98** | 0.90 / 0.92 / 0.96 |
| S 80 whole pump, plain / guided | 0.9724 / 0.9983 | 0.9886 / 0.9983 | 0.9987 / 0.9987 |
| Casing intents, plain / guided | 48/52 / 51/52 | 47/52 / 51/52 | **52/52 / 52/52** |
| Casing core, other ten parts | exact | exact | exact |
| DeepCAD test (6,762 designs) | **1.000** | – | – |

Every seed: the ten S 80 parts other than the casing exact, the casing core 24/24, guided whole pump 0.998–0.999, perturbed `len6` 0.90–0.97 (the 1.2M at 4.8M states: 0.92; the 7M at 9.7M: 0.86). Against the proposed gates (`../../docs/README_production_plan.md`, item 11): perturbed `len6` ≥ 0.85, DeepCAD, S 80 ten parts and core pass; clean ≥ 0.99 on every suite up to `len6` passes on the mean only for 26 of 29 suites (`comp2`, `len5`, `len6` at 0.98), each time from one seed. Registered as `small-v1`: seed 2 (the one tested on DeepCAD).

**v2** (the same with patch 0019: v1's 9.7M states plus 1.7M `points` states, 11.4M; 3 seeds; Spark1, 2026-10-16; `~/agg_v2.txt`): the new `points` suite (fillets, chamfers, shells on edges and faces named by points) is **1.00 clean on every seed** (perturbed 0.99). Elsewhere v2 is a little weaker than v1 on the clean suites and a little stronger perturbed:

| Clean, mean of 3 seeds | v1 | v2 |
|---|---|---|
| `iid-L3` / `len-L4` / `len2-L5` | 1.00 / 1.00 / 1.00 | 0.97 / 0.96 / 0.94 |
| `len6-L9` / `mixed_long-L4` | 0.98 / 1.00 | 0.98 / 0.94 |
| `comp2-L3` | 0.98 | 0.67 (seed 12: **0.00**) |
| Perturbed `len6` / `mixed_long` | 0.94 / 0.92 | **0.95 / 0.94** |
| S 80 whole pump, plain | 0.9724 / 0.9886 / 0.9987 | 0.9983 / 0.9987 / 0.9337 |
| S 80 whole pump, guided | 0.9983 / 0.9983 / 0.9987 | 0.9983 / 0.9987 / 0.9987 |
| Casing intents, plain / guided (seeds 12, 2, 22) | 47, 48, 52 / 51, 51, 52 | 51, **52**, 50 / 51, **52**, **52** |

Seed 12 fails one held-out composition completely (`comp2` 0.00 clean, 0.34 perturbed) and is the weakest elsewhere (`iid-L3` 0.95, `len2` 0.88); seed 2 is level with v1 seed 2 (clean ≥ 0.97 everywhere, `comp2` 1.00, `points` 1.00; perturbed `len6` 0.95, `mixed_long` 0.96) and builds the whole casing in both modes (52/52, where v1 seed 2 has 51/52 guided). v2 seed 2 is the candidate to replace v1; its DeepCAD test (7,742 goals with the split designs) is 1.000 on every bucket (`../datasets/README.md`).

**v3** (patch 0020: v2's 11.4M states plus 1.25M `angled` states, 12.0M; 3 seeds; Spark1, 2026-10-18; `~/agg_v3.txt`): every clean suite is at or above v2 (`len6` 1.00, `angled` 1.00 on every seed, `plane` 1.00, `comp2` 0.97 with no collapsed seed; perturbed `len6` 0.93), and the ten S 80 parts other than the casing are exact. **But the casing fails on every seed and in both modes** (whole pump 0.36; casing 18/52 intents, core 2/24): v3 builds the first flange pad (a datum `boss_box` on XZ sticking out of one side) and then undoes it. Diagnosis (seed 2, prefixes and variants of the casing core):

| Goal | v3 | v2 |
|---|---|---|
| core, first 3 features; box with the same pad, alone or patterned, one-sided or through, at 1/5 scale | exact | exact |
| core, first 5 features | exact | exact |
| core, first 6 features (feature 5: the volute cavity, ~40 % of the body) | **fails** (IoU 0.57) | exact |
| the same with the cavity 20 deep | exact | exact |
| the same with the cavity before the pads | exact | exact |

The state carries the part's volume over the target volume; with the cavity in the goal it is ~1.5 right after the pads, a value synthetic goals show almost only after a wrong pad (their cavities remove ~12 % of the body on average), and v3 learned to undo there. Fix: patch 0021 (family `hollow`: pads, then large cavities); v4 = v3's pool plus a `hollow` supplement. v3 is not registered.

**v4 seed 2, first check** (2026-10-19, patch 0021, before the aggregate): `k06`, `k06_shallow` and `k06_cavity_first` exact, and the whole casing core exact, every one of its 155 steps the teacher's (v3 seed 2: 2 of 24 intents).

**v4** (patch 0021: v3's pool plus 1.2M `hollow` states; 3 seeds; Spark1, 2026-10-20; `~/agg_v4.txt`). Registered-seed gates for seed 2:

| Gate | Threshold | v4 seed 2 | v4 seed 22 |
|---|---|---|---|
| Clean suites up to `len6` | ≥ 0.97 | **fail: `comp2` 0.85** (`mixed_at_long` 0.93, `mixed_long` 0.97; all others ≥ 0.98) | `comp2` 0.97; `mixed_long` 0.91, `mixed_at_long` 0.94 |
| `points` / `angled` / `hollow` clean | ≥ 0.99 | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 |
| Perturbed `len6` / `mixed_long` | ≥ 0.90 | 0.92 / 0.94 | 0.93 / 0.90 |
| DeepCAD test (7,742), 1-5 / 6-10 / 11-20 | ≥ 0.99 / 0.98 / 0.95 | 1.000 / 0.996 / 1.000 (carbonio) | not run |
| S 80 casing guided; core and ten parts | ≥ 51/52; exact | 52/52 (whole pump 0.9987); exact | 51/52 (0.9983); exact |
| Sweep: no seed below 0.50 | | pass (lowest: seed 12 `comp2` 0.81) | |

Seed 12 is the weak seed again (`len6` 0.87, casing 25/52). `comp2` (a `boss_box` then a `mirror`, the held-out pair) dropped on every seed (0.85 / 0.81 / 0.97; v3 0.97 / 0.99 / 0.95): `hollow` mirrors `boss_box` flange pads about an origin plane, a related but different form, was the suspect. Checked on the production path (2026-10-20, 100 `comp2` goals built with `taiga_build_part.py` from an open document, as the harness does): v4 seed 2 builds 97, v2 seed 2 also 97. The suite's drop comes from its protocol (random start states, a fixed small goal set) and the usual seed-to-seed variance of this held-out pair (v1 seed 2 0.93, v2 seed 12 0.00), not from a lost skill. v4 seed 2 is to be registered with this exception noted, seed 22 (`comp2` 0.97, casing 51/52) as its fallback.

## Limits of the vocabulary found on the way

- **Patterns only about the origin axes.** The suction flange's 8 bolt holes are on a circle around the port axis (z = 110), not the shaft axis, so they are written as 4 holes, each mirrored. A polar pattern about a feature's own axis would make it one hole and one pattern.
- **No pattern of a pattern.** The discharge flange's 2 × 2 holes are two mirrored pairs, and the foot holes two repeated pairs.
- **Pads start at the sketch plane.** The discharge neck is wider than the flat top of the casing; the reference neck fills down to the rounded corners, the Taiga neck starts at the top face (a few cm³).
- **Internal cavities** are pockets on datum planes inside the body, extruded both ways. That works for the 2.5D chambers of this model; real cast cavities with rounded, varying sections would need sweeps and lofts (Phase 2).
- **Long goals.** A real casing is ~50 features; the models are trained on ≤ 5.
- **The same normal with two meanings.** `nz: 1` with `off` is an XY datum plane, without `off` the top face. Models trained before patch 0016 only saw the first; write top features without a normal for them, or use `--canon-top`.

## Versions

| File | Version |
|---|---|
| `make_s80_goals.py` | 2026.10.05.2 |
| `eval_s80.py` | 2026.10.14.1 |
| `voxel_iou.py` | 2026.10.06.1 |
| `run_s80.sh` | 2026.10.15.1 |
| `feature_check.py` | 2026.10.15.1 |
