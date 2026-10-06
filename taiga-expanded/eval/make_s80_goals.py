#!/usr/bin/env python3
"""make_s80_goals.py — Taiga-S1 goals for the Victor S 80 pump end, in the taiga-expanded vocabulary.
Version: 2026.10.05.2

Writes, next to this script:
  s80_goals.json  one goal per part ({name: goal}, the format of ../../build/taiga_build_part.py)
  s80_eval.json   for eval_s80.py: per reference part, the goal(s) that build it and where they
                  go in pump coordinates (rotations + translation, as in taiga_assemble.py specs)

Every dimension comes from ../../build/pump_s80_reference_CAD/pump_s80.py (imported), so the
goals follow the reference when it changes. Needs OCP only because pump_s80.py imports it:
    ~/taiga/taiga-s1/.venv/bin/python make_s80_goals.py

Goal frames. Parts turned about the shaft (shaft, bearings, seal, wear plate, impeller,
bearing bracket) use pump coordinates directly: shaft along X through the origin. The casing,
inspection cover and check valve are built with their main extrusion along Z, like every
Taiga base feature: goal (x, y, z) = pump (y, z, x) + offset, i.e. a 120 degree rotation about
(1, 1, 1). The priming cover is upright: goal = pump - (168, 0, 208).

Two goals for the casing: "casing" (every feature, about 50: far beyond the 17 features of
Taiga's longest test suite) and "casing_core" (without the small bolt and tapped holes and
their patterns), to tell length from vocabulary when the models are scored.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REF = HERE.parents[1] / "build" / "pump_s80_reference_CAD"
sys.path.insert(0, str(REF))
import pump_s80 as S  # noqa: E402

D = S.D
AXES = "xyz"


def r3(v: float) -> float:
    return round(float(v), 3)


# ----------------------------------------------------------------------------- outlines (ext.profiles format)

def _arc(c, rad, a0, a1):
    """Arc segment from angle a0 to a1 (radians) around centre c: ["A", mid, end]."""
    am = (a0 + a1) / 2
    return ["A", r3(c[0] + rad * math.cos(am)), r3(c[1] + rad * math.sin(am)),
            r3(c[0] + rad * math.cos(a1)), r3(c[1] + rad * math.sin(a1))]


def polygon(pts):
    pts = [[r3(u), r3(v)] for u, v in pts]
    return {"start": pts[0], "segs": [["L", *p] for p in pts[1:]] + [["L", *pts[0]]]}


def lathe(pts_ar):
    """Closed outline from (along X, radius) points, for a revolve about X on the XZ plane (u = x, v = z)."""
    return polygon(pts_ar)


def circle(cu, cv, rad):
    return {"start": [r3(cu + rad), r3(cv)],
            "segs": [_arc((cu, cv), rad, 0, math.pi), _arc((cu, cv), rad, math.pi, 2 * math.pi)]}


def rounded_rect(u0, v0, u1, v1, rad):
    """Same outline as pump_s80.rounded_rect (counter-clockwise from the bottom edge)."""
    segs = [["L", r3(u1 - rad), r3(v0)], _arc((u1 - rad, v0 + rad), rad, -math.pi / 2, 0),
            ["L", r3(u1), r3(v1 - rad)], _arc((u1 - rad, v1 - rad), rad, 0, math.pi / 2),
            ["L", r3(u0 + rad), r3(v1)], _arc((u0 + rad, v1 - rad), rad, math.pi / 2, math.pi),
            ["L", r3(u0), r3(v0 + rad)], _arc((u0 + rad, v0 + rad), rad, math.pi, 1.5 * math.pi)]
    return {"start": [r3(u0 + rad), r3(v0)], "segs": segs}


def rounded_rect_below(u0, v0, u1, v1, rad, v_cut):
    """Part of the rounded rectangle below v = v_cut (v_cut inside the top corner arcs)."""
    cy = v1 - rad
    a = math.asin((v_cut - cy) / rad)
    uc = rad * math.cos(a)
    segs = [["L", r3(u1 - rad), r3(v0)], _arc((u1 - rad, v0 + rad), rad, -math.pi / 2, 0),
            ["L", r3(u1), r3(cy)], _arc((u1 - rad, cy), rad, 0, a),
            ["L", r3(u0 + rad - uc), r3(v_cut)], _arc((u0 + rad, cy), rad, math.pi - a, math.pi),
            ["L", r3(u0), r3(v0 + rad)], _arc((u0 + rad, v0 + rad), rad, math.pi, 1.5 * math.pi)]
    return {"start": [r3(u0 + rad), r3(v0)], "segs": segs}


def rounded_rect_above(u0, v0, u1, v1, rad, v_cut):
    """Part of the rounded rectangle above v = v_cut (v_cut inside the top corner arcs)."""
    cy = v1 - rad
    a = math.asin((v_cut - cy) / rad)
    uc = rad * math.cos(a)
    segs = [["L", r3(u1 - rad + uc), r3(v_cut)], _arc((u1 - rad, cy), rad, a, math.pi / 2),
            ["L", r3(u0 + rad), r3(v1)], _arc((u0 + rad, cy), rad, math.pi / 2, math.pi - a)]  # ends at the start
    return {"start": [r3(u0 + rad - uc), r3(v_cut)], "segs": segs}


def spline_loop(pts):
    """Spline through pts[1:], closed by a line back to pts[0]."""
    p = [[r3(u), r3(v)] for u, v in pts]
    return {"start": p[0], "segs": [["S", p[1:]], ["L", *p[0]]]}


def check_outline(o, where: str) -> None:
    """Closed, and no zero-length segment (FreeCAD's sketcher rejects a line between equal points)."""
    cur = tuple(o["start"])
    for s in o["segs"]:
        end = tuple(s[1][-1]) if s[0] == "S" else tuple(s[-2:])
        if s[0] == "L" and math.dist(end, cur) < 1e-3:
            raise ValueError(f"{where}: zero-length line at {end}")
        cur = end
    if math.dist(cur, tuple(o["start"])) > 1e-6:
        raise ValueError(f"{where}: outline not closed ({cur} != {tuple(o['start'])})")


def bbox(o):
    pts = [o["start"]]
    for s in o["segs"]:
        if s[0] == "L":
            pts.append(s[1:3])
        elif s[0] == "A":
            pts += [s[1:3], s[3:5]]
        else:
            pts += s[1]
    us, vs = [p[0] for p in pts], [p[1] for p in pts]
    return (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2, max(us) - min(us), max(vs) - min(vs)


# ----------------------------------------------------------------------------- features

def F(kind, **p):
    return {"kind": kind, "params": {k: (r3(v) if isinstance(v, (int, float)) else v) for k, v in p.items()}}


def _normal(n):
    """'+x' -> {"nx": 1, "ny": 0, "nz": 0}"""
    p = {"nx": 0.0, "ny": 0.0, "nz": 0.0}
    p["n" + n[1]] = 1.0 if n[0] == "+" else -1.0
    return p


def _inplane(k):
    a = [i for i in range(3) if i != k]
    return a[0], a[1]


def outline_feat(kind, outline, normal=None, plane=None, off=None, at=None, **p):
    """Profile feature. normal None: top face / XY plane, outline in (x, y). With a normal ('+x', '-z', ...)
    the outline is in that plane's (u, v) = in-plane axes in x, y, z order; `plane` is the face's coordinate
    along the normal, or `off` the datum-plane offset (extruded symmetrically)."""
    cu, cv, w, d = bbox(outline)
    q = {"outline": outline, "w": w, "d": d}
    if normal is None:
        q.update(x=cu, y=cv)
    else:
        k = AXES.index(normal[1])
        ua, va = _inplane(k)
        c = [0.0, 0.0, 0.0]
        c[ua], c[va], c[k] = cu, cv, (off if off is not None else plane)
        q.update(_normal(normal), x=c[0], y=c[1], z=c[2])
        if off is not None:
            q["off"] = off
    if at is not None:
        q["at"] = [r3(v) for v in at]
    q.update(p)
    return F(kind, **q)


def sided(kind, normal, centre, at=None, off=None, **p):
    """hole / boss_cyl / boss_box / pocket_rect on a face with `normal` (centre: global x, y, z, the
    coordinate along the normal being the face's), or on a datum plane (off)."""
    q = {**_normal(normal), "x": centre[0], "y": centre[1], "z": centre[2], **p}
    if off is not None:
        q["off"] = off
    if at is not None:
        q["at"] = [r3(v) for v in at]
    return F(kind, **q)


def revolve_x(pts_ar, kind="profile_revolve"):
    """Revolve about X (sketched on the XZ plane), like ext.revolve.revolve_params(0, ...)."""
    return F(kind, nx=0.0, ny=1.0, nz=0.0, off=0.0, ax=1.0, ay=0.0, az=0.0, x=0.0, y=0.0, z=0.0,
             outline=lathe(pts_ar), angle=360.0)


def pattern(kind, axis=None, **p):
    q = dict(p)
    if axis is not None:
        q.update(ax=0.0, ay=0.0, az=0.0)
        q["a" + axis[1]] = 1.0 if axis[0] == "+" else -1.0
    return F(kind, **q)


def polar_pt(rho, deg, cu=0.0, cv=0.0):
    a = math.radians(deg)
    return cu + rho * math.cos(a), cv + rho * math.sin(a)


# ----------------------------------------------------------------------------- parts (goal frames)

def shaft():
    ri, rb, rd = D["shaft_d_impeller"] / 2, D["shaft_d_bearing"] / 2, D["shaft_d_drive"] / 2
    return [
        revolve_x([(-310, 0), (-310, rd), (-240, rd), (-240, rb), (-20, rb), (-20, ri), (32, ri), (32, 0)]),
        # keyways: pockets on XY datum planes through the key's top, extruded both ways
        sided("pocket_rect", "+z", (-275, 0, rd + 1), off=rd + 1, w=50, d=8, depth=10),
        sided("pocket_rect", "+z", (9, 0, ri + 1), off=ri + 1, w=38, d=8, depth=8),
    ]


def bearing():  # one 6207-size ring; the reference has two (eval: the same goal placed twice)
    rb = D["shaft_d_bearing"] / 2
    return [revolve_x([(-228, rb), (-228, 36), (-211, 36), (-211, rb)])]


def seal():
    rb, x0, x1 = D["shaft_d_bearing"] / 2, S.X_REAR0 - 2, S.X_REAR1
    return [revolve_x([(x0, rb), (x0, 29), (x1, 29), (x1, 23), (x1 + 2, 23), (x1 + 2, rb)])]


def wear_plate():
    return [
        revolve_x([(31, D["eye_r"]), (31, 90), (41, 90), (41, D["eye_r"])]),
        sided("hole", "+x", (41, 72, 0), r=4.5),
        pattern("polar_pattern", "+x", n=6, angle=360),
    ]


def impeller():
    r2, rh, rs, st, b2 = D["r2"], S.IMPELLER_HUB_R, D["shaft_d_impeller"] / 2, D["shroud_t"], D["b2"]
    pa, pb = S.blade_points()
    blade = {"start": [r3(pa[0][0]), r3(pa[0][1])],
             "segs": [["S", [[r3(u), r3(v)] for u, v in pa[1:]]], ["L", r3(pb[-1][0]), r3(pb[-1][1])],
                      ["S", [[r3(u), r3(v)] for u, v in reversed(pb[:-1])]], ["L", r3(pa[0][0]), r3(pa[0][1])]]}
    return [
        revolve_x([(-6, rs), (-6, rh), (0, rh), (0, r2), (st, r2), (st, rh), (st + b2, rh), (st + b2, rs)]),
        outline_feat("profile_boss", blade, "+x", plane=st, h=b2),
        pattern("polar_pattern", "+x", n=D["blades"], angle=360),
        # keyway along the bore: pocket on a YZ datum plane, through the hub both ways
        sided("pocket_rect", "+x", (12, 0, rs + 1.15), off=12, w=8, d=4.3, depth=40),
    ]


def priming_cover():  # goal = pump - (168, 0, 208): plug z 0..14, cap 14..26, handle 26..42
    rh = 32 * math.cos(math.radians(45))
    return [
        F("base_cyl", r=21, h=14),
        F("boss_cyl", r=42, x=0, y=0, h=12),
        F("hole", r=4.5, x=rh, y=rh),
        F("polar_pattern", n=4),
        F("boss_box", w=60, d=12, x=0, y=0, h=16),
    ]


def check_valve():  # goal (x, y, z) = pump (y, z - 110, x - 189): weight z 0..6, flap + hinge tab 6..12
    ry = math.sqrt(48 ** 2 - 20 ** 2)
    flap = {"start": [20.0, r3(ry)],
            "segs": [["L", 20.0, 75.0], ["L", -20.0, 75.0], ["L", -20.0, r3(ry)], ["A", 0.0, -48.0, 20.0, r3(ry)]]}
    return [F("base_cyl", r=32, h=6), outline_feat("profile_boss", flap, h=6)]


def inspection_cover():  # goal (x, y, z) = pump (y, z + 65, x - 205): spigot z 0..10, plate 10..22
    return [
        F("base_box", w=118, d=78, h=10),
        outline_feat("profile_boss", rounded_rect(-95, -68, 95, 68, 30), h=12),
        F("hole", r=5.5, x=-75, y=-50), F("linear_pattern", n=3, length=150),
        F("hole", r=5.5, x=-75, y=50), F("linear_pattern", n=3, length=150),
    ]


def bearing_bracket():
    x_f = S.X_REAR0 - 12.5
    rh = 80 * math.cos(math.radians(45))
    g = S.Z_GROUND
    return [
        # flange, lantern (hollow, r 50..62), bearing housing (r 36..52), one profile
        revolve_x([(x_f, 36), (x_f, 95), (x_f - 14, 95), (x_f - 14, 62), (-180, 62), (-180, 52), (-240, 52),
                   (-240, 36), (-182, 36), (-182, 50), (x_f - 10, 50), (x_f - 10, 36)]),
        # seal access windows through both sides: pocket on the XZ plane, extruded both ways
        sided("pocket_rect", "+y", (-105, 0, 0), off=0, w=90, d=50, depth=170),
        sided("hole", "+x", (x_f, rh, rh), r=5.5),
        pattern("polar_pattern", "+x", n=4, angle=360),
        # foot: web and plate as pads on XY datum planes (both ways), bolt holes from below
        sided("boss_box", "+z", (-190, 0, (g + 10 - 50) / 2), off=(g + 10 - 50) / 2, w=50, d=50, h=-50 - (g + 10)),
        sided("boss_box", "+z", (-190, 0, g + 8), off=g + 8, w=80, d=190, h=16),
        sided("hole", "-z", (-190, 75, g), r=9),
        pattern("mirror", "+y"),
    ]


def casing(core: bool = False):
    """Goal frame: (x, y, z) = pump (y, z, x + 21). Rear face z = 0, front face z = 236, top y = 210."""
    W, YO, ZB, ZT, RC = S.W, S.Y_OUT, S.Z_BOT, S.Z_TOP, S.R_CORNER
    gz = lambda px: px - S.X_REAR0  # noqa: E731
    front, ground = gz(S.X_FRONT1), S.Z_GROUND
    inner = (-YO + W, ZB + W, YO - W, ZT - W, RC - W)
    f = []
    major = lambda *x: f.extend(x)  # noqa: E731
    minor = (lambda *x: None) if core else major  # small bolt and tapped holes, skipped in casing_core
    # body: the outer rounded section, extruded from the rear face to the front face
    major(outline_feat("profile_base", rounded_rect(-YO, ZB, YO, ZT, RC), h=front))
    # feet: webs and plates as pads on datum planes, the rear foot repeated 150 mm to the front.
    # Before the chambers, so the chamber pockets reopen what the webs fill (as in the reference).
    web_y = (ground + 10 + ZB + 60) / 2
    major(sided("boss_box", "+y", (0, web_y, gz(22.5)), off=web_y, w=220, d=29, h=(ZB + 60) - (ground + 10)),
          pattern("linear_pattern", "+z", n=2, length=150),
          sided("boss_box", "+y", (0, ground + 9, gz(22.5)), off=ground + 9, w=330, d=45, h=18),
          pattern("linear_pattern", "+z", n=2, length=150))
    # chambers: pockets on datum planes across the casing (normal z = pump x), extruded both ways
    suc_lo = rounded_rect_below(*inner, S.Z_SHELF0)
    sep = rounded_rect_above(*inner, S.Z_SHELF1)
    major(outline_feat("profile_pocket", suc_lo, "+z", off=gz((S.X_SEP1 + S.X_FRONT0) / 2), depth=S.X_FRONT0 - S.X_SEP1),
          outline_feat("profile_pocket", rounded_rect(*inner), "+z", off=gz((S.X_STEP1 + S.X_FRONT0) / 2),
                       depth=S.X_FRONT0 - S.X_STEP1),
          outline_feat("profile_pocket", sep, "+z", off=gz((S.X_VOL0 + S.X_STEP0) / 2), depth=S.X_STEP0 - S.X_VOL0))
    # volute (spiral channel around the impeller), tangential duct, impeller eye in the separation wall
    r3v, hth = D["r3"], D["h_throat"]
    vol_mid = gz((S.X_VOL0 + S.X_VOL1) / 2)
    y_hi = min(r3v + hth, YO - W - 1)
    major(outline_feat("profile_pocket", spline_loop(S.volute_face_points(r3v, hth)), "+z", off=vol_mid,
                       depth=S.X_VOL1 - S.X_VOL0),
          sided("pocket_rect", "+z", ((r3v + 2 + y_hi) / 2, (S.Z_SHELF1 + 1) / 2, vol_mid), off=vol_mid,
                w=y_hi - (r3v + 2), d=S.Z_SHELF1 + 1, depth=S.X_VOL1 - S.X_VOL0),
          outline_feat("profile_pocket", circle(0, 0, D["eye_r"]), "+z", off=gz((S.X_SEP0 + S.X_SEP1) / 2),
                       depth=S.X_SEP1 - S.X_SEP0 + 2))
    # rear hub for the bearing bracket: pad on the rear face, seal bore, 4 tapped holes
    hub_z = -12.0
    major(sided("boss_cyl", "-z", (0, 0, 0), r=95, h=12),
          outline_feat("profile_pocket", circle(0, 0, 30), "-z", plane=hub_z, at=(0, 0, hub_z), depth=26))
    hx = 80 * math.cos(math.radians(45))
    minor(outline_feat("profile_pocket", circle(hx, hx, 5), "-z", plane=hub_z, at=(hx, hx, hub_z), depth=21),
          pattern("polar_pattern", "+z", n=4, angle=360))
    # wear plate tapped holes, from the volute side of the separation wall
    minor(outline_feat("profile_pocket", circle(72, 0, 3.5), "-z", plane=gz(S.X_SEP0), at=(72, 0, gz(S.X_SEP0)), depth=10),
          pattern("polar_pattern", "+z", n=6, angle=360))
    # suction port on the front face: neck, DN80 PN16 flange, bore into the suction chamber, 8 bolt holes
    sz = S.SUCTION_Z
    major(sided("boss_cyl", "+z", (0, sz, front), at=(0, sz, front), r=55, h=20),
          sided("boss_cyl", "+z", (0, sz, front + 20), at=(0, sz, front + 20), r=100, h=20),
          outline_feat("profile_pocket", circle(0, sz, 40), "+z", plane=front + 40, at=(0, sz, front + 40),
                       depth=front + 40 - gz(S.X_FRONT0 - 1)))
    for deg in (22.5, -22.5, 67.5, -67.5):  # one side; the mirror makes the other
        u, v = polar_pt(80, deg, 0, sz)
        minor(outline_feat("profile_pocket", circle(u, v, 9), "+z", plane=front + 40, at=(u, v, front + 40), depth=21),
              pattern("mirror", "+x"))
    # discharge port on top: square neck and flange (pads on the top face), bore, 4 bolt holes
    dz = gz(S.DISCHARGE_X)
    major(sided("boss_box", "+y", (0, ZT, dz), at=(0, ZT, dz), w=110, d=110, h=30),
          sided("boss_box", "+y", (0, ZT + 30, dz), at=(0, ZT + 30, dz), w=160, d=160, h=20),
          outline_feat("profile_pocket", circle(0, dz, 40), "+y", plane=ZT + 50, at=(0, ZT + 50, dz),
                       depth=ZT + 50 - (S.Z_SHELF1 + 1)))
    for dv in (-57, 57):
        minor(outline_feat("profile_pocket", circle(57, dz + dv, 9), "+y", plane=ZT + 50, at=(57, ZT + 50, dz + dv), depth=21),
              pattern("mirror", "+x"))
    # priming port on top of the suction chamber: boss on the top face, bore, 4 holes (the reference's
    # boss also reaches into the chamber, but the chamber is reopened afterwards)
    pz = gz(S.PRIMING_X)
    major(sided("boss_cyl", "+y", (0, ZT, pz), at=(0, ZT, pz), r=42, h=12),
          outline_feat("profile_pocket", circle(0, pz, 22), "+y", plane=ZT + 12, at=(0, ZT + 12, pz), depth=72))
    for dv in (-1, 1):
        u, v = polar_pt(32, 45 * dv, pz * 0, 0)
        minor(outline_feat("profile_pocket", circle(u, pz + v, 4), "+y", plane=ZT + 12, at=(u, ZT + 12, pz + v), depth=17),
              pattern("mirror", "+x"))
    # inspection opening in the front wall, 6 tapped holes around it
    iz = -65.0
    major(sided("pocket_rect", "+z", (0, iz, front), at=(0, iz, front), w=120, d=80, depth=14))
    for dv in (-50, 50):
        minor(outline_feat("profile_pocket", circle(-75, iz + dv, 4), "+z", plane=front, at=(-75, iz + dv, front), depth=18),
              pattern("linear_pattern", "+x", n=3, length=150))
    # drain boss at the bottom of the suction chamber, drain hole
    major(sided("boss_cyl", "-y", (0, ZB, gz(100)), at=(0, ZB, gz(100)), r=22, h=8),
          outline_feat("profile_pocket", circle(0, gz(100), 10), "-y", plane=ZB - 8, at=(0, ZB - 8, gz(100)), depth=28))
    # foot bolt holes, from below, repeated on the front foot
    for u in (145, -145):
        minor(outline_feat("profile_pocket", circle(u, gz(22.5), 9), "-y", plane=ground, at=(u, ground, gz(22.5)), depth=20),
              pattern("linear_pattern", "+z", n=2, length=150))
    return f


CYCLIC = [[[1, 1, 1], 120]]  # goal (x, y, z) -> pump (z, x, y): rotation by 120 degrees about (1, 1, 1)
GOALS = {
    "s80_casing": (casing(), CYCLIC, [S.X_REAR0, 0, 0]),
    "s80_casing_core": (casing(core=True), CYCLIC, [S.X_REAR0, 0, 0]),
    "s80_impeller": (impeller(), [], [0, 0, 0]),
    "s80_wear_plate": (wear_plate(), [], [0, 0, 0]),
    "s80_inspection_cover": (inspection_cover(), CYCLIC, [205, 0, -65]),
    "s80_priming_cover": (priming_cover(), [], [S.PRIMING_X, 0, S.Z_TOP - 2]),
    "s80_check_valve": (check_valve(), CYCLIC, [S.X_FRONT0 - 14, 0, S.SUCTION_Z]),
    "s80_bearing_bracket": (bearing_bracket(), [], [0, 0, 0]),
    "s80_shaft": (shaft(), [], [0, 0, 0]),
    "s80_bearing": (bearing(), [], [0, 0, 0]),
    "s80_mechanical_seal": (seal(), [], [0, 0, 0]),
}
# reference part -> goals that build it (goal, rotations, translation)
EVAL = {
    "casing": [("s80_casing", None)],
    "impeller": [("s80_impeller", None)],
    "wear_plate": [("s80_wear_plate", None)],
    "inspection_cover": [("s80_inspection_cover", None)],
    "priming_cover": [("s80_priming_cover", None)],
    "check_valve": [("s80_check_valve", None)],
    "bearing_bracket": [("s80_bearing_bracket", None)],
    "shaft": [("s80_shaft", None)],
    "bearings": [("s80_bearing", None), ("s80_bearing", [31, 0, 0])],  # second ring: 31 mm further
    "mechanical_seal": [("s80_mechanical_seal", None)],
}
EXTRA_EVAL = {"casing": ["s80_casing_core"]}  # also scored against the casing


def main() -> None:
    goals = {name: {"features": feats, "level": 3} for name, (feats, _, _) in GOALS.items()}
    for name, g in goals.items():
        for i, f in enumerate(g["features"]):
            if "outline" in f["params"]:
                check_outline(f["params"]["outline"], f"{name} feature {i} ({f['kind']})")
    spec = {"_comment": ["Generated by make_s80_goals.py. Per reference part (STEP from pump_s80.py): the",
                         "goals that build it, each placed in pump coordinates with rotations (applied in order)",
                         "and a translation, as in taiga_assemble.py. 'alternatives': other goals scored against",
                         "the same part on their own."],
            "parts": []}
    for part, builds in EVAL.items():
        entry = {"part": part, "builds": []}
        for goal, extra in builds:
            _, rot, tr = GOALS[goal]
            t = [a + b for a, b in zip(tr, extra or [0, 0, 0])]
            entry["builds"].append({"goal": goal, "rotations": rot, "translation": [r3(v) for v in t]})
        if part in EXTRA_EVAL:
            entry["alternatives"] = [{"goal": g, "rotations": GOALS[g][1], "translation": [r3(v) for v in GOALS[g][2]]}
                                     for g in EXTRA_EVAL[part]]
        spec["parts"].append(entry)
    (HERE / "s80_goals.json").write_text(json.dumps(goals, indent=1) + "\n")
    (HERE / "s80_eval.json").write_text(json.dumps(spec, indent=2) + "\n")
    for name, g in goals.items():
        print(f"{name:24s} {len(g['features']):3d} features")
    print(f"wrote {HERE / 's80_goals.json'} and {HERE / 's80_eval.json'}")


if __name__ == "__main__":
    main()
