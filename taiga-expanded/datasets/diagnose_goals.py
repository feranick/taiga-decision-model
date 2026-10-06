#!/usr/bin/env python3
"""diagnose_goals.py — why can't the teacher build a goal? Runs in FreeCAD's Python.
Version: 2026.10.06.1

The scripted teacher builds each goal step by step; at the first feature FreeCAD rejects, it
prints the goal intent (index, kind, the parameters that matter) and FreeCAD's own status of
the failing object. Run through run_deepcad.sh (stage "diagnose"), or directly:
    PYTHONPATH=$FREECAD_LIB:$WORK/taiga-s1 "$FREECAD_PYTHON" diagnose_goals.py goals.json name1 name2 ...
With no names, every goal in the file is tried.
"""
from __future__ import annotations

import collections
import json
import sys

from freecad_s1.goals import StartSpec
from freecad_s1.runtime.session import HeadlessSession
from freecad_s1.schema import Goal


def scale_of(g: dict) -> float:
    p = g["features"][0]["params"]
    return max(p.get("w", 0), p.get("d", 0), p.get("h", 0), 2 * p.get("r", 0)) or 1.0


def brief(f) -> str:
    keep = ("nx", "ny", "nz", "off", "x", "y", "z", "w", "d", "h", "depth", "r", "at")
    p = {k: f.params[k] for k in keep if k in f.params}
    if "outline" in f.params:
        o = f.params["outline"]
        kinds = collections.Counter(s[0] for s in o["segs"])
        p["outline"] = f"{len(o['segs'])} segs {dict(kinds)}"
    return f"{f.kind} {json.dumps(p)}"


def diagnose(s: HeadlessSession, name: str, g: dict, max_steps: int = 2000) -> str:
    goal = Goal.from_json({**g, "scale": g.get("scale", scale_of(g))})
    s.reset(goal, StartSpec(doc_open=True, workbench="PartDesignWorkbench"))
    for _ in range(max_steps):
        acts = s.expert()
        if acts[0] == "Done":
            return f"{name}: builds"
        if acts[0] == "Std_Undo":
            i = s.progress()
            bad = [o for o in s.doc.Objects if hasattr(o, "isValid") and not o.isValid()]
            status = "; ".join(f"{o.Name} ({o.TypeId}): {o.getStatusString()}" for o in bad) or "no invalid object"
            k = min(i, len(goal.features) - 1)
            return f"{name}: feature {k} of {len(goal.features)} rejected: {brief(goal.features[k])}\n    FreeCAD: {status}"
        info = s.step(acts[0])
        if info.get("error"):
            i = min(s.progress(), len(goal.features) - 1)
            return f"{name}: step {acts[0]} failed at feature {i}: {info['error']}\n    intent: {brief(goal.features[i])}"
    return f"{name}: did not finish in {max_steps} steps"


def main() -> None:
    goals = json.loads(open(sys.argv[1]).read())
    names = sys.argv[2:] or list(goals)
    s = HeadlessSession()
    for name in names:
        try:
            print(diagnose(s, name, goals[name]), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    main()
