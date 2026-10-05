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

In the commands below, `<repro>` is the folder of this repository.

```bash
cd ~/taiga/taiga-s1
source ~/taiga/freecad.env

# One showcase part, with your model
.venv/bin/python <repro>/build/taiga_build_part.py \
    --model runs/seed2/hf --goals showcase/goals.json --name flange --out parts

# Every goal in a file
.venv/bin/python <repro>/build/taiga_build_part.py \
    --model runs/seed2/hf --goals <repro>/build/example_goals.json --out parts
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

`example_goals.json` (in this folder) has three goals (bracket plate, spacer, bolt-circle disc). The upstream repo has six more in `showcase/goals.json` (flange, hex nut, enclosure, mounting plate, washer, slotted wheel).

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
| `taiga_build_part.py` | 2026.10.05.1 |
