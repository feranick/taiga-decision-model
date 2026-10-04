#!/usr/bin/env python3
"""taiga_build_part.py — build a CAD part headless with a Taiga-S1 model (inference).
Version: 2026.10.03.1

The model drives a headless FreeCAD worker command by command toward a goal
(an ordered feature list), then the part is checked against the goal's target
solid (volumetric IoU) and saved as .FCStd.

Run from the upstream repo, with its venv and the FreeCAD paths from setup:
    cd ~/taiga/taiga-s1 && source ~/taiga/freecad.env
    .venv/bin/python /path/to/taiga_build_part.py --model runs/seed2/hf \
        --goals showcase/goals.json --name flange --out parts

--model: exported dir (runs/seed<N>/hf), a .pt checkpoint, or shhivv/taiga-s1.
--goals: JSON {name: goal}; see showcase/goals.json and example_goals.json.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from freecad_s1.model.net import from_pretrained, load_checkpoint
from freecad_s1.rollout import Policy
from freecad_s1.runtime.client import FreeCADEnv
from freecad_s1.schema import Goal, State


def base_scale(goal: dict) -> float:
    """Largest extent of the base feature; upstream's goals use this as `scale`."""
    f = goal["features"][0]
    p = f["params"]
    if f["kind"] == "base_box":
        return max(p["w"], p["d"], p["h"])
    if f["kind"] == "base_ring":
        return max(2 * p["ro"], p["h"])
    return max(2 * p["r"], p["h"])  # base_cyl, base_hex


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="shhivv/taiga-s1")
    ap.add_argument("--goals", required=True, help="JSON file of named goals ({name: goal})")
    ap.add_argument("--name", help="goal to build (default: all goals in the file)")
    ap.add_argument("--out", default="parts", help="directory for the .FCStd files")
    ap.add_argument("--quiet", action="store_true", help="only print the result line per part")
    args = ap.parse_args()

    model = load_checkpoint(args.model) if args.model.endswith(".pt") else from_pretrained(args.model)
    policy = Policy(model, "cpu")
    goals = json.loads(Path(args.goals).read_text())
    names = [args.name] if args.name else list(goals)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    env = FreeCADEnv()
    failures = 0
    try:
        for name in names:
            spec = dict(goals[name])
            spec.setdefault("scale", base_scale(spec))
            spec.setdefault("level", 3)
            r = env.call({"op": "reset", "goal": spec,
                          "start": {"doc_open": True, "workbench": "PartDesignWorkbench"}})
            goal = Goal.from_json(r["goal"])
            print(f"\n== {name}: " + " -> ".join(f"{f.kind}{json.dumps(f.params)}" for f in goal.features))
            state, actions, expert = State.from_json(r["state"]), r["actions"], r["expert"]
            steps = agree = 0
            done = False
            t0 = time.time()
            while steps < r["budget"]:
                action = policy.act([state], [goal], [actions])[0]
                steps += 1
                agree += action in expert
                if not args.quiet:
                    mark = "ok" if action in expert else f"(expert: {expert[0]})"
                    print(f"{steps:3d}  {action:34s} {mark}")
                s = env.call({"op": "step", "action": action})
                if s["info"].get("error") and not args.quiet:
                    print("      error:", s["info"]["error"])
                if s["info"]["done"] or not s["expert"]:
                    done = s["info"]["done"]
                    break
                state, actions, expert = State.from_json(s["state"]), s["actions"], s["expert"]
            sc = env.call({"op": "score"})
            fcstd = out / f"{name}.FCStd"
            env.call({"op": "save", "fcstd": str(fcstd)})
            ok = done and sc["match"]
            failures += not ok
            print(f"{name}: {'SUCCESS' if ok else 'FAIL'}  IoU {sc['iou']:.4f}  done {done}  steps {steps}  "
                  f"agreement {agree}/{steps}  {time.time() - t0:.1f}s  -> {fcstd}")
    finally:
        env.close()
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
