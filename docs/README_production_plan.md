# Toward a production model: training set and plan

What it would take to train Taiga for production use, based on the results so far (variance study, taiga-expanded steps 1–3, S 80 and DeepCAD evaluations, up to 2026-10-08). Estimates are rough and meant for sizing, not scheduling.

## 1. The model's role in production

For any goal the vocabulary can express, the scripted teacher already builds the part: the S 80 to IoU 0.9989 for the whole pump, and 6,762 of 6,821 DeepCAD test designs to IoU ≥ 0.99 with the original. The model's value lies where the teacher was not scripted:

- continuing from an existing or user-edited document, not from an empty one;
- recovering from FreeCAD behaviour the teacher never met;
- goals whose feature combinations no teacher family produced.

All three are generalization. So the production measure is how well the model handles what it did not see in training, not the synthetic suites (which are at 1.00). That is why two current findings matter most:

- **Composition regresses with budget.** At 2× data and 8 epochs, four of five seeds fail one of the held-out compositions (comp 0.53 ± 0.09), as the original model does when trained longer (`../taiga-expanded/README.md`, step 3).
- **Spurious Undo on unfamiliar geometry.** The bearings (one revolve far from the origin) and the impeller's spline blade were built correctly and then undone by the model. The loop guard now blocks that Undo (`../build/taiga_build_part.py` 2026.10.08.1), but the model itself still makes the mistake.

**Proposed runtime: teacher first, then the right model, checker always.** If the teacher can build a goal, use its build; otherwise a router picks the model suited to the goal (section 2); every result is checked against the goal's target before it is returned.

## 2. Several models and a router, not one model for everything

Production does not need one model that covers every part. Most requests are small parts (a few features), which a small model already builds exactly; long parts need the model this plan proposes, with long-goal training and more steps. Keeping them separate has three advantages:

- **Each model stays cheap to train, and to retrain after a FreeCAD update:** the small model at today's budget, the long-goal model at its own.
- **Training one does not degrade the other.** More training on the same distribution erodes generalization to unseen combinations (comp 1.00 → 0.53, `../taiga-expanded/README.md` step 3); a specialist trained for length need not pay that price on the small parts.
- **A new specialist can be added without retraining the rest** (e.g. one for revolved parts, or for sheet-like parts), as long as its coverage is recorded.

### The model registry

Each trained model is listed with what it was trained on and what it was measured to do:

```json
{
  "small-v1": {"path": "models/small-v1/hf", "freecad": "1.1.3", "params": "1.2M",
               "kinds": ["profile_base", "profile_pocket", "profile_boss", "hole", "polar_pattern", "mirror", "fillet_edges"],
               "max_features": 5,
               "measured": {"1-5": 0.99, "6-10": 0.40}},
  "long-v1":  {"path": "models/long-v1/hf", "freecad": "1.1.3", "params": "7M",
               "kinds": ["...all of small-v1...", "profile_revolve", "datum features", "..."],
               "max_features": 50, "step_budget": "2 x plan + 10",
               "measured": {"1-5": 0.99, "6-10": 0.95, "11-20": 0.85, "21-50": 0.60}}
}
```

(The numbers are placeholders.) `kinds` and `max_features` come from the training data; `measured` comes from the frozen test sets (section 6), per length bucket, so the router decides on evidence rather than on what a model was meant to do.

### The router

Given a goal (the planner's feature list), the router:

1. **Describes the goal:** number of features; feature kinds; whether it uses patterns of patterns, datum planes or overhangs; how far from the origin it sits and how large it is relative to its own scale (the bearing failed on that alone).
2. **Tries the teacher** (a few seconds, no model): if it builds the goal, that is the answer.
3. **Keeps the models that cover the goal:** every feature kind in `kinds`, length within `max_features`, and (when recorded) positions and sizes within the trained ranges.
4. **Picks the one with the best measured result for that length bucket**; on a tie, the smallest (fastest) one.
5. **Falls back** to the next model if the checker rejects the build. If none succeeds, it reports the goal as not buildable, with the attempts made. Every fallback is logged, which shows which goals the specialists miss, i.e. what to train next.

A goal that no registered model covers (a feature kind none was trained on) is reported as such before any build is attempted.

The router is a small script, `taiga_route.py`, to be written once there are two models to choose between. It sits on top of `../build/taiga_build_part.py` and the IoU checker in `../taiga-expanded/eval`.

## 3. Where the current training set falls short

| Gap | Evidence | Training data today |
|---|---|---|
| Goal length | len6 (9 intents) 0.78 ± 0.09; bearing bracket (13 features) and casing (24 / 52) fail on every seed | At most 5 features per goal |
| Composition | comp 0.53 at the larger budget | One family per goal; patterns and mirrors of a limited set of feature kinds |
| Real geometry | Bearings fail at 12× the part's size from the origin; DeepCAD goals with an XY datum feature 0–5 % before patch 0009 | Samplers keep parts near the origin, at sizes up to about 80 mm |
| Vocabulary | DeepCAD and S 80 need features the teacher lacks (limits listed in `../taiga-expanded/eval/README.md`) | Sketch, pad/pocket on faces and datum planes, revolve, holes, patterns, mirrors, fillets/chamfers on chosen edges |
| Recovery | Undo after a valid but unfamiliar step | DAgger on the model's own mistakes; perturbed DAgger tested on the baseline only |

## 4. What the training set needs

### 4.1 Long goals (largest item)

- **Check the architecture first.** The model config has `ord_table: 12` and `pos_table: 48`. If either indexes a feature's position in the goal, goals past 12 features cannot be represented, whatever the data. If so, change it in a patch (relative positions, or a pointer to the next unfinished feature) and retrain from scratch.
- **Curriculum.** Goal lengths 5 → 10 → 20 → 50 features, with the long end built by chaining families (a body, then several feature groups, then patterns and dressups).
- **Mid-part start states.** The teacher builds the first k features of a long goal, the document is saved, and episodes start from it. The model learns the late steps of long parts without paying for the whole build each time. This matters because FreeCAD steps get slow as the part grows (about 2.5 s per step on the full casing).
- **Step budget.** Patch 0008 sets it to max(200, 2 × the expert's plan + 10); keep that for long goals.

### 4.2 Composition diversity

- Goals that mix families at random instead of one family per goal.
- Patterns and mirrors of every feature kind, patterns about a feature's own axis, and patterns of patterns.
- Keep the held-out pairs as a monitor and check comp at every budget step.

### 4.3 Real-design distribution

- **DeepCAD train split** (about 160k models) converted into a training family with `../taiga-expanded/datasets/convert_deepcad.py`, mixed with the synthetic families. It brings real positions, sizes from 1 mm to 2 m, off-origin features and real proportions.
- **Sampler ranges** widened to match: profiles away from the origin (also along the revolve axis), and the size ranges measured with patch 0007.
- **Licensing for production.** DeepCAD's code is MIT and its models come from Onshape public documents; check the terms that apply to those models before production use. The Fusion 360 Gallery is non-commercial only and stays out. The company's own designs, if they may be used, are the best source of all.

### 4.4 Vocabulary for production parts

Decided from use, not guessed:

1. Convert a sample of a few dozen parts that production would actually build into goals, and build them with the teacher.
2. List what the teacher cannot express and how often each gap occurs.
3. Add features in that order. Likely candidates: shell, draft, rib, pads up to a face, patterns about a feature's own axis; then loft, sweep and threads (Phase 2).

DeepCAD contains only sketches and extrudes, so revolves, fillets, holes and patterns need synthetic families or other sources.

### 4.5 Recovery behaviour

- Perturbed DAgger (`DAGGER_PERTURB`) on the expanded model and on long goals.
- Explicit examples of continuing after a valid but unfamiliar step, so the model stops undoing correct geometry. Measure it with the loop guard off (`--no-loop-guard`), so the guard does not hide the problem.

## 5. Scale and cost

| | Now | Production target |
|---|---|---|
| Labelled states | 1.1M (2× data) | 10–50M |
| Goal length | 1–5 features | 1–50, curriculum and mid-part starts |
| Families | about 15 synthetic | synthetic + DeepCAD train split + own parts |
| Model size | 1.2M parameters | small model 1.2M; long-goal model 7–22M, decided by `../training/taiga_bench_size.py` on the DGX and a data-scaling run |
| Data generation | about 30 min | about 1–3 days across the four machines (FreeCAD is CPU-bound) |
| Training | 18 min per seed (2× data, 4 epochs) | hours per seed on a Spark |
| Evaluation | 1.2–1.5 h per seed | grows with the test sets below; parallelize across machines |

Larger models stay fast enough: the decision time is small next to the FreeCAD step time. Whether they need to be larger is a question for a data-scaling run: if the 1.2M model stops improving as data grows, it is too small.

## 6. Evaluation and acceptance

Fixed before the production training starts:

- **Frozen test sets:**
  - synthetic suites, including the held-out compositions;
  - DeepCAD test split (6,762 verified goals), bucketed by length;
  - the S 80;
  - a part nobody tuned for (the engine kit);
  - a set of the company's own parts, kept out of training.
- **Gates per length bucket:** share built exactly (IoU ≥ 0.999 against the reference), crash rate, time per part.
- **How to compare:**
  - report 5 seeds;
  - compare settings with `DETERMINISTIC=1`;
  - select checkpoints on validation data, never on a test set;
  - score with and without the loop guard.
- **Pinned platform:** one FreeCAD version and OS for production; retrain and re-run the gates whenever FreeCAD changes (`../training/README_training.md`: each platform gets its own model).

## 7. Phases

| Phase | Content | Done when |
|---|---|---|
| 0 | Convert a few dozen production parts with the teacher | The list of missing vocabulary and the real length distribution are known |
| 1 | Next patch: off-axis revolves, spline bosses, goals up to 10–12 features, mixed-family goals, wider pattern/mirror coverage; check `ord_table`/`pos_table` | Bearings and impeller build without the guard; comp back to 1.00 at the large budget; len6 ≥ the baseline's 0.89 |
| 2 | Architecture change if needed; mid-part start states; curriculum to 20–50 features | The bearing bracket and casing core build on most seeds |
| 3 | DeepCAD train-split family; own parts as a family and as a test set | DeepCAD test split by length bucket at or near the teacher's verified share |
| 4 | Scale data and model; fix the gates | Gates met on all frozen test sets, 5 seeds |
| 4b | Freeze the current small model as `small-v1` in the registry; write `taiga_route.py` once the long-goal model exists | The router picks per goal and falls back on checker rejection; fallbacks are logged |
| 5 | Production runtime: teacher first, router-selected model, checker always | Pinned FreeCAD, versioned models in the registry, gates re-run on every FreeCAD update |

Phase 0 is the cheapest and teaches the most: it sizes everything after it. The small model needs little beyond phase 1 (off-axis positions, the spurious Undo); phases 2–4 are mostly about the long-goal model.
