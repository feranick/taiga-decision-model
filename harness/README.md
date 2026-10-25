# Harness: Taiga for other tools and LLM agents

How a program or an LLM-based tool asks Taiga for CAD parts. The tool writes **goals** (one per solid part: an ordered list of features with dimensions); the harness validates them, checks that they can be built, builds them with the production runtime (scripted teacher and the trained model, with the checker), and returns the result and the files.

| File | What it is |
|---|---|
| `taiga_harness.py` | The functions, and a command line: JSON in, JSON out |
| `taiga_mcp.py` | The same as an MCP server, for LLM tools (stdio or streamable HTTP) |

Everything runs on a Linux machine with a trained work folder (FreeCAD, the venv and the code the model was trained with). Callers can be anywhere: MCP over HTTP or ssh, and `get_file` returns the built files.

## Setup (once, on the machine that builds)

```bash
W=~/taiga-expanded-0017                      # the work folder of the model (v2 models: a 0019 folder)
uv pip install --python $W/taiga-s1/.venv/bin/python "mcp>=1.2"
cp <repo>/build/registry.example.json <repo>/build/registry.json   # set small-v1's path to the trained model
export TAIGA_WORK=$W                         # also: TAIGA_REGISTRY, TAIGA_OUT (default ~/taiga_parts)
```

`TAIGA_WORK` decides which code reads the goals: a model must run with the code it was trained with (v1: 0017; v2: 0019). The production model is v2 (`small-v2`): `TAIGA_WORK=~/taiga-expanded-0019` with `TAIGA_REGISTRY` pointing at a copy of `build/registry_v2.example.json`. Models of different patch series cannot share a registry (the goal encoding grows with the series). Selection by points (`pts`) needs a 0019 folder; `goal_format` says whether it is available (`selection_by_points`).

## Production on every machine (`~/taiga_prod`)

`../build/deploy_prod.sh MODELS_TAR [WORK]` installs the production models the same way on every machine: `~/taiga_prod/models/<name>/hf`, `~/taiga_prod/registry.json` (from `../build/registry_prod.json`), `~/taiga_prod/work` (a link to the work folder they were trained with; v4: a 0021 folder), `mcp` in that folder's venv, then a test (`models` and one build). A client then always runs the same command; a new model only changes `~/taiga_prod`:

```bash
TAIGA_WORK=~/taiga_prod/work TAIGA_REGISTRY=~/taiga_prod/registry.json \
  ~/taiga_prod/work/taiga-s1/.venv/bin/python ~/Software/taiga-decision-model/harness/taiga_mcp.py
```

On a new machine, install from the GitHub release instead: `../docs/README_install.md` (the release file goes straight into `deploy_prod.sh`, which checks it against its manifest and the work folder's patch series).

Current production (2026-10-24): `small-v5` (v5 seed 2) with `small-v5-s22` (seed 22) as fallback, patches 0001-0022 (`~/taiga_prod/work` → a 0022 folder). Before: `small-v4`, patches 0001-0021.

## For LLM tools: the MCP server

| Tool | What it does |
|---|---|
| `goal_format` | The goal format: kinds, required and optional parameters, conventions, limits, an example. The agent reads it first. |
| `validate_goals` | Static checks of `{name: goal}`: unknown kinds, missing or bad parameters, open outlines, patterns of patterns, ... Fast, no FreeCAD. |
| `check_goals` | Can the scripted teacher build each goal? A goal it cannot build no model builds either. |
| `build_parts` | Builds and waits. Per part: `built`, the accepted attempt, every attempt with result and IoU, the files. `model_first=true` makes the model build first (guided), the teacher otherwise builds first. |
| `start_build`, `build_status` | The same in the background, for long parts (a 50-feature casing takes ~20 min). |
| `get_file` | A built `.step` or `.FCStd` as base64, for callers on another machine. |
| `list_models` | The registry: installed models, their training and measured results. |

The server's instructions tell the agent the workflow: read the format, write one goal per solid part, validate and fix, optionally check, build, use the files.

**Run it.** With the work folder's Python:

```bash
# stdio, for a client on the same machine or one that starts it over ssh
$W/taiga-s1/.venv/bin/python <repo>/harness/taiga_mcp.py
# streamable HTTP, for clients elsewhere (endpoint http://<host>:8765/mcp)
$W/taiga-s1/.venv/bin/python <repo>/harness/taiga_mcp.py --http 8765 --host 0.0.0.0
```

**Connect a client.** Any MCP client works. For a desktop client on another computer, the simplest is to let it start the server over ssh (no open port; the ssh key does the authentication), e.g. in a client's MCP server configuration:

```json
{"mcpServers": {"taiga": {
  "command": "ssh",
  "args": ["nicola@mochi.mit.edu",
           "TAIGA_WORK=~/taiga-expanded-0017 ~/taiga-expanded-0017/taiga-s1/.venv/bin/python ~/Software/taiga-decision-model/harness/taiga_mcp.py"]}}}
```

For HTTP, point the client at `http://<host>:8765/mcp`. The HTTP server has no authentication: keep it on a trusted network or behind a tunnel (`ssh -L 8765:localhost:8765 nicola@mochi.mit.edu`).

## Speed: the persistent pool (plan items 11 and 12)

By default (`TAIGA_PERSISTENT=1`) builds and checks run inside the harness's process through `../build/taiga_pool.py`, with `TAIGA_POOL_WORKERS` (default 4) FreeCAD workers and the models kept loaded; the MCP server starts them in the background when it launches and keeps them for its lifetime. Before, every attempt was a new `taiga_build_part.py` process (Python with PyTorch, the model, a FreeCAD worker, and a second FreeCAD process for the STEP file), about 2 s per attempt and per fallback; now an attempt is the build alone and the STEP file is exported once, for the accepted part. Results are the same, with `"runtime": "pool"` and per attempt its stage times. `TAIGA_PERSISTENT=0` goes back to separate processes; `list_models` shows the pool's state (workers running, models loaded, attempts).

**Timing benchmark** (`bench_timing.py`): the same goal set (the flange example, four draft parts, the S 80 parts other than the casing; `--long` adds the casing core) built model-first in three passes: separate processes, the pool cold, the pool warm. It prints per pass the time in target builds, model, FreeCAD, check, save and the rest (overhead), and per goal the time before and after, and writes `bench_timing.json`:

```bash
export TAIGA_WORK=~/taiga_prod/work TAIGA_REGISTRY=~/taiga_prod/registry.json
$TAIGA_WORK/taiga-s1/.venv/bin/python ~/Software/taiga-decision-model/harness/bench_timing.py --rounds 2
```

## For scripts: the command line

```bash
export TAIGA_WORK=~/taiga-expanded-0017
PY=$TAIGA_WORK/taiga-s1/.venv/bin/python; H=<repo>/harness/taiga_harness.py
$PY $H format > goal_format.json
$PY $H validate my_goals.json
$PY $H check my_goals.json
$PY $H build my_goals.json [--chain main:guided,teacher] [--workers 8] [--no-step]
$PY $H models
```

Each prints JSON. `-` reads the goals from stdin. Python callers can also `import taiga_harness` and call `goal_format()`, `validate()`, `check()`, `build()`, `read_file()`, `models()` directly.

## What a build returns

```json
{"request": "/home/nicola/taiga_parts/build_20261015_101954_808d66",
 "parts": {
   "bracket": {"built": true, "accepted": "teacher", "seconds": 4.1,
               "attempts": [{"how": "teacher", "result": "SUCCESS", "iou": 1.0, "seconds": 4.1}],
               "files": {"FCStd": ".../bracket.FCStd", "step": ".../bracket.step"}},
   "bad_part": {"built": false, "why": "invalid goal", "errors": ["feature 2 (hole): missing 'x'"]}}}
```

- `accepted`: which attempt passed the checker (`teacher`, `main:guided`, `main`, `fallback:guided`); none if no attempt did.
- Each request gets its own folder under `TAIGA_OUT` with the goals, every attempt and its log, and `runtime_log.jsonl` (`../build/README_build_part.md`, production runtime).
- Goals are normalized before building into the form the models were trained on, without changing the part: an outline feature gets its size (`w`, `d`) and, on the top face, its centre (`x`, `y`) from the outline; an origin-plane feature (`off`) gets a positive normal and its centre on the plane (`validate_goals` warns when it moves a centre). A cross bore written with `ny: -1, off: 0` made the model lose its way (IoU 0, the teacher built it); with `ny: +1` the model built it exactly.

## Tested (2026-10-15)

From the Claude desktop app over ssh to mochi (0017 folder, the 1.2M and 7M `dscale5m` models in a test registry), `model_first=true`:

| Request | Parts | Built by | IoU | Time |
|---|---|---|---|---|
| Bracket: plate, central boss with bore, four corner holes (two mirrors), vertical fillets | 1 | model | 1.0 | 6.2 s |
| Motor flange (boss, bore, 6 bolt holes polar, chamfer), spacer (ring with cross bore), cover plate (outline with an arc, pocket, 4 holes linear, fillets) | 3 | model 2, teacher 1 (the spacer's cross bore with `ny: -1`; fixed by the normalization above) | 1.0 | 4–7 s each |
| The same four parts with **v1 seed 2** (`small-v1` = `~/v1_s2/seed2/hf`) and harness 2026.10.15.2, `model_first=true` | 4 | **model 4** (the spacer with `ny: -1` included) | 1.0 | 3.7–6.4 s each; the three in one `start_build` 6.5 s |

**v2** (2026-10-17, mochi, command line: `TAIGA_WORK=~/taiga-expanded-0019`, registry with `small-v2` only, `--chain main:guided,teacher`): the three goals of `build/flange/example_goals.json` (bracket plate, spacer, bolt-circle disc) all built by the model (`main:guided`). Then from the Claude desktop app over ssh: a bracket (plate, boss with bore, four corner holes as two mirrored holes, vertical fillets), `model_first=true`: built by `small-v2` at IoU 1.0 in 5.8 s.

A `build_parts` call with all four parts timed out in the client once, right after the connector restarted, although the build itself finished on mochi in 6 s (all four by the model, `runtime_log.jsonl`): the reply was lost on the client side. `start_build` + `build_status` avoids depending on one long call when a request has several parts. All eight STEP files of these runs match the build123d translator's parts of the same goals (IoU ≥ 0.99999), an independent check of the geometry.

## Writing goals (summary; `goal_format` has the full list)

- **Units and axes:** mm, global coordinates, Z up; the base sits on z = 0, centred on the origin in X and Y.
- **First feature is the base:** `base_box`, `base_cyl`, `base_hex`, `base_ring`, `profile_base` (any closed outline) or `profile_revolve` (a turned part).
- **Top-face features** (`boss_cyl`, `boss_box`, `hole`, `hole_std`, `pocket_rect`, `profile_boss`, `profile_pocket`) take `x`, `y` on the top face; on another face add its normal (`nx`, `ny`, `nz`) and the centre `z`; pick one of several faces with `at`; on an origin plane with an offset add `off` (extruded both ways: cross bores, cavities).
- **Patterns and mirror** repeat the previous feature, about or along an origin axis (`ax`, `ay`, `az`).
- **Fillets and chamfers:** whole top or vertical edges (`fillet_top`, `fillet_vertical`, `chamfer_top`), a face's edge loop or edges along an axis (`fillet_edges`, `chamfer_edges`), or, with patch 0019, chosen edges by a point on each (`pts`). **Shell** removes the top face, or with `pts` the faces named.
- **Draft** (patch 0022): `draft` with `angle` tapers every side face about z = 0 (the part narrows upward), or only the faces named by `pts`. Put it after the features it tapers and before straight bores.
- **One goal per solid part.** An assembly is several goals; placing the parts together is outside the harness (`../build/taiga_assemble.py` does it from an assembly spec).

## Versions

| File | Version |
|---|---|
| `taiga_harness.py` | 2026.10.24.1 (persistent pool: builds and checks in-process with workers and models kept loaded, `TAIGA_PERSISTENT`, `TAIGA_POOL_WORKERS`; `warm()`, the runtime in `models()`) |
| `taiga_harness.py` (before) | 2026.10.20.1 (draft, patch 0022: kind `draft` with `angle` and optional `pts`, validated and in `goal_format` when the work folder has the patch) |
| `taiga_harness.py` (before) | 2026.10.17.2 (`build` with `--no-step` / `step=false` passed the option so the runtime rejected it: every part came back "no result"; fixed) |
| `taiga_harness.py` (before) | 2026.10.17.1 (angled planes, patch 0020: validated and described in `goal_format` when the work folder has the patch) |
| `taiga_harness.py` (before) | 2026.10.15.2 (origin-plane normals and centres normalized; `pts` left out of the format without patch 0019) |
| `taiga_mcp.py` | 2026.10.24.1 (warms the pool in the background at launch; `list_models` shows the runtime) |
| `bench_timing.py` | 2026.10.24.1 |
