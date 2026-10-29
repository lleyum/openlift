"""
OpenLift - one-time preparation of assets/base_body.blend.

  1. Bakes shape keys into the mesh (MPFB stores body shape as shape keys).
  2. Refits the skeleton to the body. MPFB can leave the rig at its default (unfitted) joint
     positions; posing such a rig tears the mesh. For the MPFB 'Game engine' rig the joints are
     rebuilt from the MakeHuman joint cubes (pipeline/mpfb_refit.json). Any other rig that is
     offset from the mesh is moved onto it. Aborts if the skeleton still does not fit.
  3. Deletes MakeHuman helper geometry (everything outside the 'body' vertex group) and Mask modifiers.
  4. Creates the 40 MUSCLE_* vertex groups listed in pipeline/muscle_mapping.json, from the rig's
     own skin weights plus body-proportion rules (rig-agnostic: uses Standard Anatomical Names).
  5. Creates procedural black shorts named 'shorts' if the file has none.
  6. Checks the result. Only if every muscle group and the shorts are non-empty does it save:
     the original is copied to assets/base_body.backup.blend (only the first time, so the backup
     is always your untouched original), then assets/base_body.blend is overwritten.

Safe to re-run: muscle groups are rebuilt each time; existing shorts are kept unless --rebuild-shorts.

Run from the repo root:
    blender --background assets/base_body.blend --python pipeline/prep_mesh.py
    blender --background assets/base_body.blend --python pipeline/prep_mesh.py -- --dry-run
"""

from __future__ import annotations

import argparse
import math
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

from pipeline import common  # noqa: E402

REFIT_TABLE_PATH = Path(__file__).resolve().parent / "mpfb_refit.json"
MAX_SKELETON_OFFSET_M = 0.05
MIN_SHORTS_VERTICES = 100

TORSO = {"Hips", "Spine01", "Spine02", "Spine03", "LeftShoulder", "RightShoulder"}
SHORTS_OFFSET_M = 0.006
SHORTS_THICKNESS_M = 0.004


# ---------------------------------------------------------------------------- cleanup


def bake_shape_keys(body: bpy.types.Object) -> int:
    keys = body.data.shape_keys
    if keys is None or len(keys.key_blocks) == 0:
        return 0
    count = len(keys.key_blocks)
    mixed = body.shape_key_add(name="__openlift_mix", from_mix=True)
    coords = [p.co.copy() for p in mixed.data]
    body.shape_key_clear()
    for v, co in zip(body.data.vertices, coords):
        v.co = co
    body.data.update()
    return count


def remove_helpers(body: bpy.types.Object) -> int:
    removed = 0
    group = body.vertex_groups.get("body")
    if group is not None:
        gi = group.index
        keep = {v.index for v in body.data.vertices if any(g.group == gi and g.weight > 0.5 for g in v.groups)}
        if 0 < len(keep) < len(body.data.vertices):
            bm = bmesh.new()
            bm.from_mesh(body.data)
            bm.verts.ensure_lookup_table()
            doomed = [v for v in bm.verts if v.index not in keep]
            removed = len(doomed)
            bmesh.ops.delete(bm, geom=doomed, context="VERTS")
            bm.to_mesh(body.data)
            bm.free()
            body.data.update()
    for mod in list(body.modifiers):
        if mod.type == "MASK":
            body.modifiers.remove(mod)
    for vg in list(body.vertex_groups):
        if vg.name.startswith(("helper-", "joint-")) or vg.name in ("HelperGeometry", "JointCubes"):
            body.vertex_groups.remove(vg)
    return removed


# ---------------------------------------------------------------------------- skeleton fit


def refit_mpfb_skeleton(arm: bpy.types.Object, body: bpy.types.Object, mapping: Dict) -> int:
    """Rebuild MPFB Game Engine joints from the MakeHuman joint cubes. Returns bones refit (0 = not applicable)."""
    if not REFIT_TABLE_PATH.exists():
        return 0
    table = common.load_json(REFIT_TABLE_PATH)
    if mapping.get("rig_preset") != table["rig_preset"] or len(body.data.vertices) != table["vertex_count"]:
        return 0
    if any(name not in arm.data.bones for name in table["bones"]):
        return 0
    mw, to_arm = body.matrix_world, arm.matrix_world.inverted()
    verts = body.data.vertices

    def center(ranges):
        idx = [i for a, b in ranges for i in range(a, b + 1)]
        local = sum((verts[i].co for i in idx), Vector()) / len(idx)
        return to_arm @ (mw @ local)

    common.set_active(arm)
    bpy.ops.object.mode_set(mode="EDIT")
    ebones = arm.data.edit_bones
    for name, spec in table["bones"].items():
        eb = ebones[name]
        head, tail = center(spec["head"]), center(spec["tail"])
        if (tail - head).length < 1e-4:
            tail = head + Vector((0.0, 0.0, 0.03))
        eb.use_connect = False
        eb.head, eb.tail = head, tail
    bpy.ops.object.mode_set(mode="OBJECT")
    return len(table["bones"])


def shift_skeleton(arm: bpy.types.Object, offset_world: Vector) -> None:
    """Move every bone by a world-space offset (fallback for rigs without a refit table)."""
    local = arm.matrix_world.inverted().to_3x3() @ offset_world
    common.set_active(arm)
    bpy.ops.object.mode_set(mode="EDIT")
    for eb in arm.data.edit_bones:
        eb.head += local
        eb.tail += local
    bpy.ops.object.mode_set(mode="OBJECT")


def fit_skeleton(arm: bpy.types.Object, body: bpy.types.Object, mapping: Dict) -> str:
    """Make sure the skeleton sits inside the mesh. Returns a one-line report; raises if it cannot."""
    _, before = common.skeleton_alignment(arm, body, mapping)
    if before is None:
        raise common.PipelineError("Cannot measure skeleton fit: the limb bones have no skin weights on the body.")
    refit = refit_mpfb_skeleton(arm, body, mapping)
    offset, after = common.skeleton_alignment(arm, body, mapping)
    if after > MAX_SKELETON_OFFSET_M and offset is not None:
        shift_skeleton(arm, offset)
        offset, after = common.skeleton_alignment(arm, body, mapping)
    if after > MAX_SKELETON_OFFSET_M:
        raise common.PipelineError(
            f"The skeleton does not fit the body (limbs are {after * 100:.0f} cm from their bones) and could not be "
            "refit automatically. If you already ran prep_mesh.py once, restore the original first: copy "
            "assets/base_body.backup.blend over assets/base_body.blend, then run prep_mesh.py again."
        )
    how = f"refit {refit} bones from MakeHuman joint cubes" if refit else ("moved onto the body" if before > MAX_SKELETON_OFFSET_M else "already fitted")
    return f"skeleton {how} (limb offset {before * 100:.1f} cm -> {after * 100:.1f} cm)"


# ---------------------------------------------------------------------------- body analysis


class BodyFrame:
    """Rest-pose measurements of the body used by the muscle region rules."""

    def __init__(self, arm: bpy.types.Object, body: bpy.types.Object, mapping: Dict):
        self.arm, self.body, self.mapping = arm, body, mapping
        mw = body.matrix_world
        self.co = [mw @ v.co for v in body.data.vertices]
        self.normals = [(mw.to_3x3() @ v.normal).normalized() for v in body.data.vertices]
        self.segment = self._segments()
        self.bones = arm.data.bones
        amw = arm.matrix_world

        def head(std):
            return amw @ self.bones[common.mapped_bones(mapping, std)[0]].head_local

        def tail(std):
            return amw @ self.bones[common.mapped_bones(mapping, std)[0]].tail_local

        self.head, self.tail = head, tail
        self.hip_z = (head("LeftThigh").z + head("RightThigh").z) * 0.5
        self.shoulder_z = (head("LeftUpperArm").z + head("RightUpperArm").z) * 0.5
        self.half_width = abs(head("LeftUpperArm").x - head("RightUpperArm").x) * 0.5
        self.hip_half = abs(head("LeftThigh").x - head("RightThigh").x) * 0.5
        self.neck = head("Neck")
        self.head_joint = head("Head")
        self._depth_bins()

    def _segments(self) -> List[str]:
        group_to_std: Dict[int, str] = {}
        for std in common.STANDARD_BONES:
            if std == "Root":
                continue
            for real in common.mapped_bones(self.mapping, std):
                vg = self.body.vertex_groups.get(real)
                if vg is not None:
                    group_to_std[vg.index] = std
        out = []
        for v in self.body.data.vertices:
            totals: Dict[str, float] = defaultdict(float)
            for g in v.groups:
                std = group_to_std.get(g.group)
                if std:
                    totals[std] += g.weight
            out.append(max(totals, key=totals.get) if totals else "")
        return out

    def _depth_bins(self) -> None:
        """Front/back extent of the torso per 2 cm height slice."""
        self.bins: Dict[int, Tuple[float, float]] = {}
        acc: Dict[int, List[float]] = defaultdict(list)
        for p, seg in zip(self.co, self.segment):
            if seg in TORSO or seg == "Neck" or (seg.endswith("Thigh") and p.z > self.hip_z - 0.15):
                if abs(p.x) < self.half_width * 0.9:
                    acc[int(p.z / 0.02)].append(p.y)
        for k, ys in acc.items():
            self.bins[k] = (min(ys), max(ys))

    def torso_coords(self, p: Vector) -> Tuple[float, float, float]:
        """(u, s, f): u=0 at hip joints .. 1 at shoulder joints; s=|x|/half shoulder width;
        f=-1 front surface .. +1 back surface."""
        u = (p.z - self.hip_z) / (self.shoulder_z - self.hip_z)
        s = abs(p.x) / self.half_width
        k = int(p.z / 0.02)
        lo, hi = self.bins.get(k) or self.bins.get(k - 1) or self.bins.get(k + 1) or (p.y - 0.1, p.y + 0.1)
        mid, half = (lo + hi) * 0.5, max((hi - lo) * 0.5, 1e-3)
        return u, s, (p.y - mid) / half

    def limb_coords(self, p: Vector, std: str) -> Tuple[float, float]:
        """(t, azimuth): t=0 at bone head .. 1 at tail; azimuth 0=front, +90=lateral, 180=back, -90=medial."""
        h, t = self.head(std), self.tail(std)
        d = t - h
        length = d.length
        d.normalize()
        rel = p - h
        tt = rel.dot(d) / length
        radial = rel - rel.dot(d) * d
        front = Vector((0, -1, 0))
        front = (front - front.dot(d) * d).normalized()
        sx = 1.0 if std.startswith("Left") else -1.0
        lateral = d.cross(front)
        if lateral.dot(Vector((sx, 0, 0))) < 0:
            lateral = -lateral
        az = math.degrees(math.atan2(radial.dot(lateral), radial.dot(front)))
        return tt, az


def _side_suffix(p: Vector, naming: Dict) -> str:
    return naming["left_suffix"] if p.x >= 0 else naming["right_suffix"]


def _between(a: float, lo: float, hi: float) -> bool:
    return lo <= a <= hi


def classify_muscle(bf: BodyFrame, i: int) -> Optional[str]:
    """Muscle key for vertex i, or None. Order matters: first match wins."""
    p, seg = bf.co[i], bf.segment[i]
    if not seg:
        return None
    side = "Left" if p.x >= 0 else "Right"

    if seg.endswith("UpperArm"):
        t, az = bf.limb_coords(p, seg)
        if t < 0.40:
            if _between(az, -60, 30):
                return "deltoid_anterior"
            if _between(az, 30, 125):
                return "deltoid_lateral"
            if az > 125 or az < -150:
                return "deltoid_posterior"
            return None
        if _between(t, 0.40, 0.93) and _between(az, -65, 45):
            return "biceps"
        if _between(t, 0.30, 0.95) and (az > 100 or az < -115):
            return "triceps"
        return None
    if seg.endswith("Forearm"):
        t, _ = bf.limb_coords(p, seg)
        return "forearms" if t < 0.88 else None
    if seg.endswith("Thigh"):
        t, az = bf.limb_coords(p, seg)
        if p.z > bf.hip_z - 0.12 and bf.torso_coords(p)[2] > 0.15:
            return "gluteus_maximus"
        if t < 0.12 and _between(az, 40, 140):
            return "gluteus_medius"
        if _between(t, 0.02, 0.62) and _between(az, -140, -45):
            return "adductors"
        if _between(t, 0.12, 0.92) and _between(az, -45, 110):
            return "quadriceps"
        if _between(t, 0.18, 0.90) and (az > 110 or az < -140):
            return "hamstrings"
        return None
    if seg.endswith("Shin"):
        t, az = bf.limb_coords(p, seg)
        if _between(t, 0.04, 0.62) and (az > 95 or az < -95):
            return "calves"
        return None
    if seg in ("Neck", "Head"):
        if seg == "Neck" and p.z < bf.head_joint.z:
            _, s, f = bf.torso_coords(p)
            if f < 0.0:
                return "neck"
            return "trapezius_upper"
        return None
    if seg in TORSO:
        u, s, f = bf.torso_coords(p)
        # shoulder cap belongs to the deltoids
        to_shoulder = (p - bf.head(f"{side}UpperArm")).length
        if s > 0.82 and u > 0.80 and to_shoulder < 0.10:
            if f < -0.25:
                return "deltoid_anterior"
            if f > 0.25:
                return "deltoid_posterior"
            return "deltoid_lateral"
        if u > 0.97 and s < 0.85 and f > -0.35:
            return "trapezius_upper"
        # Pec: fan shape whose lower edge rises toward the armpit.
        if f < -0.15 and _between(s, 0.04, 0.90) and _between(u, 0.68 + 0.12 * s, 1.04):
            return "pectoralis_upper" if u >= 0.88 - 0.04 * s else "pectoralis_lower"
        if f < -0.25 and _between(u, 0.08, 0.70) and s < 0.27:
            return "rectus_abdominis"
        if _between(u, 0.06, 0.66) and _between(s, 0.27, 0.95) and f < 0.30:
            return "obliques"
        # Middle/lower traps: diamond from the shoulder blades down to mid-back.
        if f > 0.25 and _between(u, 0.45, 0.99) and s < 0.10 + (u - 0.45) * 0.85:
            return "trapezius_middle"
        # Lats: V shape, widest under the armpit, narrowing toward the lower back.
        if f > -0.10 and _between(u, 0.30, 0.90) and s >= 0.22 + max(0.0, 0.62 - u) * 0.6:
            return "latissimus_dorsi"
        if f > 0.35 and _between(u, -0.05, 0.62) and s < 0.30:
            return "erector_spinae"
        if seg == "Hips" and f > 0.15 and p.z < bf.hip_z + 0.10:
            return "gluteus_maximus"
        if seg == "Hips" and _between(p.z, bf.hip_z - 0.02, bf.hip_z + 0.13) and abs(p.x) > bf.hip_half * 1.15 and _between(f, -0.4, 0.4):
            return "gluteus_medius"
        return None
    return None


def build_muscle_groups(bf: BodyFrame, muscle_mapping: Dict) -> Dict[str, int]:
    naming = muscle_mapping["vertex_group_naming"]
    prefix = naming["prefix"]
    defs = muscle_mapping["vertex_groups"]
    body = bf.body
    for vg in list(body.vertex_groups):
        if vg.name.startswith(prefix):
            body.vertex_groups.remove(vg)

    names: List[str] = []
    for key, info in defs.items():
        if info.get("bilateral"):
            names += [prefix + key + naming["left_suffix"], prefix + key + naming["right_suffix"]]
        else:
            names.append(prefix + key)
    groups = {n: body.vertex_groups.new(name=n) for n in names}

    members: Dict[str, List[int]] = defaultdict(list)
    for i in range(len(bf.co)):
        key = classify_muscle(bf, i)
        if key is None or key not in defs:
            continue
        if not defs[key].get("bilateral"):
            members[prefix + key].append(i)
        elif abs(bf.co[i].x) < 1e-4:  # on the midline: belongs to both sides
            members[prefix + key + naming["left_suffix"]].append(i)
            members[prefix + key + naming["right_suffix"]].append(i)
        else:
            members[prefix + key + _side_suffix(bf.co[i], naming)].append(i)
    for name, idx in members.items():
        groups[name].add(idx, 1.0, "REPLACE")
    return {n: len(members.get(n, [])) for n in names}


# ---------------------------------------------------------------------------- shorts


def shorts_vertices(bf: BodyFrame) -> Set[int]:
    waist = bf.hip_z + 0.11
    out = set()
    for i, (p, seg) in enumerate(zip(bf.co, bf.segment)):
        if seg in ("Hips", "Spine01") and p.z < waist:
            out.add(i)
        elif seg.endswith("Thigh"):
            t, _ = bf.limb_coords(p, seg)
            if t < 0.40 and p.z < waist:
                out.add(i)
    return out


def build_shorts(bf: BodyFrame) -> bpy.types.Object:
    body = bf.body
    keep = shorts_vertices(bf)
    mesh = body.data.copy()
    mesh.name = "shorts"
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.normal_update()
    for v in bm.verts:
        v.co = v.co + v.normal * SHORTS_OFFSET_M
    doomed = [v for v in bm.verts if v.index not in keep]
    bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.clear()
    mat = bpy.data.materials.get("OL_shorts") or bpy.data.materials.new("OL_shorts")
    mat.diffuse_color = common.hex_to_linear_rgba("#141414")
    common.enable_nodes(mat)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = common.hex_to_linear_rgba("#141414")
        bsdf.inputs["Roughness"].default_value = 0.85
    mesh.materials.append(mat)
    for poly in mesh.polygons:
        poly.use_smooth = True

    shorts = bpy.data.objects.new("shorts", mesh)
    for coll in body.users_collection:
        coll.objects.link(shorts)
    shorts.parent = body.parent
    shorts.matrix_world = body.matrix_world.copy()
    # Weights live in the mesh by group index; re-create every group in the same order, then drop muscles.
    for vg in body.vertex_groups:
        shorts.vertex_groups.new(name=vg.name)
    for vg in list(shorts.vertex_groups):
        if vg.name.startswith("MUSCLE_"):
            shorts.vertex_groups.remove(vg)
    mod = shorts.modifiers.new("Armature", "ARMATURE")
    mod.object = bf.arm
    sol = shorts.modifiers.new("Solidify", "SOLIDIFY")
    sol.thickness = SHORTS_THICKNESS_M
    sol.offset = 1.0
    return shorts


# ---------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(prog="prep_mesh.py")
    parser.add_argument("--dry-run", action="store_true", help="Analyse and report, but do not save")
    parser.add_argument("--rebuild-shorts", action="store_true", help="Delete existing shorts and generate procedural ones")
    args = parser.parse_args(common.script_args())

    if not bpy.data.filepath:
        raise common.PipelineError("Open assets/base_body.blend: blender --background assets/base_body.blend --python pipeline/prep_mesh.py")
    mapping = common.load_bone_mapping()
    arm = common.find_armature(mapping)
    body = common.find_body(arm, mapping)
    muscle_mapping = common.load_json(common.MUSCLE_MAPPING_PATH)
    common.ensure_object_mode()
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()

    print("")
    print(f"Body: {body.name} ({len(body.data.vertices)} vertices), armature: {arm.name}")
    baked = bake_shape_keys(body)
    print(f"[1/6] Shape keys baked: {baked}")
    print(f"[2/6] Skeleton: {fit_skeleton(arm, body, mapping)}")
    removed = remove_helpers(body)
    print(f"[3/6] Helper vertices removed: {removed} (body now {len(body.data.vertices)} vertices)")

    bf = BodyFrame(arm, body, mapping)
    unassigned = sum(1 for s in bf.segment if not s)
    if unassigned > len(bf.co) * 0.05:
        raise common.PipelineError(
            f"{unassigned} vertices have no weight on any mapped bone. Check assets/bone_mapping.json "
            "and that the body is skinned to the armature."
        )
    counts = build_muscle_groups(bf, muscle_mapping)
    print(f"[4/6] Muscle vertex groups: {len(counts)}")
    empty = []
    for name, n in counts.items():
        print(f"      {name:<34} {n:>5} vertices")
        if n == 0:
            empty.append(name)

    shorts = common.find_shorts(mapping)
    if shorts is not None and (args.rebuild_shorts or len(shorts.data.vertices) < MIN_SHORTS_VERTICES):
        bpy.data.objects.remove(shorts, do_unlink=True)
        shorts = None
    if shorts is None:
        shorts = build_shorts(bf)
        print(f"[5/6] Procedural shorts created: '{shorts.name}' ({len(shorts.data.vertices)} vertices)")
    else:
        print(f"[5/6] Existing shorts kept: '{shorts.name}' ({len(shorts.data.vertices)} vertices)")

    problems = []
    if empty:
        problems.append("empty muscle groups: " + ", ".join(empty))
    if len(shorts.data.vertices) < MIN_SHORTS_VERTICES:
        problems.append(f"shorts have only {len(shorts.data.vertices)} vertices")
    if problems:
        raise common.PipelineError(
            "Result check failed, nothing was saved (" + "; ".join(problems) + "). "
            "Paste this output to your pipeline lead."
        )
    print("[6/6] Result check passed: all muscle groups and the shorts have geometry.")

    if args.dry_run:
        print("")
        print("Dry run: nothing saved.")
        return 0

    src = Path(bpy.data.filepath)
    backup = src.with_name(src.stem + ".backup.blend")
    print("")
    if backup.exists():
        print(f"Backup kept (already exists, not overwritten): {backup}")
    else:
        shutil.copy2(src, backup)
        print(f"Backup of the original written: {backup}")
    bpy.ops.wm.save_as_mainfile(filepath=str(src))
    print(f"Saved: {src}")
    print("DONE")
    return 0


if __name__ == "__main__":
    common.run_main(main)
