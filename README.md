# OpenLift

An open-source library of static 3D exercise models (GLB) for workout apps.

Every exercise is generated from data: one shared base body, one JSON record per exercise, and a
headless Blender pipeline that poses the body, adds equipment, highlights the worked muscles, and
exports a GLB, camera metadata and three thumbnails.

## Output per exercise

| File | Description |
|---|---|
| `dist/glb/<id>.glb` | Posed body + black shorts + equipment + muscle highlight shells. glTF 2.0, Y-up, 1 unit = 1 m, transforms applied, no camera/lights/rig. About 1 MB. |
| `dist/camera/<id>.json` | Camera views for the app in glTF coordinates: `front` and `side` (orthographic, `ortho_height_m`), `hero` (85 mm perspective aimed at the primary muscles, the default view), plus bounds. |
| `dist/thumbnails/<id>_front.png` | Orthographic front view, transparent background |
| `dist/thumbnails/<id>_side.png` | Orthographic side view (turned automatically if equipment hides the muscles) |
| `dist/thumbnails/<id>_hero.png` | Perspective view aimed at the highlighted muscles |

### Objects inside each GLB

| Node | Content |
|---|---|
| `Body` | Grayscale mannequin; baked ambient occlusion in the vertex colors (`#A3A3A3` → `#404040`). Extras: `openlift_exercise_id`, `openlift_name`. |
| `Shorts` | Generic black shorts |
| `Muscles_Primary` | Solid `#EF4444` shell 2 mm above the skin. Extras: `openlift_tier: "primary"`, `openlift_muscles: "pectoralis_lower,pectoralis_upper"` |
| `Muscles_Secondary` | Same red at 30% opacity (alphaMode `BLEND`). Same extras with `secondary`. |
| `Prop_*` | Equipment proxies: barbell, dumbbell, kettlebell, bench, seat, cable frame and handles, machine frame, ... |

## Visual style

| Element | Spec |
|---|---|
| Body | Smooth grayscale mannequin, base `#A3A3A3`, baked AO down to `#404040` in crevices |
| Clothing | Generic black shorts |
| Primary muscles | `#EF4444`, seamless shell offset ~2 mm along the normals, subdivided, solidified, AO-shaded |
| Secondary muscles | Same red, 30% opacity |
| Equipment | Simple procedural proxies, fitted to the posed body |

## Architecture

```
data/exercise-database.csv
        │  pipeline/enrich_csv.py       (template, pose modifiers, grip, stance, props, muscles, confidence)
        ▼
data/enriched-database.json  ──  data/enrichment-report.csv (review triage)
        │  pipeline/generate_jsons.py   (keeps manual overrides)
        ▼
exercises/<id>.json  ──►  build.py  (Blender, headless, one fresh body per exercise)
                           ├─ pose.py        template → modifiers → grip → deltas → overrides,
                           │                 foot/hand planting, hand-reach solver, grounding
                           ├─ props.py       procedural equipment, fitted to the body; clipping checks
                           ├─ shading.py     freeze to static meshes, AO bake to vertex colors
                           ├─ muscles.py     red highlight shells from 40 muscle vertex groups
                           └─ thumbnails.py  camera metadata + 3 transparent PNGs
                                   ▼
                                 dist/
```

Key design rules:

1. **Data-driven.** Nothing exercise-specific is hard-coded in Python. Poses live in
   `schema/base_templates.json` (20 templates, 45 modifiers, 6 grips); per-exercise fixes live in
   `exercises/<id>.json` (`custom_joint_overrides`, `joint_deltas`).
2. **Rig agnostic.** Poses use Standard Anatomical Names (`LeftUpperArm`, `Spine02` ...) and anatomical
   angles (`[flexion, twist, abduction]`). `assets/bone_mapping.json` maps them to the real rig.
3. **One body.** All exercises use the same MPFB base mesh (`assets/base_body.blend`), prepared once by
   `pipeline/prep_mesh.py`. `--gender female` is supported once `assets/base_body_female.blend` exists.

## Requirements

- Blender 4.2 LTS or newer (tested on 5.2 LTS), on the PATH as `blender`
- Python 3.9+ for the data scripts
- Git

## Quick start

- New machine: [`HANDOFF.md`](HANDOFF.md)
- Test one exercise end to end: [`docs/TESTING.md`](docs/TESTING.md)
- Working on the pipeline with Claude Code: [`CLAUDE.md`](CLAUDE.md) and [`CLAUDE_CODE_PROMPT.md`](CLAUDE_CODE_PROMPT.md)
- Known issues and their fixes: [`docs/QA_BACKLOG.md`](docs/QA_BACKLOG.md)

```bash
# Data
python pipeline/enrich_csv.py
python pipeline/generate_jsons.py

# One-time body preparation
blender --background assets/base_body.blend --python pipeline/validate_rig.py
blender --background assets/base_body.blend --python pipeline/prep_mesh.py

# Build one exercise / everything (resumable)
blender --background --python build.py -- --exercise Barbell_Bench_Press_-_Medium_Grip --thumbnails
blender --background --python build.py -- --all --thumbnails --skip-existing
```

## License

Code: MIT (see `LICENSE`). The base body is made with MPFB / MakeHuman CC0 assets, so the generated
GLBs and thumbnails can be redistributed freely.
