"""Minimal goals around the S 80 casing's first flange pad, to find what v3 (patch 0020) cannot build.
Version: 2026.10.18.1

v3 builds the pad (datum boss_box on the XZ plane, off -132.5, sticking out of the body on one side
only), then undoes it (2026-10-18). These goals separate the possible causes:
  core3        the casing core's first 3 features (outline base, pad, pattern), exactly
  core2        the same without the pattern
  box_one      box base, the same pad sticking out on one side only
  box_one_pat  ... and its linear pattern along Z
  box_both     box base, a pad through the part, sticking out on both sides (as in training)
  box_both_pat ... and its linear pattern
  small_one_pat  box_one_pat at 1/5 scale (part sizes of the training goals)
Usage: python3 make_repro_datum_pad.py <s80_goals.json> <out.json>
"""
import copy
import json
import sys

src = json.load(open(sys.argv[1]))["s80_casing_core"]["features"]
goals = {"core3": {"features": copy.deepcopy(src[:3])}, "core2": {"features": copy.deepcopy(src[:2])}}


def box(s=1.0):
    return {"kind": "base_box", "params": {"w": 280 * s, "d": 360 * s, "h": 236 * s}}


def pad(off, h, s=1.0):
    return {"kind": "boss_box", "params": {"nx": 0.0, "ny": 1.0, "nz": 0.0, "x": 0.0, "y": off * s, "z": 43.5 * s,
                                           "w": 220 * s, "d": 29 * s, "h": h * s, "off": off * s}}


def pat(s=1.0):
    return {"kind": "linear_pattern", "params": {"n": 2.0, "length": 150 * s, "ax": 0.0, "ay": 0.0, "az": 1.0}}


one = (-162.5, 85.0)   # y -205 .. -120: 25 mm out of the -Y face (y = -180), the rest inside
both = (0.0, 410.0)    # y -205 .. 205: through the part, 25 mm out on both sides
goals["box_one"] = {"features": [box(), pad(*one)]}
goals["box_one_pat"] = {"features": [box(), pad(*one), pat()]}
goals["box_both"] = {"features": [box(), pad(*both)]}
goals["box_both_pat"] = {"features": [box(), pad(*both), pat()]}
goals["small_one_pat"] = {"features": [box(0.2), pad(*one, s=0.2), pat(0.2)]}
for g in goals.values():
    for f in g["features"]:
        f["params"] = {k: round(v, 4) if isinstance(v, float) else v for k, v in f["params"].items()}
json.dump(goals, open(sys.argv[2], "w"), indent=1)
print(f"wrote {len(goals)} goals to {sys.argv[2]}")
