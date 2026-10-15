#!/usr/bin/env python3
"""taiga_runtime.py — the production runtime: build every goal with a chain of attempts, keep the
first one the checker accepts, log every attempt.
Version: 2026.10.15.1

Run with the upstream venv and the FreeCAD paths from setup, like taiga_build_part.py:
    cd ~/taiga-expanded-0017/taiga-s1 && source ../freecad.env
    .venv/bin/python <repo>/build/taiga_runtime.py --registry <repo>/build/registry.json \\
        --goals my_goals.json --out parts --workers 8

Each goal goes through the chain (--chain, default "teacher,main:guided,main,fallback:guided")
until one attempt is accepted:
  teacher          the scripted teacher (exact for every goal the vocabulary expresses)
  main, fallback   a model from the registry (role "main" or "fallback"), built command by command
  :guided          with the runtime supplying the next goal intent (taiga_build_part.py
                   --guided-progress; for complete goals)
An attempt is accepted if taiga_build_part.py reports SUCCESS and its IoU with the goal's target
is at least --min-iou (default 0.999; the builder's own SUCCESS needs 0.99). A goal whose
target cannot be built at all (INFEASIBLE) stops at once: no attempt can be checked.

Every attempt runs taiga_build_part.py in its own process (one FreeCAD worker each, with its
step timeout and memory limit), so --workers goals are built in parallel. Accepted parts are
copied to <out>/<name>.FCStd and .step; every attempt stays in <out>/attempts/<name>/<how>/.
<out>/runtime_log.jsonl gets one line per goal (attempts, results, times, the accepted one).

With "teacher" first in the chain (the default), a model builds a goal only when the teacher
cannot; put a model first (e.g. --chain main:guided,teacher) to use or evaluate the model.

Registry (JSON): {"models": [{"name": "small-v1", "role": "main", "path": ".../hf",
"params": "1.2M", "freecad": "1.1.3", "patches": "0001-0017", "trained_on": "...",
"measured": {...}}, ...]}; see registry.example.json.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULT = re.compile(r"^(?P<name>\S+): (?P<res>SUCCESS|FAIL|CRASH|INFEASIBLE)\b(?:\s+IoU (?P<iou>[0-9.]+))?")


def load_registry(path: Path) -> dict[str, dict]:
    reg = json.loads(path.read_text())
    out: dict[str, dict] = {}
    for m in reg.get("models", []):
        role = m.get("role")
        if role in ("main", "fallback") and role not in out:
            if not Path(m["path"]).expanduser().exists():
                sys.exit(f"error: registry model {m.get('name')} ({role}): {m['path']} not found")
            out[role] = m
    return out


def attempt(build: Path, goals: Path, name: str, how: str, models: dict, out: Path, extra: list[str],
            timeout_s: float) -> dict:
    """One attempt: run taiga_build_part.py for one goal; return its parsed result."""
    who, _, mode = how.partition(":")
    folder = out / "attempts" / name / how.replace(":", "_")
    folder.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(build), "--goals", str(goals), "--name", name, "--out", str(folder), "--quiet", *extra]
    if who == "teacher":
        cmd.append("--teacher")
    else:
        if who not in models:
            return {"how": how, "result": "SKIPPED", "why": f"no '{who}' model in the registry"}
        cmd += ["--model", str(Path(models[who]["path"]).expanduser())]
        if mode == "guided":
            cmd.append("--guided-progress")
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        text = p.stdout + p.stderr
    except subprocess.TimeoutExpired as exc:
        text = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        text += f"\n{name}: CRASH  attempt over {timeout_s:.0f} s"
    (folder / "build.log").write_text(text)
    res = {"how": how, "result": "CRASH", "iou": 0.0, "seconds": round(time.time() - t0, 1)}
    if who != "teacher":
        res["model"] = models[who].get("name", who)
    for line in text.splitlines():
        m = RESULT.match(line.strip())
        if m and m["name"] == name:
            res["result"] = m["res"]
            res["iou"] = float(m["iou"]) if m["iou"] else 0.0
    return res


def run_goal(name: str, args, models: dict, out: Path, chain: list[str], extra: list[str]) -> dict:
    record = {"goal": name, "attempts": [], "accepted": None}
    t0 = time.time()
    for how in chain:
        r = attempt(Path(args.build), Path(args.goals), name, how, models, out, extra, args.attempt_timeout)
        record["attempts"].append(r)
        if r["result"] == "INFEASIBLE":
            record["why"] = "the goal's target cannot be built (no attempt can be checked)"
            break
        if r["result"] == "SUCCESS" and r["iou"] >= args.min_iou:
            record["accepted"] = how
            src = out / "attempts" / name / how.replace(":", "_")
            for ext in (".FCStd", ".step"):
                if (src / f"{name}{ext}").is_file():
                    shutil.copy2(src / f"{name}{ext}", out / f"{name}{ext}")
            break
    record["seconds"] = round(time.time() - t0, 1)
    tried = ", ".join(f"{a['how']} {a['result']}" + (f" {a['iou']:.4f}" if a.get("iou") else "")
                      for a in record["attempts"])
    print(f"{name:32s} {'OK  ' + record['accepted'] if record['accepted'] else 'NOT BUILT':22s} {tried}", flush=True)
    return record


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goals", required=True, help="goals JSON {name: goal}")
    ap.add_argument("--registry", help="models JSON (needed for any model in the chain)")
    ap.add_argument("--out", default="parts", help="accepted parts, attempts and runtime_log.jsonl")
    ap.add_argument("--names", nargs="*", help="only these goals (default: all)")
    ap.add_argument("--chain", default="teacher,main:guided,main,fallback:guided",
                    help="attempts in order: teacher, main, fallback, each optionally :guided")
    ap.add_argument("--min-iou", type=float, default=0.999, help="IoU with the target an accepted part needs (0.999)")
    ap.add_argument("--workers", type=int, default=4, help="goals built in parallel (one FreeCAD worker each)")
    ap.add_argument("--attempt-timeout", type=float, default=7200, help="seconds one attempt may take (7200)")
    ap.add_argument("--build", default=str(HERE / "taiga_build_part.py"), help=argparse.SUPPRESS)
    ap.add_argument("--build-args", default="", help="extra options for taiga_build_part.py, e.g. \"--no-step\"")
    args = ap.parse_args()

    chain = [c.strip() for c in args.chain.split(",") if c.strip()]
    for c in chain:
        if c.partition(":")[0] not in ("teacher", "main", "fallback") or c.partition(":")[2] not in ("", "guided"):
            sys.exit(f"error: unknown attempt '{c}' in --chain")
    needs_models = any(c.partition(":")[0] != "teacher" for c in chain)
    models = load_registry(Path(args.registry).expanduser()) if (needs_models and args.registry) else {}
    if needs_models and "main" not in models:
        print("note: no main model (no --registry, or none with role \"main\"): model attempts are skipped", flush=True)
    goals = json.loads(Path(args.goals).read_text())
    names = args.names or list(goals)
    missing = [n for n in names if n not in goals]
    if missing:
        sys.exit(f"error: not in {args.goals}: {', '.join(missing)}")
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    extra = args.build_args.split()
    print(f"runtime: {len(names)} goals, chain {' -> '.join(chain)}, min IoU {args.min_iou}, {args.workers} workers", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        records = list(pool.map(lambda n: run_goal(n, args, models, out, chain, extra), names))
    with open(out / "runtime_log.jsonl", "a") as fh:
        for r in records:
            fh.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "chain": chain,
                                 "models": {k: v.get("name") for k, v in models.items()}, **r}) + "\n")
    by = {}
    for r in records:
        by[r["accepted"] or "not built"] = by.get(r["accepted"] or "not built", 0) + 1
    print(f"\n{len(names)} goals in {time.time() - t0:.0f} s: " + ", ".join(f"{v} {k}" for k, v in by.items()))
    print(f"parts in {out}, log in {out / 'runtime_log.jsonl'}")
    sys.exit(0 if by.get("not built", 0) == 0 else 1)


if __name__ == "__main__":
    main()
