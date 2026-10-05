#!/usr/bin/env python3
"""taiga_build_part.py — build a CAD part headless with a Taiga-S1 model (inference).
Version: 2026.10.05.2

The model drives a headless FreeCAD worker command by command toward a goal
(an ordered feature list), then the part is checked against the goal's target
solid (volumetric IoU) and saved as .FCStd.

Run with the upstream venv and the FreeCAD paths from setup. Relative --model and
--goals paths are looked up in the current directory first, then in the upstream
repo (~/taiga/taiga-s1), so they work from any directory:
    cd ~/taiga/taiga-s1 && source ~/taiga/freecad.env
    .venv/bin/python /path/to/taiga_build_part.py --model runs/seed2/hf \
        --goals showcase/goals.json --name flange --out parts

--model: exported dir (runs/seed<N>/hf), a .pt checkpoint, or shhivv/taiga-s1.
--goals: JSON {name: goal}; see showcase/goals.json and example_goals.json.

Output per part: <name>.FCStd and <name>.step. The worker runs FreeCAD without a GUI,
so the saved document has no GuiDocument.xml (view settings); FreeCAD then opens it
with every object hidden. The script adds a minimal GuiDocument.xml that shows the
objects FreeCAD marked visible (the Body's final feature). --no-gui-data skips this.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

from freecad_s1.model.net import from_pretrained, load_checkpoint
from freecad_s1.rollout import Policy
from freecad_s1.runtime.client import FreeCADEnv
from freecad_s1.runtime.fcenv import REPO_ROOT, freecad_env, freecad_python
from freecad_s1.schema import Goal, State


def local_path(arg: str) -> Path | None:
    """`arg` as an existing path: as given (relative to the current directory), else
    relative to the upstream repo root. None if neither exists."""
    p = Path(arg).expanduser()
    for cand in (p, REPO_ROOT / p):
        if cand.exists():
            return cand.resolve()
    return None


def load_model(arg: str):
    p = local_path(arg)
    if p is not None:
        return load_checkpoint(str(p)) if p.suffix == ".pt" else from_pretrained(str(p))
    looks_local = arg.endswith(".pt") or arg.startswith((".", "/", "~")) or arg.count("/") != 1 \
        or arg.split("/")[0] in ("runs", "release", "checkpoints")
    if looks_local:
        sys.exit(f"error: model not found: {arg}\n  looked in {Path.cwd()} and {REPO_ROOT}")
    return from_pretrained(arg)  # Hugging Face repo id, e.g. shhivv/taiga-s1


def add_gui_document(fcstd: Path) -> int:
    """Add a minimal GuiDocument.xml so FreeCAD shows the objects whose App-side
    Visibility is true. Returns the number of visible objects."""
    with zipfile.ZipFile(fcstd) as z:
        if "GuiDocument.xml" in z.namelist():
            return -1
        doc = z.read("Document.xml").decode("utf-8")
        members = [(i, z.read(i.filename)) for i in z.infolist()]
    blocks = [(m.group(1), m.group(2)) for m in re.finditer(r'<Object name="([^"]+)"[^>]*>(.*?)</Object>', doc, re.S)]
    # Show each Body and its Tip (the finished part), like FreeCAD does after a recompute;
    # hide origins, sketches and intermediate features. Without a Tip, keep App-side Visibility.
    tips = {t.group(1) for _, body in blocks
            for t in [re.search(r'<Property name="Tip"[^>]*>\s*<Link value="([^"]+)"', body)] if t}
    bodies = {n for n, _ in blocks if n in re.findall(r'<Object type="PartDesign::Body" name="([^"]+)"', doc)}
    objs = []
    for name, body in blocks:
        if tips:
            vis = name in tips or name in bodies
        else:
            v = re.search(r'<Property name="Visibility"[^>]*>\s*<Bool value="(\w+)"', body)
            vis = bool(v and v.group(1) == "true")
        objs.append((name, vis))
    vps = "".join(
        f'        <ViewProvider name={quoteattr(name)} expanded="0">\n'
        f'            <Properties Count="1" TransientCount="0">\n'
        f'                <Property name="Visibility" type="App::PropertyBool">\n'
        f'                    <Bool value="{"true" if vis else "false"}"/>\n'
        f'                </Property>\n'
        f'            </Properties>\n'
        f'        </ViewProvider>\n' for name, vis in objs)
    gui = ("<?xml version='1.0' encoding='utf-8'?>\n<Document SchemaVersion=\"1\">\n"
           f'    <ViewProviderData Count="{len(objs)}">\n{vps}    </ViewProviderData>\n'
           '    <Camera settings=""/>\n</Document>\n')
    tmp = fcstd.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in members:
            z.writestr(info, data)
        z.writestr("GuiDocument.xml", gui)
    tmp.replace(fcstd)
    return sum(v for _, v in objs)


def export_step(fcstd: Path, step: Path) -> str | None:
    """Export the Body's final shape as STEP, in a separate FreeCAD process. Returns an error or None."""
    code = ("import sys, FreeCAD\n"
            "d = FreeCAD.openDocument(sys.argv[1])\n"
            "b = [o for o in d.Objects if o.TypeId == 'PartDesign::Body']\n"
            "s = b[0].Shape if b else None\n"
            "if s is None or s.isNull(): sys.exit('no Body shape')\n"
            "s.exportStep(sys.argv[2])\n")
    py, _ = freecad_python()
    r = subprocess.run([py, "-c", code, str(fcstd), str(step)], env=freecad_env(), capture_output=True, text=True)
    return None if r.returncode == 0 and step.is_file() else (r.stderr.strip().splitlines() or ["failed"])[-1]


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
    ap.add_argument("--no-gui-data", action="store_true", help="don't add GuiDocument.xml to the .FCStd")
    ap.add_argument("--no-step", action="store_true", help="don't export a .step file")
    args = ap.parse_args()

    if not (os.environ.get("FREECAD_PYTHON") and os.environ.get("FREECAD_LIB")):
        sys.exit("error: FreeCAD paths not set; run `source ~/taiga/freecad.env` first")
    goals_path = local_path(args.goals)
    if goals_path is None:
        sys.exit(f"error: goals file not found: {args.goals}\n  looked in {Path.cwd()} and {REPO_ROOT}")
    model = load_model(args.model)
    policy = Policy(model, "cpu")
    goals = json.loads(goals_path.read_text())
    if args.name and args.name not in goals:
        sys.exit(f"error: no goal named '{args.name}' in {goals_path}; available: {', '.join(goals)}")
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
            extra = []
            if not args.no_gui_data:
                n = add_gui_document(fcstd)
                if n == 0:
                    extra.append("no visible objects")
            if not args.no_step:
                err = export_step(fcstd, fcstd.with_suffix(".step"))
                extra.append(f"STEP export failed: {err}" if err else f"{fcstd.with_suffix('.step').name}")
            ok = done and sc["match"]
            failures += not ok
            print(f"{name}: {'SUCCESS' if ok else 'FAIL'}  IoU {sc['iou']:.4f}  done {done}  steps {steps}  "
                  f"agreement {agree}/{steps}  {time.time() - t0:.1f}s  -> {fcstd}" + (f" (+ {', '.join(extra)})" if extra else ""))
    finally:
        env.close()
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
