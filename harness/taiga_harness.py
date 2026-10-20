#!/usr/bin/env python3
"""taiga_harness.py — the interface other tools (LLM agents, scripts) use to build CAD parts
with the Taiga models: the goal format, validation, a buildability check, builds, results.
Version: 2026.10.20.1

The same functions back the MCP server (taiga_mcp.py) and this command line; everything is
JSON in, JSON out:

    python taiga_harness.py format                     # the goal format, for the caller to read
    python taiga_harness.py validate goals.json        # static checks, no FreeCAD
    python taiga_harness.py check goals.json           # can the teacher build each goal? (FreeCAD)
    python taiga_harness.py build goals.json [--chain main:guided,teacher] [--workers 8]
    python taiga_harness.py models                     # the registry

goals.json is {name: goal}, a goal being {"features": [{"kind": ..., "params": {...}}, ...]}.

Configuration (environment):
  TAIGA_WORK      work folder with the code, venv and FreeCAD paths the models were trained with
                  (default ~/taiga-expanded-0017; a v2 model needs a 0019 folder)
  TAIGA_REGISTRY  models registry (default <repo>/build/registry.json)
  TAIGA_OUT       where builds go (default ~/taiga_parts); each request gets its own folder

Run it with the work folder's Python (it imports the goal schema from there):
    ~/taiga-expanded-0017/taiga-s1/.venv/bin/python <repo>/harness/taiga_harness.py format
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUILD = REPO / "build" / "taiga_build_part.py"
RUNTIME = REPO / "build" / "taiga_runtime.py"


def work() -> Path:
    return Path(os.environ.get("TAIGA_WORK", "~/taiga-expanded-0017")).expanduser()


def out_root() -> Path:
    return Path(os.environ.get("TAIGA_OUT", "~/taiga_parts")).expanduser()


def registry_path() -> Path:
    return Path(os.environ.get("TAIGA_REGISTRY", str(REPO / "build" / "registry.json"))).expanduser()


def _schema():
    """The work folder's goal schema (kinds the trained code knows)."""
    code = work() / "taiga-s1"
    if str(code) not in sys.path:
        sys.path.insert(0, str(code))
    from freecad_s1 import schema  # noqa: E402

    return schema


def _has_points() -> bool:
    try:
        _schema()
        import freecad_s1.ext.pointsel  # noqa: F401
        return True
    except ImportError:
        return False


def _has_angled() -> bool:
    """The work folder has patch 0020 (sketches on planes at any angle)."""
    try:
        _schema()
        import freecad_s1.ext.angled  # noqa: F401
        return True
    except ImportError:
        return False


ANGLED_KINDS = ("boss_cyl", "boss_box", "hole", "pocket_rect", "profile_boss", "profile_pocket")


def fc_env() -> dict:
    """Environment with the FreeCAD paths from <work>/freecad.env."""
    env = dict(os.environ)
    f = work() / "freecad.env"
    if f.is_file():
        for line in f.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


# ----------------------------------------------------------------------------- the goal format

BASE_KINDS = ("base_box", "base_cyl", "base_hex", "base_ring", "profile_base", "profile_revolve")
PATTERN_KINDS = ("polar_pattern", "linear_pattern", "mirror")
SKETCHED_PLACEABLE = ("boss_cyl", "boss_box", "hole", "hole_std", "pocket_rect", "profile_boss", "profile_pocket")

KINDS = {
    "base_box": ({"w": "width along X", "d": "depth along Y", "h": "height along Z"}, {}, "Rectangular block, centred on the origin in X and Y, from z = 0 to h."),
    "base_cyl": ({"r": "radius", "h": "height"}, {}, "Cylinder about Z, centred on the origin, from z = 0 to h."),
    "base_hex": ({"r": "circumradius", "h": "height"}, {}, "Hexagonal prism about Z."),
    "base_ring": ({"ri": "inner radius", "ro": "outer radius", "h": "height"}, {}, "Ring about Z."),
    "profile_base": ({"outline": "closed outline in the XY plane (see outline)", "h": "height"}, {}, "Prism of any closed outline, from z = 0 to h."),
    "profile_revolve": ({"outline": "closed outline (u = along/radius, see conventions)", "ax": "axis X", "ay": "axis Y", "az": "axis Z",
                         "nx": "sketch plane normal", "ny": "", "nz": "", "off": "0", "x": "0", "y": "0", "z": "0"},
                        {"angle": "sweep in degrees (360)"}, "Turned part or collar: the outline revolved about an origin axis; as base or later feature."),
    "profile_groove": ({"outline": "", "ax": "", "ay": "", "az": "", "nx": "", "ny": "", "nz": "", "off": "", "x": "", "y": "", "z": ""},
                       {"angle": ""}, "Removes the outline revolved about an origin axis (ring grooves, bores)."),
    "boss_cyl": ({"r": "radius", "x": "centre x", "y": "centre y", "h": "height"}, {}, "Cylindrical boss on a face (top face by default)."),
    "boss_box": ({"w": "", "d": "", "x": "", "y": "", "h": ""}, {}, "Rectangular boss on a face."),
    "hole": ({"r": "radius", "x": "", "y": ""}, {}, "Through hole (sketch + pocket through all)."),
    "hole_std": ({"r": "", "x": "", "y": ""}, {}, "Through hole made with FreeCAD's Hole feature."),
    "pocket_rect": ({"w": "", "d": "", "x": "", "y": "", "depth": ""}, {}, "Rectangular pocket."),
    "profile_boss": ({"outline": "closed outline on the face", "h": "height"}, {}, "Boss of any closed outline."),
    "profile_pocket": ({"outline": ""}, {"depth": "depth (omit: through all)"}, "Pocket of any closed outline."),
    "polar_pattern": ({"n": "number of copies (incl. the original)"}, {"ax": "", "ay": "", "az": "axis (default Z)", "angle": "extent in degrees (360)"},
                      "Repeats the previous feature about an origin axis."),
    "linear_pattern": ({"n": "copies", "length": "distance first to last"}, {"ax": "", "ay": "", "az": "direction (default +X)"},
                       "Repeats the previous feature along an axis."),
    "mirror": ({}, {"ax": "", "ay": "", "az": "normal of the mirror plane (default X: the YZ plane)"}, "Mirrors the previous feature across an origin plane."),
    "fillet_top": ({"r": "radius"}, {}, "Fillets the top face's edges."),
    "fillet_vertical": ({"r": "radius"}, {}, "Fillets every vertical edge."),
    "chamfer_top": ({"size": "size"}, {}, "Chamfers the top face's edges."),
    "shell": ({"t": "wall thickness"}, {"pts": "points on the faces to remove (default: the top face)"}, "Hollows the part."),
    "fillet_edges": ({"r": "radius"}, {"pts": "a point on each edge", "nx": "", "ny": "", "nz": "edge loop of the face with this normal",
                                       "at": "[x, y, z]: which such face", "ax": "", "ay": "", "az": "every edge parallel to this axis"},
                     "Fillets chosen edges: by points (pts), a face's edge loop (normal [+at]) or all edges along an axis."),
    "chamfer_edges": ({"size": "size"}, {"pts": "", "nx": "", "ny": "", "nz": "", "at": "", "ax": "", "ay": "", "az": ""},
                      "Chamfers chosen edges, selected like fillet_edges."),
    "draft": ({"angle": "degrees, 0.5 to 20"}, {"pts": "a point on each face to draft (default: every side face)"},
              "Draft angle on side faces (patch 0022): about the XY plane (z = 0), pulled along +Z, so outer faces lean "
              "inward going up and the part narrows towards its top, as a casting does."),
}

CONVENTIONS = [
    "Units are millimetres; coordinates are global, Z up. The first feature is the base (one of: "
    + ", ".join(BASE_KINDS) + "); every later feature changes the part built so far, in order.",
    "Top-face features: x, y are the centre on the top face, measured from the origin (the base's centre).",
    "Any other face: add the face's outward normal (nx, ny, nz; one of them +1 or -1, the others 0) and the "
    "centre (x, y, z); the coordinate along the normal comes from the face. With several faces of that normal, "
    "add at = [x, y, z], a point on the face you mean (e.g. the top of a boss).",
    "Origin plane with an offset: add the plane normal (nx, ny, nz; +1, the sign does not matter) and off (the "
    "plane's position along that axis); the centre's coordinate along that axis equals off. The feature is "
    "extruded both ways (cross bores, windows, internal cavities).",
    "Rectangles: w along the face's first in-plane axis (X, then Y, then Z), d along the second.",
    "Outlines: {'start': [u, v], 'segs': [['L', u, v], ['A', um, vm, u, v], ['S', [[u, v], ...]]]}: lines, arcs "
    "through a middle point, splines through points; closed (the last point is the start). On the top face "
    "and XY planes u = X, v = Y; normal to X u = Y, v = Z; normal to Y u = X, v = Z.",
    "Revolve and groove: about the origin axis (ax, ay, az); the outline lies in an origin plane containing the "
    "axis (XZ, normal Y, for the Z and X axes; YZ, normal X, for the Y axis), with off = 0 and x = y = z = 0; it "
    "must stay on one side of the axis.",
    "Patterns and mirror repeat the feature right before them; a pattern of a pattern is not supported.",
    "Fillets, chamfers and shells must fit the material around them; holes and pockets need material around them.",
    "Selection by points (pts): a point on each edge (its midpoint is safest) or on each face; the nearest edge or "
    "face is used. Avoid points where several edges meet.",
    "Angled planes (patch 0020): a sketch plane at any angle through the origin x, y, z (the feature's "
    "centre), with its unit normal nx, ny, nz (any direction) and its in-plane x direction ux, uy, uz; the "
    "profile is drawn in the plane's own frame (circles and rectangles centred on the origin, w along u, d "
    "along v = n x u; outlines in (u, v) about the origin) and extruded both ways, h or depth the total, "
    "holes and pockets without depth through all both ways. Only bosses, holes and pockets.",
    "One goal is one solid part; several separate solids are several goals.",
    "Draft (patch 0022): draft after the features it tapers; every side face (planar faces parallel to Z, cylinders "
    "about Z) is drafted unless pts names some. Holes added before the draft are drafted too: add straight bores after it."
]

EXAMPLE = {"bracket": {"features": [
    {"kind": "base_box", "params": {"w": 80, "d": 50, "h": 12}},
    {"kind": "boss_cyl", "params": {"r": 10, "x": 0, "y": 0, "h": 15}},
    {"kind": "hole", "params": {"r": 5, "x": 0, "y": 0, "at": [0, 0, 27]}},
    {"kind": "hole", "params": {"r": 3, "x": 30, "y": 15}},
    {"kind": "mirror", "params": {"ax": 1, "ay": 0, "az": 0}},
    {"kind": "hole", "params": {"r": 4, "x": 0, "y": 0, "z": 6, "nx": 0, "ny": -1, "nz": 0}},
    {"kind": "chamfer_edges", "params": {"size": 1, "nx": 0, "ny": 0, "nz": -1}}]}}


def goal_format() -> dict:
    sch = _schema()
    known = set(sch.GOAL_KINDS)
    pts = _has_points()
    ang = _has_angled()
    kinds = {}
    for k, (req, opt, what) in KINDS.items():
        if k not in known:
            continue
        o = dict(opt)
        if k in SKETCHED_PLACEABLE:
            o.update({"nx": "face normal", "ny": "", "nz": "", "z": "centre z", "off": "origin-plane offset", "at": "point on the face"})
        if not pts:
            o.pop("pts", None)
            what = what.replace("by points (pts), ", "by ")
        if ang and k in ANGLED_KINDS:
            o.update({"ux": "angled plane: in-plane x direction (with nx, ny, nz any unit normal)", "uy": "", "uz": ""})
        kinds[k] = {"what": what, "required": list(req), "optional": list(o),
                    "descriptions": {p: d for p, d in {**req, **o}.items() if d}}
    conv = [c for c in CONVENTIONS if (pts or not c.startswith("Selection by points"))
            and (ang or not c.startswith("Angled planes")) and ("draft" in known or not c.startswith("Draft"))]
    return {"format": "Taiga goal", "work": str(work()), "selection_by_points": pts, "angled_planes": ang,
            "conventions": conv, "kinds": kinds, "example": EXAMPLE,
            "limits": "Up to 64 features per goal; lengths of 1 mm to a few metres."}


# ----------------------------------------------------------------------------- validation

def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _point(p) -> bool:
    return isinstance(p, (list, tuple)) and len(p) == 3 and all(_num(c) for c in p)


def _closed(outline) -> str | None:
    """None if the outline is well formed and closed, else what is wrong."""
    if not isinstance(outline, dict) or not isinstance(outline.get("start"), (list, tuple)) or len(outline["start"]) != 2:
        return "outline needs 'start': [u, v]"
    segs = outline.get("segs")
    if not isinstance(segs, list) or not segs:
        return "outline needs a non-empty 'segs' list"
    last = outline["start"]
    for s in segs:
        if not isinstance(s, (list, tuple)) or not s or s[0] not in ("L", "A", "S"):
            return f"bad segment {s!r}: use ['L', u, v], ['A', um, vm, u, v] or ['S', [[u, v], ...]]"
        if s[0] == "L" and len(s) == 3 and _num(s[1]) and _num(s[2]):
            last = [s[1], s[2]]
        elif s[0] == "A" and len(s) == 5 and all(_num(c) for c in s[1:]):
            last = [s[3], s[4]]
        elif s[0] == "S" and len(s) == 2 and isinstance(s[1], list) and s[1] and all(
                isinstance(q, (list, tuple)) and len(q) == 2 for q in s[1]):
            last = list(s[1][-1])
        else:
            return f"bad segment {s!r}"
    if abs(last[0] - outline["start"][0]) > 1e-6 or abs(last[1] - outline["start"][1]) > 1e-6:
        return "outline is not closed: the last point must equal 'start'"
    return None


def _unit(params, keys) -> bool | None:
    """None if none of keys given; True if exactly one is +-1 and the others 0."""
    if not any(k in params for k in keys):
        return None
    v = [params.get(k, 0) for k in keys]
    return all(_num(x) for x in v) and sorted(abs(x) for x in v) == [0, 0, 1]


def validate_goal(goal: dict) -> dict:
    errors, warnings = [], []
    sch = _schema()
    known = set(sch.GOAL_KINDS)
    feats = goal.get("features") if isinstance(goal, dict) else None
    if not isinstance(feats, list) or not feats:
        return {"ok": False, "errors": ["a goal needs a non-empty 'features' list"], "warnings": []}
    if len(feats) > 64:
        errors.append(f"{len(feats)} features: at most 64 per goal (split the part, or simplify)")
    for i, f in enumerate(feats):
        where = f"feature {i + 1}"
        if not isinstance(f, dict) or "kind" not in f:
            errors.append(f"{where}: needs 'kind' and 'params'")
            continue
        k, p = f["kind"], f.get("params", {})
        where += f" ({k})"
        if k not in known or k not in KINDS:
            errors.append(f"{where}: unknown kind; known: {', '.join(sorted(known & set(KINDS)))}")
            continue
        if not isinstance(p, dict):
            errors.append(f"{where}: params must be an object")
            continue
        if i == 0 and k not in BASE_KINDS:
            errors.append(f"{where}: the first feature must be a base ({', '.join(BASE_KINDS)})")
        if i > 0 and k in BASE_KINDS and k != "profile_revolve":
            errors.append(f"{where}: a base kind can only be the first feature")
        req = list(KINDS[k][0])
        for r in req:
            if r not in p:
                errors.append(f"{where}: missing '{r}'")
            elif r != "outline" and not _num(p[r]):
                errors.append(f"{where}: '{r}' must be a number")
        for key in ("w", "d", "h", "r", "ri", "ro", "depth", "size", "t", "length"):
            if key in p and _num(p[key]) and p[key] <= 0:
                errors.append(f"{where}: '{key}' must be positive")
        if "n" in p and (not _num(p["n"]) or p["n"] < 2):
            errors.append(f"{where}: 'n' must be at least 2")
        if "outline" in p:
            bad = _closed(p["outline"])
            if bad:
                errors.append(f"{where}: {bad}")
        angled = all(key in p for key in ("ux", "uy", "uz"))
        if angled:
            if not _has_angled():
                errors.append(f"{where}: angled planes (ux, uy, uz) need a work folder with patch 0020 (TAIGA_WORK)")
            elif k not in ANGLED_KINDS:
                errors.append(f"{where}: only {', '.join(ANGLED_KINDS)} can be on an angled plane")
            nv = [p.get(key, 0) for key in ("nx", "ny", "nz")]
            uv = [p.get(key, 0) for key in ("ux", "uy", "uz")]
            if not all(_num(x) for x in nv + uv) or sum(x * x for x in nv) < 1e-12:
                errors.append(f"{where}: an angled plane needs a non-zero normal nx, ny, nz and numbers ux, uy, uz")
            else:
                nn = sum(x * x for x in nv) ** 0.5
                if abs(sum(a * b for a, b in zip(nv, uv))) / nn > 0.999 * max(sum(x * x for x in uv) ** 0.5, 1e-12):
                    errors.append(f"{where}: ux, uy, uz must not be parallel to the normal")
            if "off" in p:
                errors.append(f"{where}: an angled plane takes its position from x, y, z, not 'off'")
        elif _unit(p, ("nx", "ny", "nz")) is False:
            errors.append(f"{where}: nx, ny, nz must be one of +-1 and the others 0")
        if _unit(p, ("ax", "ay", "az")) is False:
            errors.append(f"{where}: ax, ay, az must be one of +-1 and the others 0")
        if "at" in p and not _point(p["at"]):
            errors.append(f"{where}: 'at' must be [x, y, z]")
        if "pts" in p:
            if not _has_points():
                errors.append(f"{where}: 'pts' needs a work folder with patch 0019 (TAIGA_WORK)")
            elif not isinstance(p["pts"], list) or not p["pts"] or not all(_point(q) for q in p["pts"]):
                errors.append(f"{where}: 'pts' must be a non-empty list of [x, y, z]")
        if k in PATTERN_KINDS:
            prev = feats[i - 1].get("kind") if i > 0 and isinstance(feats[i - 1], dict) else None
            if prev in PATTERN_KINDS:
                errors.append(f"{where}: a pattern of a pattern is not supported; repeat the original feature instead")
            if prev in BASE_KINDS:
                warnings.append(f"{where}: repeats the base")
        if k == "draft" and "angle" in p and _num(p["angle"]) and not 0.5 <= p["angle"] <= 20:
            errors.append(f"{where}: 'angle' must be 0.5 to 20 degrees")
        if k in ("fillet_edges", "chamfer_edges") and not ({"pts", "nx", "ny", "nz", "ax", "ay", "az"} & set(p)):
            warnings.append(f"{where}: no edge selection given; the top face's edge loop is used")
        if "off" in p and _unit(p, ("nx", "ny", "nz")) is None:
            errors.append(f"{where}: 'off' needs the plane normal nx, ny, nz")
        elif "off" in p and _num(p["off"]) and k in SKETCHED_PLACEABLE and _unit(p, ("nx", "ny", "nz")):
            axis = next(a for a in "xyz" if p.get("n" + a, 0) != 0)
            if _num(p.get(axis)) and abs(p[axis] - p["off"]) > 1e-6:
                warnings.append(f"{where}: the centre's {axis} ({p[axis]}) differs from off ({p['off']}); "
                                f"the plane is at {axis} = off, so the centre is moved there")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def normalize_goal(goal: dict) -> dict:
    """Put a goal in the form the models were trained on, without changing the part:
    - an outline feature gets its size (w, d) and, on the top face or an XY plane, its centre (x, y);
    - an origin-plane feature (off) gets a positive normal (the builder uses only the axis; off is a
      global position) and its centre on the plane. A negative normal there is outside the training
      data: the model lost its way on such a cross bore (IoU 0) and built it exactly with +1."""
    sch = _schema()  # noqa: F841  (puts the work folder's code on the path)
    from freecad_s1.ext import profiles

    g = json.loads(json.dumps(goal))
    for f in g["features"]:
        p = f.get("params", {})
        if "off" in p and _unit(p, ("nx", "ny", "nz")):
            for a in "xyz":
                if p.get("n" + a, 0) != 0:
                    p["n" + a] = 1.0
                    if f.get("kind") in SKETCHED_PLACEABLE and a in p:
                        p[a] = p["off"]
        if "outline" in p and _closed(p["outline"]) is None:
            cu, cv, w, d = profiles.bbox(p["outline"])
            p.setdefault("w", round(w, 3))
            p.setdefault("d", round(d, 3))
            if not any(k in p for k in ("nx", "ny")):
                p.setdefault("x", round(cu, 3))
                p.setdefault("y", round(cv, 3))
    return g


def validate(goals: dict) -> dict:
    return {name: validate_goal(g) for name, g in goals.items()}


# ----------------------------------------------------------------------------- FreeCAD: check and build

def _python() -> str:
    py = work() / "taiga-s1" / ".venv" / "bin" / "python"
    return str(py) if py.exists() else sys.executable


def _request_dir(prefix: str) -> Path:
    d = out_root() / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _prepare(goals: dict, folder: Path) -> tuple[dict, dict]:
    """Validated, normalized goals written to <folder>/goals.json; and the rejected ones."""
    ok, bad = {}, {}
    for name, g in goals.items():
        v = validate_goal(g)
        if v["ok"]:
            ok[name] = normalize_goal(g)
        else:
            bad[name] = v
    (folder / "goals.json").write_text(json.dumps(ok, indent=1))
    return ok, bad


def check(goals: dict, timeout_s: float = 3600) -> dict:
    """Can the scripted teacher build each goal (the target exists)? Runs FreeCAD."""
    folder = _request_dir("check")
    ok, bad = _prepare(goals, folder)
    res = {n: {"buildable": False, "why": "invalid goal", **v} for n, v in bad.items()}
    if ok:
        p = subprocess.run([_python(), str(BUILD), "--goals", str(folder / "goals.json"), "--check"],
                           cwd=work() / "taiga-s1", env=fc_env(), capture_output=True, text=True, timeout=timeout_s)
        for line in p.stdout.splitlines():
            m = re.match(r"^(\S+): (OK|INFEASIBLE)\s+(.*)$", line.strip())
            if m and m.group(1) in ok:
                res[m.group(1)] = {"buildable": m.group(2) == "OK", "detail": m.group(3)}
        for n in ok:
            res.setdefault(n, {"buildable": False, "detail": "no result (see the log)", "log": p.stdout[-2000:] + p.stderr[-2000:]})
    return res


def build(goals: dict, chain: str = "teacher,main:guided,main,fallback:guided", workers: int = 4,
          min_iou: float = 0.999, timeout_s: float = 4 * 3600, step: bool = True) -> dict:
    """Build each goal through the runtime's chain of attempts. Returns per goal: accepted
    attempt (or none), attempts with results, files of the accepted part."""
    folder = _request_dir("build")
    ok, bad = _prepare(goals, folder)
    out = {"request": str(folder), "parts": {n: {"built": False, "why": "invalid goal", **v} for n, v in bad.items()}}
    if not ok:
        return out
    cmd = [_python(), str(RUNTIME), "--goals", str(folder / "goals.json"), "--out", str(folder),
           "--chain", chain, "--workers", str(workers), "--min-iou", str(min_iou)]
    if registry_path().is_file():
        cmd += ["--registry", str(registry_path())]
    if not step:
        cmd += ["--build-args=--no-step"]  # one token: argparse reads a separate "--no-step" as an option
    p = subprocess.run(cmd, cwd=work() / "taiga-s1", env=fc_env(), capture_output=True, text=True, timeout=timeout_s)
    (folder / "runtime.log").write_text(p.stdout + p.stderr)
    log = folder / "runtime_log.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []
    for r in records:
        n = r["goal"]
        files = {ext: str(folder / f"{n}.{ext}") for ext in ("FCStd", "step") if (folder / f"{n}.{ext}").is_file()}
        out["parts"][n] = {"built": r["accepted"] is not None, "accepted": r["accepted"], "attempts": r["attempts"],
                           "seconds": r.get("seconds"), "files": files, **({"why": r["why"]} if "why" in r else {})}
    for n in ok:
        out["parts"].setdefault(n, {"built": False, "why": "no result (see runtime.log)"})
    return out


def read_file(path: str, max_mb: float = 20) -> dict:
    """A built file (only under TAIGA_OUT), base64-encoded, for callers on another machine."""
    f = Path(path).expanduser().resolve()
    root = out_root().resolve()
    if root not in f.parents or not f.is_file():
        return {"error": f"not a file under {root}"}
    if f.stat().st_size > max_mb * 1e6:
        return {"error": f"{f.stat().st_size / 1e6:.1f} MB, over {max_mb} MB"}
    return {"name": f.name, "bytes": f.stat().st_size, "base64": base64.b64encode(f.read_bytes()).decode()}


def models() -> dict:
    p = registry_path()
    if not p.is_file():
        return {"registry": str(p), "error": "no registry; copy build/registry.example.json to it and set the paths"}
    reg = json.loads(p.read_text())
    for m in reg.get("models", []):
        m["available"] = Path(m.get("path", "")).expanduser().exists()
    return {"registry": str(p), "work": str(work()), **reg}


# ----------------------------------------------------------------------------- command line

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("format")
    sub.add_parser("models")
    for c in ("validate", "check", "build"):
        s = sub.add_parser(c)
        s.add_argument("goals", help="goals JSON {name: goal}, or - for stdin")
        if c == "build":
            s.add_argument("--chain", default="teacher,main:guided,main,fallback:guided")
            s.add_argument("--workers", type=int, default=4)
            s.add_argument("--min-iou", type=float, default=0.999)
            s.add_argument("--no-step", action="store_true")
    a = ap.parse_args()
    if a.cmd == "format":
        r = goal_format()
    elif a.cmd == "models":
        r = models()
    else:
        goals = json.load(sys.stdin) if a.goals == "-" else json.loads(Path(a.goals).read_text())
        if a.cmd == "validate":
            r = validate(goals)
        elif a.cmd == "check":
            r = check(goals)
        else:
            r = build(goals, a.chain, a.workers, a.min_iou, step=not a.no_step)
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
