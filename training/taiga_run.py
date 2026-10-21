#!/usr/bin/env python3
"""taiga_run.py — run an upstream freecad_s1 module or script with FreeCAD worker-crash
recovery and, for training, optional deterministic PyTorch.
Version: 2026.10.21.1

    python taiga_run.py freecad_s1.evaluate --ckpt ... [args]      # a module (like python -m)
    python taiga_run.py scripts/calibrate.py --model ... [args]    # a script

Why: OpenCASCADE can crash natively (e.g. a segfault in the boolean operation used to
score an episode). FreeCAD turns the crash into a silent exit, and upstream's lockstep
VecEnv then aborts the whole run. Here a dead worker is replaced by a fresh one and only
the affected episode fails:
  - crash while scoring  -> IoU 0, no match (the episode counts as a failure)
  - crash during a step  -> the episode ends as "unrecoverable"
  - crash during a reset -> the reset is retried once on a fresh worker
  - a worker already gone when a request is sent (BrokenPipeError; v5 seed 12's perturbed
    evaluation, 2026-10-21) -> handled the same way, by phase
A worker that hangs (OpenCASCADE stuck in one operation: a perturbed-DAgger evaluation on the
Quadro sat 16 hours on one step at 770 % CPU, 2026-10-13) is killed after S1_STEP_TIMEOUT
seconds (default 1800; 0: never) on one request, and then handled as a crash.
Every crash is reported on stderr and, if S1_CRASH_LOG is set, appended to that file as
one JSON line (phase, episode spec, number of steps sent), so crashes stay visible.

DETERMINISTIC=1 (or "warn") enables deterministic PyTorch kernels for freecad_s1.train_sft.
"""
from __future__ import annotations

import json
import os
import runpy
import sys
import threading
import time
import weakref


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

    def _send(env, req) -> bool:
        """Send a request; False if the worker is already gone (it died after its last answer,
        e.g. killed by the watchdog): the reply loop then handles it as a crash."""
        try:
            env.send(req)
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _recv(env, dead: bool):
        if dead:
            raise client.WorkerError("FreeCAD worker exited before the request")
        return env.recv()

    def reset(self, specs):
        self._s1_specs = list(specs) + [None] * (len(self.envs) - len(specs))
        self._s1_steps = [0] * len(self.envs)
        dead = set()
        for i, (env, spec) in enumerate(zip(self.envs, specs)):
            if not _send(env, {"op": "reset", **spec}):
                dead.add(i)
        eps = []
        for i, spec in enumerate(specs):
            try:
                r = _recv(self.envs[i], i in dead)
            except client.WorkerError:
                record(self, i, "reset")
                fresh(self, i)
                r = self.envs[i].call({"op": "reset", **spec})  # a second crash propagates
            eps.append(episode_from(r, spec))
        return eps

    def step(self, idx, actions, reward=False):
        dead = set()
        for i, a in zip(idx, actions):
            if not _send(self.envs[i], {"op": "step", "action": a, "reward": reward}):
                dead.add(i)
            if hasattr(self, "_s1_steps"):
                self._s1_steps[i] += 1
        out = []
        for i in idx:
            try:
                out.append(_recv(self.envs[i], i in dead))
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
        dead = set()
        for i in idx:
            if not _send(self.envs[i], {"op": "score"}):
                dead.add(i)
        out = []
        for i in idx:
            try:
                out.append(_recv(self.envs[i], i in dead))
            except client.WorkerError:
                record(self, i, "score")
                fresh(self, i)
                out.append({"iou": 0.0, "match": False, "worker_crash": True})
        return out

    client.VecEnv.reset = reset
    client.VecEnv.step = step
    client.VecEnv.score = score


def install_watchdog() -> None:
    """Kill a FreeCAD worker that has not answered one request within S1_STEP_TIMEOUT seconds;
    the reader then sees the worker exit and the crash recovery above takes over."""
    limit = float(os.environ.get("S1_STEP_TIMEOUT", "1800"))
    if limit <= 0:
        return
    from freecad_s1.runtime import client

    pending = weakref.WeakKeyDictionary()  # env -> time its oldest unanswered request was sent
    lock = threading.Lock()
    send0, recv0 = client.FreeCADEnv.send, client.FreeCADEnv.recv

    def send(self, req):
        with lock:
            pending.setdefault(self, time.monotonic())
        return send0(self, req)

    def recv(self):
        try:
            return recv0(self)
        finally:
            with lock:
                pending.pop(self, None)

    def watch():
        while True:
            time.sleep(min(30.0, limit / 4))
            now = time.monotonic()
            with lock:
                stuck = [env for env, t in pending.items() if now - t > limit]
            for env in stuck:
                if env.proc.poll() is None:
                    print(f"[taiga_run] FreeCAD worker pid {env.proc.pid} gave no answer in {limit:.0f} s: killed",
                          file=sys.stderr, flush=True)
                    env.proc.kill()
                with lock:
                    pending.pop(env, None)

    client.FreeCADEnv.send, client.FreeCADEnv.recv = send, recv
    threading.Thread(target=watch, daemon=True, name="s1-watchdog").start()


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
    install_watchdog()
    maybe_deterministic(target)
    sys.argv = [target] + args
    if target.endswith(".py"):
        runpy.run_path(target, run_name="__main__")
    else:
        runpy.run_module(target, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
