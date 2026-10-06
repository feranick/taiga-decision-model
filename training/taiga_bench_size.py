#!/usr/bin/env python3
"""taiga_bench_size.py — how fast is one Taiga-S1 decision at larger model sizes?
Version: 2026.10.06.1

Builds the network at several sizes with random weights (no training, no FreeCAD) and times
one decision (scoring all valid commands for one state) on the CPU and, if present, the GPU.
States are synthetic but realistic in size: a feature tree with a sketch and a feature per goal
intent, a goal from the training sampler (taiga-expanded's when its patches are applied), and
the commands the state allows. Run with the venv of a set-up work folder:

    cd ~/taiga-expanded/taiga-s1
    .venv/bin/python <repo>/training/taiga_bench_size.py                 # default sizes, CPU + GPU
    .venv/bin/python <repo>/training/taiga_bench_size.py --features 5 20 50 --threads 1 8

Prints, per size: parameters, tokens per state, and the median time per decision for one state
(batch 1, an interactive build) and for a batch (--batch, like DAgger rollouts and evaluation,
where all FreeCAD workers' states are scored together), per device and CPU thread count.
"""
from __future__ import annotations

import argparse
import random
import statistics
import time

import torch

from freecad_s1.actions import enumerate_actions
from freecad_s1.goals import sample_split_goal
from freecad_s1.model.featurize import collate, make_example
from freecad_s1.model.net import S1Config, S1Model
from freecad_s1.schema import Node, SelItem, ShapeInfo, State

# name: (width, heads, encoder layers, decoder layers, feed-forward)
SIZES = {
    "1.2M": (128, 4, 3, 2, 384),  # the current model
    "7M": (256, 8, 4, 3, 1024),
    "22M": (384, 8, 6, 4, 1536),
    "54M": (512, 8, 8, 6, 2048),
    "121M": (768, 12, 8, 6, 3072),
}
# the generalization options of the training scripts (taiga_repro_*.sh)
TRAIN_OPTS = dict(pos_mode="rand", ordinal=True, invariant_numerics=True, modular=True, pointer="done",
                  index_eval="identity", type_dropout=0.15)


def synthetic_state(goal, built: int) -> State:
    """Body + one sketch and one solid feature per built intent, a face selected."""
    tree = [Node("Body", "PartDesign::Body", num={"active_body": 1.0, "valid": 1.0, "visible": 1.0})]
    for i in range(built):
        tree.append(Node(f"Sketch{i}", "Sketcher::SketchObject", parent=0, depth=1,
                         num={"valid": 1.0, "consumed": 1.0, "n_geo": 4.0, "n_constraints": 6.0, "closed": 1.0,
                              "fully_constrained": 1.0, "support_nz": 1.0},
                         geo={"line": 4}, cons={"coincident": 4, "distance_x": 1, "distance_y": 1}))
        tree.append(Node(f"Feature{i}", "PartDesign::Pad", parent=0, depth=1,
                         num={"valid": 1.0, "visible": 1.0, "tip": float(i == built - 1)}))
    return State(doc_open=True, workbench="PartDesignWorkbench", has_body=True, undo_available=True, tree=tree,
                 selection=[SelItem("face", f"Feature{built - 1}", "PartDesign::Pad", ["Face6"], (0.0, 0.0, 1.0), 10.0, 1)],
                 recent=["PartDesign_Pad", "Select:Face+Z"],
                 shape=ShapeInfo(True, 5e4, 1e4, (60.0, 40.0, 20.0), 6 + 4 * built, 12 + 8 * built, 1,
                                 ["+Z", "-Z", "+X", "-X", "+Y", "-Y", "|Z"]))


def examples(n_features: int, n: int, rng: random.Random):
    out = []
    while len(out) < n:
        goal = sample_split_goal("train", 3, rng)
        while len(goal.features) < n_features:  # longer goals: append intents of further goals
            goal.features += sample_split_goal("train", 3, rng).features[1:]
        goal.features = goal.features[:n_features]
        state = synthetic_state(goal, max(1, n_features - 1))
        out.append((state, goal, enumerate_actions(state)))
    return out


def timed(model, batch, reps: int) -> float:
    with torch.no_grad():
        for _ in range(3):
            model(batch)
        if batch["seg"].is_cuda:
            torch.cuda.synchronize()
        ts = []
        for _ in range(reps):
            t = time.perf_counter()
            model(batch)
            if batch["seg"].is_cuda:
                torch.cuda.synchronize()
            ts.append(time.perf_counter() - t)
    return statistics.median(ts)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", nargs="+", default=list(SIZES), help=f"from: {', '.join(SIZES)}")
    ap.add_argument("--features", nargs="+", type=int, default=[5, 20], help="goal lengths (intents) to test")
    ap.add_argument("--batch", type=int, default=16, help="states per batch for the batched timing")
    ap.add_argument("--threads", nargs="+", type=int, default=[torch.get_num_threads()], help="CPU thread counts")
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--no-gpu", action="store_true")
    args = ap.parse_args()

    rng = random.Random(0)
    devices = ["cpu"] + (["cuda"] if torch.cuda.is_available() and not args.no_gpu else [])
    print(f"torch {torch.__version__}, CPU threads available {torch.get_num_threads()}"
          + (f", GPU {torch.cuda.get_device_name(0)}" if "cuda" in devices else ", no GPU"))
    print(f"{'size':12s} {'params':>8s} {'intents':>7s} {'tokens':>6s} {'device':>10s} "
          f"{'1 state (ms)':>12s} {f'{args.batch} states (ms)':>15s} {'per state (ms)':>14s}")
    for name in args.sizes:
        w, heads, enc, dec, ff = SIZES[name]
        model = S1Model(S1Config(width=w, heads=heads, enc_layers=enc, dec_layers=dec, ff=ff, **TRAIN_OPTS)).eval()
        n_params = sum(p.numel() for p in model.parameters())
        opts = model.cfg.feature_opts()
        for nf in args.features:
            exs = examples(nf, args.batch, rng)
            one = collate([make_example(*exs[0], **opts)])
            many = collate([make_example(*e, **opts) for e in exs])
            tokens = int(one["seg"].shape[1])
            for dev in devices:
                for th in (args.threads if dev == "cpu" else [0]):
                    if dev == "cpu":
                        torch.set_num_threads(th)
                    m = model.to(dev)
                    b1 = {k: v.to(dev) for k, v in one.items()}
                    bn = {k: v.to(dev) for k, v in many.items()}
                    t1, tn = timed(m, b1, args.reps), timed(m, bn, max(3, args.reps // 2))
                    label = f"cpu x{th}" if dev == "cpu" else "gpu"
                    print(f"{name:12s} {n_params / 1e6:7.1f}M {nf:7d} {tokens:6d} {label:>10s} "
                          f"{1000 * t1:12.1f} {1000 * tn:15.1f} {1000 * tn / args.batch:14.2f}", flush=True)
            model.to("cpu")


if __name__ == "__main__":
    main()
