# Pump parts kit: Victor Pumps S 80 (3", DN80) self-priming centrifugal pump

`example_goals_pump.json` is a parts kit for one pump from the Victor Pumps *S self-priming centrifugal pumps* brochure (edition 20251217): the **S 80 G31T+F**, a cast-iron, close-coupled pump with DN80 flanged ports. One goal per part, built with `../taiga_build_part.py`.

## What comes from the brochure

| Item | Brochure | Used here |
|---|---|---|
| Model | S 80 G31T+F, close coupled, cast iron, DN80 flanged ports | Kit model |
| Port size | 3" – DN80 | 80 mm ports; DN80 flanges |
| Motor | 4 kW, 2900 rpm, 400 V 50 Hz | IEC 112M frame (the standard frame for 4 kW, 2 poles) |
| Passage of solids | 32 mm | Gaps between impeller blades kept above 32 mm |
| Layout | Suction and discharge ports on top of the casing ("surface mounted for easy access"); priming cover; front inspection cover; non-return valve in the suction port; replaceable wear plates; open-blade impeller | One part each |

The brochure has no dimensional drawings, so the other dimensions come from the standards these parts follow, or are estimated:

| Part | Source of the dimensions |
|---|---|
| Port flange | EN 1092-1, DN80 PN16: Ø200, 8 bolt holes Ø18 on Ø160 |
| Flange gasket | EN 1514-1 inner-bolt-circle gasket, DN80: Ø90 × Ø142 |
| Motor flange | IEC 60072-1 FF215 (B5) for frame 112: Ø250, 4 holes Ø14 on Ø215, Ø180 spigot opening |
| Motor | IEC 112M: Ø28 × 60 mm shaft; body size estimated |
| Impeller | Ø160, from the head at 2900 rpm (≈35 m, tip speed ≈ √(2gH)); 5 open blades |
| Casing, covers, wear plate, valve flap, foot | Estimated from the photos and the port size |

## Simplifications

Taiga-S1 builds one PartDesign Body per goal and adds features only on the top face (see `../README_build_part.md`):

- **Casing:** a solid block with the two top ports and the priming port drilled through. The internal volute, the separation chamber and the side openings for the covers are not modeled.
- **Impeller:** a back shroud with 5 straight radial blades and a shaft bore. Real blades are curved; there is no hub boss.
- **Motor:** a plain cylinder with the shaft on top. No cooling fins, terminal box or feet.
- **Parts outside the training sizes:** the model was trained on parts up to about 80 mm, while this kit goes up to 300 mm. Its inputs are normalized by part size, so this should matter little, but expect a lower success rate than with the engine kit. The impeller (a patterned boss) is also a combination never seen in training.

## Build

```bash
cd ~/taiga/taiga-s1 && source ~/taiga/freecad.env
.venv/bin/python <repo>/build/taiga_build_part.py --goals <repo>/build/pump/example_goals_pump.json --check
.venv/bin/python <repo>/build/taiga_build_part.py --model runs/seed2/hf \
    --goals <repo>/build/pump/example_goals_pump.json --out parts/pump
```

`--check` confirms each goal can be built before running the model. The port flange is built once and used for both suction and discharge.
