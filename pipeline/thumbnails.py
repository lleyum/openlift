"""
OpenLift - camera metadata and transparent thumbnails.

compute_cameras()   fits a perspective camera for each of the 3 standard views around the exported
                    objects and returns app-ready metadata in glTF coordinates (Y-up, meters).
render_thumbnails() renders one transparent PNG per view.

Views:
    front   camera in front of the character (Blender -Y), slight elevation  (world-fixed)
    side    camera at the character's left (Blender +X)                     (world-fixed)
    hero    aimed at the PRIMARY muscles: starts from a front-left three-quarter view and turns
            toward the direction the primary muscle shells face (e.g. from above for a bench press,
            from behind for a row), so the highlighted muscle is always visible. App default view.

The camera is NOT stored in the GLB. Apps read dist/camera/<id>.json instead.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

from pipeline import common  # noqa: E402

LENS_MM = 50.0
SENSOR_MM = 36.0
FILL = 0.88          # fraction of the frame the subject may fill
VIEWS: Dict[str, Tuple[float, float]] = {
    # name: (azimuth degrees from front toward the character's left, elevation degrees)
    "front": (0.0, 6.0),
    "side": (90.0, 6.0),
    "hero": (40.0, 20.0),   # starting point; turned toward the primary muscles
}
HERO_MUSCLE_WEIGHT = 0.55         # 0 = fixed three-quarter view, 1 = look straight at the muscles
HERO_ELEVATION_RANGE = (8.0, 40.0)


def blender_to_gltf(v: Sequence[float]) -> List[float]:
    """Blender Z-up (x, y, z) -> glTF Y-up (x, z, -y)."""
    return [round(v[0], 5), round(v[2], 5), round(-v[1], 5)]


def bounding_box(objects: Sequence[bpy.types.Object]) -> Tuple[Vector, Vector]:
    deps = bpy.context.evaluated_depsgraph_get()
    lo = Vector((math.inf, math.inf, math.inf))
    hi = -lo
    for obj in objects:
        if obj.type != "MESH":
            continue
        ev = obj.evaluated_get(deps)
        mesh = ev.to_mesh()
        mw = ev.matrix_world
        for v in mesh.vertices:
            p = mw @ v.co
            lo = Vector((min(lo.x, p.x), min(lo.y, p.y), min(lo.z, p.z)))
            hi = Vector((max(hi.x, p.x), max(hi.y, p.y), max(hi.z, p.z)))
        ev.to_mesh_clear()
    return lo, hi


def _view_dir(azimuth: float, elevation: float) -> Vector:
    """Unit vector from the target to the camera."""
    az, el = math.radians(azimuth), math.radians(elevation)
    # azimuth 0 = front (-Y); 90 = character's left (+X)
    return Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))).normalized()


def _fit_distance(corners: Sequence[Vector], center: Vector, to_cam: Vector, half_fov: float) -> float:
    forward = -to_cam
    right = forward.cross(Vector((0, 0, 1)))
    if right.length < 1e-6:
        right = Vector((1, 0, 0))
    right.normalize()
    up = right.cross(forward).normalized()
    k = math.tan(half_fov) * FILL
    d = 0.0
    for c in corners:
        rel = c - center
        x, y, z = abs(rel.dot(right)), abs(rel.dot(up)), rel.dot(forward)
        d = max(d, max(x, y) / k - z)
    return max(d, 0.5)


def _muscle_facing(shells: Sequence[bpy.types.Object]) -> Optional[Vector]:
    """Area-weighted average outward normal of the primary muscle shells (world space)."""
    total = Vector()
    for obj in shells:
        if obj.get("openlift_tier") != "primary":
            continue
        mw3 = obj.matrix_world.to_3x3()
        for poly in obj.data.polygons:
            total += (mw3 @ poly.normal) * poly.area
    return total.normalized() if total.length > 1e-9 else None


def _hero_angles(shells: Sequence[bpy.types.Object]) -> Tuple[float, float]:
    az0, el0 = VIEWS["hero"]
    facing = _muscle_facing(shells)
    if facing is None:
        return az0, el0
    d = (_view_dir(az0, el0) * (1.0 - HERO_MUSCLE_WEIGHT) + facing * HERO_MUSCLE_WEIGHT)
    if d.length < 1e-6:
        return az0, el0
    d.normalize()
    el = math.degrees(math.asin(max(-1.0, min(1.0, d.z))))
    el = max(HERO_ELEVATION_RANGE[0], min(HERO_ELEVATION_RANGE[1], el))
    az = math.degrees(math.atan2(d.x, -d.y))
    if abs(d.x) < 1e-6 and abs(d.y) < 1e-6:
        az = az0
    return az, el


def compute_cameras(objects: Sequence[bpy.types.Object]) -> Dict:
    lo, hi = bounding_box(objects)
    center = (lo + hi) * 0.5
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    fov = 2.0 * math.atan(SENSOR_MM / (2.0 * LENS_MM))
    views = {}
    shells = [o for o in objects if o.get("openlift_tier")]
    for name, (az, el) in VIEWS.items():
        if name == "hero":
            az, el = _hero_angles(shells)
        to_cam = _view_dir(az, el)
        dist = _fit_distance(corners, center, to_cam, fov / 2.0)
        pos = center + to_cam * dist
        views[name] = {
            "_blender_position": tuple(pos),
            "azimuth_deg": round(az, 1),
            "elevation_deg": round(el, 1),
            "position": blender_to_gltf(pos),
            "target": blender_to_gltf(center),
            "up": [0.0, 1.0, 0.0],
            "yfov_deg": round(math.degrees(fov), 3),
            "znear": 0.05,
            "zfar": round(dist * 4.0 + 10.0, 2),
        }
    return {
        "coordinate_system": "glTF (Y-up, meters, -Z forward of the camera)",
        "default_view": "hero",
        "bounds": {"min": blender_to_gltf(Vector((lo.x, hi.y, lo.z))), "max": blender_to_gltf(Vector((hi.x, lo.y, hi.z)))},
        "views": views,
    }


def _set_engine(engine: str) -> None:
    scene = bpy.context.scene
    if engine == "CYCLES":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 48
        if hasattr(scene.cycles, "use_denoising"):
            scene.cycles.use_denoising = True
    elif engine == "WORKBENCH":
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "VERTEX"
    else:
        ids = [i.identifier for i in scene.render.bl_rna.properties["engine"].enum_items]
        scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids else "BLENDER_EEVEE"


def _lights(center: Vector, size: float) -> List[bpy.types.Object]:
    made = []
    rigs = (
        # name, direction from center (toward the light), energy per m^2 of distance, size
        ("OL_Key", Vector((-0.6, -1.0, 1.0)), 110.0, 2.5),
        ("OL_Fill", Vector((1.0, -0.6, 0.4)), 40.0, 3.0),
        ("OL_Rim", Vector((0.3, 1.0, 0.9)), 70.0, 1.5),
    )
    dist = max(size, 1.0) * 2.5
    for name, d, energy, s in rigs:
        light = bpy.data.lights.new(name, "AREA")
        light.energy = energy * (dist / 3.0) ** 2
        light.size = s
        obj = bpy.data.objects.new(name, light)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = center + d.normalized() * dist
        direction = center - obj.location
        obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        made.append(obj)
    return made


def render_thumbnails(objects: Sequence[bpy.types.Object], cameras: Dict, out_dir: Path, exercise_id: str,
                      engine: str = "EEVEE", resolution: int = 1024) -> List[Path]:
    scene = bpy.context.scene
    _set_engine(engine)
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("OL_World")
    scene.world.color = (0.32, 0.32, 0.33)

    keep = {o.name for o in objects}
    hidden = []
    for obj in scene.objects:
        if obj.type in ("MESH", "CURVE") and obj.name not in keep and not obj.hide_render:
            obj.hide_render = True
            hidden.append(obj)

    lo, hi = bounding_box(objects)
    center, size = (lo + hi) * 0.5, (hi - lo).length
    lights = _lights(center, size)
    cam_data = bpy.data.cameras.new("OL_ThumbCam")
    cam_data.lens = LENS_MM
    cam_data.sensor_width = SENSOR_MM
    cam = bpy.data.objects.new("OL_ThumbCam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, view in cameras["views"].items():
        pos = Vector(view["_blender_position"])
        cam.location = pos
        cam.rotation_euler = (center - pos).to_track_quat("-Z", "Y").to_euler()
        cam_data.clip_start = view["znear"]
        cam_data.clip_end = view["zfar"]
        path = out_dir / f"{exercise_id}_{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        written.append(path)

    for obj in lights + [cam]:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Light):
            bpy.data.lights.remove(data)
        elif isinstance(data, bpy.types.Camera):
            bpy.data.cameras.remove(data)
    for obj in hidden:
        obj.hide_render = False
    return written


def public_camera_metadata(cameras: Dict) -> Dict:
    """Strip Blender-only fields before writing JSON for the app."""
    out = {k: v for k, v in cameras.items() if k != "views"}
    out["views"] = {n: {k: v for k, v in view.items() if not k.startswith("_")} for n, view in cameras["views"].items()}
    return out
