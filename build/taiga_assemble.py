#!/usr/bin/env python3
"""taiga_assemble.py — put separately built parts together into one assembly document.
Version: 2026.10.05.2

Taiga-S1 builds one part per goal (one .FCStd each). This script places those parts
according to an assembly spec (rotations + translation per part, color, transparency),
and writes a single FreeCAD document plus a combined STEP file. It runs in FreeCAD's
own Python (no model, no GPU):

    source ~/taiga/freecad.env
    "$FREECAD_PYTHON" <repo>/build/taiga_assemble.py --parts parts/engine \\
        --spec <repo>/build/engine/assembly_engine.json
    "$FREECAD_PYTHON" <repo>/build/taiga_assemble.py --parts parts/engine \\
        --spec <repo>/build/engine/assembly_engine.json --explode 1      # exploded view

Each part is read from <parts>/<part>.FCStd (the Body's final shape) or, if that is
missing, <parts>/<part>.step. Writes <parts>/<name>.FCStd and <name>.step (with
--explode: <name>_exploded.*). Parts that are missing are reported and skipped.
The parts are fixed copies of the shapes (Part::Feature), not links: rebuild the parts,
then rerun this script to refresh the assembly.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

import FreeCAD as App
import Part


def load_shape(parts: Path, name: str):
    fcstd, step = parts / f"{name}.FCStd", parts / f"{name}.step"
    if fcstd.is_file():
        try:
            doc = App.openDocument(str(fcstd), hidden=True)
        except TypeError:  # older FreeCAD without the hidden argument
            doc = App.openDocument(str(fcstd))
        try:
            bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]
            shape = bodies[0].Shape.copy() if bodies and not bodies[0].Shape.isNull() else None
        finally:
            App.closeDocument(doc.Name)
        if shape is not None:
            return shape, fcstd.name
    if step.is_file():
        return Part.read(str(step)), step.name
    return None, None


def placement(entry: dict, explode: float) -> App.Placement:
    rot = App.Rotation()
    for axis, deg in entry.get("rotations", []):
        rot = App.Rotation(App.Vector(*axis), deg).multiply(rot)  # apply in the listed order
    t = App.Vector(*entry.get("translation", [0, 0, 0]))
    if explode:
        t = t + App.Vector(*entry.get("explode", [0, 0, 0])) * explode
    return App.Placement(t, rot)


def packed_color(rgb) -> int:
    r, g, b = (max(0, min(255, round(c * 255))) for c in rgb[:3])
    return (r << 24) | (g << 16) | (b << 8)


def add_gui_document(fcstd: Path, styles: dict[str, tuple[list[float], int]]) -> None:
    """Headless FreeCAD writes no view data, so FreeCAD would open the file with
    everything hidden and grey. Add a minimal GuiDocument.xml: visible, colored."""
    with zipfile.ZipFile(fcstd) as z:
        members = [(i, z.read(i.filename)) for i in z.infolist() if i.filename != "GuiDocument.xml"]
        doc = z.read("Document.xml").decode("utf-8")
    names = re.findall(r'<Object name="([^"]+)"', doc)
    vps = []
    for name in dict.fromkeys(names):
        color, transp = styles.get(name, ([0.8, 0.8, 0.8], 0))
        vps.append(
            f'        <ViewProvider name={quoteattr(name)} expanded="0">\n'
            f'            <Properties Count="3" TransientCount="0">\n'
            f'                <Property name="Visibility" type="App::PropertyBool">\n'
            f'                    <Bool value="{"true" if name in styles else "false"}"/>\n'
            f'                </Property>\n'
            f'                <Property name="ShapeColor" type="App::PropertyColor">\n'
            f'                    <PropertyColor value="{packed_color(color)}"/>\n'
            f'                </Property>\n'
            f'                <Property name="Transparency" type="App::PropertyPercent">\n'
            f'                    <Integer value="{int(transp)}"/>\n'
            f'                </Property>\n'
            f'            </Properties>\n'
            f'        </ViewProvider>\n')
    gui = ("<?xml version='1.0' encoding='utf-8'?>\n<Document SchemaVersion=\"1\">\n"
           f'    <ViewProviderData Count="{len(vps)}">\n{"".join(vps)}    </ViewProviderData>\n'
           '    <Camera settings=""/>\n</Document>\n')
    tmp = fcstd.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in members:
            z.writestr(info, data)
        z.writestr("GuiDocument.xml", gui)
    tmp.replace(fcstd)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parts", required=True, help="folder with the built parts (<part>.FCStd or .step)")
    ap.add_argument("--spec", required=True, help="assembly spec JSON (e.g. engine/assembly_engine.json)")
    ap.add_argument("--out", help="output .FCStd (default: <parts>/<spec name>[_exploded].FCStd)")
    ap.add_argument("--explode", type=float, default=0.0, help="exploded view: scale of each part's explode offset")
    ap.add_argument("--no-step", action="store_true", help="don't write the combined STEP file")
    args = ap.parse_args()

    parts = Path(args.parts).expanduser().resolve()
    spec = json.loads(Path(args.spec).expanduser().read_text())
    name = spec.get("name", "assembly") + ("_exploded" if args.explode else "")
    out = Path(args.out).expanduser().resolve() if args.out else parts / f"{name}.FCStd"

    doc = App.newDocument(re.sub(r"\W", "_", name))
    styles, placed, missing = {}, [], []
    for entry in spec["parts"]:
        shape, src = load_shape(parts, entry["part"])
        if shape is None:
            missing.append(f"{entry['label']} ({entry['part']})")
            continue
        shape.Placement = placement(entry, args.explode).multiply(shape.Placement)
        obj = doc.addObject("Part::Feature", re.sub(r"\W", "_", entry["label"]))
        obj.Label = entry["label"]
        obj.Shape = shape
        styles[obj.Name] = (entry.get("color", [0.8, 0.8, 0.8]), entry.get("transparency", 0))
        placed.append(obj)
        print(f"  {entry['label']:22s} <- {src}")
    doc.recompute()
    if not placed:
        sys.exit(f"error: none of the parts were found in {parts}")
    doc.saveAs(str(out))
    add_gui_document(out, styles)
    msg = f"assembly: {len(placed)} parts -> {out}"
    if not args.no_step:
        step = out.with_suffix(".step")
        Part.makeCompound([o.Shape for o in placed]).exportStep(str(step))
        msg += f", {step.name}"
    print(msg)
    if missing:
        print("missing (not built yet or not found):", ", ".join(missing))
    bb = Part.makeCompound([o.Shape for o in placed]).BoundBox
    print(f"overall size: {bb.XLength:.0f} x {bb.YLength:.0f} x {bb.ZLength:.0f} mm")


if __name__ == "__main__":
    main()
