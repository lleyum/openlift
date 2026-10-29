"""
OpenLift build CLI. Runs inside Blender (headless).

For each exercise: open the base body -> pose -> props -> freeze to static meshes -> bake AO ->
muscle shells -> export GLB -> camera metadata -> (optional) 3 transparent thumbnails.

Run from the repo root (you do NOT need to pass a .blend file; build.py opens the right body):

    blender --background --python build.py -- --exercise Barbell_Bench_Press_-_Medium_Grip --thumbnails
    blender --background --python build.py -- --all --thumbnails
    blender --background --python build.py -- --all --skip-existing --limit 50

Outputs (male; with --gender female everything goes under dist/female/):
    dist/glb/<id>.glb                      Y-up, meters, transforms applied, no camera/lights
    dist/camera/<id>.json                  camera views + bounds for the app (glTF coordinates)
    dist/thumbnails/<id>_<view>.png        front / side / hero, transparent (with --thumbnails)
    dist/build_report.json                 per-exercise status, warnings and timings

Options:
    --exercise ID [ID ...]   build these exercises (file names in exercises/ without .json)
    --all                    build every exercises/*.json (skips review.status == "rejected")
    --gender male|female     body to use: assets/base_body.blend (male) or assets/base_body_female.blend
    --thumbnails             also render the 3 PNGs
    --engine EEVEE|CYCLES|WORKBENCH   thumbnail renderer (default EEVEE; use CYCLES if you have no GPU)
    --resolution N           thumbnail size in pixels (default 1024)
    --skip-existing          skip exercises whose GLB already exists
    --limit N                stop after N exercises (useful for test batches)
    --no-ao                  skip the AO bake (fast preview; flat gray body)
    --draco                  Draco-compress the GLB meshes (smaller files; app must support Draco)
    --keep-blend             also save dist/blend/<id>.blend for debugging
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

import bpy  # noqa: E402

from pipeline import common, muscles, pose, props, shading, thumbnails  # noqa: E402

GENDERS = {
    "male": common.ASSETS_DIR / "base_body.blend",
    "female": common.ASSETS_DIR / "base_body_female.blend",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="build.py", description="Build OpenLift exercise GLBs.")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--exercise", nargs="+", metavar="ID")
    target.add_argument("--all", action="store_true")
    p.add_argument("--gender", default="male", choices=sorted(GENDERS))
    p.add_argument("--thumbnails", action="store_true")
    p.add_argument("--engine", default="EEVEE", choices=["EEVEE", "CYCLES", "WORKBENCH"])
    p.add_argument("--resolution", type=int, default=1024)
    p.add_argument("--skip-existing", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--no-ao", action="store_true")
    p.add_argument("--draco", action="store_true")
    p.add_argument("--keep-blend", action="store_true")
    return p.parse_args(common.script_args())


def out_root(gender: str) -> Path:
    return common.DIST_DIR if gender == "male" else common.DIST_DIR / gender


# ---------------------------------------------------------------------------- export


def export_glb(path: Path, objects: List[bpy.types.Object], draco: bool) -> None:
    common.ensure_object_mode()
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    wanted = {
        "filepath": str(path),
        "export_format": "GLB",
        "use_selection": True,
        "export_apply": True,
        "export_yup": True,
        "export_cameras": False,
        "export_lights": False,
        "export_extras": True,
        "export_animations": False,
        "export_skins": False,
        "export_morph": False,
        "export_vertex_color": "MATERIAL",
        "export_draco_mesh_compression_enable": draco,
    }
    supported = {prop.identifier for prop in bpy.ops.export_scene.gltf.get_rna_type().properties}
    kwargs = {k: v for k, v in wanted.items() if k in supported}
    if "export_vertex_color" not in supported and "export_colors" in supported:
        kwargs["export_colors"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(**kwargs)


# ---------------------------------------------------------------------------- one exercise


def build_one(exercise: Dict, args: argparse.Namespace, data: Dict) -> Dict:
    t0 = time.time()
    ex_id = exercise["id"]
    root = out_root(args.gender)
    glb_path = root / "glb" / f"{ex_id}.glb"

    bpy.ops.wm.open_mainfile(filepath=str(GENDERS[args.gender]))
    arm = common.find_armature(data["mapping"])
    engine = pose.PoseEngine(arm, mapping=data["mapping"], templates=data["templates"])
    if not any(vg.name.startswith("MUSCLE_") for vg in engine.body.vertex_groups):
        raise common.PipelineError("The body has no MUSCLE_* vertex groups. Run pipeline/prep_mesh.py first.")

    warnings = engine.pose_exercise(exercise)
    prop_objs = props.build_props(engine, exercise)
    groups = muscles.collect_muscle_vertices(engine.body, data["muscle_mapping"])

    frozen = shading.freeze_posed_scene(engine, prop_objs)
    body, shorts = frozen["body"], frozen["shorts"]
    shading.apply_body_material(body)
    if args.no_ao:
        ao = [1.0] * len(body.data.vertices)
        attr = body.data.color_attributes.new(shading.COLOR_ATTR, "FLOAT_COLOR", "POINT")
        for i in range(len(ao)):
            attr.data[i].color = shading.shade(shading.BODY_HEX, shading.CREVICE_HEX, 1.0)
    else:
        ao = shading.bake_body_ao(body, occluders=[shorts] if shorts else [])
    shading.apply_shorts_material(shorts)

    shells = muscles.build_exercise_shells(exercise, groups, body, ao, shorts, data["muscle_mapping"])
    for obj in frozen["props"]:
        obj.name = obj.data.name = "Prop_" + obj.name.replace("OL_", "", 1)

    export_objs = [body] + ([shorts] if shorts else []) + shells + frozen["props"]
    body["openlift_exercise_id"] = ex_id
    body["openlift_name"] = exercise["name"]
    export_glb(glb_path, export_objs, args.draco)

    cameras = thumbnails.compute_cameras(export_objs)
    meta = {
        "id": ex_id,
        "name": exercise["name"],
        "gender": args.gender,
        "glb": str(glb_path.relative_to(common.REPO_ROOT)).replace("\\", "/"),
        "base_template": exercise["base_template"],
        "muscles": exercise.get("muscles", {}),
        "camera": thumbnails.public_camera_metadata(cameras),
    }
    common.save_json(root / "camera" / f"{ex_id}.json", meta)

    pngs: List[Path] = []
    if args.thumbnails:
        pngs = thumbnails.render_thumbnails(export_objs, cameras, root / "thumbnails", ex_id,
                                            engine=args.engine, resolution=args.resolution)
    if args.keep_blend:
        blend = root / "blend" / f"{ex_id}.blend"
        blend.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), copy=True)

    return {
        "id": ex_id,
        "status": "ok",
        "seconds": round(time.time() - t0, 1),
        "glb": meta["glb"],
        "glb_kb": round(glb_path.stat().st_size / 1024),
        "shells": [o.name for o in shells],
        "props": [o.name for o in frozen["props"]],
        "thumbnails": [str(p.relative_to(common.REPO_ROOT)).replace("\\", "/") for p in pngs],
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------- main


def load_exercises(args: argparse.Namespace) -> List[Dict]:
    if args.all:
        paths = sorted(common.EXERCISES_DIR.glob("*.json"))
    else:
        paths = [common.EXERCISES_DIR / f"{i}.json" for i in args.exercise]
    out = []
    for p in paths:
        ex = common.load_json(p)
        if args.all and ex.get("review", {}).get("status") == "rejected":
            continue
        out.append(ex)
    return out


def main() -> int:
    args = parse_args()
    body_path = GENDERS[args.gender]
    if not body_path.exists():
        raise common.PipelineError(
            f"No body for --gender {args.gender}: {body_path} not found. "
            + ("Female support needs assets/base_body_female.blend prepared like the male body (HANDOFF.md Part C)."
               if args.gender == "female" else "")
        )
    data = {
        "mapping": common.load_bone_mapping(),
        "templates": common.load_json(common.TEMPLATES_PATH),
        "muscle_mapping": common.load_json(common.MUSCLE_MAPPING_PATH),
    }
    exercises = load_exercises(args)
    root = out_root(args.gender)
    results = []
    built = 0
    print("")
    print(f"OpenLift build: {len(exercises)} exercise(s), gender={args.gender}, thumbnails={args.thumbnails}")
    for n, ex in enumerate(exercises, 1):
        if args.limit and built >= args.limit:
            break
        glb = root / "glb" / f"{ex['id']}.glb"
        if args.skip_existing and glb.exists():
            results.append({"id": ex["id"], "status": "skipped"})
            continue
        try:
            res = build_one(ex, args, data)
            built += 1
            results.append(res)
            print(f"[{n}/{len(exercises)}] OK    {ex['id']}  ({res['seconds']}s, {res['glb_kb']} KB, "
                  f"{len(res['shells'])} muscle shells, {len(res['props'])} props)")
            for w in res["warnings"]:
                print(f"           WARNING: {w}")
        except Exception as exc:  # keep going in --all mode
            built += 1
            results.append({"id": ex["id"], "status": "failed", "error": str(exc)})
            print(f"[{n}/{len(exercises)}] FAIL  {ex['id']}: {exc}")
            if not isinstance(exc, common.PipelineError):
                traceback.print_exc()
            if not args.all:
                break

    ok = sum(1 for r in results if r["status"] == "ok")
    failed = [r for r in results if r["status"] == "failed"]
    skipped = sum(1 for r in results if r["status"] == "skipped")
    report = {"gender": args.gender, "ok": ok, "failed": len(failed), "skipped": skipped, "results": results}
    common.save_json(root / "build_report.json", report)
    print("")
    print(f"Done: {ok} built, {len(failed)} failed, {skipped} skipped. Report: "
          f"{(root / 'build_report.json').relative_to(common.REPO_ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    common.run_main(main)
