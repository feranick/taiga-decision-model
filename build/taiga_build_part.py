#!/usr/bin/env python3
"""taiga_build_part.py — build a CAD part headless with a Taiga-S1 model (inference).
Version: 2026.10.08.2

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
--goals: JSON {name: goal}; see showcase/goals.json (upstream), flange/example_goals.json
         and engine/example_goals_engine.json (this folder).
--check: only check that every goal can be built (the teacher builds the target
         solid); no model is loaded and no parts are written.
--teacher: build the parts with the scripted teacher (the expert that labels the training
         data) instead of a model, and save them like model builds. Shows what the goal
         itself produces, independent of any model (e.g. to compare goals with a reference).

Loop guard (model builds, on by default): when the model is back in a state it has already
acted from (e.g. Pad -> invalid -> Undo -> the same state) and picks an action it already took
there, the guard takes its most likely action not yet tried in that state instead. The model
is unchanged; only repeated dead ends are skipped. One exception: if the action the model wants
to repeat had worked (no error) and the model itself then undid it, the Undo was the mistake, not
the action. The guard then lets the action through once more and blocks Undo in the state it
leads to, so the model takes its next choice there (e.g. Done) instead of tearing the part down.
"Worked" only means FreeCAD accepted it, not that it was right (a hole on the wrong face also
works), so this exception is used at most --guard-redos times per part (default 1); after that
the guard treats every undo as the model's correction again.
--no-loop-guard turns it off (e.g. to score the model alone); the result line reports how often
the guard stepped in.

If the FreeCAD worker crashes on a part (e.g. on a degenerate shape the model made), the part
is reported as CRASH (a failure), the worker is restarted and the remaining goals are built.

Timing: each part reports how long it took to make, split into model decisions,
FreeCAD steps and saving/export; a summary table with the totals is printed at the end.

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
from freecad_s1.runtime.client import FreeCADEnv, WorkerError
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


def state_key(state: dict) -> str:
    """The document state without the action history (an Undo returns to the same key)."""
    return json.dumps({k: v for k, v in state.items() if k not in ("recent", "events")}, sort_keys=True)


def base_scale(goal: dict) -> float:
    """Largest extent of the base feature; upstream's goals use this as `scale`."""
    f = goal["features"][0]
    p = f["params"]
    if f["kind"] == "base_box":
        return max(p["w"], p["d"], p["h"])
    if f["kind"] == "base_ring":
        return max(2 * p["ro"], p["h"])
    if f["kind"] == "profile_base":  # taiga-expanded: outline base, size from its points
        if "w" in p and "d" in p:
            return max(p["w"], p["d"], p["h"])
        from freecad_s1.ext.profiles import bbox

        _, _, w, d = bbox(p["outline"])
        return max(w, d, p["h"])
    if f["kind"] == "profile_revolve":  # taiga-expanded: turned base, 2 x largest radius or length
        from freecad_s1.ext import profiles, revolve
        from freecad_s1.schema import GoalFeature

        k = revolve.axis_index(GoalFeature(f["kind"], p))
        radial_is_u = revolve.to_uv(k, 0.0, 1.0) == (1.0, 0.0)
        pts = profiles.polyline(p["outline"])
        rad = [abs(u if radial_is_u else v) for u, v in pts]
        along = [v if radial_is_u else u for u, v in pts]
        return max(2 * max(rad), max(along) - min(along))
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
    ap.add_argument("--check", action="store_true", help="only check that the goals can be built")
    ap.add_argument("--teacher", action="store_true", help="build with the scripted teacher instead of a model")
    ap.add_argument("--no-loop-guard", action="store_true",
                    help="don't skip actions the model already took in the same state (see Loop guard above)")
    ap.add_argument("--guard-redos", type=int, default=1,
                    help="how often per part the guard may redo a step the model undid (see Loop guard above)")
    args = ap.parse_args()

    if not (os.environ.get("FREECAD_PYTHON") and os.environ.get("FREECAD_LIB")):
        sys.exit("error: FreeCAD paths not set; run `source ~/taiga/freecad.env` first")
    goals_path = local_path(args.goals)
    if goals_path is None:
        sys.exit(f"error: goals file not found: {args.goals}\n  looked in {Path.cwd()} and {REPO_ROOT}")
    goals = json.loads(goals_path.read_text())
    policy = None if (args.check or args.teacher) else Policy(load_model(args.model), "cpu")
    if args.name and args.name not in goals:
        sys.exit(f"error: no goal named '{args.name}' in {goals_path}; available: {', '.join(goals)}")
    names = [args.name] if args.name else list(goals)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    env = FreeCADEnv()
    failures = 0
    summary = []  # (name, result, steps, model s, freecad s, save s, total s)
    t_run = time.perf_counter()
    try:
        for name in names:
            spec = dict(goals[name])
            spec.setdefault("scale", base_scale(spec))
            spec.setdefault("level", 3)
            try:
                r = env.call({"op": "reset", "goal": spec,
                              "start": {"doc_open": True, "workbench": "PartDesignWorkbench"}})
            except WorkerError as exc:
                failures += 1
                if env.proc.poll() is not None or "exited" in str(exc):  # the worker died: start a new one
                    try:
                        env.close()
                    except Exception:
                        pass
                    env = FreeCADEnv()
                print(f"\n{name}: INFEASIBLE  {exc}")
                summary.append((name, "INFEASIBLE", 0, 0.0, 0.0, 0.0, 0.0))
                continue
            goal = Goal.from_json(r["goal"])
            if args.check:
                print(f"{name}: OK  {len(goal.features)} features, budget {r['budget']} steps, "
                      f"target volume {goal.target.volume:.0f} mm3, bbox {tuple(round(x, 1) for x in goal.target.bbox)}")
                continue
            try:
                print(f"\n== {name}: " + " -> ".join(f"{f.kind}{json.dumps(f.params)}" for f in goal.features))
                raw = r["state"]
                state, actions, expert = State.from_json(raw), r["actions"], r["expert"]
                steps = agree = guarded = 0
                tried: dict[str, set[str]] = {}  # loop guard: actions already taken from each state
                went: dict[tuple[str, str], tuple[str, bool]] = {}  # (state, action) -> (next state, error)
                redone: set[tuple[str, str]] = set()  # actions let through again after an undo
                key = ""
                done = False
                t_model = t_fc = 0.0
                t0 = time.perf_counter()
                while steps < r["budget"]:
                    t = time.perf_counter()
                    if args.teacher:
                        action = expert[0]
                    elif args.no_loop_guard:
                        action = policy.act([state], [goal], [actions])[0]
                    else:
                        probs = policy.distributions([state], [goal], [actions])[0]
                        ranked = [actions[j] for j in sorted(range(len(actions)), key=lambda j: -float(probs[j]))]
                        key = state_key(raw)
                        seen = tried.setdefault(key, set())
                        top = ranked[0]
                        nxt, err = went.get((key, top), ("", True))
                        if (top in seen and top != "Std_Undo" and not err and (key, top) not in redone
                                and len(redone) < args.guard_redos and "Std_Undo" in tried.get(nxt, ())):
                            action = top  # it worked and was undone: redo it; Undo stays blocked there
                            redone.add((key, top))
                            guarded += 1
                            if not args.quiet:
                                print(f"      loop guard: {top} worked before and was undone; redo it, no Undo after it")
                        else:
                            action = next((a for a in ranked if a not in seen), top)
                            if action != top:
                                guarded += 1
                                if not args.quiet:
                                    print(f"      loop guard: {top} already tried here")
                        seen.add(action)
                    t_model += time.perf_counter() - t
                    steps += 1
                    agree += action in expert
                    if not args.quiet:
                        mark = "ok" if action in expert else f"(expert: {expert[0]})"
                        print(f"{steps:3d}  {action:34s} {mark}")
                    t = time.perf_counter()
                    s = env.call({"op": "step", "action": action})
                    t_fc += time.perf_counter() - t
                    if s["info"].get("error") and not args.quiet:
                        print("      error:", s["info"]["error"])
                    if key and s.get("state") is not None:
                        went[(key, action)] = (state_key(s["state"]), bool(s["info"].get("error")))
                    if s["info"]["done"] or not s["expert"]:
                        done = s["info"]["done"]
                        break
                    raw = s["state"]
                    state, actions, expert = State.from_json(raw), s["actions"], s["expert"]
                t_built = time.perf_counter()
                sc = env.call({"op": "score"})
                t_score = time.perf_counter() - t_built
                t_save0 = time.perf_counter()
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
                t_save = time.perf_counter() - t_save0
                t_make = t_built - t0
                ok = done and sc["match"]
                failures += not ok
                print(f"{name}: {'SUCCESS' if ok else 'FAIL'}  IoU {sc['iou']:.4f}  done {done}  steps {steps}  "
                      f"agreement {agree}/{steps}" + (f"  loop guard {guarded}x" if guarded else "")
                      + f"  -> {fcstd}" + (f" (+ {', '.join(extra)})" if extra else ""))
                print(f"   time to make: {t_make:.2f} s  (model {t_model:.2f} s = {1000 * t_model / max(steps, 1):.1f} ms/step, "
                      f"FreeCAD {t_fc:.2f} s = {1000 * t_fc / max(steps, 1):.1f} ms/step)  |  "
                      f"check {t_score:.2f} s, save/export {t_save:.2f} s  |  total {t_make + t_score + t_save:.2f} s")
                summary.append((name, "SUCCESS" if ok else "FAIL", steps, t_model, t_fc, t_save + t_score, t_make + t_score + t_save))
            except WorkerError as exc:  # FreeCAD crashed on the part the model built: count it as failed, go on
                failures += 1
                print(f"{name}: CRASH  FreeCAD worker died ({exc}); restarting the worker")
                summary.append((name, "CRASH", steps, t_model, t_fc, 0.0, time.perf_counter() - t0))
                try:
                    env.close()
                except Exception:
                    pass
                env = FreeCADEnv()
    finally:
        env.close()
    if summary and not args.check:
        w = max(10, *(len(n) for n, *_ in summary))
        print(f"\n{'part':{w}s} {'result':>10s} {'steps':>6s} {'model s':>8s} {'FreeCAD s':>10s} {'make s':>8s} "
              f"{'check+save s':>12s} {'total s':>8s}")
        for name, res, st, tm, tf, tsv, tt in summary:
            print(f"{name:{w}s} {res:>10s} {st:6d} {tm:8.2f} {tf:10.2f} {tm + tf:8.2f} {tsv:12.2f} {tt:8.2f}")
        built = [x for x in summary if x[1] != "INFEASIBLE"]
        tot = [sum(x[i] for x in built) for i in (2, 3, 4, 5, 6)]
        n_ok = sum(x[1] == "SUCCESS" for x in summary)
        ok_txt = f"{n_ok}/{len(summary)} ok"
        print(f"{'all parts':{w}s} {ok_txt:>10s} {tot[0]:6d} {tot[1]:8.2f} {tot[2]:10.2f} {tot[1] + tot[2]:8.2f} "
              f"{tot[3]:12.2f} {tot[4]:8.2f}")
        print(f"wall time for all parts (incl. building each target for the check): {time.perf_counter() - t_run:.1f} s")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
