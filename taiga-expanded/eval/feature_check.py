#!/usr/bin/env python3
"""feature_check.py — which goal intents did a build realize? A feature-level check next to IoU.
Version: 2026.10.15.1

Runs in FreeCAD's Python (needs numpy), like eval_s80.py:
    source ~/taiga-expanded/freecad.env
    "$FREECAD_PYTHON" feature_check.py --goals s80_goals.json --prefixes $OUT/teacher_prefixes \\
        --built seed2=$OUT/seed2 seed12=$OUT/seed12 --out $OUT/features.json

Why: IoU hides missing features. A casing missing everything after the suction port (bolt
holes, pockets, a boss: about 15 intents) still scores IoU 0.97, and a part missing one hole
or one pattern instance barely moves it. This names the missing intents instead.

How: the teacher builds each goal once with `taiga_build_part.py --teacher --prefixes`, which
saves the part after every finished intent (S_1 ... S_N; S_N is the target T). Intent k added
the region S_k - S_(k-1) and removed S_(k-1) - S_k. The part of that region that survives in
the target (added and still in T, or removed and still absent from T) is what intent k
contributes to the final part. A build realizes intent k if it agrees with the target on at
least --min-frac (0.9) of that region. The order in which the build made its features does
not matter, only the final solid. Everything is voxelized on one grid (voxel_iou.voxelize).

Per intent: "ok", "missing" (less than --min-frac), "hidden" (nothing of it survives in the
target, e.g. a boss later cut away) or "small" (fewer than --min-voxels voxels at this
resolution, e.g. a 0.5 mm chamfer). Per build also "excess": material outside the target, as a
share of the target volume, which no intent accounts for.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import FreeCAD as App  # noqa: F401  (FreeCAD's Python)
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "build"))
from eval_s80 import parse_built, triangles  # noqa: E402
from taiga_assemble import load_shape  # noqa: E402
from voxel_iou import voxelize  # noqa: E402


class Grid:
    def __init__(self, boxes, res: int):
        lo = np.array([min(b.XMin for b in boxes), min(b.YMin for b in boxes), min(b.ZMin for b in boxes)])
        hi = np.array([max(b.XMax for b in boxes), max(b.YMax for b in boxes), max(b.ZMax for b in boxes)])
        ext = np.maximum(hi - lo, 1e-6)
        h = float(ext.max()) / res
        self.h = h
        self.n = (np.ceil(ext / h).astype(int) + 2)
        self.lo = lo - h * np.array([0.5123, 0.5371, 0.4863])  # odd offsets: column centres off mesh edges
        self.zs = self.lo[2] + (np.arange(self.n[2]) + 0.5) * h

    def fill(self, shape) -> np.ndarray:
        if shape is None:
            return np.zeros(tuple(self.n), bool)
        return voxelize(triangles(shape), self.lo[0], self.lo[1], self.h, self.h, int(self.n[0]), int(self.n[1]), self.zs)


def intent_status(prefix_vox, target: np.ndarray, model_vox: dict, min_frac: float, min_voxels: int) -> dict:
    """Status of every intent for every build. `prefix_vox`: the voxelized part after intents
    1..N (None where no prefix was saved); `target`: the last one; `model_vox`: label -> build."""
    out = {label: [] for label in model_vox}
    prev = np.zeros_like(target)
    for cur in prefix_vox:
        known = cur is not None
        cur = cur if known else prev
        add = cur & ~prev & target  # added by this intent and still in the target
        rem = prev & ~cur & ~target  # removed by this intent and still absent from the target
        size = int(add.sum() + rem.sum())
        for label, vm in model_vox.items():
            if not known:
                st = "unknown"
            elif size == 0:
                st = "hidden"
            elif size < min_voxels:
                st = "small"
            else:
                agree = int((add & vm).sum() + (rem & ~vm).sum())
                st = "ok" if agree >= min_frac * size else "missing"
            out[label].append(st)
        prev = cur
    return out


def check_goal(goal: dict, prefix_dir: Path, builds: list[tuple[str, Path]], name: str, res: int,
               min_frac: float, min_voxels: int) -> dict:
    n = len(goal["features"])
    prefixes = [load_shape(prefix_dir, f"p{k:03d}")[0] for k in range(1, n + 1)]
    if prefixes[-1] is None:
        return {"error": f"no teacher prefix p{n:03d} in {prefix_dir}"}
    models = {label: load_shape(folder, name)[0] for label, folder in builds}
    boxes = [s.BoundBox for s in prefixes if s is not None] + [m.BoundBox for m in models.values() if m is not None]
    grid = Grid(boxes, res)
    target = grid.fill(prefixes[-1])
    model_vox = {}
    for label, m in models.items():
        try:
            model_vox[label] = grid.fill(m)
        except Exception as exc:  # noqa: BLE001  (a malformed build: everything missing)
            print(f"{name} {label}: could not voxelize ({str(exc)[:60]})", flush=True)
            model_vox[label] = np.zeros_like(target)
    res_goal = {"intents": n, "voxel_mm": round(grid.h, 3), "builds": {lb: {"status": []} for lb in models}}
    vox = (grid.fill(s) if s is not None else None for s in prefixes)  # one prefix in memory at a time
    for label, st in intent_status(vox, target, model_vox, min_frac, min_voxels).items():
        res_goal["builds"][label]["status"] = st
    tv = max(int(target.sum()), 1)
    for label, vm in model_vox.items():
        st = res_goal["builds"][label]["status"]
        measured = [s for s in st if s in ("ok", "missing")]
        missing = [k for k, s in enumerate(st) if s == "missing"]
        res_goal["builds"][label].update(
            realized=f"{sum(s == 'ok' for s in st)}/{len(measured)}",
            missing=[f"{k + 1}:{goal['features'][k]['kind']}" for k in missing],
            first_missing=(missing[0] + 1) if missing else None,
            excess=round(int((vm & ~target).sum()) / tv, 4))
    return res_goal


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goals", required=True, help="goals JSON {name: goal}, as given to taiga_build_part.py")
    ap.add_argument("--prefixes", required=True, help="teacher output folder with <name>.prefixes/ (--teacher --prefixes)")
    ap.add_argument("--built", nargs="+", required=True, help="build folders, LABEL=DIR or DIR")
    ap.add_argument("--names", nargs="*", help="only these goals (default: every goal with prefixes)")
    ap.add_argument("--res", type=int, default=256, help="voxels along the longest side (default 256)")
    ap.add_argument("--min-frac", type=float, default=0.9, help="share of an intent's region that must agree (0.9)")
    ap.add_argument("--min-voxels", type=int, default=30, help="smaller intent regions are reported as 'small' (30)")
    ap.add_argument("--out", help="JSON results (default: features.json in the first build folder's parent)")
    args = ap.parse_args()

    goals = json.loads(Path(args.goals).read_text())
    pre_root = Path(args.prefixes).expanduser().resolve()
    built = parse_built(args.built)
    names = args.names or [g for g in goals if (pre_root / f"{g}.prefixes").is_dir()]
    out = Path(args.out).expanduser() if args.out else built[0][1].parent / "features.json"
    results = {}
    for name in names:
        r = check_goal(goals[name], pre_root / f"{name}.prefixes", built, name, args.res, args.min_frac, args.min_voxels)
        results[name] = r
        if "error" in r:
            print(f"{name:28s} {r['error']}", flush=True)
            continue
        for label, b in r["builds"].items():
            miss = ", ".join(b["missing"][:6]) + (" ..." if len(b["missing"]) > 6 else "")
            print(f"{name:28s} {label:10s} intents {b['realized']:>7s}  excess {b['excess']:.3f}"
                  + (f"  missing {miss}" if b["missing"] else ""), flush=True)
    out.write_text(json.dumps(results, indent=1) + "\n")
    labels = [lb for lb, _ in built]
    print("\nIntents realized (measured intents only), per goal and build")
    print(f"{'goal':28s}" + "".join(f"{lb:>12s}" for lb in labels))
    for name, r in results.items():
        if "error" not in r:
            print(f"{name:28s}" + "".join(f"{r['builds'][lb]['realized']:>12s}" for lb in labels))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
