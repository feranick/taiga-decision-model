#!/usr/bin/env python3
"""taiga_mcp.py — MCP server that lets LLM-based tools build CAD parts with the Taiga models.
Version: 2026.10.15.1

Tools (all JSON):
  goal_format        the goal format: kinds, parameters, conventions, an example. Read it first.
  validate_goals     static checks of {name: goal}, no FreeCAD (fast; fix errors before building)
  check_goals        can the scripted teacher build each goal? (FreeCAD; seconds to minutes)
  build_parts        build {name: goal} and wait; per part: built or not, which attempt, files
  start_build        the same in the background (long parts, e.g. a 50-feature casing: ~20 min)
  build_status       state and results of a background build
  get_file           a built .step / .FCStd as base64 (for callers on another machine)
  list_models        the registry: which models are installed, their training and results

Run on a Linux machine with a trained work folder, with that folder's Python:
  stdio (a local client, or one that starts it over ssh):
      ~/taiga-expanded-0017/taiga-s1/.venv/bin/python <repo>/harness/taiga_mcp.py
  HTTP (clients elsewhere on the network; MCP streamable HTTP at http://<host>:<port>/mcp):
      ... taiga_mcp.py --http 8765 [--host 0.0.0.0]
Configuration: TAIGA_WORK, TAIGA_REGISTRY, TAIGA_OUT as for taiga_harness.py.
Needs the MCP Python SDK in that venv: uv pip install --python .venv/bin/python "mcp>=1.2".
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import taiga_harness as H  # noqa: E402

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    _V2 = True
except ImportError:  # 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    _V2 = False

INSTRUCTIONS = """Taiga builds solid CAD parts (FreeCAD PartDesign) from a goal: an ordered list of features
with dimensions (a base, then bosses, holes, pockets, outlines, revolves, patterns, fillets...).
Workflow: call goal_format once and follow its conventions; write one goal per solid part;
validate_goals and fix every error; optionally check_goals; then build_parts (short parts) or
start_build + build_status (long ones). A part counts as built only when its build matches the
goal's target (IoU >= 0.999). Results give the files (.step for other CAD tools, .FCStd with the
feature tree); get_file returns them if you are on another machine. Units are mm, Z up."""

server = _Server("taiga", instructions=INSTRUCTIONS)
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


@server.tool()
def goal_format() -> dict:
    """The Taiga goal format: feature kinds with required and optional parameters, the coordinate
    conventions, limits and an example goal. Read this before writing goals."""
    return H.goal_format()


@server.tool()
def validate_goals(goals: dict) -> dict:
    """Static checks of goals given as {name: {"features": [{"kind": ..., "params": {...}}, ...]}}.
    Returns per goal ok, errors and warnings. Fast; no FreeCAD."""
    return H.validate(goals)


@server.tool()
def check_goals(goals: dict) -> dict:
    """Whether the scripted teacher can build each goal in FreeCAD (the target exists). A goal
    that is not buildable will not be built by any model either; fix it first."""
    return H.check(goals)


@server.tool()
def build_parts(goals: dict, model_first: bool = False, workers: int = 4) -> dict:
    """Build the goals and wait for the result. Per part: built (bool), the accepted attempt,
    every attempt with its result and IoU, and the files (.step, .FCStd). By default the scripted
    teacher builds first and the model only when the teacher cannot; model_first=True makes the
    production model build first (guided), with the teacher as fallback. For long parts use
    start_build instead."""
    chain = "main:guided,teacher" if model_first else "teacher,main:guided,main,fallback:guided"
    return H.build(goals, chain=chain, workers=workers)


@server.tool()
def start_build(goals: dict, model_first: bool = False, workers: int = 4) -> dict:
    """Start build_parts in the background; returns a job id for build_status."""
    job = uuid.uuid4().hex[:10]
    chain = "main:guided,teacher" if model_first else "teacher,main:guided,main,fallback:guided"
    with _LOCK:
        _JOBS[job] = {"state": "running", "started": time.time(), "parts": list(goals)}

    def run():
        try:
            res = H.build(goals, chain=chain, workers=workers)
            state = {"state": "done", "result": res}
        except Exception as exc:  # noqa: BLE001
            state = {"state": "failed", "error": str(exc)[:2000]}
        with _LOCK:
            _JOBS[job].update(state, finished=time.time())

    threading.Thread(target=run, daemon=True).start()
    return {"job": job, "state": "running", "parts": list(goals)}


@server.tool()
def build_status(job: str) -> dict:
    """State of a background build (running, done or failed) and, when done, its results."""
    with _LOCK:
        j = _JOBS.get(job)
        if j is None:
            return {"error": f"no job {job}"}
        out = dict(j)
    out["elapsed_s"] = round((out.get("finished") or time.time()) - out["started"], 1)
    return out


@server.tool()
def get_file(path: str, max_mb: float = 20) -> dict:
    """A built file (a path from a build result) as base64, for callers on another machine."""
    return H.read_file(path, max_mb)


@server.tool()
def list_models() -> dict:
    """The model registry: each model's role (main, fallback), size, FreeCAD version, training data,
    measured results, and whether it is installed here."""
    return H.models()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, help="serve MCP over streamable HTTP on this port (default: stdio)")
    ap.add_argument("--host", default="127.0.0.1", help="HTTP interface (0.0.0.0: every interface)")
    a = ap.parse_args()
    if a.http is None:
        server.run()
    elif _V2:
        server.run(transport="streamable-http", host=a.host, port=a.http)
    else:
        server.settings.host, server.settings.port = a.host, a.http
        server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
