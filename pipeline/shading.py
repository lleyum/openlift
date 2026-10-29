"""
OpenLift - static bake and body shading.

  freeze_posed_scene()  turns the posed, rigged body + shorts into plain static meshes
                        (armature applied, transforms applied) and removes the rig.
  bake_body_ao()        bakes Ambient Occlusion into a per-vertex color attribute on the body,
                        mapping open skin to #A3A3A3 and deep crevices to #404040.
  ao_colors()           shades any base color by the baked AO (used by the muscle shells).

The resulting colors live in a color attribute named 'Col' that the material reads, so the
glTF exporter writes them as COLOR_0 and every engine shows the same shading without lights.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bpy  # noqa: E402
from mathutils import Matrix  # noqa: E402

from pipeline import common  # noqa: E402

BODY_HEX = "#A3A3A3"
CREVICE_HEX = "#404040"
SHORTS_HEX = "#141414"
COLOR_ATTR = "Col"

AO_DISTANCE_M = 0.12      # how far AO looks for occluders: small = only real crevices darken
AO_SAMPLES = 64
AO_LOW, AO_HIGH = 0.35, 0.95  # AO values mapped to darkest / lightest color
AO_GAMMA = 1.3


def _hex_srgb(hex_color: str) -> Tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def ao_to_t(ao: float) -> float:
    """Map a raw AO value (0 = fully occluded, 1 = open) to a 0..1 shading factor."""
    t = (ao - AO_LOW) / (AO_HIGH - AO_LOW)
    t = min(1.0, max(0.0, t))
    return t ** AO_GAMMA


def shade(light_hex: str, dark_hex: str, t: float) -> Tuple[float, float, float, float]:
    """Linear RGBA between dark (t=0) and light (t=1), blended in sRGB like a painter would."""
    lo, hi = _hex_srgb(dark_hex), _hex_srgb(light_hex)
    srgb = [a + (b - a) * t for a, b in zip(lo, hi)]
    return (_to_linear(srgb[0]), _to_linear(srgb[1]), _to_linear(srgb[2]), 1.0)


def darken_hex(hex_color: str, factor: float) -> str:
    r, g, b = _hex_srgb(hex_color)
    return "#{:02X}{:02X}{:02X}".format(int(r * factor * 255), int(g * factor * 255), int(b * factor * 255))


# ---------------------------------------------------------------------------- materials


def vertex_color_material(name: str, alpha: float = 1.0, roughness: float = 0.6) -> bpy.types.Material:
    """Principled material whose Base Color comes from the 'Col' color attribute."""
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    common.enable_nodes(mat)
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    attr = nt.nodes.new("ShaderNodeVertexColor")
    attr.layer_name = COLOR_ATTR
    attr.location = (-300, 200)
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = roughness
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        set_blended(mat)
    mat.diffuse_color = (0.5, 0.5, 0.5, alpha)
    return mat


def set_blended(mat: bpy.types.Material) -> None:
    """Alpha blending that the glTF exporter maps to alphaMode BLEND (Blender 4.2 and 5.x)."""
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = "BLEND"
        except (TypeError, AttributeError):
            pass
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = True
    if hasattr(mat, "show_transparent_back"):
        mat.show_transparent_back = False


def flat_material(name: str, hex_color: str, roughness: float = 0.85) -> bpy.types.Material:
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    common.enable_nodes(mat)
    color = common.hex_to_linear_rgba(hex_color)
    mat.diffuse_color = color
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
    return mat


# ---------------------------------------------------------------------------- freeze


def freeze_object(obj: bpy.types.Object, name: str) -> bpy.types.Object:
    """Static copy of an object with all modifiers and its world transform applied."""
    deps = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(deps)
    mesh = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=deps)
    mesh.transform(obj.matrix_world)
    mesh.name = name
    new = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(new)
    new.matrix_world = Matrix.Identity(4)
    return new


def freeze_posed_scene(engine, props: Sequence[bpy.types.Object]) -> Dict[str, object]:
    """Replace the rigged body/shorts with static meshes; apply prop transforms; delete the rig.

    Returns {"body": obj, "shorts": obj or None, "props": [...], "source_body": original body (deleted)}.
    The returned body keeps the same vertex order as the original body, so vertex-group indices
    read from the original (before deletion) stay valid.
    """
    bpy.context.view_layer.update()
    mapping = engine.mapping
    shorts_src = common.find_shorts(mapping)
    body = freeze_object(engine.body, "OL_frozen_body")
    shorts = freeze_object(shorts_src, "OL_frozen_shorts") if shorts_src is not None else None

    static_props: List[bpy.types.Object] = []
    for obj in props:
        if obj.type != "MESH":
            continue
        mw = obj.matrix_world.copy()
        obj.parent = None
        obj.data.transform(mw)
        obj.matrix_world = Matrix.Identity(4)
        static_props.append(obj)

    # Delete the rig and the rigged meshes.
    for obj in [engine.body, shorts_src, engine.arm]:
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    body.name = body.data.name = "Body"
    if shorts is not None:
        shorts.name = shorts.data.name = "Shorts"
    for obj in [body, shorts]:
        if obj is None:
            continue
        for mod in list(obj.modifiers):
            obj.modifiers.remove(mod)
        obj.vertex_groups.clear()
        if obj.data.shape_keys:
            obj.shape_key_clear()
    return {"body": body, "shorts": shorts, "props": static_props}


# ---------------------------------------------------------------------------- AO


def _ensure_world() -> bpy.types.World:
    scene = bpy.context.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new("OL_World")
    return scene.world


def bake_body_ao(body: bpy.types.Object, occluders: Sequence[bpy.types.Object] = (),
                 samples: int = AO_SAMPLES) -> List[float]:
    """Bake AO per vertex on the body. Only the body and `occluders` cast occlusion.

    Returns the raw AO value per vertex (0..1) and writes the shaded gray into the 'Col' attribute.
    """
    scene = bpy.context.scene
    prev_engine = scene.render.engine
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    if hasattr(scene.cycles, "use_denoising"):
        scene.cycles.use_denoising = False
    world = _ensure_world()
    world.light_settings.distance = AO_DISTANCE_M

    hidden = []
    keep = {body.name} | {o.name for o in occluders}
    for obj in scene.objects:
        if obj.name not in keep and not obj.hide_render:
            obj.hide_render = True
            hidden.append(obj)

    me = body.data
    for attr in list(me.color_attributes):
        if attr.name == COLOR_ATTR:
            me.color_attributes.remove(attr)
    attr = me.color_attributes.new(COLOR_ATTR, "FLOAT_COLOR", "POINT")
    me.color_attributes.active_color = attr
    if hasattr(me.color_attributes, "render_color_index"):
        me.color_attributes.render_color_index = list(me.color_attributes).index(attr)
    if not me.materials:
        me.materials.append(vertex_color_material("OL_Body"))

    common.set_active(body)
    bpy.ops.object.bake(type="AO", target="VERTEX_COLORS")

    ao = [attr.data[i].color[0] for i in range(len(me.vertices))]
    for i, value in enumerate(ao):
        attr.data[i].color = shade(BODY_HEX, CREVICE_HEX, ao_to_t(value))

    for obj in hidden:
        obj.hide_render = False
    scene.render.engine = prev_engine
    return ao


def apply_body_material(body: bpy.types.Object) -> None:
    me = body.data
    me.materials.clear()
    me.materials.append(vertex_color_material("OL_Body", roughness=0.65))
    for poly in me.polygons:
        poly.use_smooth = True


def apply_shorts_material(shorts: Optional[bpy.types.Object]) -> None:
    if shorts is None:
        return
    shorts.data.materials.clear()
    shorts.data.materials.append(flat_material("OL_Shorts", SHORTS_HEX, roughness=0.9))
    for poly in shorts.data.polygons:
        poly.use_smooth = True
