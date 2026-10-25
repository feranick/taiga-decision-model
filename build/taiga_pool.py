#!/usr/bin/env python3
"""taiga_pool.py — the production runtime in one long-lived process (persistent worker, plan item 12):
FreeCAD workers and models stay loaded between attempts, goals and requests.
Version: 2026.10.24.1

taiga_runtime.py runs every attempt as a new taiga_build_part.py process: a Python start with
PyTorch, loading the model, starting a FreeCAD worker, and a second FreeCAD process to export the
STEP file, about 2 s before and after the build itself, for every attempt and every fallback. A
BuildPool keeps --workers FreeCAD workers and every model it has used; an attempt is then the
build alone (taiga_build_part.build_goal, the same code as the command line, so the same chain,
loop guard, guided progress, checks and files). The STEP file is exported once, for the accepted
part only. A FreeCAD crash restarts that worker, as before; each FreeCAD request keeps its time
limit and the worker its memory limit, and an attempt stops after --attempt-timeout.

Used in-process by the harness (taiga_harness.py, and so the MCP server, which keeps one pool for
its lifetime), or from the command line with the same options as taiga_runtime.py:
    cd <work>/taiga-s1 && source ../freecad.env
    .venv/bin/python <repo>/build/taiga_pool.py --registry ~/taiga_prod/registry.json \\
        --goals my_goals.json --out parts --chain main:guided,teacher [--repeat 3]
--repeat N builds the goals N times in the same process (each round into <out>/round<k>), to see
the warm pool's times. Output: as taiga_runtime.py (<out>/<name>.FCStd and .step,
<out>/attempts/<name>/<how>/, <out>/runtime_log.jsonl), every attempt with its stage times.
"""
from __future__ import annotations

import argparse
import json
import queue
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import taiga_build_part as B  # noqa: E402

CHAIN_DEFAULT = "teacher,main:guided,main,fallback:guided"


def parse_chain(chain: str) -> list[str]:
    out = [c.strip() for c in chain.split(",") if c.strip()]
    for c in out:
        if c.partition(":")[0] not in ("teacher", "main", "fallback") or c.partition(":")[2] not in ("", "guided"):
            raise ValueError(f"unknown attempt '{c}' in the chain")
    return out


def load_registry(path: Path) -> dict[str, dict]:
    """{role: entry} for the first model of each role (main, fallback) whose path exists."""
    reg = json.loads(path.read_text())
    out: dict[str, dict] = {}
    for m in reg.get("models", []):
        role = m.get("role")
        if role in ("main", "fallback") and role not in out and Path(m["path"]).expanduser().exists():
            out[role] = m
    return out


class BuildPool:
    def __init__(self, workers: int = 4, mem_limit_gb: float | None = None, threads: int = 1,
                 device: str = "cpu", step_timeout: float = 900) -> None:
        self.n = max(1, workers)
        self.mem = B.default_worker_limit_gb() if mem_limit_gb is None else mem_limit_gb
        self.device, self.step_timeout = device, step_timeout
        if threads > 0:
            import torch

            torch.set_num_threads(threads)
        self._free: queue.Queue = queue.Queue()
        for _ in range(self.n):
            self._free.put(B.Worker(self.mem))
        self._workers = list(self._free.queue)
        self._policies: dict[str, object] = {}
        self._plock = threading.Lock()
        self._exec = ThreadPoolExecutor(max_workers=self.n, thread_name_prefix="taiga-pool")
        self.created = time.time()
        self.attempts = 0

    # -- models and workers --
    def policy(self, path: str):
        path = str(Path(path).expanduser())
        with self._plock:
            if path not in self._policies:
                from freecad_s1.rollout import Policy

                self._policies[path] = Policy(B.load_model(path), self.device)
            return self._policies[path]

    def warm(self, models: dict[str, dict]) -> float:
        """Start every FreeCAD worker and load the models now (else on first use); returns seconds."""
        t0 = time.perf_counter()
        for m in models.values():
            self.policy(m["path"])
        list(self._exec.map(lambda w: w.get(), self._workers))
        return time.perf_counter() - t0

    def status(self) -> dict:
        return {"workers": self.n, "workers_running": sum(w.env is not None and w.env.proc.poll() is None
                                                          for w in self._workers),
                "models_loaded": sorted(self._policies), "attempts": self.attempts,
                "up_s": round(time.time() - self.created, 1)}

    def close(self) -> None:
        self._exec.shutdown(wait=True)
        for w in self._workers:
            w.close()

    # -- builds --
    def _attempt(self, worker, name: str, spec: dict, how: str, models: dict, out: Path, timeout_s: float,
                 gui_data: bool) -> dict:
        who, _, mode = how.partition(":")
        if who != "teacher" and who not in models:
            return {"how": how, "result": "SKIPPED", "why": f"no '{who}' model in the registry"}
        folder = out / "attempts" / name / how.replace(":", "_")
        folder.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        policy = None if who == "teacher" else self.policy(models[who]["path"])
        t_load = time.perf_counter() - t0
        o = SimpleNamespace(teacher=who == "teacher", check=False, quiet=True, no_loop_guard=False,
                            guided_progress=mode == "guided", guard_redos=-1, step_timeout=self.step_timeout,
                            prefixes=False, no_gui_data=not gui_data, no_step=True, canon_top=False,
                            deadline=time.perf_counter() + timeout_s)
        lines: list[str] = []
        r = B.build_goal(worker, policy, name, spec, o, folder, log=lines.append)
        (folder / "build.log").write_text("\n".join(lines) + "\n")
        self.attempts += 1
        seconds = time.perf_counter() - t0
        t = dict(r["times"])
        known = sum(t.get(k, 0.0) for k in ("reset", "make", "check", "save"))
        t.update(model_load=round(t_load, 3), overhead=round(max(0.0, seconds - known), 3))
        res = {"how": how, "result": r["result"], "iou": round(float(r["iou"]), 6), "seconds": round(seconds, 2),
               "steps": r["steps"], "times": t}
        if who != "teacher":
            res["model"] = models[who].get("name", who)
        if r.get("why"):
            res["why"] = r["why"][:500]
        return res

    def run_goal(self, name: str, spec: dict, chain: list[str], models: dict, out: Path, min_iou: float = 0.999,
                 step: bool = True, attempt_timeout: float = 7200, gui_data: bool = True) -> dict:
        record = {"goal": name, "attempts": [], "accepted": None}
        t_wait = time.perf_counter()
        worker = self._free.get()
        t0 = time.perf_counter()
        record["queue_s"] = round(t0 - t_wait, 2)
        try:
            for how in chain:
                r = self._attempt(worker, name, spec, how, models, out, attempt_timeout, gui_data)
                record["attempts"].append(r)
                if r["result"] == "INFEASIBLE":
                    record["why"] = "the goal's target cannot be built (no attempt can be checked)"
                    break
                if r["result"] == "SUCCESS" and r["iou"] >= min_iou:
                    record["accepted"] = how
                    src = out / "attempts" / name / how.replace(":", "_") / f"{name}.FCStd"
                    shutil.copy2(src, out / f"{name}.FCStd")
                    if step:
                        t = time.perf_counter()
                        err = B.export_step(out / f"{name}.FCStd", out / f"{name}.step")
                        record["step_export_s"] = round(time.perf_counter() - t, 2)
                        if err:
                            record["step_error"] = err
                    break
        finally:
            self._free.put(worker)
        record["seconds"] = round(time.perf_counter() - t0, 1)
        return record

    def run(self, goals: dict, chain: str | list[str] = CHAIN_DEFAULT, models: dict | None = None,
            out: Path | str = "parts", min_iou: float = 0.999, step: bool = True, attempt_timeout: float = 7200,
            names: list[str] | None = None, log_file: bool = True, echo=None) -> list[dict]:
        """Build `goals` ({name: goal}) through the chain, up to `workers` goals at a time; returns
        one record per goal (as taiga_runtime.py's runtime_log.jsonl) and appends them to it."""
        chain = parse_chain(chain) if isinstance(chain, str) else chain
        models = models or {}
        out = Path(out).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        names = names or list(goals)

        def one(n):
            rec = self.run_goal(n, goals[n], chain, models, out, min_iou, step, attempt_timeout)
            if echo:
                tried = ", ".join(f"{a['how']} {a['result']}" + (f" {a['iou']:.4f}" if a.get("iou") else "")
                                  for a in rec["attempts"])
                echo(f"{n:32s} {'OK  ' + rec['accepted'] if rec['accepted'] else 'NOT BUILT':22s} {tried}")
            return rec

        records = list(self._exec.map(one, names))
        if log_file:
            with open(out / "runtime_log.jsonl", "a") as fh:
                for r in records:
                    fh.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "chain": chain,
                                         "models": {k: v.get("name") for k, v in models.items()},
                                         "runtime": "pool", **r}) + "\n")
        return records

    def check(self, goals: dict) -> dict:
        """Can the teacher build each goal's target? {name: {buildable, detail}} (as taiga_build_part --check)."""
        def one(n):
            worker = self._free.get()
            try:
                o = SimpleNamespace(teacher=True, check=True, quiet=True, canon_top=False, step_timeout=self.step_timeout)
                r = B.build_goal(worker, None, n, goals[n], o, Path("."), log=lambda _m: None)
            finally:
                self._free.put(worker)
            if r["result"] == "OK":
                return n, {"buildable": True, "detail": f"{r['budget']} steps budget, target volume {r['volume']:.0f} mm3, "
                                                        f"bbox {tuple(round(x, 1) for x in r['bbox'])}"}
            return n, {"buildable": False, "detail": r.get("why", r["result"])}
        return dict(self._exec.map(one, list(goals)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goals", required=True)
    ap.add_argument("--registry")
    ap.add_argument("--out", default="parts")
    ap.add_argument("--names", nargs="*")
    ap.add_argument("--chain", default=CHAIN_DEFAULT)
    ap.add_argument("--min-iou", type=float, default=0.999)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--attempt-timeout", type=float, default=7200)
    ap.add_argument("--no-step", action="store_true")
    ap.add_argument("--repeat", type=int, default=1, help="build the goals this many times in this process")
    a = ap.parse_args()
    chain = parse_chain(a.chain)
    models = load_registry(Path(a.registry).expanduser()) if a.registry else {}
    goals = json.loads(Path(a.goals).read_text())
    pool = BuildPool(a.workers)
    try:
        print(f"pool: {a.workers} workers; warm-up (FreeCAD workers, models) {pool.warm(models):.1f} s", flush=True)
        for k in range(a.repeat):
            out = Path(a.out) / f"round{k + 1}" if a.repeat > 1 else Path(a.out)
            t0 = time.perf_counter()
            recs = pool.run(goals, chain, models, out, a.min_iou, not a.no_step, a.attempt_timeout, a.names,
                            echo=lambda s: print(s, flush=True))
            ok = sum(r["accepted"] is not None for r in recs)
            print(f"round {k + 1}: {ok}/{len(recs)} built in {time.perf_counter() - t0:.1f} s -> {out}", flush=True)
    finally:
        pool.close()


if __name__ == "__main__":
    main()
