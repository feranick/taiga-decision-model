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

**Guided progress for complete goals** (2026-10-15): with the active intent supplied by the runtime's progress tracking (`taiga_build_part.py --guided-progress`), the 4.8M model builds the whole S 80 exactly (0.9987, the teacher's score; the casing 0.9983 against 0.974 without). For complete goals, which is every production request, the runtime uses it; the model's own pointer remains for continuing from an existing or edited document.

**Runtime: teacher first, then the model (guided), checker always.** If the teacher can build a goal, use its build; otherwise the production model builds it with guided progress; every result is checked against the goal's target (IoU and intent by intent) before it is returned. With one model (decision of 2026-10-15, section 7 item 10) there is nothing to route by size; section 2 keeps the registry and the fallback chain.

## 2. Several models and a router, not one model for everything

> **Update 2026-10-15:** the scaling runs showed one 1.2M model enough for everything tested (the whole S 80 exact with guided progress, better than the 7M under perturbation), so there is no size-based routing. What remains of this section: the **registry** (each model with its FreeCAD version, patch series, training data and measured results; still one model per platform), the **fallback chain** on checker rejection (the model guided, then unguided, then the 7M kept as a fallback, then "not buildable", every fallback logged), and room for **specialists** later (a new feature family before the main model is retrained, sheet metal, the UI-level model). The text below is the original reasoning.

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

### Deployment: a machine of many parts, and design changes

A whole machine is many goals built in parallel: an LLM decomposes the machine into parts, one agent per part runs the router above, and the parts are assembled and checked together. The S 80 kit (`../build/pump/`, `../taiga-expanded/eval/`) is this pipeline at 11 parts.

**Parameters, not rewritten goals.** The hard part of a change is consistency between parts, not rebuilding them: a new shaft diameter has to reach the bearing bores, the seal, the impeller hub and the bracket bore together. So the LLM writes a **parametric assembly spec** once, during decomposition: shared parameters (shaft Ø, bolt circles, wall thickness, ...) and the rules that turn them into each part's goal, as `../taiga-expanded/eval/make_s80_goals.py` does for the pump. A design change then means:

1. the LLM edits parameters in the spec (seconds);
2. every goal is regenerated from the spec, deterministically;
3. only the parts whose goal changed are rebuilt and checked;
4. the assembly checks run again (interference, fits, matching hole patterns).

Taiga builds exactly what each goal says, so the spec is where the design is right or wrong.

**Timing** (measured on the S 80, Spark and Quadro, 2026-10-13):

| | Time |
|---|---|
| Model decision | 20–30 ms per step (the model size barely matters) |
| FreeCAD step | 0.02–0.1 s on simple parts; 0.6–2 s on a large casing (it grows with the part) |
| Simple part: plate, shaft, cover (5–25 steps) | 0.3–2 s |
| Bearing bracket (52 steps) | ~5 s |
| Casing core (24 features) | ~100 s |
| Full casing (52 features, a few hundred steps) | 6–17 min |
| Check against the target | about as long again (the target is built too) |

FreeCAD, not the model, sets the pace.

**Swarm size and wall time.** One agent is one FreeCAD worker, about one CPU core and 0.3–2 GB of memory: about 16 on a 20-core Spark, about 60 on the four machines. The wall time is the longer of the slowest single part and the total work divided by the workers:

| Job | Wall time on ~60 workers |
|---|---|
| The S 80 (11 parts) | 6–17 min (the casing; about 17 min one part after another) |
| A machine of 500 parts, mostly simple, a few castings | 15–20 min (the simple parts take about a minute; the castings run in parallel and set the time) |
| One dimension changed (5–20 affected parts) | seconds to a few minutes; ~15 min if a large casting is among them |

**What it needs:**

- **Checker on every part**, with fallback to the long model and then to the teacher; for fully specified goals the teacher is the deterministic route, the model the one for continuing from existing or edited documents (section 1).
- **Watchdogs at every level** (`taiga_build_part.py --step-timeout`, `taiga_run.py` `S1_STEP_TIMEOUT`, `eval_s80.py --part-timeout`): across thousands of builds a stuck OpenCASCADE operation is a certainty, not an exception.
- **A result cache** keyed by goal (and model version), so an unchanged part is never rebuilt.
- **The vocabulary limits stay** (no sweeps, lofts, multi-body parts, patterns of patterns): such parts are flagged at decomposition, for another tool or an approximation.

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

- **Raise the model's hard limits first** (checked 2026-10-08, `../taiga-expanded/README.md`, primitive 8). `ord_table: 12` and `pos_table: 48` are not limits: they are the ranges simulated in training, and at run time the model reads ordinals up to 32 and positions up to 128. The real limits are in the featurization: only the first 40 tree objects (`MAX_NODES`, reached at about 19 sketch-based features, after which the newest objects are dropped) and the first 24 goal intents (`MAX_GOAL`) are encoded, and ordinals stop at 31 (`MAX_ORD`). A patch makes them model-config options (old checkpoints keep the old values), keeps the newest tree objects when the tree is longer, and the long-goal model is trained from scratch with larger tables.
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

**Started 2026-10-15 without company parts**, from two public sources that measure real usage:

- **DeepCAD's left-out designs:** the converter's `report_<subset>.json` lists why each design could not become a goal (angled sketch planes, intersect, bodies that stay apart, ...), over ~180k real sketch-and-extrude designs.
- **WHUCAD** (Fan et al., *Integrated Computer-Aided Engineering* 32(1), 2025; code MIT per its mirror, to be confirmed): 4,244 parts rebuilt by hand in CATIA from ABC models, with 18 feature types (extrude, revolve, groove, pocket, fillet, chamfer, shell, draft, hole, mirror, ...). `../taiga-expanded/datasets/survey_whucad.py` counts how many parts use each feature and in which combinations; the ranking of what the goals cannot yet express decides the next patches. (Its other 142k models are DeepCAD with features inserted at random, so they say nothing about real usage.)

**First results (2026-10-15).** DeepCAD training split: of 130,654 unique designs, 90.2 % convert to goals today; the gaps are designs that stay **several separate solids** (8.2 %; several parts in one document, to be split into one goal per solid rather than a vocabulary change), broken originals (1.0 %), **sketch planes not normal to X, Y or Z** (0.4 %) and **intersect** (0.1 %). WHUCAD (MIT; 4,509 hand-built parts): a median of 10 features per part (90th percentile 44, so long goals matter); share of parts using each feature: extrude 86 %, pocket 49 %, **fillet 49 %**, **chamfer 20 %**, revolve 18 %, **shell 11 %**, mirror 7 %, **sketch on a custom plane 2 %**, **draft 1.8 %**, groove 0.8 %. Fillets and chamfers in real parts pick their edges one by one (CATIA topology selections), which our selectors (a face's edge loop, all edges along an axis) express only in part. Ranking for the next patches: (1) fillet and chamfer on chosen edges, (2) shell, (3) splitting multi-solid designs (converter), (4) sketches on angled planes, (5) draft. Share of WHUCAD's real parts whose every feature the goals express, as the vocabulary grows (`survey_whucad.py` 2026.10.15.2): **31.7 % today**, 86.1 % with fillet and chamfer on chosen edges, 96.2 % with shell, 98.2 % with sketches on any plane, 100 % with draft. (By feature type only: a part also needs its sketches and selections to fit the goal format.)

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

**Data size and memory** (measured on the patch-0016 pilot, 2026-10-14): 1.2M labelled states take 97 MB as raw shards and 4.7 GB as featurized caches (`.pkl`, one per shard, built on first load). `train_sft` holds the whole featurized set in RAM, about 6 GB per million states with Python's overhead, so the current loader trains on up to about 10M states on a 121 GB Spark. Moving data between machines is cheap (only the raw shards); a loader that reads from disk is needed only if the data-scaling run shows 10M states still beating 5M. Loading also needs small shards: the loader featurizes up to 8 shards at once, about 20 GB per million states in a shard, so a machine with few generator workers (the 5060 Ti: 560k states per shard) ran out of memory at load; `taiga_pool_data.sh` `SPLIT=n` cuts shards into smaller ones (aim for ≤ 150k states per shard).

**Production data** (patches 0001–0017; 0018 for any data generated from 2026-10-15, `TAIGA_KEEP_HELDOUT=1`, DeepCAD goals at 25 %): `DATA_SCALE=8` on each machine with its own `DATA_SEED` (101 Quadro, 102 5060 Ti, 103 Spark2, 104 Spark1), about 5M states each and 20M in all, pooled with `../training/taiga_pool_data.sh`. Then the data-scaling run of the long model at about 2.5M, 5M and 10M states, then both production models with `DAGGER_PERTURB=0.2`.

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

## 7. Before the production run (checklist, 2026-10-12)

**Results to finish** (runs in progress):

1. ~~**Input limits with matched tables**~~ **done** (Quadro `s0014`, 2026-10-13): `len6` 0.99 with `--max-nodes 160 --max-goal 64 --max-ord 72 --max-pos 256 --ord-table 28 --pos-table 96 --newest-nodes`; every S 80 part except the casing exact. These are the production limits.
2. ~~**DeepCAD goals at 1.2M and 7M**~~ **done** (Spark1 `dc1`, Spark2 `long2`, 2026-10-13; `../taiga-expanded/eval/README.md`): DeepCAD goals make the casing core exact on every seed at both sizes; the full casing (52 intents) reaches 0.97 / 0.93 only at 7M (mean 0.74 against 0.51 at 1.2M without its hung build). Data mix: DeepCAD goals at 25 %; a larger long-goal model.
3. ~~**DeepCAD test split with the patch-0013 models**~~ **done** (5060 Ti, 2026-10-14; `../taiga-expanded/datasets/README.md`): **0.956 ± 0.019** of the 6,762 verified test designs built to IoU ≥ 0.99 (teacher 0.999), by length 0.965 (1–5 features), 0.858 (6–10), 0.73 (11–20, 31 designs), with no DeepCAD design in training (step-1 baseline 0.52).
4. ~~**Why the casing stalls at 0.81**~~ **found** (verbose builds on Spark1 and Spark2): not length, ordinals or the held-out pair, but the top flange read as a datum feature (`nz: 1` had only come with `off`). Fixed by patch 0016 for new models and `--canon-top` for existing ones (`../taiga-expanded/eval/README.md`).

**Code to add:**

5. ~~**`TAIGA_KEEP_HELDOUT=1`**~~ **done** (patch 0015): production trains on every feature combination; the held-out pairs exist only to measure generalization.
6. ~~**Pooled data generation**~~ **done** (`taiga_pool_data.sh`, `TRAIN_DATA_DIR`): each machine generates shards with its own `DATA_SEED` (`./train_expanded.sh <machine> data`), the helper checks and combines them into one training folder.
7. ~~**DeepCAD scoring without the second phase**~~ **done** (`run_deepcad.sh` 2026.10.12.1: off unless `VS_TEACHER=1`), which roughly halves the gate's time.
8. ~~**Patch 0016**~~ **done**: datum cavities and `at` stacks in one goal, explicit top normals (suites `mixed_at`, `mixed_at_long`); teacher check: 793 of 800 sampled goals build (Spark1, 2026-10-13).
8b. ~~**Patch 0017**~~ **done** (2026-10-14): flanges with several mirrored bolt holes in training (the 0016 pilot's casing stopped at the suction port's second hole pair). Production data is generated with 0001–0017.
9. Only if the casing still fails on length beyond 21 intents after item 8: longer mixed goals (curriculum to ~40 intents) or mid-part start states (section 4.1).

**Decisions:**

10. ~~**Model size and data size**~~ **decided** (2026-10-15; `../taiga-expanded/eval/README.md`): **one 1.2M model** (`--max-nodes 160 --max-goal 64 --max-ord 72 --max-pos 256 --ord-table 28 --pos-table 96 --newest-nodes`). On the same 4.8M states it matches the 7M on the S 80 (guided, both exact; plain, 49/52 casing intents against 34/52) and beats it under perturbation (`len6` 0.92 against 0.85). Data: about **10M states** (two machines' 8× pooled), since the 7M still gained from 4.8M to 9.7M on the mixed suites; that fits a Spark's memory with the current loader, so no disk loader is needed. The 7M stays as a router fallback. Pending: the DeepCAD test of a 1.2M model (dc1, Spark1).
11. **Pass criteria** per test set and length bucket (section 6), fixed before the run. Next to IoU, every S 80 build is also checked intent by intent (`../taiga-expanded/eval/feature_check.py`): a part counts as exact only if every measurable intent is realized, since IoU hides missing holes and patterns (the 0.974 casing realized 34 of 52 intents; the guided build 52 of 52). Proposal (mean over 5 seeds; every seed within 0.03 of it):

    | Test set | Small model (≤ 25 intents) | Long model |
    |---|---|---|
    | Synthetic suites, clean | ≥ 0.99 on every suite up to `len6` | the same, plus `mixed_at_long` ≥ 0.97 |
    | Synthetic, 20 % perturbed | `len6` ≥ 0.85 (patch-0014 runs: 0.78) | `len6` ≥ 0.90 |
    | DeepCAD test, 1–5 / 6–10 / 11–20 features | ≥ 0.98 / 0.95 / 0.90 (now 0.965 / 0.858 / 0.73) | the same |
    | S 80, ten parts other than the casing | exact on every seed | exact on every seed |
    | S 80 casing core / casing | core ≥ 0.98 | core ≥ 0.98; casing ≥ 0.95 on 3 of 5 seeds |

    The DeepCAD rows are the ones the DeepCAD training goals should move; the pilot models are scored on the test split to check that before the gates are fixed. Result (2026-10-15): the perturbed 7M pilots build 1.000 / 0.999 / 1.000 of the three buckets (0.965 / 0.858 / 0.73 without DeepCAD training goals), above every proposed gate; 18.6 % of the test goals also occur in the training split, and on the other 5,505 every seed still scores 1.000, so the DeepCAD gate can be raised to 0.99 in every bucket.
12. **Pinned platform:** FreeCAD 1.1.3–1.1.4 (decided 2026-10-12), and which machines generate data and which train.

The spurious Undo after a correct but unfamiliar step is a training issue that the loop guard covers at run time; with item 5 and the real designs (comp recovered in `long1`) it may not need its own fix, so it waits for the results above.

## 8. Phases

| Phase | Content | Done when |
|---|---|---|
| 0 | Convert a few dozen production parts with the teacher | The list of missing vocabulary and the real length distribution are known |
| 1 | Next patch: off-axis revolves, spline bosses, goals up to 10–12 features, mixed-family goals, wider pattern/mirror coverage; (done in patch 0010; the `ord_table`/`pos_table` check found the real limits elsewhere) | Bearings and impeller build without the guard; comp back to 1.00 at the large budget; len6 ≥ the baseline's 0.89 |
| 2 | Architecture change if needed; mid-part start states; curriculum to 20–50 features | The bearing bracket and casing core build on most seeds |
| 3 | DeepCAD train-split family; own parts as a family and as a test set | DeepCAD test split by length bucket at or near the teacher's verified share |
| 4 | Scale data and model; fix the gates | Gates met on all frozen test sets, 5 seeds |
| 4b | Register the production 1.2M model (and the 7M as fallback); runtime script `../build/taiga_runtime.py` (written 2026-10-15): teacher first, the model with guided progress, checker, fallback chain | Every request ends in a checked part or a logged "not buildable" with the attempts made |
| 5 | Production runtime: teacher first, router-selected model, checker always | Pinned FreeCAD, versioned models in the registry, gates re-run on every FreeCAD update |
| 6 | Goals from shapes (section 9): a goal generator from a target shape (STEP, mesh), sampled candidates built by the teacher and scored; search inside the build where the model is unsure; drawings last | Most DeepCAD test shapes rebuilt from the shape alone to IoU ≥ 0.99 with every intent realized; cost per part in CPU-seconds, no frontier model in the loop |

Phase 0 is the cheapest and teaches the most: it sizes everything after it. The small model needs little beyond phase 1 (off-axis positions, the spurious Undo); phases 2–4 are mostly about the long-goal model.

## 9. More thinking per part: goals from shapes (phase 6)

Taiga carries out a goal; it does not decide what to build. Today the planner (an LLM) writes the goal. Systems such as Spectral Labs' SGS-Mk2 (a CAD model orchestrated by a frontier vision-language model, 2026-10-14) also decide the construction themselves, sampling several and scoring them, at a cost per part dominated by the frontier model in the loop. Nothing in our approach prevents doing the same; the line is only where the goal comes from. Adding it keeps our cost profile: CPU-bound, parallel, no per-part API bill.

**Goal generator (a small "System 2").** A model that reads a target shape (STEP or mesh first; a point cloud or a drawing later) and writes a Taiga goal. The training data already exists, which is unusual for this problem:

- 116,688 verified DeepCAD designs, whose goals the teacher rebuilds to IoU ≥ 0.99 (real shape → goal pairs, `../taiga-expanded/datasets/`);
- unlimited synthetic pairs from the goal families of the patch series, since the teacher turns any goal into its exact shape;
- the DeepCAD test split (6,762 verified designs) as the frozen test set.

**Sample and score.** For each part, sample several candidate goals, build each with the teacher (deterministic, seconds of CPU) and score it against the target with IoU and the feature-level check (`../taiga-expanded/eval/feature_check.py`); keep the best. Every attempt costs CPU time, not tokens, so many candidates per part are affordable; the scoring prefers goals that are complete (every intent realized) and short.

**Search inside the build.** Where Taiga is unsure (a low top probability, or the loop guard would step in), try two or three next commands on parallel FreeCAD workers and keep the continuation the checker scores best. This would replace the loop guard with a principled version of it.

**Drawings last.** Reading dimensions, views and tolerances from an engineering drawing is a perception problem. An LLM or vision-language model can do it once per part, outside the build loop; the goal generator and the checker then take over.

**What still limits it:**

- the vocabulary: whatever the goal format cannot express (sweeps, lofts, multi-body parts, mates, patterns of patterns) no generator can produce; extending it remains the patch series' job;
- design intent: sketches are fully fixed, so parametric intent lives in the goal or the assembly spec, not in the FreeCAD file;
- consistency across parts (fits, interfaces) stays with the parametric assembly spec (section 2, deployment).

