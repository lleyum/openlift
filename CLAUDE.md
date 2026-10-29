# OpenLift - project guide for Claude Code

OpenLift generates static 3D exercise models (GLB) and thumbnails for workout apps from a CSV of
~1,000 exercises. One shared MPFB male body, posed per exercise by a data-driven pipeline that runs in
headless Blender. Read this file before changing anything.

## Environment

- Windows, PowerShell. Repo root: `C:\Users\Liam Cordero\Projects\openlift`. Run every command from the repo root.
- Blender 5.2 LTS on PATH as `blender`. Code must also work on Blender 4.2 LTS (see "Blender version rules").
- Plain Python (`py`) runs only the data scripts (`pipeline/enrich_csv.py`, `pipeline/generate_jsons.py`).
  Everything that imports `bpy` runs inside Blender.
- The user is not a programmer. Explain results in plain language, give exact commands, and ask before
  anything destructive (deleting outputs, overwriting `assets/base_body.blend`, force-pushing).

## Commands

```powershell
# Data: CSV -> data/enriched-database.json -> exercises/<id>.json (manual overrides are preserved)
py pipeline/enrich_csv.py
py pipeline/generate_jsons.py

# Body checks / one-time prep (prep only from the untouched original, see "Pitfalls")
blender --background assets/base_body.blend --python pipeline/validate_rig.py
blender --background assets/base_body.blend --python pipeline/prep_mesh.py

# Quick pose preview (does not touch assets/)
blender --background assets/base_body.blend --python pipeline/preview_pose.py -- --exercise <ID>

# Build (build.py opens the body itself - no .blend argument)
blender --background --python build.py -- --exercise <ID> [<ID> ...] --thumbnails
blender --background --python build.py -- --all --thumbnails --skip-existing
#   --limit N  --engine EEVEE|CYCLES|WORKBENCH  --resolution N  --no-ao  --draco  --keep-blend  --gender male|female
```

Outputs: `dist/glb/<id>.glb`, `dist/camera/<id>.json`, `dist/thumbnails/<id>_{front,side,hero}.png`,
`dist/build_report.json` (only the exercises of the last run - aggregate it yourself across runs).
`dist/` is git-ignored.

## Architecture (read the module docstrings for details)

| Stage | File | Notes |
|---|---|---|
| CSV enrichment | `pipeline/enrich_csv.py` | Ordered regex rules → `base_template`, `pose_modifiers`, grip, stance, props, muscles, confidence. Every modifier it emits must exist in `schema/base_templates.json` (`KNOWN_MODIFIERS` enforces this). |
| Per-exercise JSON | `pipeline/generate_jsons.py`, `schema/exercise.schema.json` | Preserves `joint_deltas`, `custom_joint_overrides`, `review.notes`; never touches `review.status: "approved"` files. |
| Muscle data | `pipeline/muscle_mapping.json` | CSV muscle groups → 21 muscle keys → 40 `MUSCLE_*` vertex groups. |
| Rig mapping | `assets/bone_mapping.json` | Standard Anatomical Names (`LeftUpperArm`, `Spine02` ...) → real MPFB Game Engine bones. **Never hard-code real bone names elsewhere.** |
| Body prep | `pipeline/prep_mesh.py`, `pipeline/mpfb_refit.json` | Bakes shape keys, refits the skeleton from MakeHuman joint cubes, deletes helpers, smooths shoulder weights, builds muscle groups + procedural shorts. Refuses to save a bad result; keeps the first backup. |
| Posing | `pipeline/pose.py` | Anatomical angles `[flexion, twist, abduction]`, same numbers for left/right, relative to neutral standing; rig-agnostic (measures bones at runtime). Layers: template → modifiers (`set`/`add`, per-template `variants`, `respect_idle`, `reach`) → grip → `joint_deltas` → `custom_joint_overrides`. `commit()` plants feet/hands, runs the hand-reach solver, grounds the body. |
| Pose library | `schema/base_templates.json` | 20 templates, 45 modifiers, 6 grips. One joint per line - keep that formatting. |
| Props | `pipeline/props.py` | Procedural equipment. Supports are fitted to the posed body surface; hand-held props sit in the grip. Emits warnings for hands off the bar (> 6 cm) and weights passing > 2.5 cm into the body. |
| Freeze + AO | `pipeline/shading.py` | Static meshes, Cycles AO bake to vertex colors (`#A3A3A3` → `#404040`). |
| Muscle shells | `pipeline/muscles.py` | One seamless shell per tier (`Muscles_Primary` opaque `#EF4444`, `Muscles_Secondary` 30% alpha), grown one ring, boundary-relaxed, subdivided, solidified; pushed out over the shorts. Muscle list in the glTF extra `openlift_muscles`. |
| Cameras | `pipeline/thumbnails.py` | front/side orthographic, hero 85 mm aimed at the primary muscles; views turn when equipment hides the muscles. Metadata in glTF coordinates. |
| CLI | `build.py` | Opens the body fresh per exercise, so every build starts from a clean state. |

## Rules

1. **Data first.** Fix poses in `schema/base_templates.json` (template, modifier, variant) or with an
   exercise's `custom_joint_overrides` / `joint_deltas`, and classification in `enrich_csv.py` rules -
   before touching Python. A fix that applies to a family of exercises belongs in a modifier or variant,
   not in 40 overrides.
2. **Never edit `exercises/*.json` by hand except** `custom_joint_overrides`, `joint_deltas` and `review`.
   Everything else is regenerated from the CSV.
3. **Rig agnostic.** Standard Anatomical Names only; new bones go through `assets/bone_mapping.json`.
4. **Every visual fix is verified by a render.** Build the affected exercises with `--thumbnails`, look
   at the PNGs (you can read images), and compare before/after. Do not report a fix as done from code alone.
5. **The golden sample must not regress.** After any change to `pipeline/`, `schema/` or `build.py`, rebuild
   `Barbell_Bench_Press_-_Medium_Grip` with thumbnails and confirm it still passes `docs/TESTING.md` §5.
6. **No warnings policy.** A build with `WARNING:` lines is not done. Fix the cause or record the exercise
   in `docs/QA_BACKLOG.md` with its thumbnail and the reason.
7. **Log every bug** in `docs/QA_BACKLOG.md` (next id is QA-16): symptom, cause, fix, acceptance check, status.
8. **Small commits** on a branch, one fix per commit, with the QA id in the message. Ask before pushing to `main`.

## Pitfalls already paid for

- **MPFB rigs can be unfitted.** The original body had bones at MPFB default positions, 79 cm off the mesh.
  `prep_mesh.py` refits them; `validate_rig.py` measures the fit ("limb offset", must be ≤ 5 cm).
- **Prep only from the original.** Helper geometry (needed for the refit) is deleted by prep. To re-prep:
  `Copy-Item -Force assets\base_body.backup.blend assets\base_body.blend`, then run `prep_mesh.py`.
- **`BVHTree.FromObject` is in object-local space.** The rig is moved by grounding, so always build BVH
  trees from world-space vertices (`BVHTree.FromPolygons([mw @ v.co ...])`).
- **Cache world points in rig space.** `commit()` lowers the whole rig onto the floor after solving;
  anything cached in world space before that is wrong afterwards (see `_reach_center`).
- **Inside tests:** posed bodies overlap themselves (deep squat); use two ray directions and a depth
  threshold, and exclude the gripping hands.
- **Bars have vertices only at their ends.** Sample along edges when testing a prop against the body.
- **Reach solver needs bounds and a natural start pose.** Distance alone finds unnatural arms (QA-14).
- **`build.py` reopens the .blend per exercise** - do not keep Blender objects across exercises.

## Blender version rules (4.2 LTS and 5.x)

- EEVEE engine id: `BLENDER_EEVEE_NEXT` (4.2-4.4) vs `BLENDER_EEVEE` (5.x) - pick from the enum (see `thumbnails._set_engine`).
- `Material.use_nodes` is deprecated in 5.x: call `common.enable_nodes(mat)`.
- Alpha: set both `surface_render_method = "BLENDED"` and `blend_method = "BLEND"` when present (`shading.set_blended`).
- glTF exporter options differ by version: filter kwargs against the operator's RNA (see `build.export_glb`).
- Workbench/EEVEE need a GPU; `--engine CYCLES` works everywhere.

## Testing

- Gate: `docs/TESTING.md` (golden sample + QA regression batch).
- Fast check while iterating: `--no-ao` builds in ~0.3 s; `preview_pose.py` renders a pose without the export.
- To inspect a joint, write a throwaway script under `tools/` (git-ignored is fine) that poses an
  exercise via `pipeline.pose.get_engine()` and prints `engine.joints` / bone directions, or renders a
  close-up. Delete or keep out of `pipeline/`.
