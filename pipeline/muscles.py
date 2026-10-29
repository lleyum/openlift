"""
OpenLift - muscle highlight shells.

For each worked muscle, create_muscle_shell() duplicates the faces of that muscle's vertex
group from the POSED, frozen body, pushes them 2 mm out along the vertex normals, adds a
Solidify modifier (applied at export), and colors them:

    primary    #EF4444, opaque
    secondary  #EF4444, 30% opacity (glTF alphaMode BLEND)

Both tiers carry the body's baked AO, so the red darkens in the same crevices as the skin.

Shorts: shell vertices that sit under the black shorts are pushed out past the shorts
(QA-08), so glute / upper-thigh highlights stay visible.

Usage (inside build.py):
    groups = collect_muscle_vertices(rigged_body, muscle_mapping)        # before freezing
    frozen = shading.freeze_posed_scene(...)
    ao = shading.bake_body_ao(frozen["body"], ...)
    shells = build_exercise_shells(exercise, groups, frozen["body"], ao, frozen["shorts"], muscle_mapping)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

from pipeline import common, shading  # noqa: E402

MUSCLE_HEX = "#EF4444"
MUSCLE_DARK_HEX = shading.darken_hex(MUSCLE_HEX, 0x40 / 0xA3)  # same AO ratio as the body
SECONDARY_ALPHA = 0.30

SHELL_OFFSET_M = 0.002       # 2 mm off the skin (spec)
SHELL_THICKNESS_M = 0.0015   # Solidify thickness
COVERED_OFFSET_M = 0.0125    # under shorts: shorts sit at 6 mm + 4 mm thick, so go to 12.5 mm
COVER_TEST_M = 0.02          # a skin vertex with shorts closer than this (along its normal) is covered


def collect_muscle_vertices(body: bpy.types.Object, muscle_mapping: Dict) -> Dict[str, Set[int]]:
    """{vertex group name: set of vertex indices} for every MUSCLE_* group on the rigged body."""
    prefix = muscle_mapping["vertex_group_naming"]["prefix"]
    index_to_name = {vg.index: vg.name for vg in body.vertex_groups if vg.name.startswith(prefix)}
    out: Dict[str, Set[int]] = {name: set() for name in index_to_name.values()}
    for v in body.data.vertices:
        for g in v.groups:
            name = index_to_name.get(g.group)
            if name is not None and g.weight >= 0.5:
                out[name].add(v.index)
    return out


def group_names_for(key: str, sides: str, muscle_mapping: Dict) -> List[str]:
    naming = muscle_mapping["vertex_group_naming"]
    info = muscle_mapping["vertex_groups"].get(key)
    if info is None:
        return []
    base = naming["prefix"] + key
    if not info.get("bilateral"):
        return [base]
    out = []
    if sides in ("both", "left"):
        out.append(base + naming["left_suffix"])
    if sides in ("both", "right"):
        out.append(base + naming["right_suffix"])
    return out


def _covered_by_shorts(points, normals, shorts: Optional[bpy.types.Object]) -> List[bool]:
    if shorts is None or not shorts.data.polygons:
        return [False] * len(points)
    tree = BVHTree.FromObject(shorts, bpy.context.evaluated_depsgraph_get())
    out = []
    for p, n in zip(points, normals):
        hit = tree.ray_cast(p + n * 0.0005, n, COVER_TEST_M)
        out.append(hit[0] is not None)
    return out


def create_muscle_shell(body: bpy.types.Object, vertex_indices: Set[int], name: str,
                        material: bpy.types.Material, ao: Sequence[float],
                        shorts: Optional[bpy.types.Object] = None,
                        offset: float = SHELL_OFFSET_M, thickness: float = SHELL_THICKNESS_M) -> Optional[bpy.types.Object]:
    """Duplicate the faces of `vertex_indices` from the frozen body as an offset, solidified, colored shell.

    body           frozen (static, posed) body mesh; vertex order matches the rigged original
    vertex_indices vertices of the muscle (from collect_muscle_vertices on the rigged body)
    ao             raw AO per body vertex (from shading.bake_body_ao)
    shorts         frozen shorts; covered shell vertices are pushed out past them
    """
    if not vertex_indices:
        return None
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.verts.ensure_lookup_table()
    bm.normal_update()
    orig_layer = bm.verts.layers.int.new("ol_orig")
    for v in bm.verts:
        v[orig_layer] = v.index

    doomed_faces = [f for f in bm.faces if not all(v.index in vertex_indices for v in f.verts)]
    bmesh.ops.delete(bm, geom=doomed_faces, context="FACES_ONLY")
    loose_edges = [e for e in bm.edges if not e.link_faces]
    bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    if not bm.faces:
        bm.free()
        return None

    # Normals of the FULL body (so shell edges match the skin), then push out.
    full_normals = [v.normal.copy() for v in body.data.vertices]
    verts = list(bm.verts)
    points = [v.co.copy() for v in verts]
    normals = [full_normals[v[orig_layer]] for v in verts]
    covered = _covered_by_shorts(points, normals, shorts)
    for v, n, cov in zip(verts, normals, covered):
        v.co = v.co + n * (COVERED_OFFSET_M if cov else offset)

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    orig = mesh.attributes["ol_orig"].data
    for old in list(mesh.color_attributes):  # drop the body's gray AO layer copied with the faces
        mesh.color_attributes.remove(old)
    attr = mesh.color_attributes.new(shading.COLOR_ATTR, "FLOAT_COLOR", "POINT")
    for i in range(len(mesh.vertices)):
        attr.data[i].color = shading.shade(MUSCLE_HEX, MUSCLE_DARK_HEX, shading.ao_to_t(ao[orig[i].value]))
    mesh.attributes.remove(mesh.attributes["ol_orig"])
    mesh.color_attributes.active_color = attr
    for poly in mesh.polygons:
        poly.use_smooth = True
    mesh.materials.append(material)

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    sol = obj.modifiers.new("Solidify", "SOLIDIFY")
    sol.thickness = thickness
    sol.offset = 1.0
    sol.use_rim = True
    sol.use_even_offset = True
    return obj


def build_exercise_shells(exercise: Dict, groups: Dict[str, Set[int]], body: bpy.types.Object,
                          ao: Sequence[float], shorts: Optional[bpy.types.Object],
                          muscle_mapping: Dict) -> List[bpy.types.Object]:
    """One shell object per highlighted muscle: 'Muscle_Primary_<key>' / 'Muscle_Secondary_<key>'."""
    muscles = exercise.get("muscles", {})
    sides = muscles.get("sides", "both")
    primary_mat = shading.vertex_color_material("OL_Muscle_Primary", alpha=1.0, roughness=0.5)
    secondary_mat = shading.vertex_color_material("OL_Muscle_Secondary", alpha=SECONDARY_ALPHA, roughness=0.5)
    shells: List[bpy.types.Object] = []
    for tier, keys, mat in (("Primary", muscles.get("primary", []), primary_mat),
                            ("Secondary", muscles.get("secondary", []), secondary_mat)):
        for key in keys:
            idx: Set[int] = set()
            for gname in group_names_for(key, sides, muscle_mapping):
                idx |= groups.get(gname, set())
            if not idx:
                print(f"WARNING: muscle '{key}' has no vertices on the body; no shell created.")
                continue
            obj = create_muscle_shell(body, idx, f"Muscle_{tier}_{key}", mat, ao, shorts)
            if obj is not None:
                obj["openlift_muscle"] = key
                obj["openlift_tier"] = tier.lower()
                shells.append(obj)
    return shells
