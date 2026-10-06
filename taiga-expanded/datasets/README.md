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
| `run_deepcad.sh` | Download, convert, teacher builds, verification against the originals, model builds, scores |

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

## Next

1. Run it on the test split; look at the report (what is left out and why) and at the verified share.
2. Score the current models on the verified goals (`models`, `eval`).
3. Face form (rule 6), then the training family.

## Versions

| File | Version |
|---|---|
| `convert_deepcad.py` | 2026.10.06.2 |
| `run_deepcad.sh` | 2026.10.06.3 |
