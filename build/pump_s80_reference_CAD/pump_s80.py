#!/usr/bin/env python3
"""pump_s80.py — parametric model of the Victor Pumps S 80 self-priming centrifugal pump
(bare-shaft pump end: casing with volute, impeller, wear plate, covers, bearing bracket,
shaft, seal and bearings). Reference geometry for extending Taiga-S1.
Version: 2026.10.05.4

Runs with any Python >= 3.10 that has OpenCASCADE's Python bindings (OCP):
    pip install 'cadquery-ocp>=7.9,<8'  # or: uv pip install --python <venv>/bin/python 'cadquery-ocp>=7.9,<8'
    python pump_s80.py --out parts/pump_s80
Writes one STEP file per part, in pump coordinates (so the parts are already assembled),
plus pump_s80.json, a spec for ../taiga_assemble.py, which builds the FreeCAD document:
    "$FREECAD_PYTHON" ../taiga_assemble.py --parts parts/pump_s80 --spec parts/pump_s80/pump_s80.json
Options: --cutaway also writes casing_cutaway.step (casing cut in half at y = 0, like the
brochure's cutaway photo); --preview writes PNG previews (needs matplotlib).

Coordinates (mm): shaft axis = X axis; impeller back face at x = 0; suction side (front)
towards +x, bearing bracket towards -x; z up. The brochure gives no internal dimensions:
everything is derived in design() from the catalogue data (DN80, 2900 rpm, 4 kW,
32 mm solids) with standard centrifugal-pump design rules, and from the photos.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire,
                                BRepBuilderAPI_Transform)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAbs import GeomAbs_C2
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
try:  # OCP 7.x (tested: 7.9.3)
    from OCP.TColgp import TColgp_Array1OfPnt
except ImportError:  # OCP 8.x (OCCT 8) dropped the TColgp typedefs; look for the NCollection template instead
    import OCP.NCollection as _nc
    _cands = [n for n in dir(_nc) if "Array1" in n and n.endswith("gp_Pnt")]
    if not _cands:
        raise ImportError("this OCP build has no array of gp_Pnt; install the tested version: "
                          "pip install 'cadquery-ocp>=7.9,<8'")
    TColgp_Array1OfPnt = getattr(_nc, _cands[0])
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS

# ---------------------------------------------------------------------------- design


def design() -> dict:
    """Main dimensions from the catalogue data and standard design rules (Stepanoff)."""
    g = 9.81
    n = 2900  # rpm (catalogue)
    H = 35.0  # m, shut-off region of the S 80 curve (catalogue chart, DN80 page)
    Q = 60.0 / 3600  # m3/s, best-efficiency region of the S 80 curve
    psi = 1.0  # head coefficient (open impeller, self-priming): H = psi * u2^2 / (2 g)
    u2 = math.sqrt(2 * g * H / psi)
    D2 = round(u2 / (math.pi * n / 60) * 1000 / 5) * 5  # impeller diameter, mm
    c3 = 0.42 * math.sqrt(2 * g * H)  # mean velocity in the volute throat (Stepanoff K3 ~ 0.42)
    A_throat = Q / c3 * 1e6  # mm2
    b3 = 40.0  # volute width, mm
    d = {
        "n_rpm": n, "H_m": H, "Q_m3h": Q * 3600, "u2_ms": round(u2, 1), "D2": D2,
        "r2": D2 / 2, "r3": D2 / 2 * 1.05,  # volute base circle (tongue clearance 5 %)
        "b3": b3, "h_throat": round(A_throat / b3, 1), "A_throat_mm2": round(A_throat),
        "blades": 3, "blade_t": 11.0, "beta_deg": 22.0,  # thick open blades, logarithmic spiral
        "b2": 24.0, "shroud_t": 6.0, "eye_r": 40.0,  # DN80 suction eye
        "solids_mm": 32,  # catalogue: passage of solids
        "shaft_d_impeller": 25.0, "shaft_d_bearing": 35.0, "shaft_d_drive": 28.0,
        "wall": 12.0,
    }
    # gap between blades at the outlet must pass the catalogue's 32 mm solids
    d["blade_gap_mm"] = round(math.pi * D2 / d["blades"] * math.sin(math.radians(d["beta_deg"])) - d["blade_t"], 1)
    return d


# ---------------------------------------------------------------------------- geometry helpers


def P(x, y, z):
    return gp_Pnt(float(x), float(y), float(z))


def plane_pt(plane: str, u: float, v: float, w: float = 0.0) -> gp_Pnt:
    if plane == "YZ":
        return P(w, u, v)
    if plane == "XZ":
        return P(u, w, v)
    return P(u, v, w)  # XY


def normal(plane: str) -> gp_Vec:
    return {"YZ": gp_Vec(1, 0, 0), "XZ": gp_Vec(0, 1, 0), "XY": gp_Vec(0, 0, 1)}[plane]


class Profile:
    """Closed 2D outline on a principal plane (built at offset 0), made of lines, arcs and
    splines; extrude() places it at any offset along the plane normal."""

    def __init__(self, plane: str, start: tuple[float, float]):
        self.plane, self.cur, self.start, self.edges, self._face = plane, start, start, [], None

    def _p(self, uv):
        return plane_pt(self.plane, uv[0], uv[1], 0.0)

    def line(self, uv):
        if math.dist(uv, self.cur) > 1e-6:
            self.edges.append(BRepBuilderAPI_MakeEdge(self._p(self.cur), self._p(uv)).Edge())
        self.cur = uv
        return self

    def arc(self, mid, end):
        self.edges.append(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(self._p(self.cur), self._p(mid), self._p(end)).Value()).Edge())
        self.cur = end
        return self

    def spline(self, pts):
        arr = TColgp_Array1OfPnt(1, len(pts) + 1)
        arr.SetValue(1, self._p(self.cur))
        for i, uv in enumerate(pts, 2):
            arr.SetValue(i, self._p(uv))
        # cubic B-spline through the points (end points exact, inner points within 0.01 mm)
        curve = GeomAPI_PointsToBSpline(arr, 3, 8, GeomAbs_C2, 1e-2).Curve()
        self.edges.append(BRepBuilderAPI_MakeEdge(curve).Edge())
        self.cur = pts[-1]
        return self

    def face(self):
        if self._face is None:
            self.line(self.start)
            mw = BRepBuilderAPI_MakeWire()
            for e in self.edges:
                mw.Add(e)
            self._face = BRepBuilderAPI_MakeFace(mw.Wire(), True).Face()
        return self._face

    def extrude(self, w0: float, w1: float):
        """Extrude from offset w0 to w1 along the plane normal."""
        tr = gp_Trsf()
        tr.SetTranslation(normal(self.plane) * w0)
        f = BRepBuilderAPI_Transform(self.face(), tr, True).Shape()
        return BRepPrimAPI_MakePrism(f, normal(self.plane) * (w1 - w0)).Shape()

    def revolve(self, axis: gp_Ax1, deg: float = 360.0):
        return BRepPrimAPI_MakeRevol(self.face(), axis, math.radians(deg)).Shape()


def prof(plane, start):
    return Profile(plane, start)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(P(x0, y0, z0), P(x1, y1, z1)).Shape()


def cyl(r, base, direction, length):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(P(*base), gp_Dir(*direction)), float(r), float(length)).Shape()


def ring_x(r_in, r_out, x0, x1, y=0.0, z=0.0):
    """Ring (tube) along X."""
    return cut(cyl(r_out, (x0, y, z), (1, 0, 0), x1 - x0), cyl(r_in, (x0 - 1, y, z), (1, 0, 0), x1 - x0 + 2))


def clean(s):
    u = ShapeUpgrade_UnifySameDomain(s, True, True, True)
    u.Build()
    return u.Shape()


def fuse(*shapes):
    s = shapes[0]
    for t in shapes[1:]:
        s = BRepAlgoAPI_Fuse(s, t).Shape()
    return clean(s)


def cut(a, *tools):
    for t in tools:
        a = BRepAlgoAPI_Cut(a, t).Shape()
    return clean(a)


def common(a, b):
    return clean(BRepAlgoAPI_Common(a, b).Shape())


def moved(s, dx=0.0, dy=0.0, dz=0.0, rot_axis=None, deg=0.0):
    tr = gp_Trsf()
    if rot_axis is not None:
        tr.SetRotation(rot_axis, math.radians(deg))
    t2 = gp_Trsf()
    t2.SetTranslation(gp_Vec(dx, dy, dz))
    tr = t2.Multiplied(tr)
    return BRepBuilderAPI_Transform(s, tr, True).Shape()


X_AXIS = gp_Ax1(P(0, 0, 0), gp_Dir(1, 0, 0))


def polar_x(shape, n, start_deg=0.0):
    """n copies of `shape` around the X axis, fused."""
    return fuse(*[moved(shape, rot_axis=X_AXIS, deg=start_deg + 360.0 * i / n) for i in range(n)])


def holes_x(r, rho, n, x0, x1, start_deg=0.0):
    """n cylinders along X on a bolt circle of radius rho around the X axis (as cutting tools)."""
    return [cyl(r, (x0, rho * math.cos(math.radians(start_deg + 360 * i / n)),
                    rho * math.sin(math.radians(start_deg + 360 * i / n))), (1, 0, 0), x1 - x0) for i in range(n)]


def rounded_rect(plane, u0, v0, u1, v1, r):
    """Rectangle with corner radius r (u/v ranges on the plane)."""
    c = r * (1 - math.sqrt(0.5))
    p = prof(plane, (u0 + r, v0))
    p.line((u1 - r, v0)).arc((u1 - c, v0 + c), (u1, v0 + r))
    p.line((u1, v1 - r)).arc((u1 - c, v1 - c), (u1 - r, v1))
    p.line((u0 + r, v1)).arc((u0 + c, v1 - c), (u0, v1 - r))
    p.line((u0, v0 + r)).arc((u0 + c, v0 + c), (u0 + r, v0))
    return p


def volume(s):
    gp = GProp_GProps()
    BRepGProp.VolumeProperties_s(s, gp)
    return gp.Mass()


def n_solids(s):
    e, n = TopExp_Explorer(s, TopAbs_SOLID), 0
    while e.More():
        n += 1
        e.Next()
    return n


# ---------------------------------------------------------------------------- parts

D = design()

# casing frame (all in pump coordinates)
W = 12.0  # wall
Y_OUT, Z_BOT, Z_TOP, R_CORNER = 140.0, -150.0, 210.0, 110.0  # outer YZ profile of the body
X_REAR0, X_REAR1 = -21.0, -9.0  # rear wall (motor side)
X_VOL0, X_VOL1 = -9.0, 41.0  # volute compartment (impeller, wear plate)
X_SEP0, X_SEP1 = 41.0, 53.0  # separation wall with the impeller eye
X_FRONT0, X_FRONT1 = 203.0, 215.0  # front wall (suction side)
Z_SHELF0, Z_SHELF1 = 112.0, 124.0  # stepped wall: shelf between suction and separation chambers
X_STEP0, X_STEP1 = 110.0, 122.0  # stepped wall: vertical part up to the top
SUCTION_Z = 110.0  # suction port axis height (front face, "high suction port")
DISCHARGE_X = 40.0  # discharge port axis (top), above the volute
Z_GROUND = -185.0
PRIMING_X = 168.0  # priming port on top of the suction chamber, clear of the discharge flange


def volute_face_points(r3, h_max, n=72):
    """Outer volute spiral: radius grows linearly with angle (constant-velocity volute),
    counter-clockwise seen from the front (+x), tongue at angle 0 (right side, y > 0)."""
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        r = r3 + 4 + (h_max - 4) * i / n
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def make_casing():
    outer = rounded_rect("YZ", -Y_OUT, Z_BOT, Y_OUT, Z_TOP, R_CORNER).extrude(X_REAR0, X_FRONT1)
    inner = rounded_rect("YZ", -Y_OUT + W, Z_BOT + W, Y_OUT - W, Z_TOP - W, R_CORNER - W).extrude(X_REAR1, X_FRONT0)
    span = 400.0
    # suction chamber (front): below the shelf behind the step, full height in front of it
    suc = prof("XZ", (X_SEP1, Z_BOT - 50))
    suc.line((X_FRONT0, Z_BOT - 50)).line((X_FRONT0, Z_TOP + 50)).line((X_STEP1, Z_TOP + 50)).line((X_STEP1, Z_SHELF0)).line((X_SEP1, Z_SHELF0))
    suction_chamber = common(suc.extrude(-span, span), inner)
    # separation chamber (rear top): above the shelf, behind the step; discharge leaves from here
    sep = prof("XZ", (X_VOL0, Z_SHELF1))
    sep.line((X_STEP0, Z_SHELF1)).line((X_STEP0, Z_TOP + 50)).line((X_VOL0, Z_TOP + 50))
    separation_chamber = common(sep.extrude(-span, span), inner)
    # volute: spiral channel around the impeller + tangential duct rising into the separation chamber
    r3, h = D["r3"], D["h_throat"]
    pts = volute_face_points(r3, h)
    v = prof("YZ", pts[0])
    v.spline(pts[1:]).line(pts[0])
    volute = v.extrude(X_VOL0, X_VOL1)
    duct = box(X_VOL0, r3 + 2, 0, X_VOL1, min(r3 + h, Y_OUT - W - 1), Z_SHELF1 + 1)
    eye = cyl(D["eye_r"], (X_SEP0 - 1, 0, 0), (1, 0, 0), X_SEP1 - X_SEP0 + 2)
    body = cut(outer, suction_chamber, separation_chamber, volute, duct, eye)
    # rear hub for the bearing bracket, with seal bore and 4 tapped holes
    hub = cyl(95, (X_REAR0 - 12, 0, 0), (1, 0, 0), 12.5)
    body = fuse(body, hub)
    body = cut(body, cyl(30, (X_REAR0 - 13, 0, 0), (1, 0, 0), X_VOL0 - X_REAR0 + 14),
               *holes_x(5, 80, 4, X_REAR0 - 13, X_REAR0 + 8, 45))
    # wear plate tapped holes in the separation wall
    body = cut(body, *holes_x(3.5, 72, 6, X_SEP0 - 1, X_SEP1 - 2))
    # suction port: neck and DN80 PN16 flange on the front face, bore into the suction chamber
    neck = cyl(55, (X_FRONT1 - 1, 0, SUCTION_Z), (1, 0, 0), 21)
    flange = cyl(100, (X_FRONT1 + 20, 0, SUCTION_Z), (1, 0, 0), 20)
    body = fuse(body, neck, flange)
    bolts = [moved(t, dz=SUCTION_Z) for t in holes_x(9, 80, 8, X_FRONT1 + 19, X_FRONT1 + 41, 22.5)]
    body = cut(body, cyl(40, (X_FRONT0 - 1, 0, SUCTION_Z), (1, 0, 0), X_FRONT1 - X_FRONT0 + 43), *bolts)
    # discharge port on top: square neck and square flange (as in the photos), bore down into the separation chamber
    dneck = box(DISCHARGE_X - 55, -55, Z_TOP - 14, DISCHARGE_X + 55, 55, Z_TOP + 30)
    dflange = box(DISCHARGE_X - 80, -80, Z_TOP + 30, DISCHARGE_X + 80, 80, Z_TOP + 50)
    body = fuse(body, dneck, dflange)
    dholes = [cyl(9, (DISCHARGE_X + sx * 57, sy * 57, Z_TOP + 29), (0, 0, 1), 22) for sx in (-1, 1) for sy in (-1, 1)]
    body = cut(body, cyl(40, (DISCHARGE_X, 0, Z_SHELF1 + 1), (0, 0, 1), Z_TOP + 60 - Z_SHELF1), *dholes)
    # priming port on top of the suction chamber, with a boss for the priming cover
    px = PRIMING_X
    body = fuse(body, cyl(42, (px, 0, Z_TOP - 40), (0, 0, 1), 52))
    body = cut(body, cyl(22, (px, 0, Z_TOP - 60), (0, 0, 1), 80),
               *[cyl(4, (px + 32 * math.cos(math.radians(a)), 32 * math.sin(math.radians(a)), Z_TOP - 5), (0, 0, 1), 20)
                 for a in (45, 135, 225, 315)])
    # inspection opening at the front, below the suction flange
    ins_z, ins_w, ins_h = -65.0, 120.0, 80.0
    body = cut(body, box(X_FRONT0 - 1, -ins_w / 2, ins_z - ins_h / 2, X_FRONT1 + 1, ins_w / 2, ins_z + ins_h / 2),
               *[cyl(4, (X_FRONT1 - 18, y, z), (1, 0, 0), 20) for y, z in inspection_bolts(ins_z)])
    # drain (clean-out) plug boss at the bottom of the suction chamber
    body = fuse(body, cyl(22, (100, 0, Z_BOT - 8), (0, 0, 1), 20))
    body = cut(body, cyl(10, (100, 0, Z_BOT - 10), (0, 0, 1), 30))
    # feet: two cast feet with bolt holes, joined to the rounded bottom by webs
    for x0, x1 in ((0.0, 45.0), (150.0, 195.0)):
        foot = box(x0, -165, Z_GROUND, x1, 165, Z_GROUND + 18)
        web = box(x0 + 8, -110, Z_GROUND + 10, x1 - 8, 110, Z_BOT + 60)
        body = fuse(body, cut(fuse(foot, web), *[cyl(9, ((x0 + x1) / 2, s * 145, Z_GROUND - 1), (0, 0, 1), 20) for s in (-1, 1)]))
    # re-open the cavities the webs may have filled
    return cut(body, common(suc.extrude(-span, span), inner), volute)


def inspection_bolts(zc):
    return [(y, zc + dz) for y in (-75, 0, 75) for dz in (-50, 50)]


IMPELLER_HUB_R = 24.0


def blade_points(n: int = 24):
    """The two sides of one blade in the YZ plane (y, z), from the hub to r2: a logarithmic
    spiral (constant blade angle beta), thickness blade_t. Backward-curved: the impeller turns
    counter-clockwise seen from the front (same sense as the volute), so the blade sweeps
    clockwise (-theta) as the radius grows. Also used by the Taiga goals for the S 80."""
    r2 = D["r2"]
    beta, t = math.radians(D["beta_deg"]), D["blade_t"]
    r1 = IMPELLER_HUB_R + 2
    theta_end = math.log(r2 / r1) / math.tan(beta)
    side_a = [(r1 * math.exp(th * math.tan(beta)), th) for th in [theta_end * i / n for i in range(n + 1)]]
    pts_a = [(r * math.cos(-th), r * math.sin(-th)) for r, th in side_a]
    pts_b = [(r * math.cos(-th + t / r), r * math.sin(-th + t / r)) for r, th in side_a]
    return pts_a, pts_b


def make_impeller():
    r2, rh = D["r2"], IMPELLER_HUB_R
    shroud = cyl(r2, (0, 0, 0), (1, 0, 0), D["shroud_t"])
    hub = cyl(rh, (-6, 0, 0), (1, 0, 0), 6 + D["shroud_t"] + D["b2"])
    dth = D["blade_t"] / r2  # angular thickness at the tip
    pts_a, pts_b = blade_points()
    bl = prof("YZ", pts_a[0])
    bl.spline(pts_a[1:]).line(pts_b[-1]).spline(list(reversed(pts_b[:-1])))
    blade = bl.extrude(D["shroud_t"] - 0.5, D["shroud_t"] + D["b2"])
    blades = polar_x(blade, D["blades"])
    imp = fuse(shroud, hub, blades)
    # trim blades to the impeller diameter, bore and keyway for the shaft
    imp = common(imp, cyl(r2, (-30, 0, 0), (1, 0, 0), 100))
    rs = D["shaft_d_impeller"] / 2
    imp = cut(imp, cyl(rs, (-25, 0, 0), (1, 0, 0), 80), box(-25, -4, rs - 1, 40, 4, rs + 3.3))
    return imp, dth


def make_wear_plate():
    plate = cyl(90, (31, 0, 0), (1, 0, 0), 10)
    return cut(plate, cyl(D["eye_r"], (30, 0, 0), (1, 0, 0), 12), *holes_x(4.5, 72, 6, 30, 42))


def make_inspection_cover():
    z = -65.0
    plate = rounded_rect("YZ", -95, z - 68, 95, z + 68, 30).extrude(X_FRONT1, X_FRONT1 + 12)
    spigot = box(X_FRONT1 - 10, -59, z - 39, X_FRONT1, 59, z + 39)  # locating spigot in the opening
    return cut(fuse(plate, spigot), *[cyl(5.5, (X_FRONT1 - 1, y, zz), (1, 0, 0), 15) for y, zz in inspection_bolts(z)])


def make_priming_cover():
    px = PRIMING_X
    cap = cyl(42, (px, 0, Z_TOP + 12), (0, 0, 1), 12)
    handle = box(px - 30, -6, Z_TOP + 24, px + 30, 6, Z_TOP + 40)
    plug = cyl(21, (px, 0, Z_TOP - 2), (0, 0, 1), 14)
    return cut(fuse(cap, handle, plug),
               *[cyl(4.5, (px + 32 * math.cos(math.radians(a)), 32 * math.sin(math.radians(a)), Z_TOP + 10), (0, 0, 1), 16)
                 for a in (45, 135, 225, 315)])


def make_check_valve():
    """Non-return valve in the suction port: rubber flap with steel weight plate and hinge tab."""
    x0 = X_FRONT0 - 8
    flap = cyl(48, (x0, 0, SUCTION_Z), (1, 0, 0), 6)
    weight = cyl(32, (x0 - 6, 0, SUCTION_Z), (1, 0, 0), 6)
    tab = box(x0, -20, SUCTION_Z + 40, x0 + 6, 20, SUCTION_Z + 75)
    return fuse(flap, weight, tab)


def make_bracket():
    """Bearing bracket: flange to the casing hub, lantern with seal windows, bearing housing, foot."""
    x_f = X_REAR0 - 12.5
    p = prof("XZ", (x_f, 36))
    p.line((x_f, 95)).line((x_f - 14, 95)).line((x_f - 14, 62)).line((-180, 62)).line((-180, 52))
    p.line((-240, 52)).line((-240, 36))
    shell = p.revolve(X_AXIS)
    shell = cut(shell, cyl(50, (-182, 0, 0), (1, 0, 0), x_f - 10 + 182))  # hollow lantern
    windows = [box(-150, -80, -25, -60, 80, 25)]  # seal access windows (both sides)
    shell = cut(shell, *windows, *holes_x(5.5, 80, 4, x_f - 15, x_f + 1, 45))
    foot = fuse(box(-230, -95, Z_GROUND, -150, 95, Z_GROUND + 16), box(-215, -25, Z_GROUND + 10, -165, 25, -50))
    foot = cut(foot, *[cyl(9, (-190, s * 75, Z_GROUND - 1), (0, 0, 1), 20) for s in (-1, 1)])
    return cut(fuse(shell, foot), cyl(36, (-245, 0, 0), (1, 0, 0), 70))  # bearing bores (6207: 72 mm)


def make_shaft():
    ri, rb, rd = D["shaft_d_impeller"] / 2, D["shaft_d_bearing"] / 2, D["shaft_d_drive"] / 2
    p = prof("XZ", (-310, 0))
    p.line((-310, rd)).line((-240, rd)).line((-240, rb)).line((-20, rb)).line((-20, ri)).line((32, ri)).line((32, 0))
    shaft = p.revolve(X_AXIS)
    keys = [box(-300, -4, rd - 4, -250, 4, rd + 1), box(-10, -4, ri - 3, 28, 4, ri + 1)]
    return cut(shaft, *keys)


def make_bearings():
    rb = D["shaft_d_bearing"] / 2
    return fuse(ring_x(rb, 36, -228, -211), ring_x(rb, 36, -197, -180))


def make_seal():
    rb = D["shaft_d_bearing"] / 2
    return fuse(ring_x(rb, 29, X_REAR0 - 2, X_REAR1), ring_x(rb, 23, X_REAR1, X_REAR1 + 2))


PARTS = [
    # name, builder, color, transparency, explode offset
    ("casing", make_casing, [0.30, 0.58, 0.30], 55, [0, 0, 0]),
    ("impeller", lambda: make_impeller()[0], [0.72, 0.55, 0.25], 0, [-60, 0, 0]),
    ("wear_plate", make_wear_plate, [0.72, 0.72, 0.75], 0, [140, 0, 0]),
    ("inspection_cover", make_inspection_cover, [0.30, 0.58, 0.30], 0, [120, 0, 0]),
    ("priming_cover", make_priming_cover, [0.30, 0.58, 0.30], 0, [0, 0, 120]),
    ("check_valve", make_check_valve, [0.12, 0.12, 0.14], 0, [0, 0, 160]),
    ("bearing_bracket", make_bracket, [0.30, 0.58, 0.30], 0, [-160, 0, 0]),
    ("shaft", make_shaft, [0.62, 0.62, 0.66], 0, [-280, 0, 0]),
    ("bearings", make_bearings, [0.80, 0.80, 0.82], 0, [-280, 0, 60]),
    ("mechanical_seal", make_seal, [0.25, 0.25, 0.25], 0, [-100, 0, 60]),
]


def write_step(shape, path: Path):
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    if w.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"STEP export failed: {path}")


def triangles(shape, tol=1.0):
    BRepMesh_IncrementalMesh(shape, tol, False, 0.5, True)
    out = []
    e = TopExp_Explorer(shape, TopAbs_FACE)
    while e.More():
        f = TopoDS.Face_s(e.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(f, loc)
        if tri is not None:
            tr = loc.Transformation()
            nodes = [tri.Node(i).Transformed(tr) for i in range(1, tri.NbNodes() + 1)]
            rev = f.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                if rev:
                    b, c = c, b
                out.append([(nodes[k - 1].X(), nodes[k - 1].Y(), nodes[k - 1].Z()) for k in (a, b, c)])
        e.Next()
    return out


def preview(shapes, path, title, cut_y=False, elev=20, azim=-50):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    import numpy as np
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection="3d")
    allp = []
    light = np.array([0.4, -0.5, 0.75])
    light = light / np.linalg.norm(light)
    for name, s, color, transp in shapes:
        if cut_y:
            s = common(s, box(-1000, -1000, -1000, 1000, 0, 1000))
        tris = triangles(s)
        if not tris:
            continue
        arr = np.array(tris)
        nrm = np.cross(arr[:, 1] - arr[:, 0], arr[:, 2] - arr[:, 0])
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
        shade = 0.45 + 0.55 * np.clip(nrm @ light, 0, 1)
        cols = np.clip(np.array(color)[None, :] * shade[:, None], 0, 1)
        alpha = 1 - (0 if cut_y else transp) / 100 * 0.8
        ax.add_collection3d(Poly3DCollection(arr, facecolors=np.c_[cols, np.full(len(cols), alpha)], linewidths=0))
        allp.append(arr.reshape(-1, 3))
    pts = np.vstack(allp)
    lo, hi = pts.min(0), pts.max(0)
    c, r = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev, azim)
    ax.set_axis_off()
    ax.set_title(title, fontsize=11)
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close(fig)


def section(shapes, plane: str, w: float, path, title):
    """2D cross-section of all parts on a principal plane at offset w (filled, one color per part)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    big = 2000.0
    if plane == "XZ":
        cutter = BRepBuilderAPI_MakeFace(gp_Pln(P(0, w, 0), gp_Dir(0, 1, 0)), -big, big, -big, big).Face()
        uv = lambda p: (p[0], p[2])
        labels = ("x (mm)", "z (mm)")
    else:  # YZ
        cutter = BRepBuilderAPI_MakeFace(gp_Pln(P(w, 0, 0), gp_Dir(1, 0, 0)), -big, big, -big, big).Face()
        uv = lambda p: (p[1], p[2])
        labels = ("y (mm)", "z (mm)")
    fig, ax = plt.subplots(figsize=(10, 8))
    handles = []
    for name, s, color, _ in shapes:
        cs = BRepAlgoAPI_Common(s, cutter).Shape()
        tris = [[uv(p) for p in t] for t in triangles(cs, 0.3)]
        if tris:
            ax.add_collection(PolyCollection(tris, facecolors=[color], edgecolors=[color], linewidths=0.2))
            handles.append(plt.Rectangle((0, 0), 1, 1, color=color, label=name))
    ax.autoscale()
    ax.set_aspect("equal")
    ax.set_xlabel(labels[0])
    ax.set_ylabel(labels[1])
    ax.grid(alpha=0.3)
    ax.legend(handles=handles, loc="upper left", fontsize=8, framealpha=0.85)
    ax.set_title(title, fontsize=11)
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="parts/pump_s80", help="output folder")
    ap.add_argument("--cutaway", action="store_true", help="also write casing_cutaway.step (casing cut at y = 0)")
    ap.add_argument("--preview", action="store_true", help="write a 3D view and three cross-sections as PNG (needs matplotlib)")
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    print("Design (from catalogue data + Stepanoff rules):")
    for k in ("Q_m3h", "H_m", "n_rpm", "u2_ms", "D2", "r3", "b3", "A_throat_mm2", "h_throat", "blades", "blade_gap_mm", "solids_mm"):
        print(f"  {k:14s} {D[k]}")
    if D["blade_gap_mm"] < D["solids_mm"]:
        print("  WARNING: blade gap smaller than the catalogue's passage of solids")

    built, spec = [], {"name": "pump_s80", "parts": []}
    for name, fn, color, transp, explode in PARTS:
        s = fn()
        ok = BRepCheck_Analyzer(s).IsValid()
        print(f"  {name:18s} volume {volume(s) / 1000:9.1f} cm3  solids {n_solids(s)}  valid {ok}")
        write_step(s, out / f"{name}.step")
        built.append((name, s, color, transp))
        spec["parts"].append({"label": name, "part": name, "rotations": [], "translation": [0, 0, 0],
                              "color": color, "transparency": transp, "explode": explode})
    (out / "pump_s80.json").write_text(json.dumps(spec, indent=2))
    print(f"wrote {len(built)} STEP files and pump_s80.json to {out}")
    if args.cutaway:
        cas = dict((n, s) for n, s, *_ in built)["casing"]
        write_step(common(cas, box(-1000, -1000, -1000, 1000, 0, 1000)), out / "casing_cutaway.step")
        print("wrote casing_cutaway.step")
    if args.preview:
        preview(built, out / "preview.png", "Victor S 80 pump end (parametric model)")
        section(built, "XZ", 0.0, out / "section_side.png", "Side section through the shaft axis (y = 0)")
        section(built, "YZ", 18.0, out / "section_volute.png", "Section through impeller and volute (x = 18, seen from the front)")
        section(built, "YZ", 150.0, out / "section_front.png", "Section through the suction chamber (x = 150)")
        print("wrote preview.png, section_side.png, section_volute.png, section_front.png")


if __name__ == "__main__":
    main()
