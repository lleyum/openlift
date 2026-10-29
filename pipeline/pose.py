"""
OpenLift - rig-agnostic posing.

Poses are written in Standard Anatomical Names and anatomical angles, never in
real bone names or bone-local axes:

    "LeftUpperArm": {"rotation_deg": [flexion, twist, abduction]}

    X  flexion(+) / extension(-)      e.g. arm raised forward, knee bent, spine bent forward
    Y  twist: internal(+) / external(-) rotation; for the spine/neck/head: turn to the left(+)
    Z  abduction(+) / adduction(-)    e.g. arm raised sideways; spine/neck/head: lean to the left(+)

Special meanings: Shoulder (clavicle) X = protraction, Z = elevation (shrug).
Hand/Fingers/Thumb X = flexion toward the palm, Z = radial deviation. Foot X = dorsiflexion
(toes up), Z = toes turned out. Toes X = curl down.
Left and right use the SAME numbers for the same anatomical motion; mirroring is automatic.

All angles are relative to OpenLift's neutral pose (the "rest pose" in the schema):
standing upright, arms hanging slightly away from the body, palms facing the thighs,
legs straight. This module measures each bone of the real rig against that neutral pose
at runtime, so any reasonably proportioned humanoid rig works without retuning templates.

Root is special: its rotation_deg is a whole-body rotation in world axes
([pitch about X, roll about Y, yaw about Z]) around the point between the feet, and
location_m moves the whole body. Example: lying on the back = Root [-90, 0, 0].

Layering order (later layers win):
    1. apply_base_template(name)        template joints (set)
    2. apply_modifiers(names)           each modifier sets or adds joints (see base_templates.json)
    3. apply_grip(grip)                 forearm twist + finger curl for the exercise grip (add)
    4. apply_exercise_deltas(joints)    exercise joint_deltas (add)
    5. apply_manual_overrides(joints)   exercise custom_joint_overrides (set)
    6. commit()                         writes the pose to the armature, plants feet/hands, grounds the body

Then props.build_props() creates equipment and parent_props() attaches it to bones.
"""

from __future__ import annotations

import copy
import math
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bpy  # noqa: E402
from mathutils import Matrix, Quaternion, Vector  # noqa: E402

from pipeline import common  # noqa: E402

X = Vector((1.0, 0.0, 0.0))
Y = Vector((0.0, 1.0, 0.0))
Z = Vector((0.0, 0.0, 1.0))
UP = Z
FORWARD = Vector((0.0, -1.0, 0.0))  # Blender front view looks along +Y, so the character faces -Y

# Joint "kind" -> (flexion axis, abduction axis) for the LEFT side, in the neutral body frame
# (+X = character's left, -Y = forward, +Z = up). Right side axes are mirrored automatically.
AXES: Dict[str, Tuple[Vector, Vector]] = {
    "center": (X, Y),         # Hips, Spine01-03, Neck, Head: bend forward / lean left
    "shoulder": (-Z, -Y),     # clavicle: protraction / elevation
    "arm": (-X, -Y),          # UpperArm, Forearm
    "hand": (Y, -X),          # Hand, Fingers, Thumb: toward palm / radial deviation
    "thigh": (-X, -Y),
    "shin": (X, -Y),          # knee flexion moves the heel back
    "foot": (-X, Z),          # dorsiflexion / toes out
    "toes": (X, Z),           # curl down
}

STANDARD_INFO: Dict[str, Tuple[str, str]] = {
    # standard name: (kind, side)  side in {"L", "R", "C"}
    "Root": ("root", "C"),
    "Hips": ("center", "C"), "Spine01": ("center", "C"), "Spine02": ("center", "C"),
    "Spine03": ("center", "C"), "Neck": ("center", "C"), "Head": ("center", "C"),
}
for _side, _p in (("L", "Left"), ("R", "Right")):
    STANDARD_INFO.update({
        f"{_p}Shoulder": ("shoulder", _side), f"{_p}UpperArm": ("arm", _side), f"{_p}Forearm": ("arm", _side),
        f"{_p}Hand": ("hand", _side), f"{_p}Fingers": ("hand", _side), f"{_p}Thumb": ("hand", _side),
        f"{_p}Thigh": ("thigh", _side), f"{_p}Shin": ("shin", _side), f"{_p}Foot": ("foot", _side),
        f"{_p}Toes": ("toes", _side),
    })


def _neutral_direction(std: str) -> Optional[Vector]:
    """Neutral (standing) direction of a limb bone, or None to keep the rig's rest direction."""
    kind, side = STANDARD_INFO.get(std, ("", "C"))
    sx = 1.0 if side == "L" else -1.0
    if std.endswith(("UpperArm", "Forearm", "Hand")):
        a = math.radians(8.0)
        return Vector((sx * math.sin(a), 0.0, -math.cos(a)))
    if std.endswith("Thigh"):
        a = math.radians(2.0)
        return Vector((sx * math.sin(a), 0.0, -math.cos(a)))
    if std.endswith("Shin"):
        return Vector((0.0, 0.0, -1.0))
    return None


def _mirror_axis(v: Vector) -> Vector:
    """Mirror a rotation axis (axial vector) across the body's midline (x -> -x)."""
    return Vector((v.x, -v.y, -v.z))


def _quat(axis: Vector, degrees: float) -> Quaternion:
    if abs(degrees) < 1e-9:
        return Quaternion()
    return Quaternion(axis.normalized(), math.radians(degrees))


def _rot3(values: Sequence[float]) -> List[float]:
    return [float(values[0]), float(values[1]), float(values[2])]


class PoseEngine:
    """Holds an anatomical pose for one armature and writes it to the rig."""

    def __init__(self, armature: bpy.types.Object, body: Optional[bpy.types.Object] = None,
                 mapping: Optional[Dict] = None, templates: Optional[Dict] = None):
        self.mapping = mapping if mapping is not None else common.load_bone_mapping()
        self.templates = templates if templates is not None else common.load_json(common.TEMPLATES_PATH)
        self.arm = armature
        self.body = body if body is not None else common.find_body(armature, self.mapping)
        self.real_to_std: Dict[str, str] = {}
        for std in common.STANDARD_BONES:
            for real in common.mapped_bones(self.mapping, std):
                if real in self.arm.data.bones:
                    self.real_to_std[real] = std
        missing = [s for s in common.REQUIRED_BONES if not any(r in self.arm.data.bones for r in common.mapped_bones(self.mapping, s))]
        if missing:
            raise common.PipelineError(
                "bone_mapping.json does not resolve these required bones: " + ", ".join(missing)
                + ". Run pipeline/validate_rig.py."
            )
        self._order = self._hierarchy_order()
        self._neutral = self._compute_neutral()
        self.reset()

    # ------------------------------------------------------------------ state

    def reset(self) -> None:
        self.joints: Dict[str, Dict[str, List[float]]] = {}
        self.template_name: Optional[str] = None
        self.plant_feet: List[str] = []
        self.plant_hands: List[str] = []
        self.ground: Dict = {"mode": "lowest", "clearance_m": 0.0}
        self.idle_sides: set = set()
        self.posed: Dict[str, Matrix] = {}
        self.posed_delta: Dict[str, Quaternion] = {}
        for pb in self.arm.pose.bones:
            pb.matrix_basis = Matrix.Identity(4)
        self.arm.location = (0.0, 0.0, 0.0)
        self.arm.rotation_euler = (0.0, 0.0, 0.0)

    def _get(self, std: str) -> Dict[str, List[float]]:
        if std not in STANDARD_INFO:
            raise common.PipelineError(f"Unknown standard bone name '{std}'.")
        return self.joints.setdefault(std, {"rotation_deg": [0.0, 0.0, 0.0], "location_m": [0.0, 0.0, 0.0]})

    def _apply_joint_map(self, joints: Dict, mode: str, source: str) -> None:
        for std, value in (joints or {}).items():
            if std.startswith("_"):
                continue
            if std not in STANDARD_INFO:
                raise common.PipelineError(f"{source}: unknown bone '{std}'. Use Standard Anatomical Names.")
            entry = self._get(std)
            if "rotation_deg" in value:
                rot = _rot3(value["rotation_deg"])
                entry["rotation_deg"] = rot if mode == "set" else [a + b for a, b in zip(entry["rotation_deg"], rot)]
            if "location_m" in value:
                loc = _rot3(value["location_m"])
                entry["location_m"] = loc if mode == "set" else [a + b for a, b in zip(entry["location_m"], loc)]

    # ------------------------------------------------------------------ layers

    def apply_base_template(self, template_name: str) -> None:
        templates = self.templates["templates"]
        if template_name not in templates:
            raise common.PipelineError(f"Unknown base_template '{template_name}'. Defined: {', '.join(sorted(templates))}")
        self.reset()
        tpl = templates[template_name]
        self.template_name = template_name
        self._apply_joint_map(tpl.get("joints", {}), "set", f"template {template_name}")
        self.plant_feet = list(tpl.get("plant_feet", ["Left", "Right"]))
        self.plant_hands = list(tpl.get("plant_hands", []))
        self.ground = dict(tpl.get("ground", {"mode": "lowest", "clearance_m": 0.0}))

    def apply_modifiers(self, modifier_names: Iterable[str]) -> List[str]:
        """Apply named pose modifiers in order. Returns the names that were not found (warnings)."""
        defs = self.templates.get("modifiers", {})
        unknown = []
        for name in modifier_names:
            mod = defs.get(name)
            if mod is None:
                unknown.append(name)
                continue
            variant = mod.get("variants", {}).get(self.template_name or "", {})
            joints = variant.get("joints", mod.get("joints", {}))
            mode = variant.get("mode", mod.get("mode", "set"))
            self._apply_joint_map(joints, mode, f"modifier {name}")
            for key in ("plant_feet", "plant_hands"):
                if key in variant or key in mod:
                    setattr(self, key, list(variant.get(key, mod.get(key))))
            if "ground" in variant or "ground" in mod:
                self.ground = dict(variant.get("ground", mod.get("ground")))
            for side in variant.get("idle_sides", mod.get("idle_sides", [])):
                self.idle_sides.add(side)
        return unknown

    def apply_grip(self, grip: Optional[Dict]) -> None:
        if not grip:
            return
        grips = self.templates.get("grips", {})
        spec = grips.get(grip.get("type", "none"))
        if spec is None:
            raise common.PipelineError(f"Unknown grip type '{grip.get('type')}'.")
        joints = {}
        for std, value in spec.get("joints", {}).items():
            side = "Left" if std.startswith("Left") else "Right" if std.startswith("Right") else ""
            if side and side in self.idle_sides:
                continue
            joints[std] = value
        self._apply_joint_map(joints, "add", f"grip {grip.get('type')}")

    def apply_exercise_deltas(self, joints_dict: Optional[Dict]) -> None:
        self._apply_joint_map(joints_dict or {}, "add", "joint_deltas")

    def apply_manual_overrides(self, overrides_dict: Optional[Dict]) -> None:
        self._apply_joint_map(overrides_dict or {}, "set", "custom_joint_overrides")

    def pose_exercise(self, exercise: Dict) -> List[str]:
        """Apply every layer of an exercise record and commit. Returns warnings."""
        warnings = []
        self.apply_base_template(exercise["base_template"])
        unknown = self.apply_modifiers(exercise.get("pose_modifiers", []))
        warnings += [f"Unknown pose modifier '{m}' ignored." for m in unknown]
        self.apply_grip(exercise.get("grip"))
        self.apply_exercise_deltas(exercise.get("joint_deltas"))
        self.apply_manual_overrides(exercise.get("custom_joint_overrides"))
        self.commit()
        return warnings

    # ------------------------------------------------------------------ math

    def _hierarchy_order(self) -> List[bpy.types.Bone]:
        out: List[bpy.types.Bone] = []

        def walk(bone):
            out.append(bone)
            for child in bone.children:
                walk(child)

        for bone in self.arm.data.bones:
            if bone.parent is None:
                walk(bone)
        return out

    def _rest_dir(self, bone: bpy.types.Bone) -> Vector:
        return (bone.tail_local - bone.head_local).normalized()

    def _hand_axis_rest(self, side: str) -> Tuple[Optional[Vector], Optional[Vector]]:
        """(wrist->knuckles direction, index->pinky knuckle axis) in rest pose, or (None, None)."""
        ref = self.mapping.get("palm_reference", {}).get("Left" if side == "L" else "Right", {})
        bones = self.arm.data.bones
        hand = common.mapped_bones(self.mapping, ("Left" if side == "L" else "Right") + "Hand")
        if not hand or hand[0] not in bones:
            return None, None
        if ref.get("index") in bones and ref.get("pinky") in bones:
            idx = bones[ref["index"]].head_local
            pky = bones[ref["pinky"]].head_local
            mid = (idx + pky) * 0.5
            return (mid - bones[hand[0]].head_local).normalized(), (idx - pky).normalized()
        return self._rest_dir(bones[hand[0]]), None

    def _compute_neutral(self) -> Dict[str, Quaternion]:
        """Rotation that takes each real bone from the rig's rest pose to OpenLift neutral."""
        overrides = self.mapping.get("neutral_overrides", {}) or {}
        neutral: Dict[str, Quaternion] = {}
        for bone in self._order:
            n_parent = neutral[bone.parent.name] if bone.parent else Quaternion()
            std = self.real_to_std.get(bone.name)
            n_bone = n_parent.copy()
            if std:
                target = None
                if std in overrides and isinstance(overrides[std], dict) and "direction" in overrides[std]:
                    target = Vector(overrides[std]["direction"]).normalized()
                else:
                    target = _neutral_direction(std)
                if target is not None:
                    kind, side = STANDARD_INFO[std]
                    rest_dir = self._rest_dir(bone)
                    knuckles = None
                    if std.endswith("Hand"):
                        hand_dir, knuckles = self._hand_axis_rest(side)
                        if hand_dir is not None:
                            rest_dir = hand_dir
                    current = n_parent @ rest_dir
                    swing = current.rotation_difference(target)
                    n_bone = swing @ n_parent
                    if knuckles is not None:
                        # Twist the hand so the index knuckle faces forward (palm faces the thigh).
                        k = n_bone @ knuckles
                        k_proj = (k - k.dot(target) * target)
                        want = FORWARD - FORWARD.dot(target) * target
                        if k_proj.length > 1e-6 and want.length > 1e-6:
                            k_proj.normalize()
                            want.normalize()
                            angle = k_proj.angle(want)
                            if k_proj.cross(want).dot(target) < 0:
                                angle = -angle
                            n_bone = Quaternion(target, angle) @ n_bone
            neutral[bone.name] = n_bone
        return neutral

    def _anatomical_quat(self, std: str, rot: Sequence[float], neutral_dir: Vector) -> Quaternion:
        kind, side = STANDARD_INFO[std]
        flex_axis, abd_axis = AXES[kind]
        twist_sign = 1.0
        if side == "R":
            flex_axis, abd_axis = _mirror_axis(flex_axis), _mirror_axis(abd_axis)
            twist_sign = -1.0
        flex, twist, abd = rot
        return _quat(flex_axis, flex) @ _quat(abd_axis, abd) @ _quat(neutral_dir, twist_sign * twist)

    def _root_matrix(self) -> Matrix:
        entry = self.joints.get("Root", {"rotation_deg": [0, 0, 0], "location_m": [0, 0, 0]})
        rx, ry, rz = [math.radians(a) for a in entry["rotation_deg"]]
        rot = (Matrix.Rotation(rz, 4, "Z") @ Matrix.Rotation(ry, 4, "Y") @ Matrix.Rotation(rx, 4, "X"))
        return Matrix.Translation(Vector(entry["location_m"])) @ rot

    def _neutral_frame_dir(self, bone: bpy.types.Bone) -> Vector:
        """Bone direction in the neutral pose (used as the twist axis)."""
        std = self.real_to_std.get(bone.name)
        if std:
            d = _neutral_direction(std)
            if d is not None:
                return d
        return (self._neutral[bone.name] @ self._rest_dir(bone)).normalized()

    def commit(self) -> None:
        """Write the current anatomical pose to the armature, plant feet/hands, then ground the body."""
        common.ensure_object_mode()
        root_m = self._root_matrix()
        root_q = root_m.to_quaternion()
        rest = {b.name: b.matrix_local.copy() for b in self.arm.data.bones}
        delta: Dict[str, Quaternion] = {}
        posed: Dict[str, Matrix] = {}
        plant_feet = {f"{s}Foot" for s in self.plant_feet}
        plant_hands = {f"{s}Hand" for s in self.plant_hands}

        for bone in self._order:
            name = bone.name
            std = self.real_to_std.get(name)
            # base: where the bone would be with an identity pose (matrix_basis = I).
            # head: where its head actually goes (parent motion, or the whole-body Root transform).
            if bone.parent is not None:
                p_parent = delta[bone.parent.name]
                n_parent = self._neutral[bone.parent.name]
                base = posed[bone.parent.name] @ rest[bone.parent.name].inverted() @ rest[name]
                head = base.translation.copy()
            else:
                p_parent = root_q
                n_parent = Quaternion()
                base = rest[name]
                head = (root_m @ rest[name]).translation.copy()
            q_parent = p_parent @ n_parent.inverted()
            n_bone = self._neutral[name]

            a = Quaternion()
            loc_offset = Vector()
            if std and std != "Root" and std in self.joints:
                entry = self.joints[std]
                a = self._anatomical_quat(std, entry["rotation_deg"], self._neutral_frame_dir(bone))
                if std == "Hips":
                    loc_offset = root_q @ Vector(entry["location_m"])
            p_bone = q_parent @ a @ n_bone

            # Planting: keep the sole flat on the floor / the palm flat on the floor.
            if std in plant_feet and std.endswith("Foot") and bone.name == common.mapped_bones(self.mapping, std)[0]:
                up_now = (p_bone @ n_bone.inverted()) @ UP
                p_bone = up_now.rotation_difference(UP) @ p_bone
            if std in plant_hands and std.endswith("Hand"):
                side = "L" if std.startswith("Left") else "R"
                palm_neutral = Vector((-1.0, 0.0, 0.0)) if side == "L" else Vector((1.0, 0.0, 0.0))
                palm_now = (p_bone @ n_bone.inverted()) @ palm_neutral
                p_bone = palm_now.rotation_difference(Vector((0.0, 0.0, -1.0))) @ p_bone

            rot = p_bone.to_matrix() @ rest[name].to_3x3()
            desired = Matrix.Translation(head + loc_offset) @ rot.to_4x4()
            self.arm.pose.bones[name].matrix_basis = base.inverted() @ desired
            delta[name] = p_bone
            posed[name] = desired

        self.posed = posed
        self.posed_delta = delta
        bpy.context.view_layer.update()
        self._ground()

    # ------------------------------------------------------------------ evaluated geometry

    def evaluated_body_points(self, objects: Optional[Sequence[bpy.types.Object]] = None) -> List[Vector]:
        objects = objects or [self.body]
        deps = bpy.context.evaluated_depsgraph_get()
        pts: List[Vector] = []
        for obj in objects:
            ev = obj.evaluated_get(deps)
            mesh = ev.to_mesh()
            mw = ev.matrix_world
            pts.extend(mw @ v.co for v in mesh.vertices)
            ev.to_mesh_clear()
        return pts

    def _ground(self) -> None:
        mode = self.ground.get("mode", "lowest")
        if mode == "none":
            return
        clearance = float(self.ground.get("clearance_m", 0.0))
        pts = self.evaluated_body_points()
        lowest = min(p.z for p in pts)
        self.arm.location.z += clearance - lowest
        bpy.context.view_layer.update()

    # ------------------------------------------------------------------ landmarks (world space)

    def bone_world(self, real_name: str) -> Matrix:
        return self.arm.matrix_world @ self.arm.pose.bones[real_name].matrix

    def std_world(self, std: str) -> Matrix:
        bones = common.mapped_bones(self.mapping, std)
        if not bones:
            raise common.PipelineError(f"Standard bone '{std}' is not mapped.")
        return self.bone_world(bones[0])

    def std_head(self, std: str) -> Vector:
        return self.std_world(std).translation.copy()

    def std_tail(self, std: str) -> Vector:
        bones = common.mapped_bones(self.mapping, std)
        pb = self.arm.pose.bones[bones[0]]
        return self.arm.matrix_world @ pb.tail

    def body_axes(self, std: str = "Spine02") -> Tuple[Vector, Vector, Vector]:
        """(left, forward, up) unit vectors of a body segment in world space."""
        bones = common.mapped_bones(self.mapping, std)
        q = self.posed_delta.get(bones[0], Quaternion()) @ self._neutral[bones[0]].inverted()
        return (q @ X).normalized(), (q @ FORWARD).normalized(), (q @ UP).normalized()

    def grip_frame(self, side: str) -> Dict[str, Vector]:
        """Grip point and axes for 'Left' or 'Right' hand, in world space.

        center: where the middle of a handle sits inside the closed fist
        axis:   handle direction (index knuckle -> little-finger knuckle)
        palm:   palm normal
        along:  wrist -> knuckles direction
        """
        hand = common.mapped_bones(self.mapping, f"{side}Hand")[0]
        mw = self.arm.matrix_world
        wrist = mw @ self.arm.pose.bones[hand].head
        ref = self.mapping.get("palm_reference", {}).get(side, {})
        pbs = self.arm.pose.bones
        if ref.get("index") in pbs and ref.get("pinky") in pbs:
            idx = mw @ pbs[ref["index"]].head
            pky = mw @ pbs[ref["pinky"]].head
            knuckles = (idx + pky) * 0.5
            axis = (pky - idx).normalized()
            along = (knuckles - wrist).normalized()
        else:
            q = self.posed_delta[hand] @ self._neutral[hand].inverted()
            along = (q @ Vector((0, 0, -1))).normalized()
            axis = (q @ Vector((0, 1, 0))).normalized()
            knuckles = wrist + along * 0.09
        sign = 1.0 if side == "Left" else -1.0
        palm = (sign * along.cross(-axis)).normalized()
        center = knuckles + palm * 0.028 - along * 0.005
        return {"center": center, "axis": axis, "palm": palm, "along": along, "wrist": wrist}

    def segment_vertices(self, std_names: Sequence[str], min_weight: float = 0.5) -> List[Vector]:
        """Evaluated world positions of body vertices mostly weighted to the given standard bones."""
        names = set()
        for std in std_names:
            names.update(common.mapped_bones(self.mapping, std))
        idx = {vg.index for vg in self.body.vertex_groups if vg.name in names}
        if not idx:
            return []
        deps = bpy.context.evaluated_depsgraph_get()
        ev = self.body.evaluated_get(deps)
        mesh = ev.to_mesh()
        mw = ev.matrix_world
        out = []
        src = self.body.data.vertices
        for v_src, v_ev in zip(src, mesh.vertices):
            w = sum(g.weight for g in v_src.groups if g.group in idx)
            if w >= min_weight:
                out.append(mw @ v_ev.co)
        ev.to_mesh_clear()
        return out


# ---------------------------------------------------------------------- module-level API

_ENGINE: Optional[PoseEngine] = None


def get_engine() -> PoseEngine:
    global _ENGINE
    if _ENGINE is None:
        mapping = common.load_bone_mapping()
        _ENGINE = PoseEngine(common.find_armature(mapping), mapping=mapping)
    return _ENGINE


def apply_base_template(template_name: str) -> None:
    get_engine().apply_base_template(template_name)


def apply_exercise_deltas(joints_dict: Optional[Dict]) -> None:
    get_engine().apply_exercise_deltas(joints_dict)


def apply_manual_overrides(overrides_dict: Optional[Dict]) -> None:
    get_engine().apply_manual_overrides(overrides_dict)


def parent_props(props: Sequence[Tuple[bpy.types.Object, str]], engine: Optional[PoseEngine] = None) -> None:
    """Parent prop objects to the bone they are attached to, keeping their current world transform.

    props: list of (object, attach) where attach is a schema attach point
           (hands, left_hand, right_hand, feet, left_foot, right_foot, upper_back, front_rack, pelvis, world).
    """
    engine = engine or get_engine()
    attach_to_std = {
        "hands": "RightHand", "right_hand": "RightHand", "left_hand": "LeftHand",
        "feet": None, "left_foot": "LeftFoot", "right_foot": "RightFoot",
        "upper_back": "Spine03", "front_rack": "Spine03", "pelvis": "Hips", "world": None,
    }
    bpy.context.view_layer.update()
    for obj, attach in props:
        std = attach_to_std.get(attach)
        if std is None:
            continue
        bone = common.mapped_bones(engine.mapping, std)[0]
        world = obj.matrix_world.copy()
        obj.parent = engine.arm
        obj.parent_type = "BONE"
        obj.parent_bone = bone
        bpy.context.view_layer.update()
        obj.matrix_world = world


def pose_from_exercise(exercise: Dict) -> Tuple[PoseEngine, List[str]]:
    engine = get_engine()
    warnings = engine.pose_exercise(exercise)
    return engine, warnings
