# Building parts with a trained Taiga-S1 model

`taiga_build_part.py` uses a Taiga-S1 model for inference. You give it a goal, an ordered list of features with their dimensions. The model drives a headless FreeCAD command by command and the result is saved as an `.FCStd` file that opens in FreeCAD.

The model only chooses **which command** to run next. All dimensions come from the goal.

---

## Prerequisites

- A machine set up with one of the training scripts in [`training/`](../training/README_training.md) (`setup` stage completed): `~/taiga/taiga-s1` with its `.venv`, and `~/taiga/freecad.env`.
- A model: your own exported one (`runs/seed<N>/hf`, from the `export` stage), a checkpoint (`runs/seed<N>/last.pt`), or the published `shhivv/taiga-s1`.

No GPU is needed. Inference runs on the CPU at about 1 ms per decision; most of the time is spent in FreeCAD.

---

## Quick start

In the commands below, `<repo>` is the folder of this repository.

```bash
cd ~/taiga/taiga-s1
source ~/taiga/freecad.env

# One showcase part, with your model
.venv/bin/python <repo>/build/taiga_build_part.py \
    --model runs/seed2/hf --goals showcase/goals.json --name flange --out parts

# Every goal in a file
.venv/bin/python <repo>/build/taiga_build_part.py \
    --model runs/seed2/hf --goals <repo>/build/flange/example_goals.json --out parts
```

Open the result with `freecad parts/flange.FCStd`.

Relative `--model` and `--goals` paths are looked up in the current directory first, then in the upstream repo (`~/taiga/taiga-s1`), so `runs/seed2/hf` and `showcase/goals.json` also work when you run the script from another directory. `--out` is always relative to the current directory.

### Options

| Option | Default | Meaning |
|---|---|---|
| `--model` | `shhivv/taiga-s1` | Exported directory, `.pt` checkpoint, or Hugging Face repo id |
| `--goals` | (required) | JSON file of named goals, `{name: goal}` |
| `--name` | all goals | Build only this goal from the file |
| `--out` | `parts` | Directory for the `.FCStd` files |
| `--quiet` | off | Print only the result line for each part |
| `--check` | off | Only check that each goal can be built (the teacher builds the target); no model needed, no files written |
| `--no-loop-guard` | guard on | Turn off the loop guard. With the guard, when the model is back in a state it already acted from (e.g. Pad, invalid, Undo) and picks the same action again, its most likely untried action is taken instead; the result line says how often that happened |
| `--teacher` | off | Build with the scripted teacher (the expert that labels the training data) instead of a model, and save the parts as usual: what the goal itself produces, independent of any model |
| `--no-step`, `--no-gui-data` | off | Skip the STEP export / the added view data |

The exit code is `0` if every part succeeded and `1` otherwise, so the script can be used in other scripts and CI.

---

## Output

For every step the script prints the command the model chose, and `ok` if it matches what the scripted teacher would do (otherwise the teacher's choice). Each part ends with a result line:

```
flange: SUCCESS  IoU 1.0000  done True  steps 27  agreement 27/27  3.1s  -> /home/…/parts/flange.FCStd
```

| Field | Meaning |
|---|---|
| `SUCCESS` / `FAIL` | Success means the model declared `Done` and the solid matches the target (volumetric IoU ≥ 0.99, no stray objects) |
| `IoU` | Volumetric overlap between the built solid and the target solid |
| `done` | Whether the model chose `Done` on its own before running out of steps |
| `steps` | Commands executed; the step budget is derived from the goal |
| `agreement` | Steps where the model chose one of the teacher's acceptable commands. A lower number with `SUCCESS` means it went off-plan and recovered. |

The `.FCStd` file is saved even when the build fails, so you can inspect what went wrong.

After the result line, each part gets a timing line: time to make the part (the model's decisions plus FreeCAD executing the commands, each also per step), then the IoU check and saving/export, and the total. When all parts are done, a table lists these per part with a total row, plus the wall time of the whole run.

Next to each `.FCStd` the script writes a `.step` file of the finished part (`--no-step` to skip). The FreeCAD worker runs without a GUI, so the saved document has no view data and FreeCAD would open it with every object hidden; the script adds a minimal `GuiDocument.xml` that shows the Body and its final feature (`--no-gui-data` to skip).

---

## Writing your own goals

A goal file is a JSON object of named goals. Each goal is an ordered list of features. The first feature is always the base; the following ones are applied to it in order.

```json
{
  "bracket_plate": {
    "features": [
      {"kind": "base_box", "params": {"w": 60, "d": 40, "h": 8}},
      {"kind": "hole", "params": {"r": 3, "x": 22, "y": 12}},
      {"kind": "mirror", "params": {}},
      {"kind": "fillet_vertical", "params": {"r": 4}}
    ]
  }
}
```

`flange/example_goals.json` has three goals (bracket plate, spacer, bolt-circle disc). The upstream repo has six more in `showcase/goals.json` (flange, hex nut, enclosure, mounting plate, washer, slotted wheel).

`engine/example_goals_engine.json` is a parts kit for a small single-cylinder four-stroke engine (40 mm bore), one goal per part: piston, cylinder block, head gasket, cylinder head, valve cover, crankcase half, connecting rod, crank web, flywheel, port flange and valve spring retainer. Taiga-S1 builds one PartDesign Body per goal and can only add features on the top face, so the parts are simplified: no piston-pin bore, ring grooves or cooling fins (side features), no horizontal crank bore, and the piston and valve cover come out as open cups (the shell removes the top face, so read the piston upside down). Putting the parts together is outside what Taiga-S1 does. Check the kit first, then build it:

```bash
.venv/bin/python <repo>/build/taiga_build_part.py --goals <repo>/build/engine/example_goals_engine.json --check
.venv/bin/python <repo>/build/taiga_build_part.py --model runs/seed2/hf \
    --goals <repo>/build/engine/example_goals_engine.json --out parts/engine
```

A goal that can't be built is reported as `INFEASIBLE` and the script moves on to the next one.

To check, build and assemble a whole kit in one go, use the kit's script: `engine/build_engine.sh` or `pump/build_pump.sh` (optionally with a model path, e.g. `./build_pump.sh runs/seed12/hf`). Each one stops if `--check` finds a goal that can't be built, otherwise builds all parts and writes the normal and exploded assemblies, even if some parts failed. `WORK`, `BASE_TAIGA` and `OUT` override the default locations (`~/taiga`, `~/taiga/taiga-s1`, `parts/<kit>`).

`pump/example_goals_pump.json` is a parts kit for a Victor Pumps S 80 self-priming centrifugal pump (DN80), derived from the manufacturer's brochure and the relevant flange and motor standards; `pump/README.md` lists where each dimension comes from, and `pump/assembly_pump.json` assembles it.

### Feature types

Sizes are in mm. `x`/`y` are positions on the top face, measured from the centre of the base.

| Group | Kind | Parameters | What it does |
|---|---|---|---|
| Base (first feature) | `base_box` | `w`, `d`, `h` | Rectangular block |
| | `base_cyl` | `r`, `h` | Cylinder |
| | `base_hex` | `r`, `h` | Hexagonal prism |
| | `base_ring` | `ri`, `ro`, `h` | Ring (revolved), inner and outer radius |
| On the top face | `boss_cyl` | `r`, `x`, `y`, `h` | Cylindrical boss |
| | `boss_box` | `w`, `d`, `x`, `y`, `h` | Rectangular boss |
| | `hole` | `r`, `x`, `y` | Through hole (sketch + pocket) |
| | `hole_std` | `r`, `x`, `y` | Through hole (PartDesign Hole feature) |
| | `pocket_rect` | `w`, `d`, `x`, `y`, `depth` | Rectangular pocket |
| Repeat the previous feature | `polar_pattern` | `n` | `n` copies around the Z axis |
| | `linear_pattern` | `n`, `length` | `n` copies along a line of total `length` |
| | `mirror` | none | Mirror across the YZ plane |
| Finishing | `fillet_top` | `r` | Fillet the top edges |
| | `fillet_vertical` | `r` | Fillet the vertical edges |
| | `chamfer_top` | `size` | Chamfer the top edges |
| | `shell` | `t` | Hollow the part, removing the top face, wall thickness `t` |

### Optional goal fields

- `scale`: if missing, the script sets it to the base's largest dimension (box: max of `w`, `d`, `h`; cylinder and hex: max of `2r`, `h`; ring: max of `2ro`, `h`). This is the rule upstream's own goals use.
- `level`: defaults to 3. It is metadata from the goal generator and doesn't change how the part is built.

### What works best

- The model was trained on goals of up to 5 features and handles up to about 11 reliably (see the `len` and stress suites in the evaluations). Longer goals work less often.
- Patterns and `mirror` repeat the feature immediately before them.
- Keep features inside the part: holes and pockets need material around them, and fillets, chamfers and shells must fit the wall thickness. If a goal can't be built, the script stops with an error before the model runs.

---

## Assembling parts

Each goal produces its own part file. `taiga_assemble.py` places built parts into one document according to an assembly spec (rotations and a translation per part, plus color and transparency), and writes a single `.FCStd` and a combined `.step`. It runs in FreeCAD's Python; no model is involved. `engine/assembly_engine.json` assembles the engine kit: crank axis along Y, shown at top dead center, with the crankcase, cylinder block and valve cover semi-transparent so the piston, rod and crank stay visible.

```bash
source ~/taiga/freecad.env
cd ~/taiga/taiga-s1      # or wherever parts/engine is
"$FREECAD_PYTHON" <repo>/build/taiga_assemble.py --parts parts/engine --spec <repo>/build/engine/assembly_engine.json
"$FREECAD_PYTHON" <repo>/build/taiga_assemble.py --parts parts/engine --spec <repo>/build/engine/assembly_engine.json --explode 1
freecad parts/engine/engine_assembly.FCStd
```

| Option | Default | Meaning |
|---|---|---|
| `--parts` | (required) | Folder with the built parts (`<part>.FCStd`, or `<part>.step` as a fallback) |
| `--spec` | (required) | Assembly spec JSON |
| `--out` | `<parts>/<name>.FCStd` | Output file (`<name>_exploded` with `--explode`) |
| `--explode` | `0` | Exploded view: moves each part by its `explode` offset times this factor |
| `--no-step` | off | Don't write the combined STEP file |

Parts that haven't been built (or failed) are listed and left out. The assembly holds fixed copies of the part shapes: after rebuilding parts, run the script again. The parts are simplified (see the engine kit above), so some don't fit perfectly: for example, the connecting rod is as wide as the crankcase's spigot hole.

In a spec, each entry has `label`, `part` (the goal name), `rotations` (a list of `[axis, degrees]`, applied in order), `translation`, `color` (RGB, 0–1), `transparency` (0–100) and `explode`. Parts are modeled sitting on z = 0..h and centered on the origin, which is the frame the rotations and translations start from.

---

## Watching it build in the FreeCAD GUI

Upstream's demo drives a live FreeCAD window instead. It needs a desktop session (not plain SSH).

```bash
cd ~/taiga/taiga-s1
FREECAD_S1_REPO=$PWD freecad scripts/freecad_gui_server.FCMacro &
.venv/bin/python scripts/gui_demo.py --model runs/seed2/hf --goals showcase/goals.json --name flange
```

It saves a screenshot and the `.FCStd` to `runs/gui_demo/`. `--delay` sets the pause between steps (default 0.4 s).

---

## Limitations

- Only the feature types above, placed on the top face of a PartDesign Body. Anything else (other workbenches, sketches on side faces, assemblies) needs new teacher rules and retraining.
- The model is specific to the FreeCAD version it was trained with. Use a model trained on the same platform (see `manifest.json` in the run folder).
- Goals come from you (or from a planner such as an LLM); the model doesn't design parts.

---

## Troubleshooting

- **`FreeCAD not found; set FREECAD_PYTHON and FREECAD_LIB`**: run `source ~/taiga/freecad.env` first.
- **`FreeCAD worker exited (code 1)`**: the worker's error output is hidden. Run upstream's `tests/test_runtime.py` (see Known issues in [`training/README_training.md`](../training/README_training.md)) to find the cause.
- **`GoalBuildError` / `expert path produced an invalid feature`**: the goal can't be built as written. Check the dimensions and positions against the base.

| Script | Version |
|---|---|
| `taiga_build_part.py` | 2026.10.09.1 |
| `taiga_assemble.py` | 2026.10.05.2 |
