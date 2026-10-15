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

`TAIGA_WORK` decides which code reads the goals: a model must run with the code it was trained with (v1: 0017; v2: 0019). Selection by points (`pts`) needs a 0019 folder; `goal_format` says whether it is available (`selection_by_points`).

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
- Goals are normalized before building: an outline feature gets its size (`w`, `d`) and, on the top face, its centre (`x`, `y`) from the outline.

## Writing goals (summary; `goal_format` has the full list)

- **Units and axes:** mm, global coordinates, Z up; the base sits on z = 0, centred on the origin in X and Y.
- **First feature is the base:** `base_box`, `base_cyl`, `base_hex`, `base_ring`, `profile_base` (any closed outline) or `profile_revolve` (a turned part).
- **Top-face features** (`boss_cyl`, `boss_box`, `hole`, `hole_std`, `pocket_rect`, `profile_boss`, `profile_pocket`) take `x`, `y` on the top face; on another face add its normal (`nx`, `ny`, `nz`) and the centre `z`; pick one of several faces with `at`; on an origin plane with an offset add `off` (extruded both ways: cross bores, cavities).
- **Patterns and mirror** repeat the previous feature, about or along an origin axis (`ax`, `ay`, `az`).
- **Fillets and chamfers:** whole top or vertical edges (`fillet_top`, `fillet_vertical`, `chamfer_top`), a face's edge loop or edges along an axis (`fillet_edges`, `chamfer_edges`), or, with patch 0019, chosen edges by a point on each (`pts`). **Shell** removes the top face, or with `pts` the faces named.
- **One goal per solid part.** An assembly is several goals; placing the parts together is outside the harness (`../build/taiga_assemble.py` does it from an assembly spec).

## Versions

| File | Version |
|---|---|
| `taiga_harness.py` | 2026.10.15.1 |
| `taiga_mcp.py` | 2026.10.15.1 |
