# taiga-build123d: feasibility plan

> **Decision (2026-10-15):** no trained model on build123d. Once a goal exists, every step in build123d can be computed from it, so a fixed translator is exact, free and faster than a model; an LLM writes the goals. The translator is a separate project, **build123d-translator** (`~/Desktop/build123d-translator`): goal → build123d solid, STEP and a standalone script, with a checker, a command line and an MCP server. It matches the FreeCAD teacher on all 24 reference goals measured so far (volume and bounding box), builds parts in 1–250 ms and the 52-feature S 80 casing in about 1 s; its `tools/compare_freecad.py` compares it with the teacher's parts at scale (DeepCAD test folders). A trained model on build123d is worth reconsidering only for Phase 6 (goals from shapes). The plan below is kept for reference.

A possible parallel project: the Taiga approach (goal → scripted teacher → small model → checker) with [build123d](https://pypi.org/p/build123d) as the CAD backend instead of FreeCAD. Taiga-FreeCAD continues unchanged; the two would be separate tools behind the same goal format and harness. Written 2026-10-15; nothing here is measured yet.

## Why consider it

build123d is a Python library on the same OpenCascade kernel as FreeCAD, with no document, recompute chain or application around it. What that could give:

| Gain | Why it matters here |
|---|---|
| **Training throughput** | Data generation and DAgger replay every action in the CAD runtime; in FreeCAD that took days across four machines. A several-times faster step means several times the data on the same hardware |
| **Phase 6 (goals from shapes)** | Building and scoring many candidate goals per part; cheap builds matter most there |
| **Long parts** | FreeCAD's time per step grows with the part (13 ms on a ring, 40 ms on a 44-step bracket, ~20 min for the 52-feature casing) because the document recomputes; build123d pays only the geometry operations |
| **Deployment** | `pip install` with a pinned version, no FreeCAD install, no ~2 s process start; runs on Linux, macOS and Windows |
| **Output as code** | A build123d script is a natural result for LLM tools and for engineers who script |
| **Selection by geometry** | Edges and faces chosen by filters (axis, position, type, groups), not by names that change after the next boolean |

Our pipeline already runs FreeCAD headless, so the saving is not the GUI: it is the document model (objects, recompute, sketch attachment), the start-up, and whatever the recompute costs on long parts. Fillets and booleans cost the same OpenCascade time in both.

What is given up: the `.FCStd` feature tree a designer opens and edits in a GUI. The script replaces it.

## The design question: what are the model's actions?

In FreeCAD the model drives a real sequence of tool operations: select a face, new sketch, draw, constrain, pad, select edges, fillet, with Undo when something goes wrong. That granularity is what makes it a learning problem (`README_beyond_freecad.md`, requirements 1–2: state-dependent steps, mistakes that compound).

If the build123d actions were coarse (`Box(...)`, `Hole(...)`), the model would only copy the goal: a translator with extra steps. taiga-build123d needs a comparable fine-grained action set, for instance:

| Step | FreeCAD (now) | build123d (sketch) |
|---|---|---|
| Choose where to work | Select a face by name or an origin plane | A face from a selector on the current solid (`faces().filter_by(Axis.Z).sort_by(Axis.Z)[-1]`), or a `Plane` with an offset |
| Draw | Sketch geometry and constraints | `BuildSketch` primitives and outlines on that plane |
| Make the feature | Pad, Pocket, Revolution, Groove, Hole | `extrude(amount, mode=ADD/SUBTRACT, both=...)`, `revolve`, `Hole` |
| Choose edges | Edge names, loops | Edge selectors (by axis, by face, by position) |
| Finish | Fillet, Chamfer, Thickness, patterns, Mirror | `fillet`, `chamfer`, `offset` (shell), `PolarLocations` / `GridLocations`, `mirror` |
| Recover | Undo | Undo by restoring the previous solid (cheap: keep the history) |

The observation also needs replacing: in FreeCAD it is read from the document's objects; in build123d it would be the operation history plus a summary of the current solid's faces and edges (normals, positions, sizes). The model and training code (`taiga-s1`) carry over largely as they are; the environment, action catalogue, teacher, goal samplers and data generator are new.

### Does an LLM writing build123d already do this?

`README_beyond_freecad.md` puts code-CAD in "an LLM writing the script is enough". build123d's geometric selectors remove much of FreeCAD's "which face is it now?" problem, so for short parts an LLM may write good scripts directly. The case for a trained model then rests on long parts (where selector logic and order still compound), on cost and speed per part (milliseconds against an LLM call), on reliability measured against a checker, and on Phase 6. This must be measured, not assumed (step 3 below).

## Feasibility steps

| # | Step | Output | Decides |
|---|---|---|---|
| 1 | **Speed.** A throwaway goal → build123d script translator for the test only (not a product); build the S 80 parts and ~500 DeepCAD test goals in build123d and in headless FreeCAD (the teacher). Time per feature, growth with part length, memory, process start; IoU of the two results (also shows whether the two OpenCascade versions agree) | A table per part length bucket | Whether the speed gain is large enough to matter (several times on long parts, or only on start-up) |
| 2 | **Action set and observation.** Draft the build123d action catalogue and the state summary, mapped against FreeCAD's ~50 commands; list what has no counterpart (constraints, Hole standards, datum planes) | A design note | The size of the new environment |
| 3 | **LLM baseline.** The same goals given to an LLM writing build123d directly, scored by the checker, by length bucket | Success rate and cost per part | Whether a trained model adds value over direct scripting, and where |
| 4 | **Data-generation estimate.** From step 1: states per hour per machine against FreeCAD's measured rates | Time to the ~10M states of v1 | Whether retraining is cheap enough to try |

If steps 1 and 3 show a large gain on long parts and an LLM baseline that fails there, start the project: environment and teacher first, then data, then the same evaluation suites (synthetic, DeepCAD, S 80). If not, the persistent worker (production plan, to-do item 12) recovers most of FreeCAD's deployment overhead, and build123d can still serve as an output format or a fast checker.

## How it would fit

- **One goal format, one harness.** The registry gets a `backend` field (`freecad` or `build123d`); the MCP tools and agents do not change, and both backends can be scored on the same goals.
- **Separate repository**, taiga-build123d, with its own patch series and pinned versions (build123d and its OCP/OpenCascade build), like this one is pinned to FreeCAD 1.1.3.
- Reuses from this repository: the goal format and validation (`harness/`), the goal samplers and DeepCAD converter (`taiga-expanded/datasets`), the evaluation suites and feature check, the training scripts and the lessons in `training/README_training.md`.
