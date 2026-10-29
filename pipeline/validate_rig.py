"""
OpenLift - validate assets/base_body.blend before posing anything.

Prints every bone in the armature (as a tree), then checks:
  - exactly one armature and one body mesh
  - body height and applied scale (1 unit = 1 meter)
  - a 'shorts' object (or a 'shorts' vertex group) exists
  - MakeHuman helper geometry / shape keys (removed by prep_mesh.py)
  - the 40 MUSCLE_* vertex groups (created by prep_mesh.py)
  - assets/bone_mapping.json resolves every Standard Anatomical Name to a real bone

Read-only unless you pass --write-mapping, which writes a detected preset to
assets/bone_mapping.json (only if that file does not exist yet, or with --force).

Run from the repo root:
    blender --background assets/base_body.blend --python pipeline/validate_rig.py
    blender --background assets/base_body.blend --python pipeline/validate_rig.py -- --write-mapping
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bpy  # noqa: E402

from pipeline import common  # noqa: E402

# Known rig naming schemes. Used to suggest a bone_mapping.json.
PRESETS = {
    "mpfb_game_engine": {
        "Root": "Root", "Hips": "pelvis", "Spine01": "spine_01", "Spine02": "spine_02", "Spine03": "spine_03",
        "Neck": "neck_01", "Head": "head",
        "LeftShoulder": "clavicle_l", "LeftUpperArm": "upperarm_l", "LeftForearm": "lowerarm_l", "LeftHand": "hand_l",
        "LeftFingers": [f"{f}_0{i}_l" for f in ("index", "middle", "ring", "pinky") for i in (1, 2, 3)],
        "LeftThumb": ["thumb_01_l", "thumb_02_l", "thumb_03_l"],
        "RightShoulder": "clavicle_r", "RightUpperArm": "upperarm_r", "RightForearm": "lowerarm_r", "RightHand": "hand_r",
        "RightFingers": [f"{f}_0{i}_r" for f in ("index", "middle", "ring", "pinky") for i in (1, 2, 3)],
        "RightThumb": ["thumb_01_r", "thumb_02_r", "thumb_03_r"],
        "LeftThigh": "thigh_l", "LeftShin": "calf_l", "LeftFoot": "foot_l", "LeftToes": "ball_l",
        "RightThigh": "thigh_r", "RightShin": "calf_r", "RightFoot": "foot_r", "RightToes": "ball_r",
        "_palm": {"Left": {"index": "index_01_l", "pinky": "pinky_01_l"},
                  "Right": {"index": "index_01_r", "pinky": "pinky_01_r"}},
    },
    "mixamo": {
        "Root": "", "Hips": "mixamorig:Hips", "Spine01": "mixamorig:Spine", "Spine02": "mixamorig:Spine1",
        "Spine03": "mixamorig:Spine2", "Neck": "mixamorig:Neck", "Head": "mixamorig:Head",
        "LeftShoulder": "mixamorig:LeftShoulder", "LeftUpperArm": "mixamorig:LeftArm",
        "LeftForearm": "mixamorig:LeftForeArm", "LeftHand": "mixamorig:LeftHand",
        "LeftFingers": [f"mixamorig:LeftHand{f}{i}" for f in ("Index", "Middle", "Ring", "Pinky") for i in (1, 2, 3)],
        "LeftThumb": [f"mixamorig:LeftHandThumb{i}" for i in (1, 2, 3)],
        "RightShoulder": "mixamorig:RightShoulder", "RightUpperArm": "mixamorig:RightArm",
        "RightForearm": "mixamorig:RightForeArm", "RightHand": "mixamorig:RightHand",
        "RightFingers": [f"mixamorig:RightHand{f}{i}" for f in ("Index", "Middle", "Ring", "Pinky") for i in (1, 2, 3)],
        "RightThumb": [f"mixamorig:RightHandThumb{i}" for i in (1, 2, 3)],
        "LeftThigh": "mixamorig:LeftUpLeg", "LeftShin": "mixamorig:LeftLeg", "LeftFoot": "mixamorig:LeftFoot",
        "LeftToes": "mixamorig:LeftToeBase",
        "RightThigh": "mixamorig:RightUpLeg", "RightShin": "mixamorig:RightLeg", "RightFoot": "mixamorig:RightFoot",
        "RightToes": "mixamorig:RightToeBase",
        "_palm": {"Left": {"index": "mixamorig:LeftHandIndex1", "pinky": "mixamorig:LeftHandPinky1"},
                  "Right": {"index": "mixamorig:RightHandIndex1", "pinky": "mixamorig:RightHandPinky1"}},
    },
}


class Report:
    def __init__(self) -> None:
        self.rows = []

    def ok(self, msg: str) -> None:
        self.rows.append(("PASS", msg))

    def warn(self, msg: str) -> None:
        self.rows.append(("WARN", msg))

    def fail(self, msg: str) -> None:
        self.rows.append(("FAIL", msg))

    @property
    def failed(self) -> bool:
        return any(level == "FAIL" for level, _ in self.rows)

    def print(self) -> None:
        print("")
        print("=" * 72)
        print("VALIDATION SUMMARY")
        print("=" * 72)
        for level, msg in self.rows:
            print(f"[{level}] {msg}")
        print("=" * 72)
        if self.failed:
            print("RESULT: FAIL - fix the FAIL lines above before continuing.")
        else:
            print("RESULT: OK" + (" (with warnings)" if any(l == "WARN" for l, _ in self.rows) else ""))


def print_bone_tree(arm: bpy.types.Object) -> None:
    bones = arm.data.bones
    print("")
    print(f"ARMATURE '{arm.name}': {len(bones)} bones")
    print("-" * 72)

    def walk(bone, depth):
        head = bone.head_local
        print(f"{'  ' * depth}{bone.name:<{max(4, 32 - 2 * depth)}} head=({head.x:+.3f}, {head.y:+.3f}, {head.z:+.3f})"
              f"  length={bone.length:.3f}")
        for child in bone.children:
            walk(child, depth + 1)

    for root in [b for b in bones if b.parent is None]:
        walk(root, 0)
    print("-" * 72)
    print("All bone names (copy-paste friendly):")
    print(", ".join(b.name for b in bones))


def detect_preset(arm: bpy.types.Object):
    names = set(arm.data.bones.keys())
    best, best_score = None, 0.0
    for preset_name, preset in PRESETS.items():
        wanted = []
        for std, value in preset.items():
            if std.startswith("_"):
                continue
            wanted.extend([value] if isinstance(value, str) else value)
        wanted = [w for w in wanted if w]
        score = sum(1 for w in wanted if w in names) / max(1, len(wanted))
        if score > best_score:
            best, best_score = preset_name, score
    return best, best_score


def build_mapping_from_preset(preset_name: str, arm: bpy.types.Object, body: bpy.types.Object) -> dict:
    preset = PRESETS[preset_name]
    bones = {k: v for k, v in preset.items() if not k.startswith("_")}
    return {
        "_comment": "Generated by pipeline/validate_rig.py --write-mapping. Edit the right-hand side if your rig differs.",
        "rig_preset": preset_name,
        "armature_object": arm.name,
        "body_object": body.name,
        "shorts_object": "shorts",
        "bones": bones,
        "palm_reference": preset["_palm"],
        "neutral_overrides": {},
    }


def check_mapping(mapping: dict, arm: bpy.types.Object, report: Report) -> None:
    names = set(arm.data.bones.keys())
    missing_required, missing_optional, bad_names = [], [], []
    for std in common.STANDARD_BONES:
        real = common.mapped_bones(mapping, std)
        if not real:
            (missing_required if std in common.REQUIRED_BONES else missing_optional).append(std)
            continue
        for r in real:
            if r not in names:
                bad_names.append(f"{std} -> '{r}'")
    unknown = [k for k in mapping["bones"] if k not in common.STANDARD_BONES]
    if bad_names:
        report.fail("bone_mapping.json points at bones that do not exist: " + "; ".join(bad_names))
    if missing_required:
        report.fail("bone_mapping.json has no bone for required names: " + ", ".join(missing_required))
    if missing_optional:
        report.warn("Optional names not mapped (fine, they are skipped): " + ", ".join(missing_optional))
    if unknown:
        report.warn("bone_mapping.json has unknown standard names (ignored): " + ", ".join(unknown))
    palm = mapping.get("palm_reference", {})
    for side in ("Left", "Right"):
        ref = palm.get(side, {})
        if not ref or ref.get("index") not in names or ref.get("pinky") not in names:
            report.warn(f"palm_reference.{side} missing or invalid: grip points will be estimated from the hand bone only.")
    if not bad_names and not missing_required:
        mapped_count = sum(len(common.mapped_bones(mapping, s)) for s in common.STANDARD_BONES)
        report.ok(f"bone_mapping.json resolves all required names ({mapped_count} real bones mapped).")


def main() -> int:
    parser = argparse.ArgumentParser(prog="validate_rig.py")
    parser.add_argument("--write-mapping", action="store_true", help="Write a detected preset to assets/bone_mapping.json")
    parser.add_argument("--force", action="store_true", help="With --write-mapping: overwrite an existing bone_mapping.json")
    args = parser.parse_args(common.script_args())

    report = Report()
    print("")
    print(f"Blender {bpy.app.version_string}")
    print(f"File: {bpy.data.filepath or '(unsaved)'}")
    if not bpy.data.filepath:
        report.fail("No .blend file loaded. Run: blender --background assets/base_body.blend --python pipeline/validate_rig.py")

    print("")
    print("OBJECTS")
    print("-" * 72)
    for obj in bpy.data.objects:
        extra = f" verts={len(obj.data.vertices)}" if obj.type == "MESH" else ""
        parent = f" parent={obj.parent.name}" if obj.parent else ""
        print(f"  {obj.name:<28} type={obj.type:<9}{extra}{parent}")

    mapping = None
    if common.BONE_MAPPING_PATH.exists():
        mapping = common.load_bone_mapping()

    # --- armature ---
    try:
        arm = common.find_armature(mapping)
    except common.PipelineError as exc:
        report.fail(str(exc))
        report.print()
        return 1
    report.ok(f"Armature found: '{arm.name}' ({len(arm.data.bones)} bones).")
    print_bone_tree(arm)

    if any(abs(s - 1.0) > 1e-4 for s in arm.scale):
        report.fail(f"Armature scale is {tuple(round(s, 4) for s in arm.scale)}, expected (1, 1, 1). Select it in Blender, Ctrl+A > All Transforms.")

    # --- body ---
    try:
        body = common.find_body(arm, mapping)
    except common.PipelineError as exc:
        report.fail(str(exc))
        report.print()
        return 1
    report.ok(f"Body mesh found: '{body.name}' ({len(body.data.vertices)} vertices).")
    if any(abs(s - 1.0) > 1e-4 for s in body.scale):
        report.fail(f"Body scale is {tuple(round(s, 4) for s in body.scale)}, expected (1, 1, 1). Apply scale (Ctrl+A > All Transforms).")

    world_z = [(body.matrix_world @ v.co).z for v in body.data.vertices]
    height = max(world_z) - min(world_z)
    if 1.5 <= height <= 2.1:
        report.ok(f"Body height {height:.3f} m (1 unit = 1 meter).")
    elif 15 <= height <= 21:
        report.fail(f"Body height {height:.2f} units: the body was created in decimeters. Recreate it with MPFB scale factor 'Meter'.")
    else:
        report.fail(f"Body height {height:.3f} units is not a plausible human height in meters.")
    if abs(min(world_z)) > 0.05:
        report.warn(f"Lowest body point is at z={min(world_z):.3f}; expected feet on the ground (z=0). The pipeline grounds the body anyway.")

    # Facing direction: the nose should be at -Y (Blender front).
    head_bones = common.mapped_bones(mapping, "Head") if mapping else []
    if head_bones and head_bones[0] in arm.data.bones:
        head_z = (arm.matrix_world @ arm.data.bones[head_bones[0]].head_local).z
        head_verts = [body.matrix_world @ v.co for v in body.data.vertices if (body.matrix_world @ v.co).z > head_z]
        if head_verts:
            ys = [v.y for v in head_verts]
            if min(ys) + max(ys) < 0:
                report.ok("Body faces -Y (Blender front view). Correct.")
            else:
                report.warn("Body may be facing +Y. In Blender front view (numpad 1) the character should face you.")

    # --- shape keys / helpers / modifiers ---
    if body.data.shape_keys and len(body.data.shape_keys.key_blocks) > 0:
        report.warn(f"Body has {len(body.data.shape_keys.key_blocks)} shape keys. prep_mesh.py bakes them into the mesh.")
    helper_groups = [vg.name for vg in body.vertex_groups if vg.name.startswith(("helper-", "joint-"))
                     or vg.name in ("HelperGeometry", "JointCubes")]
    if "body" in body.vertex_groups and len(body.data.vertices) > 13500:
        report.warn("Body still contains MakeHuman helper geometry (hidden by a Mask modifier). prep_mesh.py deletes it.")
    elif helper_groups:
        report.warn(f"Helper vertex groups present ({len(helper_groups)}); prep_mesh.py removes them.")
    else:
        report.ok("No MakeHuman helper geometry.")
    other_mods = [m.type for m in body.modifiers if m.type not in ("ARMATURE",)]
    if other_mods:
        report.warn(f"Body has extra modifiers {other_mods}. prep_mesh.py removes MASK modifiers; others are kept.")

    # --- skeleton fit (bones must sit inside the mesh they deform) ---
    has_helpers = "body" in body.vertex_groups and len(body.data.vertices) > 13500
    if mapping is not None:
        _, offset = common.skeleton_alignment(arm, body, mapping)
        if offset is None:
            report.fail("Cannot measure skeleton fit: limb bones have no skin weights on the body.")
        elif offset <= 0.05:
            report.ok(f"Skeleton fits the body (limb offset {offset * 100:.1f} cm).")
        elif has_helpers:
            report.warn(f"Skeleton does NOT fit the body (limbs are {offset * 100:.0f} cm from their bones). "
                        "prep_mesh.py refits it - run it next.")
        else:
            report.fail(f"Skeleton does NOT fit the body (limbs are {offset * 100:.0f} cm from their bones). "
                        "Restore assets/base_body.backup.blend over assets/base_body.blend, then run prep_mesh.py.")

    # --- shorts ---
    shorts = common.find_shorts(mapping)
    if shorts is not None and len(shorts.data.vertices) < 100:
        report.fail(f"Shorts object '{shorts.name}' has only {len(shorts.data.vertices)} vertices (broken). Re-run prep_mesh.py.")
    elif shorts is not None:
        report.ok(f"Shorts object found: '{shorts.name}' ({len(shorts.data.vertices)} vertices).")
    elif "shorts" in body.vertex_groups:
        report.ok("Shorts vertex group 'shorts' found on the body. prep_mesh.py will build the shorts mesh from it.")
    else:
        report.warn("No 'shorts' object or vertex group. prep_mesh.py will generate procedural shorts.")

    # --- muscle groups ---
    try:
        mm = common.load_json(common.MUSCLE_MAPPING_PATH)
        prefix = mm["vertex_group_naming"]["prefix"]
        expected = []
        for key, info in mm["vertex_groups"].items():
            if info.get("bilateral"):
                expected += [prefix + key + mm["vertex_group_naming"]["left_suffix"],
                             prefix + key + mm["vertex_group_naming"]["right_suffix"]]
            else:
                expected.append(prefix + key)
        present = [n for n in expected if n in body.vertex_groups]
        used = set()
        for v in body.data.vertices:
            for g in v.groups:
                used.add(g.group)
        empty = [n for n in present if body.vertex_groups[n].index not in used]
        if present and empty:
            report.fail(f"{len(empty)} muscle vertex groups are empty: {', '.join(empty)}. Re-run prep_mesh.py.")
        elif len(present) == len(expected):
            report.ok(f"All {len(expected)} muscle vertex groups present and populated.")
        elif not present:
            report.warn(f"No muscle vertex groups yet (expected {len(expected)}). Run pipeline/prep_mesh.py.")
        else:
            missing = sorted(set(expected) - set(present))
            report.warn(f"{len(present)}/{len(expected)} muscle vertex groups present. Missing: {', '.join(missing)}. Run pipeline/prep_mesh.py.")
    except common.PipelineError as exc:
        report.warn(str(exc))

    # --- bone mapping ---
    preset_name, score = detect_preset(arm)
    if preset_name and score > 0.8:
        print("")
        print(f"Detected rig naming: {preset_name} ({score:.0%} of preset bones found)")
    if mapping is None:
        if args.write_mapping and preset_name and score > 0.8:
            common.save_json(common.BONE_MAPPING_PATH, build_mapping_from_preset(preset_name, arm, body))
            report.ok(f"Wrote {common.BONE_MAPPING_PATH.relative_to(common.REPO_ROOT)} from preset '{preset_name}'.")
            mapping = common.load_bone_mapping()
        else:
            hint = f" Detected preset '{preset_name}': re-run with '-- --write-mapping'." if preset_name and score > 0.8 else \
                " No known preset matched: copy the bone names printed above into assets/bone_mapping.json by hand."
            report.fail("assets/bone_mapping.json not found." + hint)
    elif args.write_mapping:
        if args.force and preset_name and score > 0.8:
            common.save_json(common.BONE_MAPPING_PATH, build_mapping_from_preset(preset_name, arm, body))
            report.ok(f"Overwrote bone_mapping.json from preset '{preset_name}'.")
            mapping = common.load_bone_mapping()
        else:
            report.warn("bone_mapping.json already exists; not overwritten (add --force to overwrite).")
    if mapping is not None:
        check_mapping(mapping, arm, report)

    report.print()
    return 1 if report.failed else 0


if __name__ == "__main__":
    common.run_main(main)
