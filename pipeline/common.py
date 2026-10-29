"""
OpenLift - shared helpers for scripts that run inside Blender.

Every Blender entry script does:
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from pipeline import common

Compatible with Blender 4.2 LTS and newer (tested on 4.2-style API and 5.x).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import bpy

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
SCHEMA_DIR = REPO_ROOT / "schema"
PIPELINE_DIR = REPO_ROOT / "pipeline"
EXERCISES_DIR = REPO_ROOT / "exercises"
DIST_DIR = REPO_ROOT / "dist"

BODY_BLEND = ASSETS_DIR / "base_body.blend"
BONE_MAPPING_PATH = ASSETS_DIR / "bone_mapping.json"
TEMPLATES_PATH = SCHEMA_DIR / "base_templates.json"
MUSCLE_MAPPING_PATH = PIPELINE_DIR / "muscle_mapping.json"

# Standard Anatomical Names. Order = parents before children.
STANDARD_BONES: List[str] = [
    "Root", "Hips", "Spine01", "Spine02", "Spine03", "Neck", "Head",
    "LeftShoulder", "LeftUpperArm", "LeftForearm", "LeftHand", "LeftFingers", "LeftThumb",
    "RightShoulder", "RightUpperArm", "RightForearm", "RightHand", "RightFingers", "RightThumb",
    "LeftThigh", "LeftShin", "LeftFoot", "LeftToes",
    "RightThigh", "RightShin", "RightFoot", "RightToes",
]

# Bones the pipeline cannot work without.
REQUIRED_BONES: List[str] = [
    "Hips", "Spine01", "Spine02", "Spine03", "Neck", "Head",
    "LeftShoulder", "LeftUpperArm", "LeftForearm", "LeftHand",
    "RightShoulder", "RightUpperArm", "RightForearm", "RightHand",
    "LeftThigh", "LeftShin", "LeftFoot", "RightThigh", "RightShin", "RightFoot",
]


class PipelineError(RuntimeError):
    """A problem the user must fix (bad file, missing bone, ...). Printed without a traceback."""


def script_args(argv: Optional[Sequence[str]] = None) -> List[str]:
    """Arguments after '--' on the Blender command line."""
    argv = list(sys.argv if argv is None else argv)
    if "--" in argv:
        return argv[argv.index("--") + 1:]
    return []


def load_json(path: Union[str, Path]) -> Dict:
    path = Path(path)
    if not path.exists():
        raise PipelineError(f"File not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:
            raise PipelineError(f"{path} is not valid JSON: {exc}") from exc


def save_json(path: Union[str, Path], data: Dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_bone_mapping(path: Union[str, Path] = BONE_MAPPING_PATH) -> Dict:
    data = load_json(path)
    if "bones" not in data:
        raise PipelineError(f"{path} has no 'bones' section.")
    return data


def mapped_bones(mapping: Dict, standard_name: str) -> List[str]:
    """Real bone names for a standard name (always a list; empty if unmapped)."""
    value = mapping["bones"].get(standard_name)
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    return [v for v in value if v]


def find_armature(mapping: Optional[Dict] = None) -> bpy.types.Object:
    name = (mapping or {}).get("armature_object")
    if name and name in bpy.data.objects and bpy.data.objects[name].type == "ARMATURE":
        return bpy.data.objects[name]
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        raise PipelineError("No armature found in the .blend file. Did you add the MPFB rig (HANDOFF.md Part C4)?")
    if len(armatures) > 1:
        names = ", ".join(o.name for o in armatures)
        raise PipelineError(
            f"Found {len(armatures)} armatures ({names}). Rename the one to use to 'Armature' "
            "or set 'armature_object' in assets/bone_mapping.json."
        )
    return armatures[0]


def find_body(armature: bpy.types.Object, mapping: Optional[Dict] = None) -> bpy.types.Object:
    name = (mapping or {}).get("body_object")
    if name and name in bpy.data.objects and bpy.data.objects[name].type == "MESH":
        return bpy.data.objects[name]
    shorts_name = (mapping or {}).get("shorts_object", "shorts")
    candidates = []
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.name == shorts_name or obj.name.startswith("OL_"):
            continue
        uses_armature = any(m.type == "ARMATURE" and m.object == armature for m in obj.modifiers)
        if uses_armature:
            candidates.append(obj)
    if not candidates:
        raise PipelineError("No body mesh found (a mesh with an Armature modifier pointing at the rig).")
    return max(candidates, key=lambda o: len(o.data.vertices))


def find_shorts(mapping: Optional[Dict] = None) -> Optional[bpy.types.Object]:
    name = (mapping or {}).get("shorts_object", "shorts")
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type == "MESH":
        return obj
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.name.lower().startswith("shorts"):
            return obj
    return None


def ensure_object_mode() -> None:
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def set_active(obj: bpy.types.Object) -> None:
    ensure_object_mode()
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def get_or_create_collection(name: str) -> bpy.types.Collection:
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    return coll


def hex_to_linear_rgba(hex_color: str, alpha: float = 1.0):
    """'#A3A3A3' -> linear-space RGBA tuple for Blender material colors."""
    hex_color = hex_color.lstrip("#")
    srgb = [int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]

    def to_linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return (to_linear(srgb[0]), to_linear(srgb[1]), to_linear(srgb[2]), alpha)


# Limb bones used to check that the skeleton sits inside the mesh it deforms.
ALIGNMENT_BONES: List[str] = [
    "LeftUpperArm", "RightUpperArm", "LeftForearm", "RightForearm",
    "LeftThigh", "RightThigh", "LeftShin", "RightShin",
]


def skeleton_alignment(arm: bpy.types.Object, body: bpy.types.Object, mapping: Dict,
                       coords: Optional[Sequence] = None, min_weight: float = 0.6):
    """How far the skeleton is from the mesh, measured on the limbs.

    For each limb bone: centroid of the vertices it mostly deforms, minus the closest point on the bone.
    Returns (median offset vector in world space, median distance in meters), or (None, None).
    A well-fitted rig gives a distance of about 0.01-0.02 m.
    """
    from mathutils import Vector

    mw, amw = body.matrix_world, arm.matrix_world
    co = coords if coords is not None else [v.co for v in body.data.vertices]
    offsets = []
    for std in ALIGNMENT_BONES:
        names = mapped_bones(mapping, std)
        if not names or names[0] not in arm.data.bones or names[0] not in body.vertex_groups:
            continue
        gi = body.vertex_groups[names[0]].index
        pts = [mw @ co[v.index] for v in body.data.vertices
               if any(g.group == gi and g.weight >= min_weight for g in v.groups)]
        if len(pts) < 10:
            continue
        centroid = sum(pts, Vector()) / len(pts)
        bone = arm.data.bones[names[0]]
        h, t = amw @ bone.head_local, amw @ bone.tail_local
        d = t - h
        s = max(0.0, min(1.0, (centroid - h).dot(d) / max(d.length_squared, 1e-9)))
        offsets.append(centroid - (h + d * s))
    if not offsets:
        return None, None
    mid = len(offsets) // 2
    median = Vector(sorted(o[i] for o in offsets)[mid] for i in range(3))
    distance = sorted(o.length for o in offsets)[mid]
    return median, distance


def enable_nodes(mat: bpy.types.Material) -> None:
    """Turn on shader nodes (Blender 4.x). Blender 5+ always uses nodes and deprecates the flag."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if hasattr(mat, "use_nodes") and not mat.use_nodes:
            mat.use_nodes = True


def run_main(main) -> None:
    """Run a script's main(); print PipelineError cleanly and exit non-zero on failure."""
    try:
        code = main()
    except PipelineError as exc:
        print("")
        print(f"ERROR: {exc}")
        print("")
        sys.exit(1)
    sys.exit(code or 0)
