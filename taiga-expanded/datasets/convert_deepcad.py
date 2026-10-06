#!/usr/bin/env python3
"""convert_deepcad.py — DeepCAD build histories (sketch + extrude) as Taiga-S1 goals.
Version: 2026.10.06.4

Reads DeepCAD's JSON files (data/cad_json/<group>/<id>.json, the Fusion 360 Gallery
reconstruction format) and writes, into --out:

  goals_<subset>.json   {name: goal} for ../../build/taiga_build_part.py
  eval_<subset>.json    spec for ../eval/eval_s80.py: each model is a "part", built by its goal
  refs/<name>.step      (with --refs) the original model, built from the JSON exactly as DeepCAD
                        does (OpenCASCADE prisms and booleans), in the goal's frame
  report_<subset>.json  what was converted, what was left out and why, lengths

Rules (see README.md in this folder):
  frame     the first sketch becomes the XY plane, its extrude starts at z = 0 and goes up
  planes    every other sketch must then be normal to X, Y or Z (else the model is left out)
  outlines  loops of lines, arcs and circles -> Taiga outlines (circles as two arcs)
  extrudes  first: profile_base (outer loop, height); inner loops: pockets through the base.
            later: the exact equivalent on a datum plane, extruded both ways: one side of d
            from plane z0 = d both ways about z0 + d/2. Join/NewBody -> profile_boss (inner
            loops: profile_pocket over the same slab), Cut -> profile_pocket (inner loops:
            profile_boss), Intersect -> left out
  units     DeepCAD stores metres (--units m, default); Fusion 360 Gallery centimetres (cm)

Build order (with --refs): PartDesign keeps one solid after every feature, while DeepCAD may
join pieces that touch only later. The converter replays the goal in OpenCASCADE, reorders
consecutive joins so that every step stays one solid (a union doesn't depend on order), and
leaves the model out if no order works or a cut empties or splits the part.

Inner loops of a join/cut are only exact when the slab holds no earlier material inside the
loop; the --refs + teacher + IoU check (run_deepcad.sh) keeps only goals that match.

Needs numpy; --refs also needs OCP (cadquery-ocp 7.9, e.g. ~/taiga-expanded/ocp-venv):
    python convert_deepcad.py --data ~/deepcad/data/cad_json --split ~/deepcad/data/train_val_test_split.json \\
        --subset test --out ~/deepcad/goals --refs --max-features 40
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

UNITS = {"m": 1000.0, "cm": 10.0, "mm": 1.0}
AXES = "xyz"
TOL = 1e-6


class Skip(Exception):
    """The model can't be expressed (reason in the message)."""


# ----------------------------------------------------------------------------- reading


def _p2(d):
    return np.array([d["x"], d["y"]], float)


def _p3(d):
    return np.array([d["x"], d["y"], d["z"]], float)


def arc_mid(c: dict) -> np.ndarray:
    """Mid point of an Arc3D, as DeepCAD computes it (cadlib/curves.py)."""
    center, radius = _p2(c["center_point"]), c["radius"]
    ref = _p2(c["reference_vector"])
    a = (c["start_angle"] + c["end_angle"]) / 2
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return center + rot @ ref * radius


def loop_curves(loop: dict) -> list[tuple]:
    """('L', start, end) / ('A', start, mid, end) / ('C', center, radius), sketch-local 2D."""
    out = []
    for c in loop["profile_curves"]:
        t = c["type"]
        if t == "Line3D":
            s, e = _p2(c["start_point"]), _p2(c["end_point"])
            if np.linalg.norm(e - s) > TOL:
                out.append(("L", s, e))
        elif t == "Arc3D":
            out.append(("A", _p2(c["start_point"]), arc_mid(c), _p2(c["end_point"])))
        elif t == "Circle3D":
            out.append(("C", _p2(c["center_point"]), float(c["radius"])))
        else:
            raise Skip(f"curve type {t}")
    return out


def chain(curves: list[tuple]) -> list[tuple]:
    """Order and orient the curves of a loop so each starts where the previous ended."""
    if len(curves) == 1 and curves[0][0] == "C":
        return curves
    if any(c[0] == "C" for c in curves):
        raise Skip("circle inside a multi-curve loop")
    scale = max(np.abs(np.concatenate([c[1:] for c in curves])).max(), 1e-9)
    tol = 1e-5 * scale + 1e-9
    rest = list(curves)
    out = [rest.pop(0)]
    while rest:
        end = out[-1][-1]
        for i, c in enumerate(rest):
            if np.linalg.norm(c[1] - end) < tol:
                out.append(rest.pop(i))
                break
            if np.linalg.norm(c[-1] - end) < tol:
                c = rest.pop(i)
                out.append(("L", c[2], c[1]) if c[0] == "L" else ("A", c[3], c[2], c[1]))
                break
        else:
            raise Skip("loop does not close")
    if np.linalg.norm(out[-1][-1] - out[0][1]) > tol:
        raise Skip("loop does not close")
    return out


def read_model(path: Path) -> list[dict]:
    """Extrudes in build order, one per profile: {op, extent: (a, b) along the normal,
    frame: (origin, x, y, n), loops: [[curves], ...] outer first}."""
    data = json.loads(path.read_text())
    ents = data["entities"]
    seq = [it["entity"] for it in data["sequence"] if it["type"] == "ExtrudeFeature"]
    if not seq:
        raise Skip("no extrude")
    out = []
    for k, eid in enumerate(seq):
        ex = ents[eid]
        if ex.get("start_extent", {}).get("type", "ProfilePlaneStartDefinition") != "ProfilePlaneStartDefinition":
            raise Skip("extrude starts at an offset")
        op = ex["operation"].replace("FeatureOperation", "")
        if op == "Intersect":
            raise Skip("intersect")
        e1 = ex["extent_one"]["distance"]["value"]
        typ = ex["extent_type"]
        if typ == "SymmetricFeatureExtentType":  # DeepCAD: extent_one each way
            a, b = -abs(e1), abs(e1)
        else:
            lo, hi = min(0.0, e1), max(0.0, e1)
            if typ == "TwoSidesFeatureExtentType":  # second prism along -normal x extent_two
                e2 = -ex["extent_two"]["distance"]["value"]
                lo, hi = min(lo, e2), max(hi, e2)
            a, b = lo, hi
        if b - a < TOL:
            raise Skip("zero-length extrude")
        for j, pr in enumerate(ex["profiles"]):
            sk = ents[pr["sketch"]]
            tr = sk["transform"]
            frame = (_p3(tr["origin"]), _p3(tr["x_axis"]), _p3(tr["y_axis"]), _p3(tr["z_axis"]))
            loops = sk["profiles"][pr["profile"]]["loops"]
            loops = sorted(loops, key=lambda lp: not lp.get("is_outer", False))  # outer first
            if not loops or not loops[0].get("is_outer", True):
                raise Skip("profile without an outer loop")
            this_op = "NewBody" if (k == 0 and j == 0) else ("Join" if op == "NewBody" else op)
            out.append({"op": this_op, "extent": (a, b), "frame": frame,
                        "loops": [chain(loop_curves(lp)) for lp in loops]})
    if out[0]["op"] != "NewBody":
        raise Skip("does not start with a new body")
    return out


# ----------------------------------------------------------------------------- goal frame


def goal_frame(first: dict, unit: float):
    """Rotation R (rows) and translation t: goal = R @ (unit * world) + t. The first sketch's
    x, y, normal become X, Y, Z, and its extrude interval [a, b] becomes z in [0, b - a]."""
    o, x, y, n = first["frame"]
    n = n / np.linalg.norm(n)
    x = x - n * (x @ n)
    x = x / np.linalg.norm(x)
    R = np.stack([x, np.cross(n, x), n])
    a = first["extent"][0] * unit
    t = -R @ (o * unit) + np.array([0.0, 0.0, -a])
    return R, t


def to_goal(p2: np.ndarray, frame, R, t, unit) -> np.ndarray:
    o, x, y, _ = frame
    return R @ ((o + p2[0] * x + p2[1] * y) * unit) + t


def r3(v: float) -> float:
    return round(float(v), 4) + 0.0


# ----------------------------------------------------------------------------- outlines


def outline(curves, frame, R, t, unit, k: int) -> dict:
    """Taiga outline on the plane normal to axis k (in-plane axes in x, y, z order)."""
    ua, va = [i for i in range(3) if i != k]

    def uv(p2):
        g = to_goal(p2, frame, R, t, unit)
        return [r3(g[ua]), r3(g[va])]

    if curves[0][0] == "C":
        _, c, rad = curves[0]
        ex = c + np.array([rad, 0.0])  # a point on the circle; centre and radius in the goal plane
        cu, cv = uv(c)
        pu, pv = uv(ex)
        r = math.hypot(pu - cu, pv - cv)
        return {"start": [r3(cu + r), r3(cv)],
                "segs": [["A", r3(cu), r3(cv + r), r3(cu - r), r3(cv)], ["A", r3(cu), r3(cv - r), r3(cu + r), r3(cv)]]}
    start = uv(curves[0][1])
    segs = []
    for c in curves:
        if c[0] == "L":
            segs.append(["L", *uv(c[2])])
        else:
            segs.append(["A", *uv(c[2]), *uv(c[3])])
    segs[-1][-2:] = start  # close exactly
    return {"start": start, "segs": segs}


def bbox(o: dict):
    pts = [o["start"]]
    for s in o["segs"]:
        pts += [s[1:3], s[3:5]] if s[0] == "A" else [s[1:3]]
    us, vs = [p[0] for p in pts], [p[1] for p in pts]
    return (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2, max(us) - min(us), max(vs) - min(vs)


def feature(kind: str, o: dict, k: int, off: float, size: float) -> dict:
    """Profile feature on the datum plane normal to axis k at `off`, `size` both ways."""
    cu, cv, w, d = bbox(o)
    ua, va = [i for i in range(3) if i != k]
    c = [0.0, 0.0, 0.0]
    c[ua], c[va], c[k] = cu, cv, off
    p = {"outline": o, "w": r3(w), "d": r3(d), "nx": 0.0, "ny": 0.0, "nz": 0.0,
         "x": r3(c[0]), "y": r3(c[1]), "z": r3(c[2]), "off": r3(off)}
    p["n" + AXES[k]] = 1.0
    p["h" if kind == "profile_boss" else "depth"] = r3(size)
    return {"kind": kind, "params": p}


def convert(extrudes: list[dict], unit: float) -> tuple[list[tuple[str, list[dict]]], tuple]:
    """Feature groups in build order, one per extrude profile: ("base" | "join" | "cut", features)
    (the outer loop's feature, then its inner loops), and the goal frame (R, t)."""
    R, t = goal_frame(extrudes[0], unit)
    groups = []
    for i, ex in enumerate(extrudes):
        o, x, y, n = ex["frame"]
        ng = R @ (n / np.linalg.norm(n))
        k = int(np.argmax(np.abs(ng)))
        if abs(abs(ng[k]) - 1) > 1e-6:
            raise Skip("sketch plane not normal to X, Y or Z")
        s = 1.0 if ng[k] > 0 else -1.0
        c = to_goal(np.zeros(2), ex["frame"], R, t, unit)[k]  # plane coordinate along axis k
        a, b = ex["extent"][0] * unit, ex["extent"][1] * unit
        lo, hi = (c + a, c + b) if s > 0 else (c - b, c - a)
        mid, size = (lo + hi) / 2, hi - lo
        outer, inner = ex["loops"][0], ex["loops"][1:]
        if i == 0:  # base: outline on XY at z = 0, height size (the frame puts it at z 0..size)
            ob = outline(outer, ex["frame"], R, t, unit, 2)
            cu, cv, w, d = bbox(ob)
            feats = [{"kind": "profile_base", "params": {"outline": ob, "w": r3(w), "d": r3(d),
                                                         "x": r3(cu), "y": r3(cv), "h": r3(size)}}]
            feats += [feature("profile_pocket", outline(lp, ex["frame"], R, t, unit, 2), 2, mid, size) for lp in inner]
            groups.append(("base", feats))
            continue
        join = ex["op"] in ("Join", "NewBody")
        main, other = ("profile_boss", "profile_pocket") if join else ("profile_pocket", "profile_boss")
        feats = [feature(main, outline(outer, ex["frame"], R, t, unit, k), k, mid, size)]
        feats += [feature(other, outline(lp, ex["frame"], R, t, unit, k), k, mid, size) for lp in inner]
        groups.append(("join" if join else "cut", feats))
    return groups, (R, t)


def make_goal(groups, max_features: int) -> dict:
    feats = [f for _, fs in groups for f in fs]
    if len(feats) > max_features:
        raise Skip(f"more than {max_features} features")
    return {"features": feats, "level": 3}


# ----------------------------------------------------------------------------- build order (OCP)


def feature_solid(f: dict):
    """The prism a goal feature adds or removes (goal frame)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Pnt, gp_Vec

    p = f["params"]
    if f["kind"] == "profile_base":
        k, lo, hi = 2, 0.0, p["h"]
    else:
        k = [p.get("nx", 0), p.get("ny", 0), p.get("nz", 0)].index(1.0)
        size = p.get("h") if f["kind"] == "profile_boss" else p["depth"]
        lo, hi = p["off"] - size / 2, p["off"] + size / 2
    ua, va = [i for i in range(3) if i != k]

    def P(u, v):
        q = [0.0, 0.0, 0.0]
        q[ua], q[va], q[k] = u, v, lo
        return gp_Pnt(*q)

    o = f["params"]["outline"]
    mw, cur = BRepBuilderAPI_MakeWire(), o["start"]
    for seg in o["segs"]:
        if seg[0] == "L":
            mw.Add(BRepBuilderAPI_MakeEdge(P(*cur), P(*seg[1:3])).Edge())
            cur = seg[1:3]
        else:
            mw.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(P(*cur), P(*seg[1:3]), P(*seg[3:5])).Value()).Edge())
            cur = seg[3:5]
    v = [0.0, 0.0, 0.0]
    v[k] = hi - lo
    return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(mw.Wire(), True).Face(), gp_Vec(*v)).Shape()


def one_solid(shape) -> bool:
    """PartDesign's rule after every feature (as the runtime checks it): one valid solid with volume."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    if shape is None or shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        return False
    e, n = TopExp_Explorer(shape, TopAbs_SOLID), 0
    while e.More():
        n += 1
        e.Next()
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return n == 1 and g.Mass() > 1e-6


def apply_group(state, group):
    """`state` after the group's features, or None as soon as one leaves more or less than one solid."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

    for f in group[1]:
        tool = feature_solid(f)
        if state is None:
            state = tool
        elif f["kind"] == "profile_pocket":
            state = BRepAlgoAPI_Cut(state, tool).Shape()
        else:
            state = BRepAlgoAPI_Fuse(state, tool).Shape()
        if not one_solid(state):
            return None
    return state


def order_for_partdesign(groups):
    """Groups in an order PartDesign can build: consecutive joins reordered so every step is one
    solid (a union is the same in any order); cuts stay where they are."""
    state = apply_group(None, groups[0])
    if state is None:
        raise Skip("base is not one solid")
    out, rest = [groups[0]], list(groups[1:])
    while rest:
        if rest[0][0] == "cut":
            g = rest.pop(0)
            state = apply_group(state, g)
            if state is None:
                raise Skip("a cut empties or splits the part")
            out.append(g)
            continue
        run = []
        while rest and rest[0][0] == "join":
            run.append(rest.pop(0))
        while run:
            for j, g in enumerate(run):
                nxt = apply_group(state, g)
                if nxt is not None:
                    state = nxt
                    out.append(run.pop(j))
                    break
            else:
                raise Skip("a join stays apart from the part (no build order keeps one solid)")
    return out


# ----------------------------------------------------------------------------- reference solid (OCP)


def reference_shape(extrudes: list[dict], R, t, unit: float):
    """The original model, built like DeepCAD's create_CAD (prisms of profile faces, fused /
    cut in order), in the goal frame."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire,
                                    BRepBuilderAPI_Transform)
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    def P(p2, frame):
        return gp_Pnt(*to_goal(p2, frame, R, t, unit))

    body = None
    for ex in extrudes:
        frame = ex["frame"]
        n = R @ (frame[3] / np.linalg.norm(frame[3]))
        wires = []
        for curves in ex["loops"]:
            mw = BRepBuilderAPI_MakeWire()
            for c in curves:
                if c[0] == "L":
                    mw.Add(BRepBuilderAPI_MakeEdge(P(c[1], frame), P(c[2], frame)).Edge())
                elif c[0] == "A":
                    mw.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(P(c[1], frame), P(c[2], frame), P(c[3], frame)).Value()).Edge())
                else:
                    circ = gp_Circ(gp_Ax2(P(c[1], frame), gp_Dir(*n)), c[2] * unit)
                    mw.Add(BRepBuilderAPI_MakeEdge(circ).Edge())
            wires.append(mw.Wire())
        a, b = ex["extent"][0] * unit, ex["extent"][1] * unit
        tr = gp_Trsf()
        tr.SetTranslation(gp_Vec(*(n * a)))

        def prism_of(wire):
            face = BRepBuilderAPI_Transform(BRepBuilderAPI_MakeFace(wire, True).Face(), tr, True).Shape()
            return BRepPrimAPI_MakePrism(face, gp_Vec(*(n * (b - a)))).Shape()

        prism = prism_of(wires[0])  # the profile's region: outer loop minus its inner loops
        for w in wires[1:]:
            prism = BRepAlgoAPI_Cut(prism, prism_of(w)).Shape()
        if body is None or ex["op"] in ("NewBody", "Join"):
            body = prism if body is None else BRepAlgoAPI_Fuse(body, prism).Shape()
        else:
            body = BRepAlgoAPI_Cut(body, prism).Shape()
    return body


def check_reference(shape) -> None:
    """The original must be one valid solid: Taiga builds one PartDesign body."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    if not BRepCheck_Analyzer(shape).IsValid():
        raise Skip("original model invalid")
    e, n = TopExp_Explorer(shape, TopAbs_SOLID), 0
    while e.More():
        n += 1
        e.Next()
    if n != 1:
        raise Skip("original model has several solids" if n > 1 else "original model has no solid")
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    if g.Mass() <= 0:
        raise Skip("original model has no volume")


class quiet_fds:
    """Silence C-level stdout/stderr: OpenCASCADE's STEP writer prints coloured statistics
    (terminal escape codes) for every file, which floods or garbles the terminal."""

    def __enter__(self):
        import os

        sys.stdout.flush()
        sys.stderr.flush()
        self.null = os.open(os.devnull, os.O_WRONLY)
        self.saved = [os.dup(1), os.dup(2)]
        os.dup2(self.null, 1)
        os.dup2(self.null, 2)
        return self

    def __exit__(self, *exc):
        import os

        os.dup2(self.saved[0], 1)
        os.dup2(self.saved[1], 2)
        for fd in (*self.saved, self.null):
            os.close(fd)
        return False


def write_step(shape, path: Path) -> None:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    with quiet_fds():
        w = STEPControl_Writer()
        w.Transfer(shape, STEPControl_AsIs)
        ok = w.Write(str(path)) == IFSelect_RetDone
    if not ok:
        raise RuntimeError(f"STEP export failed: {path}")


# ----------------------------------------------------------------------------- main


def model_files(data: Path, split: Path | None, subset: str) -> list[tuple[str, Path]]:
    if split is not None:
        ids = json.loads(split.read_text())[subset]
        return [(i, data / f"{i}.json") for i in ids]
    return [(str(p.relative_to(data).with_suffix("")), p) for p in sorted(data.rglob("*.json"))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="DeepCAD cad_json folder (or any folder of such JSON files)")
    ap.add_argument("--split", help="train_val_test_split.json (DeepCAD); without it, every JSON under --data")
    ap.add_argument("--subset", default="test", help="split subset: train, validation or test")
    ap.add_argument("--out", required=True)
    ap.add_argument("--units", choices=list(UNITS), default="m", help="units of the JSON (DeepCAD: m)")
    ap.add_argument("--max-features", type=int, default=60)
    ap.add_argument("--limit", type=int, default=0, help="stop after this many converted models (0: all)")
    ap.add_argument("--refs", action="store_true", help="also write the original models as STEP (needs OCP)")
    args = ap.parse_args()

    data, out = Path(args.data).expanduser(), Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    if args.refs:
        (out / "refs").mkdir(exist_ok=True)
    unit = UNITS[args.units]
    goals, spec, seen = {}, {"_comment": ["Generated by convert_deepcad.py: each DeepCAD model is a 'part' built by one goal, "
                                          "in the goal's frame (no placement)."], "parts": []}, set()
    reasons, lengths = collections.Counter(), collections.Counter()
    files = model_files(data, Path(args.split).expanduser() if args.split else None, args.subset)
    for n_done, (mid, path) in enumerate(files, 1):
        name = "dc_" + mid.replace("/", "_")
        try:
            extrudes = read_model(path)
            groups, (R, t) = convert(extrudes, unit)
            if args.refs:
                groups = order_for_partdesign(groups)
            goal = make_goal(groups, args.max_features)
            key = hashlib.sha1(json.dumps(goal["features"], sort_keys=True).encode()).hexdigest()
            if key in seen:
                raise Skip("duplicate")
            seen.add(key)
            if args.refs:
                ref = reference_shape(extrudes, R, t, unit)
                check_reference(ref)
                write_step(ref, out / "refs" / f"{name}.step")
        except Skip as exc:
            reasons[str(exc)] += 1
            continue
        except (KeyError, ValueError, TypeError, IndexError, FileNotFoundError, json.JSONDecodeError) as exc:
            reasons[f"unreadable ({type(exc).__name__})"] += 1
            continue
        except Exception as exc:  # noqa: BLE001  (OCC failures while building the reference)
            reasons[f"reference build failed ({type(exc).__name__})"] += 1
            continue
        goals[name] = goal
        n = len(goal["features"])
        lengths["1-5" if n <= 5 else "6-10" if n <= 10 else "11-20" if n <= 20 else "21+"] += 1
        spec["parts"].append({"part": name, "builds": [{"goal": name, "rotations": [], "translation": [0, 0, 0]}]})
        if args.limit and len(goals) >= args.limit:
            break
        if n_done % 1000 == 0:
            print(f"  {n_done} read, {len(goals)} converted", flush=True)
    sub = args.subset if args.split else "all"
    (out / f"goals_{sub}.json").write_text(json.dumps(goals, separators=(",", ":")) + "\n")
    (out / f"eval_{sub}.json").write_text(json.dumps(spec, indent=1) + "\n")
    report = {"read": n_done if files else 0, "converted": len(goals), "left_out": dict(reasons.most_common()),
              "features": dict(sorted(lengths.items())), "units": args.units, "max_features": args.max_features}
    (out / f"report_{sub}.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    sys.exit(main())
