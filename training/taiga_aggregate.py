#!/usr/bin/env python3
"""taiga_aggregate.py — distribution of results across training runs.
Version: 2026.10.04.3

Summarizes a group of runs (runs/<exp>/seed*/) as a distribution instead of a
best pick: per suite mean, standard deviation, min and max over runs, for the
clean and the perturbed evaluation, plus per-run training diagnostics (final
validation accuracy/NLL, how much the last SFT epoch still improved, DAgger
rollout success) to help find the cause of run-to-run spread.

Usage (from the upstream repo, with its venv):
    python taiga_aggregate.py runs/e8                 # one group: full report
    python taiga_aggregate.py runs runs/e8 runs/x2    # several groups: side-by-side means ± sd
Only seed*/ subfolders with an eval.json are used. Writes <group>/sweep_summary.json
for single-group reports.

Reading the numbers:
  - Clean evaluation uses the same goals for every run (fixed episode seeds) and
    argmax decisions, so its spread across runs is purely model-to-model
    variance: it is not evaluation noise.
  - Perturbed evaluation also depends on the injected random actions, so part of
    its spread is evaluation noise; "noise sd" is the binomial reference
    sqrt(p(1-p)/episodes) for comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics as st
import sys
from pathlib import Path

KEY_SUITES = ["comp-L3", "comp2-L3", "comp3-L3", "len3-L6", "len4-L7", "len5-L8", "len6-L9"]
VAL_RE = re.compile(r"^\[(\w+)\] epoch (\d+) val acc ([\d.]+) nll ([\d.]+)")
DAGGER_RE = re.compile(r"^\[dagger (\d+)\] beta [\d.]+ rollout success (\{.*\}) \+(\d+) labeled")
LOADED_RE = re.compile(r"^loaded (\d+) examples \((\d+) train / (\d+) val\)")


def suite_order(name: str) -> tuple:
    m = re.match(r"([a-z]+)(\d*)-L(\d+)", name)
    return (int(m.group(3)), m.group(1), m.group(2)) if m else (99, name, "")


def load_eval(path: Path) -> dict:
    if not path.is_file():
        return {"step_acc": None, "episodes": {}, "missing": True}
    r = json.loads(path.read_text())
    eps = {k: v for k, v in r.get("episodes", {}).items() if isinstance(v, dict)}
    return {"step_acc": r.get("per_step", {}).get("acc"), "episodes": eps}


def parse_train_log(path: Path) -> dict:
    """Last training session in the log: validation curve and DAgger rollouts."""
    if not path.is_file():
        return {}
    lines = path.read_text(errors="replace").splitlines()
    starts = [i for i, line in enumerate(lines) if LOADED_RE.match(line)]
    lines = lines[starts[-1]:] if starts else lines
    out: dict = {"val": [], "dagger": []}
    for line in lines:
        if m := LOADED_RE.match(line):
            out["n_train"], out["n_val"] = int(m.group(2)), int(m.group(3))
        elif m := VAL_RE.match(line):
            out["val"].append({"tag": m.group(1), "epoch": int(m.group(2)),
                               "acc": float(m.group(3)), "nll": float(m.group(4))})
        elif m := DAGGER_RE.match(line):
            try:
                succ = json.loads(m.group(2))
            except json.JSONDecodeError:
                succ = {}
            out["dagger"].append({"round": int(m.group(1)), "overall": succ.get("overall_success"),
                                  "added": int(m.group(3))})
    sft = [v for v in out["val"] if v["tag"] == "sft"]
    if sft:
        out["sft_final_nll"] = sft[-1]["nll"]
        out["sft_final_acc"] = sft[-1]["acc"]
        # relative NLL drop in the last SFT epoch: large -> training had not converged
        if len(sft) >= 2 and sft[-2]["nll"] > 0:
            out["sft_last_epoch_drop"] = (sft[-2]["nll"] - sft[-1]["nll"]) / sft[-2]["nll"]
    if out["val"]:
        out["final_nll"], out["final_acc"] = out["val"][-1]["nll"], out["val"][-1]["acc"]
    return out


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def load_run(d: Path) -> dict:
    manifest = json.loads((d / "manifest.json").read_text()) if (d / "manifest.json").is_file() else {}
    return {"name": d.name, "clean": load_eval(d / "eval.json"), "perturb": load_eval(d / "eval_perturb.json"),
            "train": parse_train_log(d / "train.log"), "manifest": manifest, "ckpt": sha256(d / "last.pt"),
            "crashes": {ph: sum(1 for _ in (d / f"crashes_{ph}.jsonl").open()) if (d / f"crashes_{ph}.jsonl").is_file()
                        else None for ph in ("train", "eval", "eval_perturb", "calib")}}


def load_group(g: Path) -> list[dict]:
    runs = [load_run(d) for d in sorted(g.glob("seed*")) if (d / "eval.json").is_file()]
    return sorted(runs, key=lambda r: int(re.sub(r"\D", "", r["name"]) or 0))


def dist(values: list[float]) -> dict:
    values = [v for v in values if v is not None]
    if not values:
        return {}
    return {"n": len(values), "mean": st.fmean(values), "sd": st.stdev(values) if len(values) > 1 else 0.0,
            "min": min(values), "max": max(values)}


def suite_stats(runs: list[dict], kind: str, metric: str) -> dict:
    suites = sorted({s for r in runs for s in r[kind].get("episodes", {})}, key=suite_order)
    out = {}
    for s in suites:
        vals = [r[kind]["episodes"][s][metric] for r in runs if s in r[kind].get("episodes", {})]
        eps = [r[kind]["episodes"][s].get("episodes", 100) for r in runs if s in r[kind].get("episodes", {})]
        d = dist(vals)
        if d:
            n_ep = round(st.fmean(eps))
            d["noise_sd"] = math.sqrt(d["mean"] * (1 - d["mean"]) / n_ep) if n_ep else None
            d["episodes"] = n_ep
        out[s] = d
    return out


def fmt(x, nd=2) -> str:
    return "  -  " if x is None else f"{x:.{nd}f}"


def report_group(g: Path, runs: list[dict]) -> dict:
    summary = {"group": str(g), "runs": [r["name"] for r in runs], "clean": {}, "perturb": {}}
    print(f"\n=== {g}  ({len(runs)} runs: {', '.join(r['name'] for r in runs)})")
    incomplete = [r["name"] for r in runs if r["perturb"].get("missing")]
    if incomplete:
        print("incomplete runs (no eval_perturb.json, shown as '-'):", ", ".join(incomplete))
    m0 = runs[0]["manifest"] if runs else {}
    if m0:
        knobs = {k: m0.get(k) for k in ("exp", "data_seed", "data_scale", "epochs", "dagger_rounds", "dagger_episodes",
                                        "dagger_epochs", "deterministic", "freecad", "torch", "gpu_name") if k in m0}
        print("settings (first run):", json.dumps(knobs))
    for kind, label in (("clean", "CLEAN (same goals for every run: spread = model-to-model variance)"),
                        ("perturb", "PERTURBED, 20% random actions (spread includes evaluation noise)")):
        for metric, mlabel in (("success", "success"), ("zero_deviation_success", "zero-deviation success")):
            stats = suite_stats(runs, kind, metric)
            summary[kind][metric] = stats
            if not stats:
                continue
            print(f"\n{label} — {mlabel}")
            print(f"  {'suite':10s} {'mean':>6s} {'sd':>6s} {'min':>6s} {'max':>6s}" +
                  (f" {'noise sd':>9s}" if kind == "perturb" else "") + "   per run")
            for s, d in stats.items():
                per = " ".join(fmt(r[kind]["episodes"].get(s, {}).get(metric)) for r in runs)
                extra = f" {fmt(d.get('noise_sd')):>9s}" if kind == "perturb" else ""
                print(f"  {s:10s} {fmt(d['mean']):>6s} {fmt(d['sd']):>6s} {fmt(d['min']):>6s} {fmt(d['max']):>6s}"
                      f"{extra}   {per}")
    print("\nPer-run diagnostics")
    print(f"  {'run':8s} {'ckpt':12s} {'step_acc':>8s} {'train ex':>9s} {'sft acc':>8s} {'sft nll':>8s} "
          f"{'last-ep drop':>12s} {'final nll':>9s} {'dagger succ':>12s} {'crashes t/e/p':>14s}")
    diag = []
    for r in runs:
        t = r["train"]
        dag = "/".join(fmt(d["overall"]) for d in t.get("dagger", [])) or "-"
        drop = t.get("sft_last_epoch_drop")
        print(f"  {r['name']:8s} {r['ckpt'] or '-':12s} {fmt(r['clean'].get('step_acc'), 4):>8s} "
              f"{t.get('n_train', '-')!s:>9s} {fmt(t.get('sft_final_acc'), 4):>8s} {fmt(t.get('sft_final_nll'), 4):>8s} "
              f"{(f'{100 * drop:.1f}%' if drop is not None else '-'):>12s} {fmt(t.get('final_nll'), 4):>9s} {dag:>12s} "
              f"{'/'.join('-' if r['crashes'][k] is None else str(r['crashes'][k]) for k in ('train', 'eval', 'eval_perturb')):>14s}")
        diag.append({"run": r["name"], "ckpt_sha256_12": r["ckpt"], "step_acc": r["clean"].get("step_acc"),
                     "worker_crashes": r["crashes"],
                     **{k: v for k, v in t.items() if k not in ("val",)}, "val_curve": t.get("val", [])})
    summary["diagnostics"] = diag
    same = [r["ckpt"] for r in runs if r["ckpt"]]
    if len(same) > 1 and len(set(same)) == 1:
        print("\nAll checkpoints are identical (same sha256): these runs produced the same model.")
    print("\nCrashes: FreeCAD worker crashes recovered by taiga_run.py (train/eval/perturbed eval; '-' = not"
          "\nrecorded). Each crashed episode counts as a failure in the results above.")
    print("\nHints: a large last-epoch NLL drop means training had not converged (try more EPOCHS); spread that"
          "\nshrinks with DATA_SCALE points to too little data; compare a fixed-data sweep (DATA_SEED set) with a"
          "\nfresh-data sweep to separate optimization variance from data-sampling variance.")
    return summary


def report_compare(groups: list[tuple[Path, list[dict]]]) -> None:
    print("\n=== Comparison: clean success, mean ± sd over runs (n)")
    names = [str(g) for g, _ in groups]
    w = max(14, *(len(n) for n in names))
    print(f"  {'suite':10s} " + " ".join(f"{n:>{w}s}" for n in names))
    stats = [suite_stats(runs, "clean", "success") for _, runs in groups]
    suites = sorted({s for st_ in stats for s in st_}, key=suite_order)
    for s in suites:
        cells = []
        for st_ in stats:
            d = st_.get(s)
            cells.append(f"{d['mean']:.2f} ± {d['sd']:.2f} ({d['n']})" if d else "-")
        print(f"  {s:10s} " + " ".join(f"{c:>{w}s}" for c in cells))
    print("\n=== Comparison: perturbed success, mean ± sd over runs (n)")
    stats = [suite_stats(runs, "perturb", "success") for _, runs in groups]
    for s in [s for s in suites if any(s in st_ for st_ in stats)]:
        cells = []
        for st_ in stats:
            d = st_.get(s)
            cells.append(f"{d['mean']:.2f} ± {d['sd']:.2f} ({d['n']})" if d else "-")
        print(f"  {s:10s} " + " ".join(f"{c:>{w}s}" for c in cells))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("groups", nargs="+", type=Path, help="folders containing seed*/ run folders")
    ap.add_argument("--no-write", action="store_true", help="don't write sweep_summary.json")
    args = ap.parse_args()
    loaded = []
    for g in args.groups:
        runs = load_group(g)
        if not runs:
            print(f"warning: no seed*/eval.json under {g}", file=sys.stderr)
            continue
        loaded.append((g, runs))
    if not loaded:
        sys.exit("no runs found")
    if len(loaded) == 1:
        g, runs = loaded[0]
        summary = report_group(g, runs)
        if not args.no_write:
            (g / "sweep_summary.json").write_text(json.dumps(summary, indent=2))
            print(f"\nwritten: {g / 'sweep_summary.json'}")
    else:
        report_compare(loaded)


if __name__ == "__main__":
    main()
