\# OpenLift



An open-source library of static 3D exercise models (GLB) for workout apps.



Every exercise is generated from data: one shared base body, one JSON record per exercise, and a headless Blender pipeline that poses the body, adds equipment, highlights the worked muscles, and exports a GLB plus three thumbnails.



\## Output per exercise



| File | Description |

|---|---|

| `dist/glb/<Exercise\_ID>.glb` | Posed body + shorts + equipment + muscle highlight shells. Y-up, 1 unit = 1 m, transforms applied. No camera. |

| `dist/thumbnails/<Exercise\_ID>\_front.png` | Transparent-background render |

| `dist/thumbnails/<Exercise\_ID>\_side.png` | Transparent-background render |

| `dist/thumbnails/<Exercise\_ID>\_three\_quarter.png` | Transparent-background render |

| `dist/camera/<Exercise\_ID>.json` | Recommended camera position/target/FOV for the app |



\## Visual style



| Element | Spec |

|---|---|

| Body | Smooth grayscale mannequin, base `#A3A3A3`, baked AO vertex colors down to `#404040` in crevices |

| Clothing | Generic black shorts |

| Primary muscle | `#EF4444`, separate shell offset 2 mm along normals, Solidify modifier, baked AO |

| Secondary muscles | Same red, 30% opacity |

| Equipment | Simple procedural proxies (barbell, dumbbell, kettlebell, padded bench/seat, dark-gray machine handles + frame) |



\## Architecture



```

data/exercise-database.csv

&#x20;       │  pipeline/enrich\_csv.py      (assigns base\_template, grip, pose\_cues)

&#x20;       ▼

data/enriched-database.json

&#x20;       │  pipeline/generate\_jsons.py

&#x20;       ▼

exercises/<Exercise\_ID>.json  ──►  build.py (Blender, headless)

&#x20;                                   ├─ pose.py       base template → exercise deltas → manual overrides

&#x20;                                   ├─ props.py      procedural equipment, parented to hands/feet

&#x20;                                   ├─ muscles.py    red highlight shells from vertex groups

&#x20;                                   └─ thumbnails.py 3 transparent PNGs + camera metadata

&#x20;                                           ▼

&#x20;                                        dist/

```



\### Key design rules



1\. \*\*Data-driven.\*\* Nothing exercise-specific is hardcoded in Python. Exercises live in `exercises/\*.json`.

2\. \*\*Pose templates.\*\* Each exercise inherits one of 15–20 `base\_templates` (`schema/base\_templates.json`), then applies deltas and `custom\_joint\_overrides` for niche cases.

3\. \*\*Rig agnostic.\*\* The pipeline uses Standard Anatomical Names (`LeftUpperArm`, `Spine02`, `LeftThigh`, ...). `assets/bone\_mapping.json` translates them to whatever bone names the base mesh uses. Swapping the base mesh means editing one JSON file.

4\. \*\*One body.\*\* All exercises use the same base mesh with identical proportions. A `--gender` flag is reserved for a future female body.



\## Requirements



\- Blender 4.2 LTS or newer (run headless via `blender --background`)

\- Python 3.10+ (for the data scripts in `pipeline/` that do not need Blender)

\- Git



\## Quick start



Setup instructions for a fresh machine are in \[`HANDOFF.md`](HANDOFF.md).

Testing a single exercise end-to-end is in \[`docs/TESTING.md`](docs/TESTING.md).



```bash

\# Generate exercise JSONs from the CSV

python pipeline/enrich\_csv.py

python pipeline/generate\_jsons.py



\# Build one exercise

blender --background assets/base\_body.blend --python build.py -- --exercise Barbell\_Bench\_Press\_-\_Medium\_Grip --thumbnails



\# Build everything

blender --background assets/base\_body.blend --python build.py -- --all --thumbnails

```



\## License



Code: MIT (see `LICENSE`). The base mesh must be CC0 or similarly permissive so generated GLBs can be redistributed freely.

