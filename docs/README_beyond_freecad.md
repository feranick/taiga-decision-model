# Beyond FreeCAD: where the Taiga approach applies

Taiga-S1 is a small model that drives FreeCAD one command at a time, trained by imitation of a scripted teacher plus DAgger. This note asks which other engineering and scientific tools the same approach would suit, and which are better served by an LLM writing a script. It was written while working on this repository (2026-10-08). The candidate list is the capability table of [AGIneer](https://github.com/ricfulop/AGIneer) (`docs/capabilities_and_status.md`, as of 2026-10-07).

## The dividing line

An LLM is enough when the tool's input is **declarative**: a script or input deck that describes the system, where the order of statements hardly matters and the result is fixed once the description is right. LAMMPS, DFT codes, most CFD case dictionaries and SPICE netlists are like this. The LLM writes the file, the tool runs it, and errors show up as messages the LLM can read and fix.

FreeCAD PartDesign is different. Each command acts on what the earlier commands created: the face a sketch goes on only exists after the pad that made it, its name can change after the next boolean, and a wrong step early on breaks every later one. Building a part means tracking that state step by step and recovering when something goes wrong (Undo, another choice). This is where LLMs lose track and where a small model trained on the tool's real state does well: Taiga-S1 decides in about 1 ms per step and builds what the goal says, while the planner (an LLM or a person) decides what to build.

## What a tool needs for this approach

| # | Requirement | What it means | FreeCAD (this repo) |
|---|---|---|---|
| 1 | **State-dependent steps** | Each step refers to things earlier steps created (a face, a net, a mesh entity), which are only known after running them | Sketches on faces chosen after each pad, features on datum planes, patterns of earlier features |
| 2 | **Order matters, mistakes compound** | A wrong step makes later steps fail or produce the wrong thing; recovery is part of the skill | Invalid pads, Undo, the loop guard |
| 3 | **An automatic checker** | The end state can be compared with a target without a person looking at it; this is the training signal | Volumetric IoU of the part against the goal's target solid |
| 4 | **A scripted teacher** | Code that generates goals procedurally and solves them, giving unlimited labelled examples; DAgger then labels the model's own mistakes | `expert.py` and the goal samplers; taiga-expanded adds families as patches |
| 5 | **A fast, headless, scriptable runtime** | Millions of steps for data and DAgger, in parallel, without a GUI or a licence server | Headless FreeCAD workers, a few ms per step |
| 6 | **A finite action set** | Commands plus selections, not free text; the numbers come from the goal | About 50 command types with selections; dimensions filled in from the goal |

Requirements 1–2 say whether the approach is *needed*; 3–6 say whether it is *feasible*. A tool that fails 1–2 is LLM territory. A tool that passes 1–2 but fails 5 (slow, licensed, cloud-metered) can still use a model trained elsewhere, but cannot be the training environment.

## Candidates from the AGIneer list

### Good candidates

| Tool | Why it fits | Checker | Teacher and goals | Main cost |
|---|---|---|---|---|
| **Onshape** (REST adapter and FeatureScript) | A parametric feature tree like PartDesign: sketches on queried faces, extrudes, booleans, patterns. DeepCAD itself was collected from Onshape documents | Mass properties, or the exported STEP against the target (the `eval/` IoU) | The goal format and the DeepCAD converter carry over almost unchanged | The API quota (2,500 calls per seat per year on free and standard plans) rules it out for training. Train on FreeCAD and translate goals to Onshape features at run time |
| **Gmsh with OpenCASCADE** (mesh interchange) | Booleans and `fragment` renumber entities; physical groups, mesh sizes and boundary conditions refer to those tags. The same "which face is it now?" problem as FreeCAD | Mesh quality metrics, and each physical group covering the intended region | Procedural geometry plus a scripted tagging and meshing teacher | Small: free, headless, fast |
| **Blender** | Modifier stacks and edit-mode operations act on the current selection; order changes the result. A fixed action set would also replace AGIneer's current "executes generated Python without data guards" | Mesh or voxel IoU against the target | Procedural goals; `bpy` runs headless | Small; defining the action vocabulary is the work |
| **XRD Rietveld refinement** (GSAS-II) | The order of refinement is the craft: scale and background, then cell, then profile, then positions and thermal parameters. Freeing parameters too early makes the fit diverge, and the next step depends on the current fit | Rwp / χ², plus recovering the known structure | Unlimited goals by simulating patterns from known structures; the standard refinement strategy is the teacher | Small, all Python. A good first tool outside CAD |
| **Microfluidic placement and routing** | Placement and routing are already sequential decisions, today solved by seeded annealing ("a different seed is a different device") | The network solver already in the tool | Everything is native and cheap; the annealer's best results can seed the teacher | Small; the gain depends on how far the heuristic is from good layouts |

### Possible, with caveats

- **Sequential optical design (Optiland).** Like Rietveld: which variables to free, when to add a surface, when to change glass. The merit function is the checker. Worth it only where plain optimization stalls.
- **COMSOL.** Structurally ideal: the model tree goes geometry, selections, physics, mesh, study, and selections are entity numbers that change after geometry edits. But it needs a licence and each step is slow, so DAgger-scale data is impractical. A model trained on Gmsh plus an open solver could carry over the selection skill.
- **Rhino** (McNeel MCP). State-dependent modelling like FreeCAD, but it needs a running licensed session; training would be slow and tied to seats.
- **Multibody dynamics and deployment.** Assembling joints and constraints is partly sequential (each one picks geometry from parts already placed), but most of the setup is declarative.
- **KiCad** (on AGIneer's roadmap only). Would be a strong candidate: placement and routing depend on what is already placed, and DRC/ERC is the checker.

### An LLM writing the script is enough

| Group | Tools |
|---|---|
| Atomistic and crystal work | ASE / pymatgen / spglib, OVITO analysis and rendering |
| Solvers driven by an input deck or script | NGSolve (and its solver-slot adapter), scikit-fem, CalculiX, XFEM / BEM / Trefftz, NGSolve waveguide modes, ngspice, Cantera |
| Optics computations | Physical optics, geometric optics and telescopes, HCIPy, occulter / starshade diffraction, POPPY / AOtools |
| Analysis and statistics | XRD peak fitting, SALib / UQ studies, tolerance stack-up and durability, Astropy, COMSOL export visualization |
| Code-CAD | PicoGK / LEAP 71 (signed-distance fields compose declaratively), Zoo / KCL (Zoo already offers text-to-CAD) |
| Image pipelines | Siril, PixInsight (standard calibrate, register, stack, stretch sequence; choosing parameters is tuning, not ordering) |
| Lookup and retrieval | Match! phase identification, TraceParts, McMaster, CADENAS / 3Dfindit |

## What carries over from this repository

The FreeCAD-specific parts are the runtime and the vocabulary. The rest is general:

- **Goal → teacher → model → checker.** Goals describe *what*; the teacher and the model decide *how*; the checker scores the result against the goal's target. `taiga-expanded/README.md` ("Workflow") shows the stages.
- **Expanding coverage as patches.** New action families and goal samplers added as a patch series on a pinned upstream commit, each with its own evaluation suite (`taiga-expanded/patches`).
- **Real-design test sets.** A converter from public design histories to goals, with the teacher verifying that the vocabulary can express each design before any model is scored (`taiga-expanded/datasets`).

Lessons from the training runs that should apply to any such tool (`training/README_training.md`, "Variance studies"):

- **Run-to-run spread is a property of the training budget and data, not of the seed.** Same seed and data on the same GPU give models as different as different seeds, unless deterministic mode is on. Report several seeds; compare settings with `DETERMINISTIC=1`.
- **More training improves skills that every example practises (length) and erodes generalization to combinations never seen (composition).** The cure for the second is more varied training data, and it needs checking at every budget step.
- **Real parts expose coverage gaps that synthetic suites miss** (in FreeCAD: XY datum planes, overhanging features, parts far from the origin). A real-design test set belongs in the loop from the start.
- **Safeguards at run time need their own tests.** The loop guard that stopped repeated dead ends also turned one spurious Undo into a full teardown, until it learned to block the Undo instead.

## Suggested order

1. **Onshape** as a run-time target for the existing FreeCAD model: goal-to-feature translation through the REST adapter, IoU on the exported STEP. Cheapest, and it reaches a widely used CAD system.
2. **Rietveld refinement** as the first tool outside CAD: easy environment, checker and teacher, and it shows the approach is not specific to geometry.
3. **Gmsh** for meshing pipelines, which also prepares the selection skill a COMSOL or NGSolve workflow would need.
