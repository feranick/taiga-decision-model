# Using a trained model

How to try a trained Taiga model on your own parts, how production runs it, and how other tools call it. For training see `../training/README_training.md`; for the goal format in full see `../build/README_build_part.md` and `../taiga-expanded/README.md` (design, primitives 1–9).

## Where it runs

On a Linux machine with the **work folder the model was trained in**: FreeCAD, the venv and the code (patch series) of that training. A model only runs with the code it was trained with:

| Model | Work folder | Machines that have one |
|---|---|---|
| v1 (1.2M, patches 0001–0017) | `~/taiga-expanded-0017` | Spark2 (where it trains), mochi, carbonio |
| v2 (1.2M, patches 0001–0019: + fillet, chamfer, shell on edges and faces named by points) | `~/taiga-expanded-0019` | mochi; Spark1 when set up |

A v1 model does not load in a 0019 folder (the 0019 command list is two commands longer), and goals with `pts` need a 0019 folder. Models are also tied to the FreeCAD version (1.1.3 here): after a FreeCAD update, retrain and re-run the pass criteria.

Look at the results on any computer: every part is saved as `.FCStd` (with its feature tree) and `.step`.

## 1. Find the model

After a sweep and its aggregate, pick a seed (the seeds of one configuration score within a point or two of each other). The exported model is a small folder:

```bash
M=~/taiga-expanded-0017/taiga-s1/runs/gdeepcad_goalsf0.25_kh_pt0.2_mcb119a_dscale10m/seed2/hf
```

To use it on another machine with the same work folder, copy that folder (a few MB).

## 2. Write goals

A goal file is `{name: goal}`, a goal an ordered list of features with dimensions in mm (the first is the base):

```json
{"bracket": {"features": [
  {"kind": "base_box", "params": {"w": 80, "d": 50, "h": 12}},
  {"kind": "boss_cyl", "params": {"r": 10, "x": 0, "y": 0, "h": 15}},
  {"kind": "hole", "params": {"r": 5, "x": 0, "y": 0, "at": [0, 0, 27]}},
  {"kind": "hole", "params": {"r": 3, "x": 30, "y": 15}},
  {"kind": "mirror", "params": {"ax": 1, "ay": 0, "az": 0}},
  {"kind": "chamfer_edges", "params": {"size": 1, "nx": 0, "ny": 0, "nz": -1}}]}}
```

`python ../harness/taiga_harness.py format` prints every kind with its parameters and the conventions; `validate` checks a goal file without FreeCAD. Examples: `../build/flange/example_goals.json`, `../build/engine/`, `../taiga-expanded/eval/s80_goals.json` (a 52-feature pump casing).

## 3. Build with the model

```bash
cd ~/taiga-expanded-0017/taiga-s1 && source ../freecad.env
.venv/bin/python <repo>/build/taiga_build_part.py --model $M --goals ~/my_goals.json \
    --guided-progress --out ~/my_parts
```

- `--guided-progress` is how production runs the model on complete goals: the runtime tells it which goal intent is next (the teacher's progress tracking), and the model picks every command. Without it the model also keeps count itself (needed when continuing from an existing document).
- Without `--quiet` every step is printed, with `(expert: ...)` where the teacher would have done something else; each part ends with `SUCCESS` or `FAIL` and its IoU with the goal's target.
- `--teacher` instead of `--model $M` builds the same goals with the scripted teacher, for comparison; `--check` only checks that the goals can be built.
- Other options (step timeout, memory limit, loop guard, GUI data): `../build/README_build_part.md`.

## 4. Or through the production runtime

The runtime builds each goal through a chain of attempts and keeps the first one the checker accepts (IoU ≥ 0.999 with the goal's target), logging every attempt:

```bash
cp <repo>/build/registry.example.json <repo>/build/registry.json   # set small-v1's path to $M
.venv/bin/python <repo>/build/taiga_runtime.py --registry <repo>/build/registry.json \
    --goals ~/my_goals.json --chain main:guided,teacher --out ~/my_parts --workers 8
```

- The default chain, `teacher,main:guided,main,fallback:guided`, is the production rule: the teacher first, the model when the teacher cannot. `--chain main:guided,teacher` makes the model build first, to try or evaluate it.
- Output: accepted parts in `~/my_parts`, every attempt under `attempts/`, one line per goal in `runtime_log.jsonl`.

## 5. Check a build feature by feature

IoU hides missing features (a casing missing a third of its features scored 0.974). `../taiga-expanded/eval/feature_check.py` names the goal intents a build realized, against the teacher's part after each intent; for the S 80, `../taiga-expanded/eval/run_s80.sh features` does it in one step.

## 6. Look at the parts

```bash
scp -r nicola@mochi.mit.edu:my_parts ~/Desktop/         # then open the .FCStd files in FreeCAD
```

To watch a build live, `scripts/gui_demo.py` drives a visible FreeCAD window step by step (`../build/README_build_part.md`, "Watching it build in the FreeCAD GUI"); it needs a display on the Linux machine.

## 7. From other tools and LLM agents

`../harness/` gives other programs the same in one interface: the goal format, validation, a buildability check, builds through the production runtime, and the files. LLM-based tools use its MCP server (`taiga_mcp.py`, stdio or HTTP; a desktop client can start it over ssh); scripts use the command line or import the module. See `../harness/README.md`.

A typical agent loop: read `goal_format`; turn the request into one goal per solid part; `validate_goals` and fix the errors; `check_goals` if unsure; `build_parts` (or `start_build` for long parts); return the `.step` files, or report the parts that could not be built with the reasons.
