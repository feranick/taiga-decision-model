# Real designs as Taiga goals

Converting public CAD build histories into Taiga goals, so the model also learns from how people actually build parts, not only from our generators.

## Which datasets, and may we use them?

Both candidates are *sketch and extrude* histories in the same JSON format (DeepCAD parsed Onshape documents into the format of the Fusion 360 Gallery reconstruction set), so one converter serves both.

| Dataset | Size | License | Can we train on it? |
|---|---|---|---|
| [DeepCAD](https://github.com/rundiwu/DeepCAD) | 178,238 build sequences, from public [Onshape](https://www.onshape.com) documents listed in the [ABC dataset](https://deep-geometry.github.io/abc-dataset/) | Code: MIT. Models: copyright of their creators, licensed by the [Onshape Terms of Use](https://www.onshape.com/en/legal/terms-of-use), section on public documents | **Mostly yes, including commercial use.** Public documents of free-plan users created after 2018-08-07, and public documents created before that date without a `LICENSE` tab, come with a worldwide, royalty-free license to use, copy, modify and distribute. Documents with a `LICENSE` tab reserving more rights keep those rights; they would have to be identified and left out. |
| [Fusion 360 Gallery, reconstruction set](https://github.com/AutodeskAILab/Fusion360GalleryDataset) | 8,625 sequences | [Dataset license](https://github.com/AutodeskAILab/Fusion360GalleryDataset/blob/master/LICENSE.md): **non-commercial research only**; binds the user's employer if they work for a for-profit company | **Only for non-commercial research.** Fine as an extra test set in a research context; not for a tool meant for commercial use. |

So: **DeepCAD first**, with the `LICENSE`-tab check. Before using either dataset for anything beyond research, have the licensing reviewed; this table is a summary, not legal advice.

## The format (as used by DeepCAD's `cadlib`)

- **Sequence:** a list of `ExtrudeFeature` entities, in build order.
- **Sketch:** a coordinate system (origin, x/y/z axes) and profiles; each profile is one or more loops (one outer, the rest holes) of `Line3D`, `Arc3D` and `Circle3D` curves, in sketch coordinates.
- **Extrude:** the profiles it uses, an operation (`NewBody`, `Join`, `Cut`, `Intersect`), an extent type (one side, symmetric, two sides) and distances.

## Conversion rules (DeepCAD → Taiga goal)

1. **Frame.** Rotate and move the whole model so the first sketch lies on the XY plane at z = 0 with its normal along +Z (Taiga's base features start there). Keep the model only if every other sketch plane is then normal to X, Y or Z: Taiga has no sketch planes at other angles yet (a gap for later).
2. **Units and size.** Onshape units to mm, then keep the real size: Taiga's encoding divides by the part's size, and the `large` suite showed size doesn't matter.
3. **Outlines.** Each loop becomes one `outline`: lines `["L", u, v]`, arcs `["A", mid, end]` (three-point), circles as two half arcs. The in-plane (u, v) follow Taiga's convention for the plane's normal.
4. **First extrude** (`NewBody`): `profile_base` with the outer loop and height = extent; each inner loop becomes a `profile_pocket` through all.
5. **Later extrudes.** Every extrude from a plane can be written exactly as a feature on a datum plane extruded both ways: a one-sided extrude of d from offset z0 is the symmetric extrude of d about z0 + d/2; two sides d1, d2 is d1 + d2 about z0 + (d1 − d2)/2. So:
   - `Join` → `profile_boss` (`off`, `h`); inner loops → `profile_pocket` over the same extent.
   - `Cut` → `profile_pocket` (`off`, `depth`); inner loops (islands) → `profile_boss` over the same extent.
   - `Intersect`, or a second `NewBody` (several bodies) → leave the model out.
6. **Face form where natural (second pass).** When a sketch plane coincides with a planar face of the part built so far, and the extrude goes outward (join) or inward (cut), write it as a face feature (`nx`/`ny`/`nz`, `at`) instead: closer to how people build, and to our own families. This needs the part built so far, so it runs with the teacher in FreeCAD.
7. **Keep only verified goals.** The teacher builds each goal (`taiga_build_part.py --check`), and the result must match the original model (rebuilt from the JSON with OpenCASCADE, as `pump_s80.py` does) to IoU ≥ 0.99. Duplicates (DeepCAD has many) are removed by a hash of the converted goal.

## How the converted goals get used

- **Test suites first:** goals from DeepCAD's own test split, bucketed by length (e.g. 1–5, 6–10, 11–20 extrudes), as suites `deepcad`, `deepcad_long`. They measure how the current models do on real designs before anything is trained on them.
- **Then training:** goals from the training split become one more family in the training mix (share set like `TAIGA_EXT_FRACTION`), with DAgger as for every other family. Real designs bring what the generators lack: odd profiles, many features, features placed relative to earlier ones, long goals.
- **Coverage:** DeepCAD has only sketches and extrudes, so it complements the generators (revolve, patterns, fillets, chosen faces) rather than replacing them.

## Files

| File | What it is |
|---|---|
| `convert_deepcad.py` | DeepCAD JSON → goals (rules 1–5), the original models as STEP (`--refs`, needs OCP), an eval spec and a report of what was left out and why |
| `run_deepcad.sh` | Download, convert, teacher builds, verification against the originals, diagnosis of failures, model builds, scores |
| `diagnose_goals.py` | For goals the teacher can't build: the first feature FreeCAD rejects, and FreeCAD's reason |

## Run (on the DGX, after `../train_expanded.sh dgx setup`)

```bash
cd taiga-expanded/datasets
./run_deepcad.sh download      # DeepCAD data -> ~/deepcad (archive about 200 MB)
./run_deepcad.sh convert       # test split, first 300 convertible models -> ~/taiga-expanded/deepcad_test
./run_deepcad.sh teacher       # the teacher builds every goal
./run_deepcad.sh verify        # keep the goals whose teacher build matches the original (IoU >= 0.99)
./run_deepcad.sh models        # every seed of runs/data2 builds the verified goals
./run_deepcad.sh eval          # share built to IoU >= 0.99, by goal length, mean ± sd over seeds
```

`LIMIT=0` converts the whole split; `SUBSET=train` the training split (for the training family, later); `MAX_FEATURES`, `MIN_IOU`, `OUT`, `DATA` and `REF_PY` override the defaults. `convert` uses the OCP venv that `../eval/run_s80.sh eval` creates (`~/taiga-expanded/ocp-venv`).

**Tested** on synthetic models in DeepCAD's format (a plate with a hole, a boss, a cut slot on the XZ plane extruded both ways, a two-sided join on the YZ plane; a model whose first sketch is on the XZ plane with a negative extrude): the goals rebuild the originals to IoU 0.9999 in an OpenCASCADE re-implementation of the goal semantics; angled sketch planes and intersect are left out as intended. The real data and FreeCAD's builds are next (`convert`, `teacher`, `verify`).

## First results (spark DGX, FreeCAD 1.1.3, 9 patches, 2026-10-06)

First 300 convertible models of DeepCAD's test split (originals that aren't one valid solid left out by the converter):

- **Teacher:** builds 280 of 300 goals. In the other 20, FreeCAD accepts every object, but the runtime's rule after each feature (one valid solid with volume) fails, most likely because DeepCAD joins pieces that touch only later, while PartDesign keeps one solid after every feature (`diagnose` 2026.10.06.2 prints solids and volume of the failing feature). From `convert_deepcad.py` 2026.10.06.3 on, the converter reorders consecutive joins so every step stays one solid, and leaves out the models where no order works.
- **After reordering joins** (`convert_deepcad.py` 2026.10.06.3): the teacher builds **300 of 300**; the reordered models include all 20 that failed before.
- **Verified:** 298 of 300 at first. Both misses were measuring errors, not conversion errors: a 1700 × 1490 × 1 mm plate fell between the voxel centres (now at least 48 voxels along every side, refined until the voxel volumes match), and one reference whose mesh had an untriangulated face (now: random points classified against the solids when a mesh isn't watertight). Rebuilt here, both score IoU 1.0.
- **Length:** median 1 feature, at most 13. The start of the test split is mostly single extrudes (plates); longer designs need the whole split (`LIMIT=0`), bucketed by length.

**Full test split, first pass** (8,052 models): 6,210 converted (540 duplicates). The largest groups left out were "base is not one solid" (524) and "a join stays apart" (506). A sample of 19 of them showed four converter issues, fixed in `convert_deepcad.py` 2026.10.06.6–10:

- DeepCAD marks every loop of a profile as outer: the outer loop is now the one enclosing the others, and a profile with separate regions becomes one feature group per region.
- A first extrude without a profile (skipped by DeepCAD's own loader) no longer leaves the model out.
- Inner loops are made exact against the earlier material: a join's hole may only remove (hole minus earlier material), a cut's island may only put back (island and earlier material). The converter computes that region and writes it as pockets / bosses of its own outlines when it is a straight prism (e.g. a frame around a plate, a hex nut around an existing tube), or leaves the feature out when the region is empty.
- The report lists five example models per reason.

On the sample: 10 of the 19 now convert, 9 of them to IoU ≥ 0.99. The rest are degenerate designs (profiles with zero-width cusps or slivers, rejected as invalid shapes) or joins that touch the part only along an edge (two solids in the original too).

**Full test split, second pass** (`convert_deepcad.py` 2026.10.06.10): 6,836 converted; the teacher builds 6,821, and **6,762 match their original** (IoU ≥ 0.99). Features per goal: median 2, at most 18 (6,187 with 1–5, 544 with 6–10, 31 with 11–20).

**Models on the verified goals** (step-1 expanded models, `runs/data2`, 4 epochs, before patch 0009). In this run each model build stopped early: the FreeCAD worker crashed on one part, and `taiga_build_part.py` (2026.10.06.1) then gave up on the remaining goals, which `eval` counted as IoU 0. The `eval` table (12.5 ± 8.8 % overall) therefore mostly shows where each run crashed. The share of goals built correctly among those the models actually attempted:

| seed | goals attempted | 1 feature | 2–5 | 6+ | with an XY datum-plane feature | side datum planes only |
|---|---|---|---|---|---|---|
| 2 | 3,360 | 0.96 | 0.06 | 0.01 | 0.02 | 0.35 |
| 12 | 254 | 0.97 | 0.06 | 0.00 | 0.00 | 0.64 |
| 22 | 736 | 0.99 | 0.05 | 0.00 | 0.00 | 0.45 |
| 32 | 2,250 | 1.00 | 0.14 | 0.02 | 0.05 | 0.80 |
| 42 | 1,963 | 0.98 | 0.08 | 0.02 | 0.03 | 0.45 |

The converter writes every DeepCAD extrude after the first as a feature on a datum plane, and most of them are parallel to XY (a hole or boss started from the sketch plane, often symmetric about mid-height). That is the case the S 80 showed these models can't do. They build single extrudes almost perfectly and fail nearly every goal with an XY datum feature, usually declaring the part done with the wrong shape. Patch 0009 adds exactly these features to training, so the numbers to compare are the 0009 models'.

Fix: `taiga_build_part.py` 2026.10.07.1 reports a crashed part as CRASH (a failure), restarts the worker and goes on, so every goal gets a result.

## Next

1. Rerun `models` and `eval` with `taiga_build_part.py` 2026.10.07.1 (a clean baseline, and the crash count).
2. The same with the patch-0009 models, once trained.
3. Face form (rule 6), then the training family.

## Versions

| File | Version |
|---|---|
| `convert_deepcad.py` | 2026.10.06.10 |
| `run_deepcad.sh` | 2026.10.06.5 |
| `diagnose_goals.py` | 2026.10.06.2 |
