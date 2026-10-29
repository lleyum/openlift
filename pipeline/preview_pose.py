"""
OpenLift - Phase 3 check: pose one exercise, build its props, and save a preview.

Writes:
    dist/preview/<id>.blend       open it in Blender to inspect the pose
    dist/preview/<id>_front.png   quick front render
    dist/preview/<id>_side.png    quick side render

Does NOT modify assets/base_body.blend.

Run from the repo root:
    blender --background assets/base_body.blend --python pipeline/preview_pose.py -- --exercise Barbell_Bench_Press_-_Medium_Grip
Options:
    --engine WORKBENCH|EEVEE|CYCLES   render engine for the PNGs (default WORKBENCH: fast, no GPU setup)
    --no-render                       only save the .blend
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from pipeline import common, pose, props  # noqa: E402


def setup_render(engine: str) -> None:
    scene = bpy.context.scene
    if engine == "EEVEE":
        ids = [item.identifier for item in scene.render.bl_rna.properties["engine"].enum_items]
        scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids else "BLENDER_EEVEE"
    elif engine == "CYCLES":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 16
        scene.cycles.device = "CPU"
    else:
        scene.render.engine = "BLENDER_WORKBENCH"
        shading = scene.display.shading
        shading.light = "STUDIO"
        shading.color_type = "MATERIAL"
        shading.show_cavity = True
    scene.render.resolution_x = 800
    scene.render.resolution_y = 800
    scene.render.film_transparent = False
    if scene.world is None:
        scene.world = bpy.data.worlds.new("OL_preview_world")
    scene.world.color = (0.18, 0.18, 0.2)
    if engine != "WORKBENCH":
        sun = bpy.data.objects.get("OL_preview_sun")
        if sun is None:
            sun = bpy.data.objects.new("OL_preview_sun", bpy.data.lights.new("OL_preview_sun", "SUN"))
            scene.collection.objects.link(sun)
        sun.data.energy = 3.5
        sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(30))


def frame_camera(points, view: str) -> bpy.types.Object:
    scene = bpy.context.scene
    cam = bpy.data.objects.get("OL_preview_cam")
    if cam is None:
        cam = bpy.data.objects.new("OL_preview_cam", bpy.data.cameras.new("OL_preview_cam"))
        scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = "ORTHO"
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (lo + hi) * 0.5
    if view == "front":
        cam.location = (center.x, center.y - 10.0, center.z)
        cam.rotation_euler = (math.pi / 2, 0.0, 0.0)
        extent = max(hi.x - lo.x, hi.z - lo.z)
    else:
        cam.location = (center.x + 10.0, center.y, center.z)
        cam.rotation_euler = (math.pi / 2, 0.0, math.pi / 2)
        extent = max(hi.y - lo.y, hi.z - lo.z)
    cam.data.ortho_scale = extent * 1.15
    cam.data.clip_end = 50.0
    return cam


def main() -> int:
    parser = argparse.ArgumentParser(prog="preview_pose.py")
    parser.add_argument("--exercise", required=True, help="Exercise ID (file name in exercises/ without .json)")
    parser.add_argument("--engine", default="WORKBENCH", choices=["WORKBENCH", "EEVEE", "CYCLES"])
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args(common.script_args())

    path = common.EXERCISES_DIR / f"{args.exercise}.json"
    exercise = common.load_json(path)

    engine, warnings = pose.pose_from_exercise(exercise)
    created = props.build_props(engine, exercise)
    pose.parent_props([(o, o["openlift_attach"]) for o in created], engine)

    out_dir = common.DIST_DIR / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("")
    print(f"Exercise:  {exercise['id']} ({exercise['name']})")
    print(f"Template:  {exercise['base_template']}")
    print(f"Modifiers: {', '.join(exercise.get('pose_modifiers', [])) or '(none)'}")
    print(f"Grip:      {exercise['grip']['type']} / {exercise['grip']['width']}")
    print(f"Props:     {', '.join(o.name for o in created) or '(none)'}")
    for w in warnings:
        print(f"WARNING:   {w}")

    if not args.no_render:
        setup_render(args.engine)
        shorts = common.find_shorts(engine.mapping)
        objs = [engine.body] + ([shorts] if shorts else []) + [o for o in created if o.type == "MESH"]
        pts = engine.evaluated_body_points(objs)
        for view in ("front", "side"):
            frame_camera(pts, view)
            png = out_dir / f"{exercise['id']}_{view}.png"
            bpy.context.scene.render.filepath = str(png)
            bpy.ops.render.render(write_still=True)
            print(f"Render:    {png.relative_to(common.REPO_ROOT)}")

    blend = out_dir / f"{exercise['id']}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), copy=True)
    print(f"Saved:     {blend.relative_to(common.REPO_ROOT)}")
    print("DONE")
    return 0


if __name__ == "__main__":
    common.run_main(main)
