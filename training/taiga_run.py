#!/usr/bin/env python3
"""taiga_run.py — run an upstream freecad_s1 module or script with FreeCAD worker-crash
recovery and, for training, optional deterministic PyTorch.
Version: 2026.10.04.1

    python taiga_run.py freecad_s1.evaluate --ckpt ... [args]      # a module (like python -m)
    python taiga_run.py scripts/calibrate.py --model ... [args]    # a script

Why: OpenCASCADE can crash natively (e.g. a segfault in the boolean operation used to
score an episode). FreeCAD turns the crash into a silent exit, and upstream's lockstep
VecEnv then aborts the whole run. Here a dead worker is replaced by a fresh one and only
the affected episode fails:
  - crash while scoring  -> IoU 0, no match (the episode counts as a failure)
  - crash during a step  -> the episode ends as "unrecoverable"
  - crash during a reset -> the reset is retried once on a fresh worker
Every crash is reported on stderr and, if S1_CRASH_LOG is set, appended to that file as
one JSON line (phase, episode spec, number of steps sent), so crashes stay visible.

DETERMINISTIC=1 (or "warn") enables deterministic PyTorch kernels for freecad_s1.train_sft.
"""
from __future__ import annotations

import json
import os
import runpy
import sys
import time


def install_crash_recovery() -> None:
    from freecad_s1.runtime import client

    log_path = os.environ.get("S1_CRASH_LOG")

    def record(vec, i: int, phase: str) -> None:
        env = vec.envs[i]
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "phase": phase, "worker": i,
                 "exit_code": env.proc.poll(), "spec": (getattr(vec, "_s1_specs", None) or [None] * (i + 1))[i],
                 "steps_sent": (getattr(vec, "_s1_steps", None) or [0] * (i + 1))[i]}
        print(f"[taiga_run] FreeCAD worker {i} died during {phase} (exit {entry['exit_code']}); "
              f"episode {entry['spec']}, {entry['steps_sent']} steps — replaced, episode counted as failed",
              file=sys.stderr, flush=True)
        if log_path:
            with open(log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")

    def fresh(vec, i: int) -> None:
        try:
            vec.envs[i].close()
        except Exception:
            pass
        vec.envs[i] = client.FreeCADEnv()

    def episode_from(r: dict, spec: dict):
        goal = client.Goal.from_json(r["goal"])
        return client.Episode(goal, r["budget"], spec.get("level", goal.level), client.State.from_json(r["state"]),
                              r["actions"], r["expert"], progress=r.get("progress", -1))

    def reset(self, specs):
        self._s1_specs = list(specs) + [None] * (len(self.envs) - len(specs))
        self._s1_steps = [0] * len(self.envs)
        for env, spec in zip(self.envs, specs):
            env.send({"op": "reset", **spec})
        eps = []
        for i, spec in enumerate(specs):
            try:
                r = self.envs[i].recv()
            except client.WorkerError:
                record(self, i, "reset")
                fresh(self, i)
                r = self.envs[i].call({"op": "reset", **spec})  # a second crash propagates
            eps.append(episode_from(r, spec))
        return eps

    def step(self, idx, actions, reward=False):
        for i, a in zip(idx, actions):
            self.envs[i].send({"op": "step", "action": a, "reward": reward})
            if hasattr(self, "_s1_steps"):
                self._s1_steps[i] += 1
        out = []
        for i in idx:
            try:
                out.append(self.envs[i].recv())
            except client.WorkerError:
                record(self, i, "step")
                fresh(self, i)
                spec = (getattr(self, "_s1_specs", None) or [None] * (i + 1))[i]
                if spec is None:
                    raise
                r = self.envs[i].call({"op": "reset", **spec})  # valid state; empty expert ends the episode
                resp = {"info": {"done": False, "error": "FreeCAD worker crashed"}, "state": r["state"],
                        "actions": r["actions"], "expert": [], "progress": -1}
                if reward:
                    resp.update(iou=0.0, delta_iou=0.0)
                out.append(resp)
        return out

    def score(self, idx):
        for i in idx:
            self.envs[i].send({"op": "score"})
        out = []
        for i in idx:
            try:
                out.append(self.envs[i].recv())
            except client.WorkerError:
                record(self, i, "score")
                fresh(self, i)
                out.append({"iou": 0.0, "match": False, "worker_crash": True})
        return out

    client.VecEnv.reset = reset
    client.VecEnv.step = step
    client.VecEnv.score = score


def maybe_deterministic(target: str) -> None:
    mode = os.environ.get("DETERMINISTIC", "0")
    if mode in ("", "0") or not target.endswith("train_sft"):
        return
    import torch

    torch.use_deterministic_algorithms(True, warn_only=(mode == "warn"))
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    target, args = sys.argv[1], sys.argv[2:]
    install_crash_recovery()
    maybe_deterministic(target)
    sys.argv = [target] + args
    if target.endswith(".py"):
        runpy.run_path(target, run_name="__main__")
    else:
        runpy.run_module(target, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
