# taiga-expanded

Extends Taiga-S1's vocabulary so it can build real parts like the Victor S 80 pump (`../build/pump_s80_reference_CAD`). The changes are kept as a **git patch series** on a pinned upstream commit, not as a fork. Training, sweeps and evaluation reuse the scripts in `../training`.

## Status

| # | Primitive | State |
|---|---|---|
| 1 | **Features on any planar face** (±X, ±Y, ±Z): holes, pockets, bosses on side faces; new evaluation suite `side` | Patch 0001. All 38 tests pass, including the FreeCAD ones (DGX, FreeCAD 1.1.3, 2026-10-05) |
| 2 | **Features on origin planes with an offset**, extruded symmetrically: cross bores (e.g. a piston-pin bore through a cylinder wall), through windows, cross pins and lugs; new evaluation suite `plane` | Patch 0002. All tests pass, including FreeCAD (DGX, FreeCAD 1.1.3, 2026-10-05) |
| 3 | **Curved outlines**: closed profiles of lines, arcs and splines, drawn by one command (`Sketcher_CreateProfile`) and fixed by `Sketcher_ConstrainBlock`; outline bases, bosses and pockets on any face or plane; new evaluation suite `outline` | Patch 0003. All 50 tests pass, including FreeCAD with sketch B-splines (DGX, FreeCAD 1.1.3, 2026-10-05) |
| 4 | **Revolve and groove with a profile**: a closed outline turned about the X, Y or Z axis (`PartDesign_Revolution`, `PartDesign_Groove`), drawn like a lathe drawing (position along the axis, radius); turned bases, collars, ring grooves, bores; new evaluation suite `revolve`. (Pads and pockets of a given depth from any face are already covered by primitives 1–3.) | Patch 0004. All 55 tests pass, including FreeCAD revolve and groove builds (DGX, FreeCAD 1.1.3, 2026-10-05) |
| 5 | **Patterns and mirrors about any axis**, of any feature: polar about X, Y or Z (full or part circle), linear along ±X, ±Y, ±Z, mirror across any origin plane; goal rows widened from 48 to 64 numbers; new suite `pattern` | Patch 0005. All 67 tests pass, including FreeCAD (DGX, FreeCAD 1.1.3, 2026-10-05) |
| 5b | **Fillets and chamfers on chosen edges** (`fillet_edges`, `chamfer_edges`): the edge loop of a face with any normal, or all edges along X, Y or Z; **features on a chosen face** (point `at`), e.g. a hole or hub on top of a boss, a chamfer on a shoulder; new suite `edges` | Patch 0006. All 67 tests pass, including FreeCAD (DGX, FreeCAD 1.1.3, 2026-10-05) |
| 6 | **Training options**: size test suites `large` and `large_ext` (goals ×4, up to ~360 mm) and optional size augmentation (`TAIGA_SIZE_AUG`); perturbed DAgger (`DAGGER_PERTURB`) in the training scripts; data and experiment names follow these settings | Patch 0007 + training scripts. All 71 tests pass, including FreeCAD builds of the ×4 goals (DGX, FreeCAD 1.1.3, 2026-10-05) |
| 7 | **Training coverage found with the S 80**: features on XY datum planes (closed cavities, slots through two sides, plates through the part, foot plates, keyways and webs on turned parts) and features wider than the face they stand on (caps over plugs, cover plates over spigots, flaps, flanges on necks); new suites `datum_z` and `overhang` | Patch 0009. Pure-Python tests pass; FreeCAD tests run during `setup` |

Out of scope for now (Phase 2): sweep, loft, helix, multi-body parts, assemblies.

## Layout

```
taiga-expanded/
├── README.md            this file
├── patches/             the patch series (git format-patch) + BASE (upstream commit it applies to)
├── dev/                 make_dev_branch.sh, export_patches.sh, check_patches.sh (+ config.sh)
├── train_expanded.sh    runs ../training/taiga_repro_<machine>.sh on the patched (or baseline) code
├── eval/                S 80 goals and their part-by-part comparison with the reference CAD (eval/README.md)
└── datasets/            public CAD build histories (DeepCAD) converted into goals and verified (datasets/README.md)
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
  - The expanded goals never contain a composition upstream holds out for its `comp`, `comp2` and `comp3` suites (e.g. a patterned `boss_box` or a mirrored `hole_std`), so those suites still test unseen compositions for the expanded model too. (Fixed in patch 0001 on 2026-10-05: before, about 4 % of `side` and `plane` goals contained one.)
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

### Primitive 4: revolve and groove with a profile

New kinds `profile_revolve` (adds material) and `profile_groove` (removes it) turn a closed outline about a global axis through the origin:

```json
{"kind": "profile_revolve", "params": {"ax": 0, "ay": 0, "az": 1, "angle": 360,
  "nx": 0, "ny": 1, "nz": 0, "off": 0, "x": 0, "y": 0, "z": 0,
  "outline": {"start": [0, 0], "segs": [["L", 10, 0], ["L", 10, 20], ["L", 6, 20], ["L", 6, 40], ["L", 0, 40], ["L", 0, 0]]}}}
```

- **Axis:** (ax, ay, az), one of them 1. `angle` is the sweep in degrees (default 360).
- **Sketch plane:** an origin plane that contains the axis, given like a primitive 2 feature (`off: 0`): XZ (normal Y) for the Z and X axes, YZ (normal X) for the Y axis.
- **Outline:** same format and (u, v) convention as primitive 3. About Z on the XZ plane, u = X is the radius and v = Z the position along the axis (the example above is a stepped shaft, Ø20 × 20 then Ø12 × 20). About X, u = X is along the axis and v = Z the radius. About Y, u = Y is along and v = Z the radius. The outline must stay on one side of the axis (radius ≥ 0).
- **No new commands:** the outline is drawn by `Sketcher_CreateProfile` and fixed by `Sketcher_ConstrainBlock`, as in primitive 3. The model chooses `PartDesign_Revolution` or `PartDesign_Groove`; the runtime revolves about the sketch axis that runs along the goal's axis (`H_Axis` or `V_Axis`, found from the sketch placement) by the goal's angle. Upstream's own revolve goals are unchanged (`V_Axis`, 360°).
- **Encoding:** the axis and angle / 360 are added to the extra block of the goal encoding (positions 43–46). (Rows are widened to 64 numbers in primitive 5.)
- **New training goals:** stepped turned parts (1–3 steps, optionally hollow) about Z (mostly), X or Y, with ring grooves, revolved collars and, for solid parts turned about Z whose top step is the widest, an axial hole and a top chamfer.
- **New evaluation suite `revolve`:** level 3.

This covers the S 80's shaft, bearing bracket, seal housing, wear ring and the round parts of the casing.

### Primitive 5: patterns and mirrors about any axis

Upstream patterns are fixed: polar about Z, linear along +X, mirror across the YZ plane. A pattern goal may now carry an axis (`ax`, `ay`, `az`, one of them ±1), the same keys as a revolve:

```json
{"kind": "polar_pattern",  "params": {"n": 6, "ax": 1, "ay": 0, "az": 0, "angle": 360}}
{"kind": "linear_pattern", "params": {"n": 3, "length": 40, "ax": 0, "ay": 0, "az": -1}}
{"kind": "mirror",         "params": {"ax": 0, "ay": 1, "az": 0}}
```

- **Polar:** about the X, Y or Z origin axis; the sign sets the direction of rotation. `angle` (default 360) is the angular extent: 360 spreads `n` copies round the full circle, less than 360 puts the first and last copies `angle` apart.
- **Linear:** along the axis, towards + or −; `length` is the distance from the first to the last copy, as upstream.
- **Mirror:** the axis is the normal of the origin plane to mirror across (X → YZ, Y → XZ, Z → XY).
- **Any feature:** the pattern repeats the previous feature (`Tip`), as upstream, so side-face, datum-plane, outline and top features all work. Exception: compositions upstream holds out for its `comp` suites (patterns of `boss_box`, patterns of `pocket_rect`, mirrors of `boss_box` and `hole_std`) are never used in training, to keep those suites meaningful.
- **No new commands:** the model chooses the pattern command; the runtime sets the axis, plane, direction and angle from the goal. Without an axis, patterns behave exactly as upstream.
- **Encoding:** the axis (now signed) and angle use the same positions as a revolve (43–46). Each token row is widened from 48 to 64 numbers (zeros for everything upstream), leaving room for the rest of the extra block.
- **New training goals:** holes and pins in rows on side faces (horizontal or vertical) and on top (along −X or ±Y), rows of cross bores, mirrors across XZ or to the opposite face, bolt circles on discs turned about X or Y (polar about the disc axis, full or part circle, or mirrored across XY), part-circle polar patterns about Z; optional top dressup.
- **New evaluation suite `pattern`:** level 3.

### Primitive 5b: fillets and chamfers on chosen edges, features on a chosen face

New kinds `fillet_edges` (`r`) and `chamfer_edges` (`size`) take their edges from the goal:

```json
{"kind": "chamfer_edges", "params": {"size": 1, "nx": 0, "ny": 0, "nz": 1, "at": [12, 0, 38]}}
{"kind": "fillet_edges",  "params": {"r": 2, "ax": 1, "ay": 0, "az": 0}}
```

- **Edge loop of a face:** with a normal (nx, ny, nz), the outer edge loop of a planar face with that outward normal (new selections `Edges@Face-Z`, `Edges@Face±X`, `Edges@Face±Y`; upstream has `Edges@Face+Z`).
- **Edges along an axis:** with an axis (ax, ay, az), every straight edge parallel to it (new `Edges|X`, `Edges|Y`; upstream has `Edges|Z`).
- **Which face (`at`):** by default the largest face with that normal, as upstream does for the top face. With `at` = [x, y, z] (global), the face with that normal nearest to the point: the top of a boss, a shoulder of a turned part. The first example chamfers the top edge of a boss whose top face is at z = 38.
- **Features on a chosen face:** `at` works the same for the sketch face of any sketched feature, so a hole or a hub can go on top of a boss, or a pocket in a lower step: `{"kind": "hole", "params": {"r": 3, "x": 12, "y": 0, "at": [12, 0, 38]}}`.
- **Encoding:** `at` / scale and a flag at positions 47–50.
- **New training goals:** fillets and chamfers on the top edge of a boss, holes and hubs on top of a boss, fillets and chamfers on every edge along X, Y or Z of a box, on the edge loop of its bottom or a side face, and on the ends and shoulders of stepped turned parts.
- **New evaluation suite `edges`:** level 3.

Together with primitives 1–4, these cover the S 80's bolt circles (port flanges, covers, motor flange), the impeller's blade ring, and the edge breaks on its machined parts.

### Primitive 6: training options

**Sizes.** The goal samplers make parts up to about 80 mm; the S 80 casing is 360 mm. The goal encoding divides every length by the part's scale, so a part scaled by k looks the same to the model (a test checks this), but FreeCAD may behave differently at other sizes. Rather than widening the samplers blindly, patch 0007 first measures it:

- **Suite `large`:** the `iid` goals, from the same seeds, with every length ×4 (`TAIGA_LARGE_FACTOR`). Comparing `large` with `iid` isolates the effect of size.
- **Suite `large_ext`:** expanded goals (a random new family each) ×4.
- **Size augmentation, off by default:** `TAIGA_SIZE_AUG=p` scales a fraction p of the training goals by a log-uniform factor in [1, `TAIGA_SIZE_MAX`] (5). With the default 0 the training data are exactly as without the patch.

**Perturbed DAgger.** Upstream's `--dagger-perturb` (in `4a31bcf`, not in the `a6e81d3` used by the variance study) makes DAgger rollouts take random off-plan commands, so the model learns to recover from mistakes. The training scripts pass it when `DAGGER_PERTURB` > 0 (upstream's Mesa-S1 uses 0.2, in half of the rollout batches: `DAGGER_PERTURB_FRAC=0.5`). It applies to the baseline too (`BASELINE=1`), so it can be compared on both.

**Names.** Settings that change the training data (`TAIGA_EXT_FRACTION`, `TAIGA_SIZE_AUG`, `TAIGA_SIZE_MAX`) are added to the data folder name, so runs with different settings never share data; they and `DAGGER_PERTURB` are also added to the experiment name and recorded in `manifest.json` (`taiga_env`, `dagger_perturb`).

### Primitive 7: coverage gaps found with the S 80

The first S 80 model builds (step 1 models, 2026-10-06) failed in two systematic ways, the same on every seed: every pocket or pad on an **XY datum plane** (the casing's chambers, the shaft's keyways, the bracket's foot), and every feature **wider than the face it stands on** (the priming cover's cap, the check valve's flap, the inspection cover's plate). Neither ever occurred in training: the datum generators only used the YZ and XZ planes, and every generator kept bosses inside their face. Both are common in real parts, so patch 0009 adds them as general families, not as S 80 parts:

- **`datum_z`:** features on XY datum planes at any height, extruded both ways: closed cavities inside the part (single, or a row of outline cavities), slots through two sides of a block, plates and collars through the part, foot plates under it; on parts turned about X, keyways and webs with a foot plate.
- **`overhang`:** caps over plugs (with a polar row of holes through the rim), cover plates over spigots (a row of holes through the rim), flaps and lug plates over discs, flanges on necks on the top or a side face (the flange's face picked with `at`, a bore through flange and neck, a mirrored bolt hole).

Both mix into training like the other families and have their own level-3 suites. The original suites and the suites of patches 0001–0008 produce the same goals as before, except `large_ext`, which now also draws from the two new families.

Because these families were added after looking at S 80 failures, the next S 80 score is no longer a fully independent test: after retraining, also check a part the changes were not made for (the engine kit, or a dataset slice).

### Training plan

Each step as a sweep on the same machine (DGX shown), with fixed data (`DATA_SEED=2`), so differences come from the model:

| # | Question | Commands |
|---|---|---|
| 1 | No regression, and how well the new families are learned at the default budget | `BASELINE=1 DATA_SEED=2 ./train_expanded.sh dgx sweep` and `DATA_SEED=2 ./train_expanded.sh dgx sweep` |
| 2 | Is the budget too small for the larger task? | `DATA_SEED=2 DATA_SCALE=2 ./train_expanded.sh dgx sweep` (and `EPOCHS=8` if the training loss is still falling) |
| 3 | Does perturbed DAgger help, on both? | Step 1 with `DAGGER_PERTURB=0.2` |
| 4 | Only if `large` is clearly below `iid`: size augmentation | `DATA_SEED=2 TAIGA_SIZE_AUG=0.3 ./train_expanded.sh dgx sweep` |

**Step 1 results** (DGX spark-0808, FreeCAD 1.1.3, 5 seeds each, same data `DATA_SEED=2`, 2026-10-06). Clean success, mean ± sd over seeds:

| Suite | Baseline (unpatched `4a31bcf`) | Expanded |
|---|---|---|
| iid L1–L3, len L4–L6 | 1.00 ± 0.00 | 1.00 ± 0.00 |
| comp / comp2 / comp3 | 0.81 ± 0.22 / 0.99 ± 0.02 / 0.96 ± 0.09 | 1.00 ± 0.00 (all three) |
| len4 (7 intents) | 0.99 ± 0.02 | 0.98 ± 0.05 |
| len5 (8 intents) | 0.83 ± 0.19 | 0.68 ± 0.18 |
| len6 (9 intents) | 0.41 ± 0.34 | 0.14 ± 0.03 |
| side, plane, outline, revolve, pattern, edges, large, large_ext | — | 1.00 ± 0.00 (all) |

- **Held-out compositions improve** and their spread disappears: the new families (patterns and mirrors of many feature types) transfer to the compositions upstream holds out, which are still never trained on.
- **Size doesn't matter:** `large` (the `iid` goals ×4) scores exactly like `iid`, clean and perturbed, seed by seed. Size augmentation (step 4) is not needed.
- **Length extrapolation regresses:** goals longer than anything in training (8–9 intents, training has ≤ 5) fail more often, also when perturbed (len6 0.38 → 0.08). The published model reaches 0.97 on len6 on the same machine, so the training budget matters for length. Real parts are long (the S 80 casing has 52 features), so this is the axis to work on: step 2, then training on longer goals.
- Perturbed (20 % random actions): new suites 0.93–0.99 (outline and revolve lowest); original suites as the baseline except len5/len6.
- Time per sweep: about 7–8 h, mostly evaluation (training 0.9 h for 5 seeds, perturbed evaluation 3.8 h).

Compare with `../training/taiga_aggregate.py` on the run folders (`~/taiga-head/taiga-s1/runs/data2`, `~/taiga-expanded/taiga-s1/runs/data2`, `.../x2_data2`, ...).

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
DATA_SEED=2 ./train_expanded.sh 5060ti sweep                    # expanded model, 5 seeds, suites incl. "side", "plane", "outline", "revolve", "pattern", "edges", "large", "large_ext", "datum_z", "overhang"
BASELINE=1 ./train_expanded.sh 5060ti setup                     # ~/taiga-head: same upstream commit, no patches
BASELINE=1 DATA_SEED=2 ./train_expanded.sh 5060ti sweep         # baseline for the no-regression check
```

Compare the original suites between the two with `../training/taiga_aggregate.py` (mean ± sd over seeds), e.g. `taiga_aggregate.py ~/taiga-head/taiga-s1/runs/data2 ~/taiga-expanded/taiga-s1/runs/data2`.

### Test on real parts

Two test sets of real parts, each with its own script. Both follow the same stages: first the scripted teacher builds the goals (no model: can the vocabulary express the part?), then the trained models build them (how close do they get?). Everything goes to `~/taiga-expanded/<set>`.

```
design ──convert──► goal ──teacher or model──► FreeCAD commands ──► part ──verify / eval──► IoU with the original design
(what exists)        (what to build)            (how to build it)                            (did it come out right?)
```

The goal plays the role of a planner's output (in real use, an LLM writes it); the teacher, the scripted expert that also writes the training data, is the perfect executor; the trained model is the executor being tested.

| Stage | Flow | S 80 pump (`eval/run_s80.sh`) | DeepCAD designs (`datasets/run_deepcad.sh`) | What it does | Needs a trained model |
|---|---|---|---|---|---|
| `download` | — | — | ✓ | Fetches DeepCAD's archive (about 200 MB) to `~/deepcad` | no |
| `convert` | design → goal (+ reference part) | — (the goals are in `eval/s80_goals.json`) | ✓ | Turns each design into a Taiga goal, and writes the original as a reference STEP file; `report_<subset>.json` lists what was left out and why | no |
| `check` | goal → can it be built? | ✓ | — | Can every goal be built? Prints OK or INFEASIBLE per goal, saves nothing | no |
| `teacher` | goal → commands → part | ✓ | ✓ | The scripted teacher builds every goal and saves the parts (`teacher/`) | no |
| `verify` | teacher's part vs reference | — (`eval` scores the teacher) | ✓ | Scores the teacher's builds against the originals and keeps the goals that match (IoU ≥ 0.99): `goals_<subset>_verified.json` | no |
| `diagnose` | goal → where the build fails | — | ✓ | For goals the teacher can't build: the first feature FreeCAD rejects, and why | no |
| `models [RUNS]` | goal → commands → part, by the model | ✓ | ✓ | Every seed in `RUNS/seed*/hf` (default: `runs/data2`) builds the (verified) goals: `seed<N>/` | yes |
| `eval` | parts vs reference (and vs teacher) | ✓ | ✓ | IoU with the reference for the teacher and every seed; IoU with the teacher's build per goal; mean ± sd over seeds. S 80: per part, plus overlay `.FCStd` files. DeepCAD: share built to IoU ≥ 0.99 by goal length | for the model scores |

Typical order: S 80 `check → teacher → eval` (vocabulary), then `models → eval` after each training run; DeepCAD `download → convert → teacher → verify`, then `models → eval`. Details: [`eval/README.md`](eval/README.md), [`datasets/README.md`](datasets/README.md).

## Success criteria

1. **No regression:** on the original suites, the expanded model's distribution over seeds is not worse than the baseline's at the same commit.
2. **New features:** high clean and perturbed success on each new suite.
3. **Real parts:** S 80 parts rebuilt with Taiga goals, scored by IoU against `../build/pump_s80_reference_CAD`, first with the scripted teacher (can the vocabulary express them?), then with the trained models (`eval/`, see [`eval/README.md`](eval/README.md)). Teacher: every part builds, IoU 0.9986–1.0000 per part, 0.9989 for the whole pump (2026-10-05); the models are next.

## Versions

| File | Version |
|---|---|
| `train_expanded.sh` | 2026.10.06.1 |
| `dev/*.sh` | 2026.10.05.1 (`export_patches.sh` 2026.10.05.2) |
| `patches/` | 0001 (features on any planar face), 0002 (features on origin planes with an offset), 0003 (curved outlines), 0004 (revolve and groove with a profile), 0005 (patterns and mirrors about any axis, wider goal rows), 0006 (fillets and chamfers on chosen edges, features on a chosen face), 0007 (goals at other sizes), 0008 (enough steps for the target build of long goals), 0009 (XY datum planes and overhanging features in training), against upstream `4a31bcf` |
