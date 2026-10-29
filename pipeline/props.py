"""
OpenLift - procedural equipment proxies.

build_props(engine, exercise) reads exercise["props"] (written by enrich_csv.py) and creates
simple low-poly meshes positioned against the POSED body:

  hand-held   barbell, ez_bar, dumbbell, kettlebell, medicine_ball, resistance_band,
              cable_handle, cable_rope, cable_bar, machine_handle   -> placed in the grip
  supports    bench, seat, backrest, preacher_pad, leg_pad, footplate, box,
              stability_ball, foam_roller                            -> fitted to the body surface
  structures  pullup_bar, parallel_bars, cable_frame, machine_frame  -> anchor hands/feet

Supports are fitted, not hard-coded: a pad is placed so its surface touches the body's
vertices for that region (e.g. the bench top touches the lowest point of the back). This keeps
props correct when templates or the base mesh change.

Run after pose.PoseEngine.commit(). Call pose.parent_props() afterwards to attach props to bones.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

from pipeline import common  # noqa: E402

UP = Vector((0.0, 0.0, 1.0))
COLLECTION = "OL_Props"

MATERIALS = {
    "metal": "#3A3A3A",      # bars, handles, frames
    "plate": "#1C1C1C",      # weight plates, dumbbell heads, kettlebells
    "pad": "#262626",        # upholstered pads
    "cable": "#111111",      # cables and ropes
    "ball": "#2E2E2E",       # medicine / stability balls, foam roller
}

HANDHELD = {"barbell", "ez_bar", "dumbbell", "kettlebell", "medicine_ball", "resistance_band",
            "cable_handle", "cable_rope", "cable_bar", "machine_handle"}
LIFTERS = {"box", "stability_ball", "foam_roller"}


# ---------------------------------------------------------------------------- materials


def material(kind: str) -> bpy.types.Material:
    name = f"OL_{kind}"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        common.enable_nodes(mat)
        color = common.hex_to_linear_rgba(MATERIALS[kind])
        mat.diffuse_color = color
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = color
            bsdf.inputs["Roughness"].default_value = 0.45 if kind in ("metal", "plate") else 0.8
            if "Metallic" in bsdf.inputs:
                bsdf.inputs["Metallic"].default_value = 0.6 if kind == "metal" else 0.0
    return mat


# ---------------------------------------------------------------------------- mesh primitives


class MeshBuilder:
    """Accumulates primitives into one bmesh, with one material slot per kind."""

    def __init__(self) -> None:
        self.bm = bmesh.new()
        self.kinds: List[str] = []

    def _slot(self, kind: str) -> int:
        if kind not in self.kinds:
            self.kinds.append(kind)
        return self.kinds.index(kind)

    def _finish(self, geom_faces, kind: str) -> None:
        slot = self._slot(kind)
        for f in geom_faces:
            f.material_index = slot
            f.smooth = False

    def cylinder(self, p0: Vector, p1: Vector, radius: float, kind: str = "metal", segments: int = 16) -> None:
        axis = p1 - p0
        length = axis.length
        if length < 1e-5:
            return
        ret = bmesh.ops.create_cone(self.bm, cap_ends=True, cap_tris=False, segments=segments,
                                    radius1=radius, radius2=radius, depth=length)
        rot = axis.normalized().to_track_quat("Z", "Y").to_matrix().to_4x4()
        mat = Matrix.Translation((p0 + p1) * 0.5) @ rot
        bmesh.ops.transform(self.bm, matrix=mat, verts=ret["verts"])
        faces = {f for v in ret["verts"] for f in v.link_faces}
        self._finish(faces, kind)

    def box(self, center: Vector, size: Sequence[float], rot: Optional[Matrix] = None, kind: str = "pad") -> None:
        ret = bmesh.ops.create_cube(self.bm, size=1.0)
        scale = Matrix.Diagonal(Vector((size[0], size[1], size[2], 1.0)))
        r = (rot or Matrix.Identity(3)).to_4x4()
        bmesh.ops.transform(self.bm, matrix=Matrix.Translation(center) @ r @ scale, verts=ret["verts"])
        faces = {f for v in ret["verts"] for f in v.link_faces}
        self._finish(faces, kind)

    def sphere(self, center: Vector, radius: float, kind: str = "ball") -> None:
        ret = bmesh.ops.create_uvsphere(self.bm, u_segments=20, v_segments=12, radius=radius)
        bmesh.ops.translate(self.bm, vec=center, verts=ret["verts"])
        faces = {f for v in ret["verts"] for f in v.link_faces}
        self._finish(faces, kind)
        for f in faces:
            f.smooth = True

    def to_object(self, name: str, ptype: str, attach: str) -> bpy.types.Object:
        mesh = bpy.data.meshes.new(name)
        self.bm.to_mesh(mesh)
        self.bm.free()
        obj = bpy.data.objects.new(name, mesh)
        common.get_or_create_collection(COLLECTION).objects.link(obj)
        for kind in self.kinds:
            mesh.materials.append(material(kind))
        obj["openlift_prop"] = ptype
        obj["openlift_attach"] = attach
        return obj


# ---------------------------------------------------------------------------- geometry helpers


def _horizontal(v: Vector) -> Vector:
    h = Vector((v.x, v.y, 0.0))
    return h.normalized() if h.length > 1e-6 else Vector((0.0, -1.0, 0.0))


def _basis_from(u: Vector, n: Vector) -> Matrix:
    """3x3 rotation whose X = u (length axis), Z = n (pad normal)."""
    n = n.normalized()
    u = (u - u.dot(n) * n).normalized()
    w = n.cross(u)
    return Matrix((u, w, n)).transposed()


def _fit_pad(points: Sequence[Vector], u: Vector, n: Vector, length: float, width: float,
             thickness: float, shift: float = 0.0) -> Tuple[Vector, Matrix]:
    """Place a pad (box) so its top face (normal n) touches the extreme points along -n.

    Returns (box center, rotation). The pad is centered on the points' centroid along u, plus shift.
    """
    n = n.normalized()
    rot = _basis_from(u, n)
    u_axis = rot.col[0]
    contact = min(p.dot(n) for p in points)
    centroid = sum(points, Vector()) / len(points)
    top_center = centroid - (centroid.dot(n) - contact) * n + u_axis * shift
    center = top_center - n * (thickness * 0.5)
    return center, rot


def _pad_corners_bottom(center: Vector, rot: Matrix, size: Sequence[float]) -> List[Vector]:
    u, w, n = rot.col[0], rot.col[1], rot.col[2]
    out = []
    for su in (-0.42, 0.42):
        for sw in (-0.3, 0.3):
            out.append(center + u * (size[0] * su) + w * (size[1] * sw) - n * (size[2] * 0.5))
    return out


def _posts_to_floor(mb: MeshBuilder, tops: Sequence[Vector], radius: float = 0.025) -> None:
    for t in tops:
        if t.z > 0.02:
            mb.cylinder(Vector((t.x, t.y, 0.0)), t, radius, "metal", 8)


# ---------------------------------------------------------------------------- context


class PropContext:
    def __init__(self, engine, exercise: Dict):
        self.engine = engine
        self.exercise = exercise
        self.template = exercise.get("base_template", "")
        self.modifiers = set(exercise.get("pose_modifiers", []))
        left, fwd, up = engine.body_axes("Spine02")
        self.torso_left, self.torso_fwd, self.torso_up = left, fwd, up
        self.fwd_h = _horizontal(fwd if abs(fwd.z) < 0.9 else -up)
        self.left_h = _horizontal(UP.cross(self.fwd_h)) * -1.0
        self.anchors: List[Vector] = []

    def grip(self, side: str) -> Dict[str, Vector]:
        return self.engine.grip_frame(side)

    def both_grips(self) -> Tuple[Dict[str, Vector], Dict[str, Vector]]:
        return self.grip("Left"), self.grip("Right")

    def verts(self, stds: Sequence[str], min_weight: float = 0.5) -> List[Vector]:
        return self.engine.segment_vertices(stds, min_weight)

    def head(self, std: str) -> Vector:
        return self.engine.std_head(std)


# ---------------------------------------------------------------------------- hand-held


def _bar_through(ctx: PropContext, center: Vector, axis: Vector, mb: MeshBuilder, half_len: float,
                 sleeve_from: float, plate_radius: float, ez: bool = False) -> None:
    axis = axis.normalized()
    a, b = center - axis * sleeve_from, center + axis * sleeve_from
    if ez:
        # Shallow zig-zag in the grip section.
        perp = axis.cross(UP)
        if perp.length < 1e-3:
            perp = axis.cross(Vector((1, 0, 0)))
        perp.normalize()
        pts = [a, center - axis * 0.2 + perp * 0.02, center - axis * 0.1 - perp * 0.02,
               center + axis * 0.1 - perp * 0.02, center + axis * 0.2 + perp * 0.02, b]
        for p0, p1 in zip(pts, pts[1:]):
            mb.cylinder(p0, p1, 0.014, "metal", 12)
    else:
        mb.cylinder(a, b, 0.014, "metal", 12)
    for s in (-1.0, 1.0):
        collar = center + axis * s * sleeve_from
        end = center + axis * s * half_len
        mb.cylinder(collar, collar + axis * s * 0.03, 0.035, "metal", 16)
        mb.cylinder(collar + axis * s * 0.03, end, 0.025, "metal", 16)
        p = collar + axis * s * 0.05
        mb.cylinder(p, p + axis * s * 0.06, plate_radius, "plate", 32)


def build_barbell(ctx: PropContext, prop: Dict, ez: bool = False) -> bpy.types.Object:
    gl, gr = ctx.both_grips()
    attach = prop["attach"]
    axis = (gl["center"] - gr["center"])
    center = (gl["center"] + gr["center"]) * 0.5
    if axis.length < 0.05:
        axis = ctx.torso_left.copy()
    if attach == "upper_back":
        neck = ctx.head("Neck")
        center = neck - ctx.torso_fwd * 0.07 - ctx.torso_up * 0.05
        axis = ctx.torso_left
    elif attach == "front_rack":
        neck = ctx.head("Neck")
        center = neck + ctx.torso_fwd * 0.11 - ctx.torso_up * 0.03
        axis = ctx.torso_left
    elif attach == "pelvis":
        hips = ctx.head("Hips")
        pelvis_pts = ctx.verts(["Hips"])
        reach = max((p - hips).dot(ctx.torso_fwd) for p in pelvis_pts) if pelvis_pts else 0.12
        center = hips + ctx.torso_fwd * (reach + 0.03)
        axis = ctx.torso_left
    mb = MeshBuilder()
    if ez:
        _bar_through(ctx, center, axis, mb, 0.62, 0.42, 0.15, ez=True)
    else:
        _bar_through(ctx, center, axis, mb, 1.10, 0.66, 0.225)
    return mb.to_object("OL_" + ("ez_bar" if ez else "barbell"), prop["type"], attach)


def _hand_sides(attach: str) -> List[str]:
    return {"left_hand": ["Left"], "right_hand": ["Right"], "hands": ["Left", "Right"]}.get(attach, ["Right"])


def build_dumbbell(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    if prop["attach"] == "hands":
        gl, gr = ctx.both_grips()
        center = (gl["center"] + gr["center"]) * 0.5
        axis = UP.copy() if (gl["center"] - gr["center"]).length < 0.25 else (gl["center"] - gr["center"]).normalized()
        if "ARMS_GOBLET" not in ctx.modifiers:
            axis = (gl["center"] - gr["center"]).normalized()
    else:
        g = ctx.grip(_hand_sides(prop["attach"])[0])
        center, axis = g["center"], g["axis"]
    axis = axis.normalized()
    mb.cylinder(center - axis * 0.07, center + axis * 0.07, 0.016, "metal", 12)
    for s in (-1.0, 1.0):
        p0 = center + axis * s * 0.07
        mb.cylinder(p0, p0 + axis * s * 0.07, 0.055, "plate", 6)
    return mb.to_object(f"OL_dumbbell_{prop['attach']}", prop["type"], prop["attach"])


def build_kettlebell(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    if prop["attach"] == "hands":
        gl, gr = ctx.both_grips()
        center = (gl["center"] + gr["center"]) * 0.5
        axis = (gl["center"] - gr["center"]).normalized() if (gl["center"] - gr["center"]).length > 0.02 else ctx.torso_left
        palm = (gl["palm"] - gr["palm"]).normalized()
    else:
        g = ctx.grip(_hand_sides(prop["attach"])[0])
        center, axis, palm = g["center"], g["axis"], g["palm"]
    head_z = ctx.head("Head").z
    if center.z > head_z:
        hang = -(palm.normalized())  # racked overhead: bell rests on the back of the forearm
        hang = (hang - UP * 0.6).normalized()
    else:
        hang = Vector((0.0, 0.0, -1.0))
    axis = (axis - axis.dot(hang) * hang).normalized()
    mb.cylinder(center - axis * 0.06, center + axis * 0.06, 0.014, "plate", 10)
    for s in (-1.0, 1.0):
        end = center + axis * s * 0.06
        mb.cylinder(end, end + hang * 0.07 + axis * s * 0.01, 0.014, "plate", 10)
    mb.sphere(center + hang * 0.16, 0.105, "plate")
    return mb.to_object(f"OL_kettlebell_{prop['attach']}", prop["type"], prop["attach"])


def build_medicine_ball(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    gl, gr = ctx.both_grips()
    center = (gl["center"] + gr["center"]) * 0.5 + ((gl["palm"] - gr["palm"]).normalized() * 0.0)
    mb = MeshBuilder()
    mb.sphere(center, 0.12, "ball")
    return mb.to_object("OL_medicine_ball", prop["type"], prop["attach"])


def build_resistance_band(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    for side in ("Left", "Right"):
        g = ctx.grip(side)
        foot = ctx.head(f"{side}Foot")
        anchor = Vector((foot.x, foot.y - 0.05, 0.01))
        mb.cylinder(anchor, g["center"], 0.008, "cable", 8)
    return mb.to_object("OL_resistance_band", prop["type"], prop["attach"])


# ---------------------------------------------------------------------------- cables


def _pulley_positions(ctx: PropContext, hand_points: Sequence[Vector], towers: int) -> List[Vector]:
    mid = sum(hand_points, Vector()) / len(hand_points)
    f = ctx.fwd_h
    m = ctx.modifiers
    if towers >= 2:
        out = []
        for side, p in zip(("Left", "Right"), hand_points):
            lateral = ctx.left_h * (1.0 if side == "Left" else -1.0)
            q = p + lateral * 0.9 + f * 0.15
            out.append(Vector((q.x, q.y, max(p.z + 0.5, 1.9))))
        return out
    if ctx.template == "SEATED_VERTICAL_PULL" or "ARMS_PULLDOWN_BOTTOM" in m:
        return [Vector((mid.x, mid.y, mid.z + 1.0)) + f * 0.1]
    if m & {"ARMS_PUSHDOWN", "TORSO_CRUNCH", "ARMS_OVERHEAD_EXTENSION"} or ("TORSO_LEAN_FORWARD" in m and "ARMS_FRONT_RAISE" in m):
        q = mid + f * 0.35
        return [Vector((q.x, q.y, 2.1))]
    if "ARMS_FACE_PULL" in m or "ARMS_ROW_PULLED" in m or ctx.template == "SEATED_ROW":
        q = mid + f * 1.1
        return [Vector((q.x, q.y, mid.z))]
    if "ARMS_CURL_MID" in m or "ARMS_UPRIGHT_ROW" in m:
        q = mid + f * 0.45
        return [Vector((q.x, q.y, 0.15))]
    q = mid + f * 0.8
    return [Vector((q.x, q.y, max(0.15, min(mid.z, 1.9))))]


def build_cable_attachment(ctx: PropContext, prop: Dict, pulleys: List[Vector]) -> bpy.types.Object:
    mb = MeshBuilder()
    ptype, attach = prop["type"], prop["attach"]
    sides = _hand_sides(attach)
    if ptype == "cable_bar":
        gl, gr = ctx.both_grips()
        axis = (gl["center"] - gr["center"])
        center = (gl["center"] + gr["center"]) * 0.5
        half = axis.length * 0.5 + 0.12
        axis = axis.normalized() if axis.length > 1e-3 else ctx.torso_left
        mb.cylinder(center - axis * half, center + axis * half, 0.014, "metal", 12)
        mb.cylinder(center, pulleys[0], 0.004, "cable", 6)
    elif ptype == "cable_rope":
        gl, gr = ctx.both_grips()
        knot = (gl["center"] + gr["center"]) * 0.5
        to_pulley = (pulleys[0] - knot).normalized()
        knot = knot + to_pulley * 0.12
        for g in (gl, gr):
            mb.cylinder(g["center"] - g["along"] * 0.0, knot, 0.014, "cable", 10)
            mb.sphere(g["center"] - (knot - g["center"]).normalized() * 0.03, 0.022, "cable")
        mb.cylinder(knot, pulleys[0], 0.004, "cable", 6)
    else:  # cable_handle (one per hand, or a V-handle held by both hands)
        if attach == "hands":
            gl, gr = ctx.both_grips()
            center = (gl["center"] + gr["center"]) * 0.5
            axis = (gl["center"] - gr["center"]).normalized() if (gl["center"] - gr["center"]).length > 0.02 else UP
            mb.cylinder(center - axis * 0.08, center + axis * 0.08, 0.016, "metal", 12)
            mb.cylinder(center, pulleys[0], 0.004, "cable", 6)
        else:
            for i, side in enumerate(sides):
                g = ctx.grip(side)
                c, ax = g["center"], g["axis"]
                pulley = pulleys[min(i if len(sides) > 1 else (0 if side == "Left" else len(pulleys) - 1), len(pulleys) - 1)]
                apex = c + (pulley - c).normalized() * 0.09
                mb.cylinder(c - ax * 0.06, c + ax * 0.06, 0.016, "metal", 12)
                mb.cylinder(c - ax * 0.06, apex, 0.006, "metal", 6)
                mb.cylinder(c + ax * 0.06, apex, 0.006, "metal", 6)
                mb.cylinder(apex, pulley, 0.004, "cable", 6)
    return mb.to_object(f"OL_{ptype}_{attach}", ptype, attach)


def build_cable_frame(ctx: PropContext, prop: Dict, pulleys: List[Vector]) -> bpy.types.Object:
    mb = MeshBuilder()
    for p in pulleys:
        away = _horizontal(p - ctx.head("Hips"))
        col = Vector((p.x, p.y, 0.0)) + away * 0.08
        top = max(2.2, p.z + 0.15)
        mb.box(col + Vector((0, 0, top * 0.5)), (0.08, 0.08, top), kind="metal")
        mb.box(col + Vector((0, 0, 0.02)), (0.5, 0.5, 0.04), kind="metal")
        mb.cylinder(p - ctx.left_h * 0.02, p + ctx.left_h * 0.02, 0.05, "metal", 16)
        mb.cylinder(p, col + Vector((0, 0, p.z)), 0.015, "metal", 8)
        ctx.anchors.append(col + Vector((0, 0, 1.0)))
    return mb.to_object("OL_cable_frame", prop["type"], prop["attach"])


# ---------------------------------------------------------------------------- machines


def build_machine_frame(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    pts = ctx.engine.evaluated_body_points()
    params = prop.get("params", {})
    if params.get("style") == "smith":
        gl, gr = ctx.both_grips()
        axis = (gl["center"] - gr["center"]).normalized() if (gl["center"] - gr["center"]).length > 0.05 else ctx.torso_left
        center = (gl["center"] + gr["center"]) * 0.5
        for s in (-1.0, 1.0):
            base = center + axis * s * 0.95
            base = Vector((base.x, base.y, 0.0))
            mb.box(base + Vector((0, 0, 1.1)), (0.06, 0.06, 2.2), kind="metal")
            ctx.anchors.append(base + Vector((0, 0, 1.0)))
        a, b = center + axis * 0.95, center - axis * 0.95
        mb.cylinder(Vector((a.x, a.y, 2.2)), Vector((b.x, b.y, 2.2)), 0.03, "metal", 8)
        return mb.to_object("OL_machine_frame", prop["type"], prop["attach"])
    hips = ctx.head("Hips")
    back = min((p - hips).dot(ctx.fwd_h) for p in pts)
    lat = max(abs((p - hips).dot(ctx.left_h)) for p in pts)
    top = max(max(p.z for p in pts) + 0.15, 1.3)
    for s in (-1.0, 1.0):
        base = hips + ctx.fwd_h * (back - 0.25) + ctx.left_h * s * (lat + 0.12)
        base = Vector((base.x, base.y, 0.0))
        mb.box(base + Vector((0, 0, top * 0.5)), (0.07, 0.07, top), kind="metal")
        mb.box(base + ctx.fwd_h * 0.35 + Vector((0, 0, 0.02)), (0.07, 0.8, 0.04),
               rot=_basis_from(ctx.left_h, UP), kind="metal")
        ctx.anchors.append(base + Vector((0, 0, 1.0)))
    a = hips + ctx.fwd_h * (back - 0.25) + ctx.left_h * (lat + 0.12)
    b = hips + ctx.fwd_h * (back - 0.25) - ctx.left_h * (lat + 0.12)
    mb.cylinder(Vector((a.x, a.y, top)), Vector((b.x, b.y, top)), 0.035, "metal", 8)
    return mb.to_object("OL_machine_frame", prop["type"], prop["attach"])


def build_machine_handle(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    side = _hand_sides(prop["attach"])[0]
    g = ctx.grip(side)
    c, ax = g["center"], g["axis"]
    mb.cylinder(c - ax * 0.07, c + ax * 0.07, 0.017, "metal", 12)
    if ctx.anchors:
        anchor = min(ctx.anchors, key=lambda a: (a - c).length)
        elbow = Vector((anchor.x, anchor.y, c.z))
        end = c + ax * 0.07 if (c + ax * 0.07 - elbow).length < (c - ax * 0.07 - elbow).length else c - ax * 0.07
        mb.cylinder(end, elbow, 0.02, "metal", 8)
    return mb.to_object(f"OL_machine_handle_{prop['attach']}", prop["type"], prop["attach"])


# ---------------------------------------------------------------------------- supports


def build_bench(ctx: PropContext, prop: Dict) -> Optional[bpy.types.Object]:
    params = prop.get("params", {})
    role = params.get("role", "press")
    mb = MeshBuilder()
    T = 0.07
    if role in ("press",) and ctx.template in ("HORIZONTAL_PUSH", "INCLINE_PUSH", "SUPINE_FLOOR"):
        hips, neck = ctx.head("Hips"), ctx.head("Neck")
        if ctx.template == "INCLINE_PUSH":
            back_pts = ctx.verts(["Spine01", "Spine02", "Spine03"])
            seat_pts = ctx.verts(["Hips"])
            torso_axis = (neck - hips).normalized()
            c, r = _fit_pad(back_pts, torso_axis, ctx.torso_fwd, 0.85, 0.3, T, shift=0.08)
            mb.box(c, (0.85, 0.3, T), r, "pad")
            sc, sr = _fit_pad(seat_pts, ctx.fwd_h, UP, 0.38, 0.3, T, shift=0.05)
            mb.box(sc, (0.38, 0.3, T), sr, "pad")
            low = c - r.col[0] * 0.35 - r.col[2] * T
            _posts_to_floor(mb, [low, sc - UP * (T * 0.5), c + r.col[0] * 0.35 - r.col[2] * T])
        else:
            pts = ctx.verts(["Hips", "Spine01", "Spine02", "Spine03"])
            u = _horizontal(neck - hips)
            mid_shift = ((neck - hips).dot(u)) * 0.5 - 0.05
            length = 1.2
            c, r = _fit_pad(pts, u, UP, length, 0.28, T)
            c = Vector((hips.x, hips.y, c.z)) + u * mid_shift
            mb.box(c, (length, 0.28, T), r, "pad")
            for s in (-1.0, 1.0):
                top = c + u * s * (length * 0.36) - UP * (T * 0.5)
                mb.box(Vector((top.x, top.y, top.z * 0.5)), (0.05, 0.05, max(top.z, 0.01)), r, "metal")
                mb.box(Vector((top.x, top.y, 0.02)), (0.06, 0.36, 0.04), r, "metal")
    elif role == "upper_back_support":
        pts = ctx.verts(["Spine02", "Spine03"])
        c, r = _fit_pad(pts, ctx.left_h, UP, 1.1, 0.35, 0.35)
        top = min(p.z for p in pts)
        if top < 0.2:
            return None  # shoulders already on the floor (glute bridge): no bench needed
        mb.box(Vector((c.x, c.y, top * 0.5)), (1.1, 0.35, top), r, "pad")
    elif role in ("prone",):
        pts = ctx.verts(["Hips", "Spine01", "Spine02", "Spine03", "LeftThigh", "RightThigh"])
        hips, neck = ctx.head("Hips"), ctx.head("Neck")
        u = _horizontal(neck - hips)
        c, r = _fit_pad(pts, u, UP, 1.25, 0.3, T)
        mb.box(c, (1.25, 0.3, T), r, "pad")
        _posts_to_floor(mb, [c + u * 0.45 - UP * T * 0.5, c - u * 0.45 - UP * T * 0.5], 0.03)
    elif role == "hands_behind":
        gl, gr = ctx.both_grips()
        mid = (gl["center"] + gr["center"]) * 0.5
        top = min(gl["center"].z, gr["center"].z) - 0.03
        c = Vector((mid.x, mid.y, top - T * 0.5)) - ctx.fwd_h * 0.12
        r = _basis_from(ctx.left_h, UP)
        mb.box(c, (1.0, 0.3, T), r, "pad")
        _posts_to_floor(mb, [c + ctx.left_h * 0.4, c - ctx.left_h * 0.4], 0.03)
    elif role == "knee_and_hand_support":
        g = ctx.grip("Left")
        top = g["center"].z - 0.03
        c = Vector((g["center"].x, g["center"].y, top - T * 0.5)) + ctx.fwd_h * -0.35
        r = _basis_from(ctx.fwd_h, UP)
        mb.box(c, (1.1, 0.3, T), r, "pad")
        _posts_to_floor(mb, [c + ctx.fwd_h * 0.4, c - ctx.fwd_h * 0.4], 0.03)
    elif role == "rear_foot_support":
        foot = ctx.verts(["LeftFoot", "LeftToes"], 0.3)
        if not foot:
            return None
        top = min(p.z for p in foot)
        ctr = sum(foot, Vector()) / len(foot)
        c = Vector((ctr.x, ctr.y, top - T * 0.5))
        r = _basis_from(ctx.left_h, UP)
        mb.box(c, (0.9, 0.3, T), r, "pad")
        _posts_to_floor(mb, [c + ctx.left_h * 0.35, c - ctx.left_h * 0.35], 0.03)
    else:
        return build_seat(ctx, prop)
    return mb.to_object(f"OL_bench_{role}", prop["type"], prop["attach"])


def build_seat(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    T = 0.07
    pts = [p for p in ctx.verts(["Hips"]) ]
    c, r = _fit_pad(pts, ctx.fwd_h, UP, 0.42, 0.42, T, shift=0.04)
    mb.box(c, (0.42, 0.42, T), r, "pad")
    mb.cylinder(Vector((c.x, c.y, 0.0)), c - UP * (T * 0.5), 0.035, "metal", 12)
    mb.box(Vector((c.x, c.y, 0.02)), (0.5, 0.5, 0.04), kind="metal")
    return mb.to_object("OL_seat", prop["type"], prop["attach"])


def build_backrest(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    T = 0.07
    pts = ctx.verts(["Spine01", "Spine02", "Spine03"])
    hips, neck = ctx.head("Hips"), ctx.head("Neck")
    torso_axis = (neck - hips).normalized()
    c, r = _fit_pad(pts, torso_axis, ctx.torso_fwd, 0.62, 0.38, T, shift=-0.02)
    mb.box(c, (0.62, 0.38, T), r, "pad")
    low = c - r.col[0] * 0.28 - r.col[2] * (T * 0.5)
    _posts_to_floor(mb, [low], 0.03)
    return mb.to_object("OL_backrest", prop["type"], prop["attach"])


def build_preacher_pad(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    pts = ctx.verts(["LeftUpperArm", "RightUpperArm"])
    n = (UP - ctx.fwd_h).normalized()
    c, r = _fit_pad(pts, ctx.left_h, n, 0.55, 0.32, 0.07)
    mb.box(c, (0.55, 0.32, 0.07), r, "pad")
    _posts_to_floor(mb, [c - r.col[2] * 0.035], 0.03)
    return mb.to_object("OL_preacher_pad", prop["type"], prop["attach"])


def _leg_axes(ctx: PropContext, side: str, segment: str) -> Tuple[Vector, Vector, Vector]:
    return ctx.engine.body_axes(f"{side}{segment}")


def build_leg_pad(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    role = prop.get("params", {}).get("role", "extension")
    R = 0.055
    if role == "shoulder_pads":
        for side in ("Left", "Right"):
            sh = ctx.head(f"{side}UpperArm")
            top = sh + ctx.torso_up * 0.09 - ctx.torso_left * (0.04 if side == "Left" else -0.04)
            mb.box(top, (0.12, 0.22, 0.08), _basis_from(ctx.fwd_h, ctx.torso_up), "pad")
            ctx.anchors.append(top)
        return mb.to_object("OL_leg_pad_shoulders", prop["type"], prop["attach"])
    if role in ("thigh_hold_down", "knee_pad"):
        for side in ("Left", "Right"):
            knee, hip = ctx.head(f"{side}Shin"), ctx.head(f"{side}Thigh")
            p = knee * (0.75 if role == "thigh_hold_down" else 0.95) + hip * (0.25 if role == "thigh_hold_down" else 0.05)
            p = p + UP * 0.1
            mb.cylinder(p - ctx.left_h * 0.13, p + ctx.left_h * 0.13, R, "pad", 16)
        return mb.to_object(f"OL_leg_pad_{role}", prop["type"], prop["attach"])
    # extension / curl roller at the ankles
    la, ra = ctx.head("LeftFoot"), ctx.head("RightFoot")
    _, fwd_l, _ = _leg_axes(ctx, "Left", "Shin")
    _, fwd_r, _ = _leg_axes(ctx, "Right", "Shin")
    fwd = (fwd_l + fwd_r).normalized()
    off = 0.045 + R
    center = (la + ra) * 0.5 + (fwd * off if role == "extension" else -fwd * off) + (ctx.engine.body_axes("LeftShin")[2] * 0.06)
    axis = (la - ra).normalized()
    mb.cylinder(center - axis * 0.24, center + axis * 0.24, R, "pad", 16)
    return mb.to_object(f"OL_leg_pad_{role}", prop["type"], prop["attach"])


def build_footplate(ctx: PropContext, prop: Dict) -> Optional[bpy.types.Object]:
    mb = MeshBuilder()
    role = prop.get("params", {}).get("role", "plate")
    pts = ctx.verts(["LeftFoot", "RightFoot", "LeftToes", "RightToes"], 0.3)
    if not pts:
        return None
    if role == "calf_block":
        fore = ctx.verts(["LeftToes", "RightToes"], 0.3) or pts
        top = min(p.z for p in fore)
        ctr = sum(fore, Vector()) / len(fore)
        c = Vector((ctr.x, ctr.y, top - 0.04))
        mb.box(c, (0.6, 0.14, 0.08), _basis_from(ctx.left_h, UP), "metal")
        return mb.to_object("OL_calf_block", prop["type"], prop["attach"])
    _, _, foot_up_l = ctx.engine.body_axes("LeftFoot")
    _, _, foot_up_r = ctx.engine.body_axes("RightFoot")
    sole_normal = (foot_up_l + foot_up_r).normalized()  # plate surface faces the soles
    c, r = _fit_pad(pts, ctx.left_h, sole_normal, 0.7, 0.45, 0.04)
    mb.box(c, (0.7, 0.45, 0.04), r, "metal")
    if c.z > 0.1:
        _posts_to_floor(mb, [c - r.col[2] * 0.02], 0.035)
    return mb.to_object("OL_footplate", prop["type"], prop["attach"])


def build_box(ctx: PropContext, prop: Dict) -> Optional[bpy.types.Object]:
    side = "Right" if prop["attach"] != "left_foot" else "Left"
    pts = ctx.verts([f"{side}Foot", f"{side}Toes"], 0.3)
    if not pts:
        return None
    top = min(p.z for p in pts)
    h = max(top, float(prop.get("params", {}).get("height_m", 0.3)) if top < 0.05 else top)
    ctr = sum(pts, Vector()) / len(pts)
    mb = MeshBuilder()
    mb.box(Vector((ctr.x, ctr.y, top - h * 0.5)), (0.5, 0.4, h), _basis_from(ctx.fwd_h, UP), "pad")
    return mb.to_object("OL_box", prop["type"], prop["attach"])


def build_ball_or_roller(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    pts = ctx.verts(["Hips", "Spine01", "Spine02", "Spine03"])
    low = min(pts, key=lambda p: p.z)
    torso_flat = abs(ctx.torso_up.z) < 0.6
    if prop["type"] == "stability_ball":
        R = 0.33
        if torso_flat:
            c = Vector((low.x, low.y, low.z - R))
        else:
            foot = ctx.head("RightFoot")
            c = Vector((foot.x, foot.y, R)) + ctx.left_h * -0.5
        mb.sphere(c, R, "ball")
    else:
        R = 0.075
        c = Vector((low.x, low.y, low.z - R))
        mb.cylinder(c - ctx.left_h * 0.45, c + ctx.left_h * 0.45, R, "ball", 20)
    return mb.to_object(f"OL_{prop['type']}", prop["type"], prop["attach"])


def build_bars(ctx: PropContext, prop: Dict) -> bpy.types.Object:
    mb = MeshBuilder()
    gl, gr = ctx.both_grips()
    if prop["type"] == "pullup_bar":
        axis = (gl["center"] - gr["center"])
        axis = axis.normalized() if axis.length > 0.05 else ctx.left_h
        mid = (gl["center"] + gr["center"]) * 0.5
        a, b = mid + axis * 0.65, mid - axis * 0.65
        mb.cylinder(a, b, 0.016, "metal", 12)
        for p in (a, b):
            mb.cylinder(Vector((p.x, p.y, 0.0)), p, 0.03, "metal", 8)
    else:
        for g in (gl, gr):
            c = g["center"]
            a, b = c + ctx.fwd_h * 0.35, c - ctx.fwd_h * 0.35
            mb.cylinder(a, b, 0.02, "metal", 12)
            for p in (a, b):
                mb.cylinder(Vector((p.x, p.y, 0.0)), p, 0.025, "metal", 8)
    return mb.to_object(f"OL_{prop['type']}", prop["type"], prop["attach"])


# ---------------------------------------------------------------------------- orchestration


def _move_everything(engine, objects: Sequence[bpy.types.Object], dz: float) -> None:
    if abs(dz) < 1e-6:
        return
    engine.arm.location.z += dz
    for o in objects:
        if o.parent is None:
            o.location.z += dz
    bpy.context.view_layer.update()


def _lowest(engine, objects: Sequence[bpy.types.Object]) -> float:
    pts = engine.evaluated_body_points([engine.body] + [o for o in objects if o.type == "MESH"])
    return min(p.z for p in pts)


def clear_props() -> None:
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        return
    for obj in list(coll.objects):
        mesh = obj.data if obj.type == "MESH" else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def build_props(engine, exercise: Dict) -> List[bpy.types.Object]:
    """Create every prop listed in exercise['props'] for the current (committed) pose.

    Returns the created objects. Each has custom properties 'openlift_prop' and 'openlift_attach'.
    """
    clear_props()
    ctx = PropContext(engine, exercise)
    props = list(exercise.get("props", []))
    created: List[bpy.types.Object] = []
    warnings: List[str] = []

    handheld = [p for p in props if p["type"] in HANDHELD and p["type"] not in ("cable_handle", "cable_rope", "cable_bar", "machine_handle")]
    lifters = [p for p in props if p["type"] in LIFTERS]
    rest = [p for p in props if p not in handheld and p not in lifters]

    # 1. Hand-held weights, then make sure nothing is below the floor.
    for p in handheld:
        t = p["type"]
        if t in ("barbell", "ez_bar"):
            created.append(build_barbell(ctx, p, ez=(t == "ez_bar")))
        elif t == "dumbbell":
            created.append(build_dumbbell(ctx, p))
        elif t == "kettlebell":
            created.append(build_kettlebell(ctx, p))
        elif t == "medicine_ball":
            created.append(build_medicine_ball(ctx, p))
        elif t == "resistance_band":
            created.append(build_resistance_band(ctx, p))

    # 2. Props that lift the body (box, ball, roller); then re-ground everything.
    for p in lifters:
        obj = build_box(ctx, p) if p["type"] == "box" else build_ball_or_roller(ctx, p)
        if obj is not None:
            created.append(obj)
    low = _lowest(engine, created)
    if engine.ground.get("mode", "lowest") != "none":
        floor = float(engine.ground.get("clearance_m", 0.0)) if not lifters else 0.0
        if low < floor - 1e-4 or lifters:
            _move_everything(engine, created, (floor if not lifters else 0.0) - low)
    ctx = PropContext(engine, exercise)

    # 3. Frames first (handles anchor to them), then supports and attachments.
    ordered = sorted(rest, key=lambda p: 0 if p["type"] in ("machine_frame", "cable_frame") else 1)
    cable_types = {"cable_handle", "cable_rope", "cable_bar"}
    pulleys: Optional[List[Vector]] = None
    if any(p["type"] in cable_types or p["type"] == "cable_frame" for p in ordered):
        hand_pts = []
        attach_sides = set()
        for p in ordered:
            if p["type"] in cable_types:
                attach_sides.update(_hand_sides(p["attach"]))
        attach_sides = attach_sides or {"Left", "Right"}
        for side in ("Left", "Right"):
            if side in attach_sides:
                hand_pts.append(ctx.grip(side)["center"])
        towers = next((int(p.get("params", {}).get("towers", 1)) for p in ordered if p["type"] == "cable_frame"), 1)
        pulleys = _pulley_positions(ctx, hand_pts, towers)

    for p in ordered:
        t = p["type"]
        obj = None
        if t == "machine_frame":
            obj = build_machine_frame(ctx, p)
        elif t == "cable_frame":
            obj = build_cable_frame(ctx, p, pulleys or [])
        elif t in cable_types:
            obj = build_cable_attachment(ctx, p, pulleys or [])
        elif t == "machine_handle":
            obj = build_machine_handle(ctx, p)
        elif t == "bench":
            obj = build_bench(ctx, p)
        elif t == "seat":
            obj = build_seat(ctx, p)
        elif t == "backrest":
            obj = build_backrest(ctx, p)
        elif t == "preacher_pad":
            obj = build_preacher_pad(ctx, p)
        elif t == "leg_pad":
            obj = build_leg_pad(ctx, p)
        elif t == "footplate":
            obj = build_footplate(ctx, p)
        elif t in ("pullup_bar", "parallel_bars"):
            obj = build_bars(ctx, p)
        else:
            warnings.append(f"Prop type '{t}' has no builder; skipped.")
        if obj is not None:
            created.append(obj)

    # A calf block or similar may now sit below the floor: lift everything once more.
    low = _lowest(engine, created)
    if low < -1e-4:
        _move_everything(engine, created, -low)

    for w in warnings:
        print("WARNING:", w)
    return created
