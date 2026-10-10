#!/usr/bin/env python3
"""eval_s80.py — score Taiga-built S 80 parts against the reference CAD, part by part.
Version: 2026.10.10.1

Runs in FreeCAD's Python (needs numpy):
    source ~/taiga-expanded/freecad.env
    "$FREECAD_PYTHON" eval_s80.py --ref ~/taiga/taiga-s1/parts/pump_s80 \\
        --built teacher=~/taiga-expanded/s80/teacher seed2=~/taiga-expanded/s80/seed2 ... \\
        --teacher ~/taiga-expanded/s80/teacher --out ~/taiga-expanded/s80/eval_s80.json

--ref      folder with the reference STEP files (pump_s80.py --out ...)
--built    one or more build folders (taiga_build_part.py --out ...), each LABEL=DIR or DIR
           (label = folder name); a part counts as missing if its .FCStd/.step is not there
--teacher  folder with the teacher builds (taiga_build_part.py --teacher): every other build
           is also scored against it, goal by goal (did the model do what the teacher does?)
--overlay  also write <out dir>/s80_overlay_<label>.FCStd: reference (grey, transparent) and
           the build (orange) in pump coordinates

Scores: volumetric IoU on a voxel grid (voxel_iou.py; OCC booleans of two nearly identical
solids can fail silently), plus the built and reference volumes. "coverage" = share of the
reference volume that the build fills, "excess" = built volume outside the reference, as a
share of the reference volume. If a mesh has holes (OCC sometimes leaves a face untriangulated)
or the grid can't get fine enough, the IoU comes from 20,000 random points classified against
the solids instead ("sampled" in the JSON). With several model builds (seeds), each part also gets the
mean and standard deviation over them (the teacher is not included).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import FreeCAD as App
import Part

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "build"))
try:
    import numpy as np  # noqa: F401
except ImportError:
    sys.exit("error: numpy is missing in FreeCAD's Python (Ubuntu: sudo apt install python3-numpy)")
from taiga_assemble import add_gui_document, load_shape, placement  # noqa: E402
from voxel_iou import voxel_iou_checked  # noqa: E402


def triangles(shape, deflection: float = 0.05):
    """Fine triangle mesh of a shape as an (N, 3, 3) array."""
    try:
        import MeshPart
        mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=deflection, AngularDeflection=0.05, Relative=False)
        verts, faces = mesh.Topology
        pts = np.array([(v.x, v.y, v.z) for v in verts], float)
        return pts[np.array(faces, dtype=np.int64)]
    except Exception:  # noqa: BLE001  (no MeshPart: FreeCAD's own tessellation)
        verts, faces = shape.tessellate(deflection)
        pts = np.array([(v.x, v.y, v.z) for v in verts], float)
        return pts[np.array(faces, dtype=np.int64)]


def placed(folder: Path, build: dict):
    shape, _ = load_shape(folder, build["goal"])
    if shape is None:
        return None
    shape = shape.copy()
    shape.Placement = placement(build, 0.0).multiply(shape.Placement)
    return shape


def assemble(folder: Path, builds: list[dict]):
    shapes = [placed(folder, b) for b in builds]
    if any(s is None for s in shapes):
        return None
    return shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)


def sampled_iou(a, b, n: int = 20000, seed: int = 0) -> dict:
    """IoU from random points classified against the solids themselves (no mesh): the fallback
    when a mesh is not watertight (OCC sometimes leaves a face without triangles), which breaks
    the voxel fill. Identical solids still give exactly 1."""
    import random

    bb = a.BoundBox
    bb.add(b.BoundBox)
    rng = random.Random(seed)
    na = nb = ni = 0
    for _ in range(n):
        p = App.Vector(rng.uniform(bb.XMin, bb.XMax), rng.uniform(bb.YMin, bb.YMax), rng.uniform(bb.ZMin, bb.ZMax))
        ia, ib = a.isInside(p, 1e-6, True), b.isInside(p, 1e-6, True)
        na, nb, ni = na + ia, nb + ib, ni + (ia and ib)
    box = bb.XLength * bb.YLength * bb.ZLength / n
    union = na + nb - ni
    return {"iou": ni / union if union else 0.0, "vol_a": na * box, "vol_b": nb * box, "vol_inter": ni * box,
            "h": 0.0, "converged": True, "sampled": True}


def score(shape, ref_tris, ref_volume: float, res: int, ref_shape=None) -> dict:
    if shape is None:
        return {"iou": 0.0, "missing": True}
    if abs(shape.Volume) < 1e-6 * ref_volume:  # not a solid (open shell, faces): its triangles can still
        return {"iou": 0.0, "volume_cm3": 0.0, "coverage": 0.0, "excess": 0.0, "h_mm": 0.0, "no_volume": True}
    v = voxel_iou_checked(triangles(shape), ref_tris, shape.Volume, ref_volume, res)
    if not v["converged"] and ref_shape is not None:  # a mesh with holes, or a grid too coarse: classify points
        v = sampled_iou(shape, ref_shape)
    ref_v = v["vol_b"] or ref_volume
    return {"iou": round(v["iou"], 5), "volume_cm3": round(shape.Volume / 1000, 2),
            "coverage": round(v["vol_inter"] / ref_v, 5), "excess": round((v["vol_a"] - v["vol_inter"]) / ref_v, 5),
            "h_mm": round(v["h"], 3), **({} if v["converged"] else {"coarse": True}),
            **({"sampled": True} if v.get("sampled") else {})}


def parse_built(items: list[str]) -> list[tuple[str, Path]]:
    out = []
    for it in items:
        label, _, path = it.rpartition("=")
        p = Path(path).expanduser().resolve()
        out.append((label or p.name, p))
    return out


def mean_sd(xs):
    if not xs:
        return None, None
    m = sum(xs) / len(xs)
    return m, (math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", default=str(HERE / "s80_eval.json"))
    ap.add_argument("--ref", required=True, help="folder with the reference STEP files")
    ap.add_argument("--built", nargs="+", required=True, help="build folders, LABEL=DIR or DIR")
    ap.add_argument("--teacher", help="teacher build folder: also score each build against it, goal by goal")
    ap.add_argument("--out", help="JSON results (default: eval_s80.json next to the first build folder)")
    ap.add_argument("--res", type=int, default=384, help="voxels along the longest side of each part (default 384)")
    ap.add_argument("--overlay", action="store_true", help="write an overlay .FCStd per build folder")
    args = ap.parse_args()

    spec = json.loads(Path(args.spec).read_text())
    ref_dir = Path(args.ref).expanduser().resolve()
    built = parse_built(args.built)
    teacher = Path(args.teacher).expanduser().resolve() if args.teacher else None
    out = Path(args.out).expanduser().resolve() if args.out else built[0][1].parent / "eval_s80.json"

    rows = []  # (part, alternative goal or None, builds)
    for entry in spec["parts"]:
        rows.append((entry["part"], None, entry["builds"]))
        for alt in entry.get("alternatives", []):
            rows.append((entry["part"], alt["goal"], [alt]))

    results = {"spec": str(Path(args.spec).resolve()), "ref": str(ref_dir), "res": args.res,
               "builds": {label: str(p) for label, p in built}, "parts": {}, "vs_teacher": {}}
    refs = {}
    for part, alt, builds in rows:
        if part not in refs:
            step = ref_dir / f"{part}.step"
            if not step.is_file():
                sys.exit(f"error: reference {step} not found (run pump_s80.py --out {ref_dir})")
            shape = Part.read(str(step))
            try:
                ok = shape.isValid() and shape.Volume > 0
            except Exception:  # noqa: BLE001  (FreeCAD raises on some broken shapes)
                ok = False
            refs[part] = (shape, triangles(shape)) if ok else None
            if not ok:
                print(f"{part:36s} reference invalid: skipped", flush=True)
                results.setdefault("invalid_refs", []).append(part)
        if refs[part] is None:
            continue
        ref, ref_tris = refs[part]
        key = part if alt is None else f"{part} [{alt}]"
        results["parts"][key] = {"ref_volume_cm3": round(ref.Volume / 1000, 2)}
        for label, folder in built:
            r = score(assemble(folder, builds), ref_tris, ref.Volume, args.res, ref)
            results["parts"][key][label] = r
            print(f"{key:36s} {label:12s} " + ("missing" if r.get("missing") else
                  f"IoU {r['iou']:.4f}  coverage {r['coverage']:.4f}  excess {r['excess']:.4f}  "
                  f"vol {r['volume_cm3']:.1f} / {ref.Volume / 1000:.1f} cm3"), flush=True)

    # each goal against the teacher's build (goal frame, no placement)
    if teacher is not None and any(p != teacher for _, p in built):
        goals = sorted({b["goal"] for _, _, bs in rows for b in bs})
        for goal in goals:
            t_shape, _ = load_shape(teacher, goal)
            if t_shape is None:
                continue
            t_tris = triangles(t_shape)
            results["vs_teacher"][goal] = {}
            for label, folder in built:
                if folder == teacher:
                    continue
                shape, _ = load_shape(folder, goal)
                r = score(shape, t_tris, t_shape.Volume, args.res, t_shape)
                results["vs_teacher"][goal][label] = r
                print(f"{goal:36s} {label:12s} vs teacher " + ("missing" if r.get("missing") else f"IoU {r['iou']:.4f}"),
                      flush=True)

    # summary: mean and sd over the model builds (everything except the teacher folder)
    models = [label for label, p in built if p != teacher]
    w = max(30, *(len(k) + 2 for k in results["parts"]))
    labels = [label for label, _ in built]
    print("\nIoU with the reference")
    print(f"{'part':{w}s}" + "".join(f"{lb:>12s}" for lb in labels) + ("   mean ± sd (models)" if len(models) > 1 else ""))
    for key, res in results["parts"].items():
        vals = [res[lb]["iou"] for lb in labels]
        line = f"{key:{w}s}" + "".join(f"{v:12.4f}" for v in vals)
        if len(models) > 1:
            m, s = mean_sd([res[lb]["iou"] for lb in models])
            res["models_mean"], res["models_sd"] = round(m, 5), round(s, 5)
            line += f"   {m:.4f} ± {s:.4f}"
        print(line)
    # whole pump: volume-weighted (sum of intersections / sum of unions) over the main parts
    main_keys = [k for k in results["parts"] if "[" not in k]
    print(f"{'whole pump (volume-weighted)':{w}s}", end="")
    pump = {}
    for lb in labels:
        inter = union = 0.0
        for k in main_keys:
            r, rv = results["parts"][k][lb], results["parts"][k]["ref_volume_cm3"]
            if r.get("missing"):
                union += rv
                continue
            i = r["coverage"] * rv
            inter += i
            union += rv + r["excess"] * rv
        pump[lb] = inter / union if union else 0.0
        print(f"{pump[lb]:12.4f}", end="")
    results["whole_pump"] = pump
    print()
    if results["vs_teacher"]:
        print("\nIoU with the teacher's build, goal by goal")
        gw = max(len(g) for g in results["vs_teacher"]) + 2
        print(f"{'goal':{gw}s}" + "".join(f"{lb:>12s}" for lb in models) + ("   mean ± sd" if len(models) > 1 else ""))
        for goal, res in results["vs_teacher"].items():
            vals = [res.get(lb, {}).get("iou", 0.0) for lb in models]
            line = f"{goal:{gw}s}" + "".join(f"{v:12.4f}" for v in vals)
            if len(models) > 1:
                m, s = mean_sd(vals)
                line += f"   {m:.4f} ± {s:.4f}"
            print(line)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1) + "\n")
    print(f"\nwrote {out}")

    if args.overlay:
        for label, folder in built:
            doc = App.newDocument(re.sub(r"\W", "_", f"s80_overlay_{label}"))
            styles = {}
            for entry in spec["parts"]:
                if refs.get(entry["part"]) is None:
                    continue
                ref = doc.addObject("Part::Feature", f"ref_{entry['part']}")
                ref.Shape = refs[entry["part"]][0]
                styles[ref.Name] = ([0.6, 0.6, 0.65], 70)
                shape = assemble(folder, entry["builds"])
                if shape is not None:
                    obj = doc.addObject("Part::Feature", f"taiga_{entry['part']}")
                    obj.Shape = shape
                    styles[obj.Name] = ([0.95, 0.55, 0.15], 0)
            doc.recompute()
            path = out.parent / f"s80_overlay_{label}.FCStd"
            doc.saveAs(str(path))
            add_gui_document(path, styles)
            App.closeDocument(doc.Name)
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
