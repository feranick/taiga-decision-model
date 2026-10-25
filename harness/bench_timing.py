#!/usr/bin/env python3
"""bench_timing.py — the timing benchmark (plan item 11): where the time of a build goes, with every
attempt a separate process (taiga_runtime.py) and with the persistent pool (taiga_pool.py).
Version: 2026.10.24.1

Builds a fixed goal set through the harness (model first: main:guided, then the teacher), in three
passes: "process" (TAIGA_PERSISTENT=0), "pool cold" (the first pass of a new pool: FreeCAD workers
and models start on first use) and "pool warm" (--rounds more passes with the same pool, as the MCP
server runs). Per attempt it splits the time into the target build (reset), model decisions,
FreeCAD steps, check, save (and STEP export) and the rest ("overhead": process start, imports,
model loading, FreeCAD start, waiting), and prints the totals per pass and per goal.

Run on a machine with the production install (docs/README_install.md), with its Python:
    export TAIGA_WORK=~/taiga_prod/work TAIGA_REGISTRY=~/taiga_prod/registry.json
    $TAIGA_WORK/taiga-s1/.venv/bin/python <repo>/harness/bench_timing.py [--rounds 2] [--long] [--out bench.json]
Goal set: the three parts of build/flange/example_goals.json, four draft parts, and the S 80 parts
other than the casing (taiga-expanded/eval/s80_goals.json); --long adds the S 80 casing core
(24 intents, about 2.5 min per attempt). --workers (default 1) builds that many goals at a time;
with 1 the times per attempt are not mixed with other builds.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

S80_SHORT = ["s80_impeller", "s80_wear_plate", "s80_inspection_cover", "s80_priming_cover", "s80_check_valve",
             "s80_bearing_bracket", "s80_shaft", "s80_bearing", "s80_mechanical_seal"]
DRAFT = {
    "draft_box": {"features": [{"kind": "base_box", "params": {"w": 40, "d": 30, "h": 20}},
                               {"kind": "draft", "params": {"angle": 5}}]},
    "draft_cyl": {"features": [{"kind": "base_cyl", "params": {"r": 15, "h": 20}}, {"kind": "draft", "params": {"angle": 5}}]},
    "draft_pts": {"features": [{"kind": "base_box", "params": {"w": 40, "d": 30, "h": 20}},
                               {"kind": "draft", "params": {"angle": 3, "pts": [[20, 0, 10]]}}]},
    "draft_boss": {"features": [{"kind": "base_box", "params": {"w": 60, "d": 40, "h": 15}},
                                {"kind": "boss_cyl", "params": {"r": 8, "x": 10, "y": 0, "h": 10}},
                                {"kind": "draft", "params": {"angle": 4}}]},
}
STAGES = ("reset", "model", "freecad", "check", "save", "overhead")
TIMING = re.compile(r"time to make: (?P<make>[0-9.]+) s\s+\(model (?P<model>[0-9.]+) s.*?FreeCAD (?P<freecad>[0-9.]+) s"
                    r".*?check (?P<check>[0-9.]+) s, save/export (?P<save>[0-9.]+) s\s+\|\s+total (?P<total>[0-9.]+) s"
                    r"(?:\s+\|\s+target build \(reset\) (?P<reset>[0-9.]+) s)?")


def goal_set(long: bool, kinds: set[str]) -> dict:
    goals = json.loads((REPO / "build" / "flange" / "example_goals.json").read_text())
    s80 = json.loads((REPO / "taiga-expanded" / "eval" / "s80_goals.json").read_text())
    goals.update({n: s80[n] for n in S80_SHORT if n in s80})
    if "draft" in kinds:
        goals.update(DRAFT)
    if long and "s80_casing_core" in s80:
        goals["s80_casing_core"] = s80["s80_casing_core"]
    return goals


def stage_times_process(folder: Path, name: str, att: dict) -> dict:
    """Stage times of a process-mode attempt, from its build.log (taiga_build_part.py's timing line)."""
    log = folder / "attempts" / name / att["how"].replace(":", "_") / "build.log"
    m = TIMING.search(log.read_text()) if log.is_file() else None
    t = {k: 0.0 for k in STAGES}
    if m:
        for k in ("model", "freecad", "check", "save"):
            t[k] = float(m[k])
        t["reset"] = float(m["reset"]) if m["reset"] else 0.0
    t["overhead"] = max(0.0, float(att.get("seconds") or 0.0) - sum(t[k] for k in STAGES if k != "overhead"))
    return t


def stage_times_pool(att: dict, step_export: float) -> dict:
    t = att.get("times", {})
    out = {k: float(t.get(k, 0.0)) for k in ("reset", "model", "freecad", "check", "save")}
    out["save"] += step_export
    out["overhead"] = float(t.get("overhead", 0.0)) + float(t.get("model_load", 0.0))
    return out


def run_pass(H, goals: dict, chain: str, workers: int, mode: str) -> dict:
    t0 = time.perf_counter()
    res = H.build(goals, chain=chain, workers=workers)
    wall = time.perf_counter() - t0
    folder = Path(res["request"])
    log = folder / "runtime_log.jsonl"
    records = {r["goal"]: r for r in (json.loads(x) for x in log.read_text().splitlines())} if log.is_file() else {}
    per_goal, totals, n_att = {}, {k: 0.0 for k in STAGES}, 0
    for name, part in res["parts"].items():
        rec = records.get(name, {})
        st = {k: 0.0 for k in STAGES}
        for att in part.get("attempts", []):
            if att.get("result") == "SKIPPED":
                continue
            n_att += 1
            t = (stage_times_process(folder, name, att) if res.get("runtime") == "process"
                 else stage_times_pool(att, rec.get("step_export_s", 0.0) if att["how"] == part.get("accepted") else 0.0))
            for k in STAGES:
                st[k] += t[k]
        for k in STAGES:
            totals[k] += st[k]
        per_goal[name] = {"built": part.get("built"), "accepted": part.get("accepted"),
                          "attempts": [a.get("how") + " " + a.get("result", "") for a in part.get("attempts", [])],
                          "seconds": part.get("seconds"), "stages": {k: round(v, 2) for k, v in st.items()}}
    return {"mode": mode, "runtime": res.get("runtime"), "wall_s": round(wall, 1), "attempts": n_att,
            "built": sum(bool(p.get("built")) for p in res["parts"].values()), "goals": len(res["parts"]),
            "stages_s": {k: round(v, 2) for k, v in totals.items()}, "per_goal": per_goal, "request": str(folder)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, default=2, help="warm pool passes after the cold one (default 2)")
    ap.add_argument("--workers", type=int, default=1, help="goals built at a time (default 1)")
    ap.add_argument("--chain", default="main:guided,teacher")
    ap.add_argument("--long", action="store_true", help="add the S 80 casing core")
    ap.add_argument("--skip-process", action="store_true", help="only the pool passes")
    ap.add_argument("--out", default="bench_timing.json")
    a = ap.parse_args()
    os.environ["TAIGA_POOL_WORKERS"] = str(a.workers)
    import taiga_harness as H

    kinds = set(H._schema().GOAL_KINDS)
    goals = goal_set(a.long, kinds)
    print(f"benchmark on {socket.gethostname()}: {len(goals)} goals, chain {a.chain}, {a.workers} worker(s), "
          f"work {H.work()}", flush=True)
    passes = []
    if not a.skip_process:
        os.environ["TAIGA_PERSISTENT"] = "0"
        passes.append(run_pass(H, goals, a.chain, a.workers, "process"))
        print(f"  process:   {passes[-1]['wall_s']:7.1f} s", flush=True)
    os.environ["TAIGA_PERSISTENT"] = "1"
    passes.append(run_pass(H, goals, a.chain, a.workers, "pool cold"))
    print(f"  pool cold: {passes[-1]['wall_s']:7.1f} s", flush=True)
    for k in range(a.rounds):
        passes.append(run_pass(H, goals, a.chain, a.workers, f"pool warm {k + 1}"))
        print(f"  pool warm: {passes[-1]['wall_s']:7.1f} s", flush=True)

    print(f"\n{'pass':14s} {'wall s':>8s} {'built':>7s} {'att':>4s} " + " ".join(f"{k:>9s}" for k in STAGES))
    for p in passes:
        print(f"{p['mode']:14s} {p['wall_s']:8.1f} {p['built']:>3d}/{p['goals']:<3d} {p['attempts']:4d} "
              + " ".join(f"{p['stages_s'][k]:9.2f}" for k in STAGES))
    base, warm = passes[0], passes[-1]
    print(f"\nper goal (s): {base['mode']} -> {warm['mode']}")
    for n in goals:
        b, w = base["per_goal"].get(n, {}), warm["per_goal"].get(n, {})
        print(f"  {n:24s} {b.get('seconds') or 0:7.1f} -> {w.get('seconds') or 0:7.1f}   "
              f"overhead {b.get('stages', {}).get('overhead', 0):5.2f} -> {w.get('stages', {}).get('overhead', 0):5.2f}"
              f"   {', '.join(w.get('attempts', []))}")
    Path(a.out).write_text(json.dumps({"host": socket.gethostname(), "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                       "chain": a.chain, "workers": a.workers, "passes": passes}, indent=1))
    print(f"\nwritten: {a.out}")
    p = H.pool()
    if p is not None:
        p.close()


if __name__ == "__main__":
    main()
