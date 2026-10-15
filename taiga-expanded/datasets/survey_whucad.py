#!/usr/bin/env python3
"""survey_whucad.py — which CAD features real parametric parts use, and how often (vocabulary
survey for the production plan, section 4.4).
Version: 2026.10.15.2

WHUCAD (Fan et al., Integrated Computer-Aided Engineering 32(1), 2025) stores CAD models as
command sequences in DeepCAD's vector format, extended to 18 feature types (extrude, revolve,
groove, pocket, fillet, chamfer, shell, draft, hole, mirror, ...). It has two subsets:
  0000-0099  DeepCAD models with advanced features inserted at random (synthetic)
  0100-0103  4,244 models rebuilt by hand in CATIA from ABC parts (real feature usage)
The second subset is what this survey is for: which features real parts need, how often, in
what combinations and how many per part. The first is reported separately for comparison.

    git clone https://www.gitlink.org.cn/fzhe/WHUCAD.git ~/WHUCAD
    python3 survey_whucad.py --repo ~/WHUCAD [--out whucad_survey.json]

Needs numpy and h5py. Command names come from the repository's own cadlib/macro.py
(ALL_COMMANDS); without it, command indices are reported.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

# sketch-level commands (everything else counts as a feature operation)
SKETCH = {"line", "arc", "circle", "spline", "ellipse", "sol", "eos", "point", "topo", "sketch", "profile"}
# topology selection commands (which edges / faces a feature acts on), not features themselves
SELECTION = ("select", "included", "mirrorstart")
# what the Taiga goals express today, and candidate additions in order (cumulative coverage)
COVERAGE_STEPS = [
    ("today (extrude, pocket, revolve, groove, mirror)", {"Ext", "Pocket", "Rev", "Groove", "Mirror"}),
    ("+ fillet / chamfer on chosen edges", {"Fillet", "Chamfer"}),
    ("+ shell", {"Shell"}),
    ("+ sketch on any plane (SCP)", {"SCP"}),
    ("+ draft", {"Draft"}),
]


def is_feature(c: str) -> bool:
    low = c.lower()
    return low not in SKETCH and not any(k in low for k in SELECTION)


def command_names(repo: Path) -> list[str] | None:
    sys.path.insert(0, str(repo))
    try:
        from cadlib import macro  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"note: could not import cadlib.macro from {repo} ({exc}); reporting command indices", file=sys.stderr)
        return None
    for attr in ("ALL_COMMANDS", "ALL_COMMAND", "COMMANDS"):
        if hasattr(macro, attr):
            return [str(c) for c in getattr(macro, attr)]
    print("note: cadlib.macro has no ALL_COMMANDS; reporting command indices", file=sys.stderr)
    return None


def read_vec(path: Path):
    import h5py

    with h5py.File(path, "r") as fh:
        key = "vec" if "vec" in fh else next(iter(fh.keys()))
        return np.asarray(fh[key][:])


def survey(files: list[Path], names: list[str] | None) -> dict:
    def name(i: int) -> str:
        return names[i] if names and 0 <= i < len(names) else f"cmd{i}"

    total = collections.Counter()  # command uses
    models_with = collections.Counter()  # models using the command at least once
    n_feat = []
    combos = collections.Counter()  # set of feature kinds per model
    bad = 0
    for f in files:
        try:
            vec = read_vec(f)
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        cmds = [name(int(c)) for c in vec[:, 0] if int(c) >= 0]
        total.update(cmds)
        models_with.update(set(cmds))
        feats = [c for c in cmds if is_feature(c)]
        n_feat.append(len(feats))
        combos[tuple(sorted(set(feats)))] += 1
    n = len(files) - bad
    lengths = np.array(n_feat) if n_feat else np.zeros(1)
    vocab, coverage = set(), []
    for label, add in COVERAGE_STEPS:
        vocab |= add
        covered = sum(v for k, v in combos.items() if set(k) <= vocab)
        coverage.append({"vocabulary": label, "models": covered, "share": round(covered / max(n, 1), 4)})
    other = collections.Counter()
    for k, v in combos.items():
        for c in set(k) - vocab:
            other[c] += v
    return {
        "coverage": coverage, "beyond_all_steps": dict(other.most_common()),
        "models": n, "unreadable": bad,
        "features_per_model": {"median": float(np.median(lengths)), "p90": float(np.percentile(lengths, 90)),
                               "max": int(lengths.max())},
        "commands": {c: {"uses": total[c], "models": models_with[c], "share": round(models_with[c] / max(n, 1), 4)}
                     for c, _ in models_with.most_common()},
        "top_combinations": [{"features": list(k), "models": v} for k, v in combos.most_common(25)],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="WHUCAD clone (with data/vec and cadlib)")
    ap.add_argument("--out", default="whucad_survey.json")
    args = ap.parse_args()
    repo = Path(args.repo).expanduser().resolve()
    vec_dir = repo / "data" / "vec"
    if not vec_dir.is_dir():
        sys.exit(f"error: {vec_dir} not found")
    names = command_names(repo)
    groups = {"manual (0100-0103, real parts)": [], "augmented (0000-0099, synthetic)": []}
    for d in sorted(p for p in vec_dir.iterdir() if p.is_dir()):
        key = "manual (0100-0103, real parts)" if d.name >= "0100" else "augmented (0000-0099, synthetic)"
        groups[key] += sorted(d.glob("*.h5"))
    result = {"commands_known": names, "groups": {}}
    for g, files in groups.items():
        r = survey(files, names)
        result["groups"][g] = r
        print(f"\n== {g}: {r['models']} models ({r['unreadable']} unreadable); features per model: "
              f"median {r['features_per_model']['median']:.0f}, 90th percentile {r['features_per_model']['p90']:.0f}, "
              f"max {r['features_per_model']['max']}")
        print(f"{'command':16s} {'models':>8s} {'share':>7s} {'uses':>8s}")
        for c, v in r["commands"].items():
            print(f"{c:16s} {v['models']:8d} {v['share']:7.1%} {v['uses']:8d}")
        print("parts whose every feature is expressible, as the vocabulary grows:")
        for row in r["coverage"]:
            print(f"  {row['share']:6.1%}  ({row['models']:6d})  {row['vocabulary']}")
        if r["beyond_all_steps"]:
            print("  still beyond: " + ", ".join(f"{k} ({v})" for k, v in r["beyond_all_steps"].items()))
        print("most common feature combinations:")
        for row in r["top_combinations"][:10]:
            print(f"  {row['models']:6d}  {' + '.join(row['features']) or '(none)'}")
    Path(args.out).write_text(json.dumps(result, indent=1) + "\n")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
