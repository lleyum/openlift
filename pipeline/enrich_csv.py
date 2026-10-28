#!/usr/bin/env python3
"""
OpenLift - CSV enrichment.

Reads data/exercise-database.csv and writes data/enriched-database.json, adding:
  - base_template   (which of the 20 foundational poses to start from)
  - pose_modifiers  (named joint-delta presets layered on the template)
  - stance / grip   (guessed from the exercise name, equipment and template)
  - pose_cues       (plain-English pose description)
  - muscles         (vertex group keys from pipeline/muscle_mapping.json)
  - props           (equipment proxies to build and where to attach them)
  - enrichment      (confidence + which rule matched, for triage)

Also writes data/enrichment-report.csv listing every row with its confidence,
so low-confidence rows can be reviewed first.

Pure standard library. Python 3.9+. Does NOT need Blender.

Usage (from the repo root):
    python3 pipeline/enrich_csv.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "exercise-database.csv"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "enriched-database.json"
DEFAULT_REPORT = REPO_ROOT / "data" / "enrichment-report.csv"
DEFAULT_MAPPING = REPO_ROOT / "pipeline" / "muscle_mapping.json"
DEFAULT_SCHEMA = REPO_ROOT / "schema" / "exercise.schema.json"

SCHEMA_VERSION = 1

REQUIRED_COLUMNS = [
    "Muscle Group",
    "Type",
    "Exercise",
    "Equipment",
    "Also Works",
    "Logged As",
    "Exercise ID",
    "Current Images",
]

# Every modifier name the rules below can emit. Phase 3's schema/base_templates.json
# must define each of these under "modifiers". Unknown modifiers are a hard error here
# so typos are caught before Blender ever runs.
KNOWN_MODIFIERS = {
    # Arms
    "ARMS_HANG",
    "ARMS_OVERHEAD",
    "ARMS_PRESS_LOCKOUT",
    "ARMS_PRESS_FORWARD",
    "ARMS_FLY_MID",
    "ARMS_ROW_PULLED",
    "ARMS_PULLDOWN_BOTTOM",
    "ARMS_FRONT_RAISE",
    "ARMS_LATERAL_RAISE",
    "ARMS_UPRIGHT_ROW",
    "ARMS_FACE_PULL",
    "ARMS_CURL_MID",
    "ARMS_PUSHDOWN",
    "ARMS_SKULLCRUSHER",
    "ARMS_OVERHEAD_EXTENSION",
    "ARMS_KICKBACK",
    "ARMS_DIP_SUPPORT",
    "ARMS_FRONT_RACK",
    "ARMS_BACK_RACK",
    "ARMS_GOBLET",
    "ARMS_FOREARMS_ON_THIGHS",
    "WRISTS_FLEXED",
    "SHOULDERS_SHRUGGED",
    "GRIP_NARROW",
    "GRIP_WIDE",
    "SINGLE_ARM_RIGHT",
    # Torso
    "TORSO_CRUNCH",
    "TORSO_TWIST",
    "TORSO_EXTENDED",
    "TORSO_LEAN_FORWARD",
    # Hips / legs
    "HIPS_BRIDGED",
    "HIPS_FLEXED_90",
    "KNEES_BENT_FEET_FLAT",
    "KNEES_FLEXED_90",
    "KNEES_FLEXED_DEEP",
    "KNEES_EXTENDED",
    "SQUAT_PARTIAL",
    "LEGS_EXTENDED",
    "ANKLES_PLANTARFLEXED",
    "STANCE_WIDE",
    "STANCE_NARROW",
    "SINGLE_LEG_RIGHT",
}

# Raw CSV equipment value (lowercased, trimmed) -> normalized category.
EQUIPMENT_ALIASES = {
    "barbell": "barbell",
    "barbells": "barbell",
    "olympic bar": "barbell",
    "smith machine": "machine",
    "e-z curl bar": "ez_bar",
    "ez curl bar": "ez_bar",
    "ez bar": "ez_bar",
    "ez-bar": "ez_bar",
    "dumbbell": "dumbbell",
    "dumbbells": "dumbbell",
    "kettlebell": "kettlebell",
    "kettlebells": "kettlebell",
    "cable": "cable",
    "cables": "cable",
    "machine": "machine",
    "body only": "bodyweight",
    "bodyweight": "bodyweight",
    "body weight": "bodyweight",
    "none": "none",
    "bands": "band",
    "band": "band",
    "resistance band": "band",
    "medicine ball": "medicine_ball",
    "exercise ball": "stability_ball",
    "stability ball": "stability_ball",
    "swiss ball": "stability_ball",
    "foam roll": "foam_roller",
    "foam roller": "foam_roller",
    "other": "other",
    "": "none",
}

HANDHELD_EQUIPMENT = {"barbell", "ez_bar", "dumbbell", "kettlebell", "cable", "band", "medicine_ball"}

UPPER_BODY_GROUPS = {
    "Chest", "Shoulders", "Biceps", "Triceps", "Forearms",
    "Lats", "Middle Back", "Traps", "Neck",
}

# Templates and what they mean, used for pose cues and stance.position.
TEMPLATE_INFO: Dict[str, Tuple[str, str]] = {
    # name: (stance.position, pose cue)
    "STANDING_NEUTRAL": ("standing", "Standing upright, feet hip-width, torso vertical, arms at the sides."),
    "STANDING_OVERHEAD_PUSH": ("standing", "Standing upright, core braced, arms pressed overhead."),
    "STANDING_FLY": ("standing", "Standing in a staggered stance between cable towers, slight forward lean, arms out to the sides."),
    "SQUAT": ("standing", "Squat position: hips back and down, thighs near parallel, knees tracking over toes, chest up."),
    "LUNGE": ("split", "Split stance lunge: right foot forward, both knees bent about 90 degrees, torso upright."),
    "HINGE": ("standing", "Hip hinge: hips pushed back, slight knee bend, flat back, torso about 45 degrees forward."),
    "BENT_OVER_ROW": ("standing", "Bent over: hips hinged, torso near 45 degrees, flat back, knees soft."),
    "HORIZONTAL_PUSH": ("supine", "Lying on a flat bench, feet flat on the floor, shoulder blades retracted."),
    "INCLINE_PUSH": ("reclined", "Lying on an incline bench at about 30 degrees, feet flat on the floor."),
    "PRONE_PUSH": ("plank", "Plank position: body straight from head to heels, hands under shoulders."),
    "SEATED_UPRIGHT": ("seated", "Seated upright, back supported, knees at 90 degrees, feet flat."),
    "SEATED_VERTICAL_PULL": ("seated", "Seated with thighs under pads, torso upright with slight lean back, arms reaching overhead."),
    "SEATED_ROW": ("seated", "Seated with feet braced on a platform, knees slightly bent, torso upright."),
    "LEG_PRESS": ("reclined", "Reclined in a leg press seat, feet on the platform, hips and knees flexed."),
    "SEATED_LEG_MACHINE": ("seated", "Seated in a leg machine, back against the pad, hands on side handles."),
    "HANGING": ("hanging", "Hanging from an overhead bar, body vertical, legs straight or slightly bent."),
    "SUPINE_FLOOR": ("supine", "Lying on the back on the floor."),
    "PRONE_FLOOR": ("prone", "Lying face down."),
    "KNEELING": ("kneeling", "Kneeling on both knees, torso upright."),
    "SIDE_LYING": ("side_lying", "Lying on the side, body in a straight line."),
}

MODIFIER_CUES = {
    "ARMS_HANG": "Arms hanging straight down holding the weight.",
    "ARMS_OVERHEAD": "Arms locked out overhead.",
    "ARMS_PRESS_LOCKOUT": "Arms extended vertically over the chest (top of the press).",
    "ARMS_PRESS_FORWARD": "Arms pressed straight forward at chest height.",
    "ARMS_FLY_MID": "Arms wide in a hugging arc, elbows slightly bent (mid-fly).",
    "ARMS_ROW_PULLED": "Elbows pulled back past the torso, hands near the lower ribs.",
    "ARMS_PULLDOWN_BOTTOM": "Bar pulled down to the upper chest, elbows down and back.",
    "ARMS_FRONT_RAISE": "Arms raised straight forward to shoulder height.",
    "ARMS_LATERAL_RAISE": "Arms raised out to the sides to shoulder height, elbows soft.",
    "ARMS_UPRIGHT_ROW": "Hands pulled up to chest height, elbows high and out.",
    "ARMS_FACE_PULL": "Hands pulled toward the face, elbows high and wide.",
    "ARMS_CURL_MID": "Elbows pinned at the sides, forearms curled to about 90 degrees.",
    "ARMS_PUSHDOWN": "Elbows pinned at the sides, forearms pushed down to full extension.",
    "ARMS_SKULLCRUSHER": "Upper arms vertical, elbows bent so the hands are near the forehead.",
    "ARMS_OVERHEAD_EXTENSION": "Upper arms overhead, elbows bent so the hands are behind the head.",
    "ARMS_KICKBACK": "Upper arm parallel to the torso, forearm extended back.",
    "ARMS_DIP_SUPPORT": "Supporting body weight on straight arms, hands at the hips.",
    "ARMS_FRONT_RACK": "Bar racked on the front of the shoulders, elbows high.",
    "ARMS_BACK_RACK": "Bar resting on the upper back, hands gripping outside the shoulders.",
    "ARMS_GOBLET": "Weight held at the chest with both hands, elbows down.",
    "ARMS_FOREARMS_ON_THIGHS": "Forearms resting on the thighs, wrists past the knees.",
    "WRISTS_FLEXED": "Wrists curled up.",
    "SHOULDERS_SHRUGGED": "Shoulders shrugged up toward the ears.",
    "GRIP_NARROW": "Hands closer than shoulder width.",
    "GRIP_WIDE": "Hands wider than shoulder width.",
    "SINGLE_ARM_RIGHT": "Right arm is the working arm; left arm relaxed or bracing.",
    "TORSO_CRUNCH": "Upper spine curled forward (crunch).",
    "TORSO_TWIST": "Torso rotated to one side.",
    "TORSO_EXTENDED": "Spine extended, chest lifted.",
    "TORSO_LEAN_FORWARD": "Torso leaning slightly forward.",
    "HIPS_BRIDGED": "Hips driven up so knees, hips and shoulders form a line.",
    "HIPS_FLEXED_90": "Hips flexed so the thighs are at 90 degrees to the torso.",
    "KNEES_BENT_FEET_FLAT": "Knees bent, feet flat.",
    "KNEES_FLEXED_90": "Knees bent to 90 degrees.",
    "KNEES_FLEXED_DEEP": "Knees deeply flexed, heels toward the glutes.",
    "KNEES_EXTENDED": "Knees fully extended.",
    "SQUAT_PARTIAL": "Partial squat (quarter to half depth).",
    "LEGS_EXTENDED": "Legs nearly straight.",
    "ANKLES_PLANTARFLEXED": "Up on the balls of the feet, heels raised.",
    "STANCE_WIDE": "Feet wider than shoulder width, toes turned out.",
    "STANCE_NARROW": "Feet close together.",
    "SINGLE_LEG_RIGHT": "Weight on the right leg; left leg lifted.",
}

TRICEPS_OVERHEAD = r"\b(triceps?|french) (extension|press)|\boverhead (triceps?|tricep|extension)"

STRETCH_PATTERN = r"\bstretch|\bsmr\b|\bfoam roll|\bmyofascial|\bmobility\b"

# --------------------------------------------------------------------------------------
# Context + rule helpers
# --------------------------------------------------------------------------------------


def normalize_text(value: str) -> str:
    """Lowercase, punctuation to spaces, collapse whitespace. 'Get-Up (Lunge)' -> 'get up lunge'."""
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


@dataclass
class Ctx:
    name: str          # normalized exercise name
    type: str          # normalized CSV Type
    muscle_group: str  # canonical muscle group key (or raw value if unknown)
    equipment: str     # normalized equipment

    def has(self, pattern: str) -> bool:
        return re.search(pattern, self.name) is not None

    def type_has(self, pattern: str) -> bool:
        return re.search(pattern, self.type) is not None


Predicate = Callable[[Ctx], bool]
Modifiers = Union[Sequence[str], Callable[[Ctx], List[str]]]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    when: Predicate
    template: str
    modifiers: Modifiers = ()
    confidence: str = "high"

    def resolve_modifiers(self, c: Ctx) -> List[str]:
        if callable(self.modifiers):
            return list(self.modifiers(c))
        return list(self.modifiers)


def match(all_of: Union[str, Sequence[str]] = (),
          none_of: Union[str, Sequence[str]] = (),
          equipment: Optional[Sequence[str]] = None) -> Predicate:
    """Every pattern in all_of must match the name, none in none_of may match,
    and equipment (if given) must be one of the listed categories."""
    all_list = [all_of] if isinstance(all_of, str) else list(all_of)
    none_list = [none_of] if isinstance(none_of, str) else list(none_of)
    equip_set = set(equipment) if equipment is not None else None

    def pred(c: Ctx) -> bool:
        if equip_set is not None and c.equipment not in equip_set:
            return False
        if any(not c.has(p) for p in all_list):
            return False
        if any(c.has(p) for p in none_list):
            return False
        return True

    return pred


def squat_mods(c: Ctx) -> List[str]:
    mods: List[str] = []
    if c.has(r"\bfront squat|\bzercher"):
        mods.append("ARMS_FRONT_RACK")
    elif c.has(r"\boverhead squat|\bsnatch"):
        mods.append("ARMS_OVERHEAD")
    elif c.has(r"\bgoblet") or c.equipment in ("kettlebell", "medicine_ball"):
        mods.append("ARMS_GOBLET")
    elif c.equipment == "barbell" or c.has(r"\bsmith\b"):
        mods.append("ARMS_BACK_RACK")
    elif c.equipment in ("dumbbell",):
        mods.append("ARMS_HANG")
    elif c.equipment in ("bodyweight", "none", "other"):
        mods.append("ARMS_FRONT_RAISE")
    if c.has(r"\bhalf\b|\bquarter\b|\bpartial\b"):
        mods.append("SQUAT_PARTIAL")
    return mods


def loaded_carry_mods(c: Ctx) -> List[str]:
    """Arm position for lunges / hinges depending on what is held."""
    if c.equipment == "barbell" or c.has(r"\bsmith\b"):
        return ["ARMS_BACK_RACK"]
    if c.has(r"\bgoblet"):
        return ["ARMS_GOBLET"]
    if c.equipment in ("dumbbell", "kettlebell", "cable", "band"):
        return ["ARMS_HANG"]
    return []


def hinge_mods(c: Ctx) -> List[str]:
    if c.has(r"\bgood ?mornings?\b") and c.equipment in ("barbell", "machine"):
        return ["ARMS_BACK_RACK"]
    return ["ARMS_HANG"]


# --------------------------------------------------------------------------------------
# Template rules. ORDER MATTERS: the first matching rule wins. Specific before generic.
# --------------------------------------------------------------------------------------

NAME_RULES: List[Rule] = [
    # Niche / compound oddities first
    Rule("turkish_get_up", match(r"\bturkish get ?up"), "LUNGE", ["ARMS_OVERHEAD"], "medium"),
    Rule("ab_rollout", match(r"\b(ab )?roll ?outs?\b|\bab wheel|\bab roller|\bwheel roll"), "KNEELING", ["ARMS_FRONT_RAISE", "TORSO_LEAN_FORWARD"]),
    Rule("woodchop_pallof", match(r"\bwood ?chop|\bpallof"), "STANDING_NEUTRAL", ["ARMS_PRESS_FORWARD", "TORSO_TWIST"]),
    Rule("farmer_carry", match(r"\bfarmers?\b|\bcarry\b|\bsuitcase\b"), "STANDING_NEUTRAL", ["ARMS_HANG"]),

    # Calves
    Rule("calf_leg_press", match((r"\bcalf|\bcalves", r"\bleg press|\bsled")), "LEG_PRESS", ["LEGS_EXTENDED", "ANKLES_PLANTARFLEXED"]),
    Rule("calf_seated", match((r"\bcalf|\bcalves", r"\bseated")), "SEATED_UPRIGHT", ["ANKLES_PLANTARFLEXED"]),
    Rule("calf_standing", match(r"\bcalf (raise|press)|\bcalf raises|\bheel raise|\bdonkey calf"), "STANDING_NEUTRAL", ["ANKLES_PLANTARFLEXED"]),

    # Machines for legs
    Rule("leg_press", match(r"\bleg press|\bsled press"), "LEG_PRESS"),
    Rule("hack_squat", match(r"\bhack squat"), "SQUAT", ["ARMS_HANG"]),
    Rule("leg_extension", match(r"\bleg extensions?\b"), "SEATED_LEG_MACHINE", ["KNEES_EXTENDED"]),
    Rule("leg_curl_seated", match((r"\bleg curl|\bhamstring curl", r"\bseated")), "SEATED_LEG_MACHINE", ["KNEES_FLEXED_DEEP"]),
    Rule("leg_curl_standing", match((r"\bleg curl|\bhamstring curl", r"\bstanding")), "STANDING_NEUTRAL", ["SINGLE_LEG_RIGHT", "KNEES_FLEXED_90"]),
    Rule("leg_curl_lying", match(r"\bleg curl|\bhamstring curl|\bnordic"), "PRONE_FLOOR", ["KNEES_FLEXED_90"]),
    Rule("hip_adduction_machine", match(r"\b(hip )?(adduct|abduct)\w*", equipment=("machine",)), "SEATED_LEG_MACHINE"),

    # Hanging
    Rule("hanging_leg_raise", match((r"\bhanging\b", r"\bleg raise|\bknee raise|\bknees? (up|tuck)|\btoes to bar")), "HANGING", ["HIPS_FLEXED_90"]),
    Rule("pull_up", match(r"\bpull ?ups?\b|\bchin ?ups?\b|\bmuscle ?ups?\b|\bpullups?\b|\bchinups?\b"), "HANGING"),
    Rule("dead_hang", match(r"\bhang\b|\bhanging\b", none_of=r"\bclean|\bsnatch"), "HANGING", (), "medium"),

    # Dips
    Rule("bench_dip", match(r"\bbench dips?\b|\btriceps? dips?\b.*\bbench"), "SEATED_UPRIGHT", ["ARMS_DIP_SUPPORT", "KNEES_BENT_FEET_FLAT"], "medium"),
    Rule("dip", match(r"\bdips?\b"), "HANGING", ["ARMS_DIP_SUPPORT", "KNEES_FLEXED_90"]),

    # Planks / push-ups
    Rule("side_plank", match(r"\bside (plank|bridge)"), "SIDE_LYING"),
    Rule("push_up", match(r"\bpush ?ups?\b|\bpushups?\b|\bpress ?ups?\b|\bplank\b|\bmountain climbers?"), "PRONE_PUSH"),

    # Bridges / hip thrusts
    Rule("hip_thrust_bridge", match(r"\bhip thrusts?\b|\bglute bridge|\bbridge\b"), "SUPINE_FLOOR", ["HIPS_BRIDGED", "KNEES_BENT_FEET_FLAT"]),

    # Rear delts before flys / raises
    Rule("rear_delt_fly", match(r"\brear delt|\breverse fl(y|ies)|\bbent over (lateral|raise|rear)|\bbent over fly", none_of=r"\bmachine|\bpec deck"),
         "BENT_OVER_ROW", ["ARMS_LATERAL_RAISE"]),
    Rule("rear_delt_machine", match(r"\brear delt|\breverse fl(y|ies)|\breverse pec deck"), "SEATED_UPRIGHT", ["ARMS_LATERAL_RAISE"]),
    Rule("face_pull", match(r"\bface pulls?\b"), "STANDING_NEUTRAL", ["ARMS_FACE_PULL"]),

    # Chest presses / flys
    Rule("pullover", match(r"\bpull ?overs?\b"), "HORIZONTAL_PUSH", ["ARMS_OVERHEAD"]),
    Rule("incline_fly", match((r"\bincline\b", r"\bfl(y|ye|ys|yes|ies)\b")), "INCLINE_PUSH", ["ARMS_FLY_MID"]),
    Rule("incline_press", match((r"\bincline\b", r"\bpress|\bbench")), "INCLINE_PUSH", ["ARMS_PRESS_LOCKOUT"]),
    Rule("machine_fly", match(r"\bpec deck|\bbutterfly|\bmachine fl(y|ye)"), "SEATED_UPRIGHT", ["ARMS_FLY_MID"]),
    Rule("cable_fly", match(r"\bcrossovers?\b|\bfl(y|ye|ys|yes|ies)\b", equipment=("cable", "band")), "STANDING_FLY", ["ARMS_FLY_MID"]),
    Rule("standing_fly", match((r"\bstanding\b", r"\bfl(y|ye|ys|yes|ies)\b")), "STANDING_FLY", ["ARMS_FLY_MID"]),
    Rule("lying_fly", match(r"\bfl(y|ye|ys|yes|ies)\b"), "HORIZONTAL_PUSH", ["ARMS_FLY_MID"]),
    Rule("floor_press", match(r"\bfloor press"), "SUPINE_FLOOR", ["ARMS_PRESS_LOCKOUT"]),
    Rule("machine_chest_press", match(r"\bchest press|\bmachine (bench )?press", equipment=("machine", "cable", "band")),
         "SEATED_UPRIGHT", ["ARMS_PRESS_FORWARD"]),
    Rule("bench_press", match(r"\bbench press|\bchest press|\bdecline\b.*\bpress|\bguillotine"),
         "HORIZONTAL_PUSH", ["ARMS_PRESS_LOCKOUT"]),
    Rule("chest_dumbbell_press", lambda c: c.muscle_group == "Chest" and c.has(r"\b(dumbbell|db) press\b"),
         "HORIZONTAL_PUSH", ["ARMS_PRESS_LOCKOUT"]),

    # Triceps overhead extensions (before shoulder presses: "seated triceps press" is not a shoulder press)
    Rule("overhead_extension_seated", match((r"\bseated\b", TRICEPS_OVERHEAD)), "SEATED_UPRIGHT", ["ARMS_OVERHEAD_EXTENSION"]),
    Rule("overhead_extension", match(TRICEPS_OVERHEAD), "STANDING_NEUTRAL", ["ARMS_OVERHEAD_EXTENSION"]),

    # Olympic lifts
    Rule("clean", match(r"\bclean", none_of=r"\bjerk|\bpress|\bdeadlift|\bpull|\bsquat"), "SQUAT", ["ARMS_FRONT_RACK", "SQUAT_PARTIAL"], "medium"),
    Rule("snatch_jerk", match(r"\bsnatch|\bjerk\b|\bclean and press|\bthrusters?\b"), "STANDING_OVERHEAD_PUSH", ["ARMS_OVERHEAD"], "medium"),

    # Shoulders
    Rule("seated_overhead_press", match((r"\bseated\b", r"\bshoulder press|\boverhead press|\bmilitary press|\barnold|\bpress\b")),
         "SEATED_UPRIGHT", ["ARMS_OVERHEAD"]),
    Rule("machine_shoulder_press", lambda c: c.equipment == "machine" and c.muscle_group == "Shoulders" and c.has(r"\bpress\b"),
         "SEATED_UPRIGHT", ["ARMS_OVERHEAD"]),
    Rule("overhead_press", match(r"\bshoulder press|\boverhead press|\bmilitary press|\bpush press|\barnold|\bstanding press|\blandmine press|\bbradford"),
         "STANDING_OVERHEAD_PUSH", ["ARMS_OVERHEAD"]),
    Rule("upright_row", match(r"\bupright\b.*\brows?\b"), "STANDING_NEUTRAL", ["ARMS_UPRIGHT_ROW"]),
    Rule("lateral_raise_seated", match((r"\blateral raise|\bside raise|\blateral", r"\bseated")), "SEATED_UPRIGHT", ["ARMS_LATERAL_RAISE"]),
    Rule("lateral_raise", match(r"\blateral raise|\bside raise|\blateral\b"), "STANDING_NEUTRAL", ["ARMS_LATERAL_RAISE"]),
    Rule("front_raise", match(r"\bfront raise|\bfront plate raise"), "STANDING_NEUTRAL", ["ARMS_FRONT_RAISE"]),
    Rule("shrug", match(r"\bshrugs?\b"), "STANDING_NEUTRAL", ["SHOULDERS_SHRUGGED", "ARMS_HANG"]),

    # Back
    Rule("straight_arm_pulldown", match((r"\bstraight arm", r"\bpull ?down|\bpulldown|\bpushdown")), "STANDING_NEUTRAL", ["ARMS_FRONT_RAISE", "TORSO_LEAN_FORWARD"]),
    Rule("pulldown", match(r"\bpull ?downs?\b|\bpulldowns?\b"), "SEATED_VERTICAL_PULL", ["ARMS_PULLDOWN_BOTTOM"]),
    Rule("inverted_row", match(r"\binverted rows?\b|\bbody rows?\b|\baustralian pull"), "SUPINE_FLOOR", ["ARMS_ROW_PULLED", "LEGS_EXTENDED"], "low"),
    Rule("seated_row", match((r"\brows?\b", r"\bseated\b|\bcable row|\blow row|\bmachine row")), "SEATED_ROW", ["ARMS_ROW_PULLED"]),
    Rule("seated_row_equipment", match(r"\brows?\b", equipment=("cable", "machine")), "SEATED_ROW", ["ARMS_ROW_PULLED"], "medium"),
    Rule("bent_over_row", match(r"\brows?\b|\browing\b"), "BENT_OVER_ROW", ["ARMS_ROW_PULLED"]),
    Rule("back_extension", match(r"\bhyperextensions?\b|\bback extensions?\b|\bsuperman\b|\breverse hyper"), "PRONE_FLOOR", ["TORSO_EXTENDED"], "medium"),

    # Arms: triceps
    Rule("pushdown", match(r"\bpush ?downs?\b|\bpushdowns?\b|\bpress ?downs?\b"), "STANDING_NEUTRAL", ["ARMS_PUSHDOWN"]),
    Rule("skullcrusher", match(r"\bskull ?crushers?\b|\blying (triceps?|tricep|french|ez|barbell|dumbbell)?\s?(extension|press)|\btriceps? extension\b.*\blying"),
         "HORIZONTAL_PUSH", ["ARMS_SKULLCRUSHER"]),
    Rule("kickback", match(r"\bkick ?backs?\b"), "BENT_OVER_ROW", ["ARMS_KICKBACK"]),

    # Arms: wrists + biceps
    Rule("wrist_curl", match(r"\bwrist (curl|roller|extension)"), "SEATED_UPRIGHT", ["ARMS_FOREARMS_ON_THIGHS", "WRISTS_FLEXED"]),
    Rule("seated_curl", match((r"\bcurls?\b", r"\bpreacher|\bconcentration|\bseated|\bscott\b|\bincline\b"), none_of=r"\bleg curl"),
         "SEATED_UPRIGHT", ["ARMS_CURL_MID"]),
    Rule("lying_curl", match((r"\bcurls?\b", r"\blying|\bspider"), none_of=r"\bleg curl"), "HORIZONTAL_PUSH", ["ARMS_CURL_MID"], "low"),
    Rule("curl", match(r"\bcurls?\b", none_of=r"\bleg curl|\bnordic|\bhamstring"), "STANDING_NEUTRAL", ["ARMS_CURL_MID"]),

    # Legs: squat / lunge / hinge
    Rule("split_squat", match(r"\bsplit squat|\bbulgarian|\blunges?\b|\bstep ?ups?\b|\bcurtsy"), "LUNGE", loaded_carry_mods),
    Rule("pistol_squat", match(r"\bpistol|\bsingle leg squat|\bone leg squat"), "SQUAT", ["SINGLE_LEG_RIGHT", "ARMS_FRONT_RAISE"]),
    Rule("squat", match(r"\bsquats?\b"), "SQUAT", squat_mods),
    Rule("hinge", match(r"\bdeadlifts?\b|\brdl\b|\bromanian|\bgood ?mornings?\b|\bswings?\b|\bpull ?through|\brack pull|\bstiff leg"),
         "HINGE", hinge_mods),

    # Abs
    Rule("cable_crunch", match((r"\bcrunch", r"\bkneeling|\bcable|\brope")), "KNEELING", ["TORSO_CRUNCH"]),
    Rule("russian_twist", match(r"\brussian twist|\bseated twist"), "SUPINE_FLOOR", ["TORSO_CRUNCH", "TORSO_TWIST", "KNEES_BENT_FEET_FLAT"], "medium"),
    Rule("crunch_situp", match(r"\bcrunch(es)?\b|\bsit ?ups?\b|\bsitups?\b|\bv ups?\b|\bjackknife|\btoe touch"),
         "SUPINE_FLOOR", ["TORSO_CRUNCH", "KNEES_BENT_FEET_FLAT"]),
    Rule("lying_leg_raise", match(r"\bleg raises?\b|\bflutter kicks?\b|\bscissor|\bdead bug|\bknee raise"), "SUPINE_FLOOR", ["HIPS_FLEXED_90"]),

    # Generic position words (last resort among name rules)
    Rule("generic_kneeling", match(r"\bkneeling\b"), "KNEELING", (), "medium"),
    Rule("generic_seated", match(r"\bseated\b|\bsitting\b"), "SEATED_UPRIGHT", (), "medium"),
    Rule("generic_lying", match(r"\blying\b|\bsupine\b"), "SUPINE_FLOOR", (), "medium"),
    Rule("generic_prone", match(r"\bprone\b"), "PRONE_FLOOR", (), "medium"),
]

# Fallback on the CSV 'Type' column (normalized). Confidence: medium.
TYPE_RULES: List[Tuple[str, str, Modifiers]] = [
    (r"\bflat press", "HORIZONTAL_PUSH", ["ARMS_PRESS_LOCKOUT"]),
    (r"\bincline", "INCLINE_PUSH", ["ARMS_PRESS_LOCKOUT"]),
    (r"\bdecline", "HORIZONTAL_PUSH", ["ARMS_PRESS_LOCKOUT"]),
    (r"\bfl(y|ys|ye|yes|ies)\b", "STANDING_FLY", ["ARMS_FLY_MID"]),
    (r"\bpush ?ups?\b", "PRONE_PUSH", []),
    (r"\bplank", "PRONE_PUSH", []),
    (r"\bdips?\b", "HANGING", ["ARMS_DIP_SUPPORT", "KNEES_FLEXED_90"]),
    (r"\bleg press", "LEG_PRESS", []),
    (r"\bleg extension", "SEATED_LEG_MACHINE", ["KNEES_EXTENDED"]),
    (r"\bleg curl", "PRONE_FLOOR", ["KNEES_FLEXED_90"]),
    (r"\bsquat", "SQUAT", squat_mods),
    (r"\blunge|\bsplit", "LUNGE", loaded_carry_mods),
    (r"\bdeadlift|\bhinge|\bgood morning|\bswing", "HINGE", hinge_mods),
    (r"\bpull ?ups?\b|\bchin", "HANGING", []),
    (r"\bpull ?down", "SEATED_VERTICAL_PULL", ["ARMS_PULLDOWN_BOTTOM"]),
    (r"\brows?\b", "BENT_OVER_ROW", ["ARMS_ROW_PULLED"]),
    (r"\bcurls?\b", "STANDING_NEUTRAL", ["ARMS_CURL_MID"]),
    (r"\bextension|\bpushdown|\bskull", "STANDING_NEUTRAL", ["ARMS_PUSHDOWN"]),
    (r"\bshoulder press|\boverhead press|\bmilitary", "STANDING_OVERHEAD_PUSH", ["ARMS_OVERHEAD"]),
    (r"\blateral", "STANDING_NEUTRAL", ["ARMS_LATERAL_RAISE"]),
    (r"\bfront raise", "STANDING_NEUTRAL", ["ARMS_FRONT_RAISE"]),
    (r"\bshrug", "STANDING_NEUTRAL", ["SHOULDERS_SHRUGGED", "ARMS_HANG"]),
    (r"\bcalf|\bcalves", "STANDING_NEUTRAL", ["ANKLES_PLANTARFLEXED"]),
    (r"\bcrunch|\bsit ?ups?", "SUPINE_FLOOR", ["TORSO_CRUNCH", "KNEES_BENT_FEET_FLAT"]),
    (r"\bbridge|\bthrust", "SUPINE_FLOOR", ["HIPS_BRIDGED", "KNEES_BENT_FEET_FLAT"]),
    (r"\bcarr(y|ies)|\bwalk", "STANDING_NEUTRAL", ["ARMS_HANG"]),
    (r"\bolympic|\bpower|\bexplosive", "SQUAT", ["ARMS_FRONT_RACK", "SQUAT_PARTIAL"]),
]

# Last resort: CSV 'Muscle Group'. Confidence: low.
MUSCLE_FALLBACK: Dict[str, Tuple[str, List[str]]] = {
    "Chest": ("HORIZONTAL_PUSH", ["ARMS_PRESS_LOCKOUT"]),
    "Shoulders": ("STANDING_OVERHEAD_PUSH", ["ARMS_OVERHEAD"]),
    "Biceps": ("STANDING_NEUTRAL", ["ARMS_CURL_MID"]),
    "Triceps": ("STANDING_NEUTRAL", ["ARMS_PUSHDOWN"]),
    "Forearms": ("STANDING_NEUTRAL", ["ARMS_HANG"]),
    "Lats": ("SEATED_VERTICAL_PULL", ["ARMS_PULLDOWN_BOTTOM"]),
    "Middle Back": ("BENT_OVER_ROW", ["ARMS_ROW_PULLED"]),
    "Lower Back": ("HINGE", ["ARMS_HANG"]),
    "Traps": ("STANDING_NEUTRAL", ["SHOULDERS_SHRUGGED", "ARMS_HANG"]),
    "Neck": ("STANDING_NEUTRAL", []),
    "Abdominals": ("SUPINE_FLOOR", ["TORSO_CRUNCH", "KNEES_BENT_FEET_FLAT"]),
    "Obliques": ("SUPINE_FLOOR", ["TORSO_CRUNCH", "TORSO_TWIST", "KNEES_BENT_FEET_FLAT"]),
    "Quadriceps": ("SQUAT", []),
    "Hamstrings": ("HINGE", ["ARMS_HANG"]),
    "Glutes": ("SUPINE_FLOOR", ["HIPS_BRIDGED", "KNEES_BENT_FEET_FLAT"]),
    "Calves": ("STANDING_NEUTRAL", ["ANKLES_PLANTARFLEXED"]),
    "Adductors": ("STANDING_NEUTRAL", ["STANCE_WIDE"]),
    "Abductors": ("STANDING_NEUTRAL", []),
    "Full Body": ("STANDING_NEUTRAL", []),
}


# --------------------------------------------------------------------------------------
# Classification steps
# --------------------------------------------------------------------------------------


def classify_template(c: Ctx) -> Tuple[str, List[str], str, str, List[str]]:
    """Returns (template, modifiers, confidence, matched_rule, warnings)."""
    warnings: List[str] = []
    is_stretch = c.has(STRETCH_PATTERN)

    if is_stretch:
        warnings.append("Stretch/mobility exercise: pose is a placeholder and needs manual overrides.")
    else:
        for rule in NAME_RULES:
            if rule.when(c):
                return rule.template, rule.resolve_modifiers(c), rule.confidence, "name:" + rule.rule_id, warnings

    for pattern, template, mods in TYPE_RULES:
        if c.type_has(pattern):
            resolved = list(mods(c)) if callable(mods) else list(mods)
            confidence = "low" if is_stretch else "medium"
            return template, resolved, confidence, "type:" + pattern, warnings

    if c.muscle_group in MUSCLE_FALLBACK:
        template, mods = MUSCLE_FALLBACK[c.muscle_group]
        return template, list(mods), "low", "muscle_group:" + c.muscle_group, warnings

    warnings.append("No rule matched; defaulted to STANDING_NEUTRAL.")
    return "STANDING_NEUTRAL", [], "low", "default", warnings


def guess_laterality(c: Ctx) -> Tuple[str, bool, bool]:
    """Returns (laterality, single_arm, single_leg)."""
    single_arm = c.has(r"\bone arm|\bsingle arm|\bone hand|\bsingle hand|\bturkish get ?up|\bone handed|\bsingle handed|\bconcentration")
    single_leg = c.has(r"\bsingle leg|\bone leg|\bpistol|\bbulgarian|\bsingle limb")
    if c.has(r"\bunilateral\b") and not (single_arm or single_leg):
        if c.muscle_group in UPPER_BODY_GROUPS:
            single_arm = True
        else:
            single_leg = True
    if single_arm or single_leg:
        return "unilateral_right", single_arm, single_leg
    if c.has(r"\balternat"):
        return "alternating", False, False
    return "bilateral", False, False


def holds_something(c: Ctx, template: str, modifiers: Sequence[str]) -> bool:
    if template in ("PRONE_PUSH", "HANGING", "SEATED_VERTICAL_PULL", "SEATED_ROW"):
        return True
    if "ARMS_DIP_SUPPORT" in modifiers:
        return True
    if c.equipment in HANDHELD_EQUIPMENT:
        return True
    if c.equipment == "machine" and template not in ("LEG_PRESS", "SEATED_LEG_MACHINE", "PRONE_FLOOR"):
        return True
    return False


def guess_grip(c: Ctx, template: str, modifiers: Sequence[str]) -> Dict[str, str]:
    if not holds_something(c, template, modifiers):
        return {"type": "none", "width": "none"}

    # Grip type: explicit name cues first
    grip_type: Optional[str] = None
    if c.has(r"\bmixed grip|\balternat\w* grip|\bover under"):
        grip_type = "mixed"
    elif c.has(r"\breverse\b") and c.has(r"\bcurls?\b") and not c.has(r"\bleg curl"):
        grip_type = "pronated"
    elif c.has(r"\bunderhand|\bsupinated?|\breverse grip|\bchin ?ups?\b|\bchinups?\b"):
        grip_type = "supinated"
    elif c.has(r"\bhammer\b|\bneutral grip|\bparallel grip|\bv (bar|handle)|\brope\b|\bclose grip pull ?down"):
        grip_type = "neutral"
    elif c.has(r"\boverhand|\bpronated?\b"):
        grip_type = "pronated"

    # Grip type: defaults
    if grip_type is None:
        if template == "PRONE_PUSH":
            grip_type = "open_palm"
        elif "ARMS_DIP_SUPPORT" in modifiers:
            grip_type = "neutral" if template == "HANGING" else "open_palm"
        elif "ARMS_CURL_MID" in modifiers:
            grip_type = "supinated"
        elif c.equipment in ("kettlebell", "band", "medicine_ball"):
            grip_type = "neutral"
        elif c.equipment == "dumbbell":
            if any(m in modifiers for m in ("ARMS_FLY_MID", "ARMS_HANG", "ARMS_ROW_PULLED", "ARMS_GOBLET",
                                            "ARMS_SKULLCRUSHER", "ARMS_OVERHEAD_EXTENSION", "ARMS_KICKBACK")):
                grip_type = "neutral"
            else:
                grip_type = "pronated"
        elif c.equipment == "cable":
            if any(m in modifiers for m in ("ARMS_FLY_MID", "ARMS_FACE_PULL", "ARMS_OVERHEAD_EXTENSION",
                                            "TORSO_CRUNCH", "ARMS_ROW_PULLED")):
                grip_type = "neutral"
            else:
                grip_type = "pronated"
        elif c.equipment == "machine":
            grip_type = "neutral"
        else:
            grip_type = "pronated"

    # Width
    if c.has(r"\bclose grip|\bnarrow grip|\bclose hand|\bdiamond\b|\bnarrow\b"):
        width = "narrow"
    elif c.has(r"\bwide grip|\bwide hand|\bwide\b(?! stance)"):
        width = "wide"
    elif c.has(r"\bmedium grip"):
        width = "medium"
    else:
        width = "shoulder"
    return {"type": grip_type, "width": width}


def guess_stance(c: Ctx, template: str, laterality: str) -> Dict[str, str]:
    position = TEMPLATE_INFO[template][0]
    name_wo_grip = re.sub(r"\b(wide|close|narrow|medium) grip\b", " ", c.name)

    width = "none"
    if position in ("standing", "split", "kneeling"):
        if re.search(r"\bsumo\b|\bwide stance\b|\bplie\b|\bwide\b", name_wo_grip):
            width = "wide"
        elif re.search(r"\bclose stance\b|\bnarrow stance\b|\bfeet together\b|\bclose\b|\bnarrow\b", name_wo_grip):
            width = "narrow"
        elif template == "SQUAT":
            width = "shoulder"
        else:
            width = "hip"
    return {"position": position, "width": width, "laterality": laterality}


def finalize_modifiers(c: Ctx, template: str, base_mods: List[str], grip: Dict[str, str],
                       stance: Dict[str, str], single_arm: bool, single_leg: bool) -> List[str]:
    mods = list(base_mods)
    if grip["width"] == "wide" and grip["type"] not in ("none", "open_palm"):
        mods.append("GRIP_WIDE")
    elif grip["width"] == "narrow" and grip["type"] != "none":
        mods.append("GRIP_NARROW")
    if stance["width"] == "wide" and template not in ("LUNGE",):
        mods.append("STANCE_WIDE")
    elif stance["width"] == "narrow" and template not in ("LUNGE",):
        mods.append("STANCE_NARROW")
    if single_arm:
        mods.append("SINGLE_ARM_RIGHT")
    if single_leg and template != "LUNGE":
        mods.append("SINGLE_LEG_RIGHT")

    # De-duplicate preserving order
    seen = set()
    out = []
    for m in mods:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def build_props(c: Ctx, template: str, modifiers: Sequence[str], grip: Dict[str, str],
                laterality: str, single_arm: bool) -> List[Dict]:
    props: List[Dict] = []

    def add(ptype: str, attach: str, **params) -> None:
        props.append({"type": ptype, "attach": attach, "params": params})

    one_hand = single_arm or (laterality == "unilateral_right" and c.muscle_group in UPPER_BODY_GROUPS)
    eq = c.equipment

    # --- Hand-held equipment ---
    if eq == "barbell":
        if "HIPS_BRIDGED" in modifiers:
            add("barbell", "pelvis")
        elif "ARMS_BACK_RACK" in modifiers:
            add("barbell", "upper_back")
        elif "ARMS_FRONT_RACK" in modifiers:
            add("barbell", "front_rack")
        else:
            add("barbell", "hands")
        if c.has(r"\bsmith\b"):
            add("machine_frame", "world", style="smith")
    elif eq == "ez_bar":
        add("ez_bar", "hands")
    elif eq == "dumbbell":
        if "ARMS_GOBLET" in modifiers or c.has(r"\bpull ?over|\btwo hand"):
            add("dumbbell", "hands")
        elif one_hand:
            add("dumbbell", "right_hand")
        else:
            add("dumbbell", "left_hand")
            add("dumbbell", "right_hand")
    elif eq == "kettlebell":
        if c.has(r"\bdouble\b|\btwo kettlebells|\bkettlebells\b"):
            add("kettlebell", "left_hand")
            add("kettlebell", "right_hand")
        elif one_hand:
            add("kettlebell", "right_hand")
        else:
            add("kettlebell", "hands")
    elif eq == "band":
        add("resistance_band", "hands")
    elif eq == "medicine_ball":
        add("medicine_ball", "hands")
    elif eq == "stability_ball":
        add("stability_ball", "world")
    elif eq == "foam_roller":
        add("foam_roller", "world")

    # --- Cables ---
    if eq == "cable":
        if c.has(r"\brope\b") or any(m in modifiers for m in ("ARMS_FACE_PULL", "ARMS_OVERHEAD_EXTENSION", "TORSO_CRUNCH")):
            add("cable_rope", "hands")
        elif template in ("SEATED_VERTICAL_PULL",) or c.has(r"\bbar\b|\bstraight bar|\bez bar|\bpush ?down|\bpushdown|\bpull ?down|\bpulldown|\bcurl"):
            if one_hand:
                add("cable_handle", "right_hand")
            else:
                add("cable_bar", "hands")
        elif one_hand:
            add("cable_handle", "right_hand")
        elif template == "SEATED_ROW":
            add("cable_handle", "hands", style="v_handle" if grip["type"] == "neutral" else "single")
        else:
            add("cable_handle", "left_hand")
            add("cable_handle", "right_hand")
        add("cable_frame", "world", towers=2 if template == "STANDING_FLY" else 1)

    # --- Machines ---
    if eq == "machine":
        add("machine_frame", "world")
        if "ANKLES_PLANTARFLEXED" in modifiers and template == "STANDING_NEUTRAL":
            add("leg_pad", "upper_back", role="shoulder_pads")
            add("footplate", "feet", angle_deg=0, role="calf_block")
        elif "ANKLES_PLANTARFLEXED" in modifiers and template == "SEATED_UPRIGHT":
            add("leg_pad", "world", role="knee_pad")
            add("footplate", "feet", angle_deg=0, role="calf_block")
        elif template in ("SEATED_UPRIGHT", "SEATED_VERTICAL_PULL", "SEATED_ROW", "STANDING_NEUTRAL",
                        "STANDING_FLY", "STANDING_OVERHEAD_PUSH", "HORIZONTAL_PUSH", "INCLINE_PUSH", "BENT_OVER_ROW"):
            if one_hand:
                add("machine_handle", "right_hand")
            else:
                add("machine_handle", "left_hand")
                add("machine_handle", "right_hand")
        if template == "SQUAT":
            add("leg_pad", "upper_back", role="shoulder_pads")
            add("footplate", "feet", angle_deg=0)

    # --- Body supports by template ---
    def has_prop(ptype: str) -> bool:
        return any(p["type"] == ptype for p in props)

    if template == "HORIZONTAL_PUSH":
        angle = -15 if c.has(r"\bdecline\b") else 0
        add("bench", "world", angle_deg=angle)
    elif template == "INCLINE_PUSH":
        add("bench", "world", angle_deg=30)
    elif template == "LEG_PRESS":
        add("seat", "world", recline_deg=45)
        add("backrest", "world", recline_deg=45)
        add("footplate", "feet", angle_deg=45)
        if not has_prop("machine_frame"):
            add("machine_frame", "world")
    elif template == "SEATED_LEG_MACHINE":
        add("seat", "world")
        add("backrest", "world", recline_deg=10)
        add("leg_pad", "feet", role="extension" if "KNEES_EXTENDED" in modifiers else "curl")
        if not has_prop("machine_frame"):
            add("machine_frame", "world")
    elif template == "SEATED_VERTICAL_PULL":
        add("seat", "world")
        add("leg_pad", "world", role="thigh_hold_down")
    elif template == "SEATED_ROW":
        add("seat", "world")
        add("footplate", "feet", angle_deg=75)
    elif template == "SEATED_UPRIGHT":
        if c.has(r"\bpreacher|\bscott\b"):
            add("seat", "world")
            add("preacher_pad", "world", angle_deg=45)
        elif "ARMS_DIP_SUPPORT" in modifiers:
            add("bench", "world", angle_deg=0, role="hands_behind")
        else:
            add("seat", "world")
            if c.equipment == "machine" or c.has(r"\bseated (shoulder|overhead|military|dumbbell|barbell) press|\barnold"):
                add("backrest", "world", recline_deg=10)
    elif template == "PRONE_FLOOR":
        if c.has(r"\bleg curl|\bhamstring curl|\bhyperextension|\bback extension|\breverse hyper") or c.equipment == "machine":
            add("bench", "world", angle_deg=0, role="prone")
            if c.has(r"\bleg curl|\bhamstring curl"):
                add("leg_pad", "feet", role="curl")
    elif template == "HANGING":
        if "ARMS_DIP_SUPPORT" in modifiers:
            add("parallel_bars", "hands")
        else:
            add("pullup_bar", "hands")
    elif template == "SUPINE_FLOOR":
        if c.has(r"\bhip thrusts?\b"):
            add("bench", "world", angle_deg=0, role="upper_back_support")
        if template == "SUPINE_FLOOR" and c.has(r"\binverted row|\bbody row|\baustralian"):
            add("pullup_bar", "hands", height="low")
    elif template == "BENT_OVER_ROW":
        if c.has(r"\bone arm|\bsingle arm|\bkickback|\bkick back") and eq == "dumbbell":
            add("bench", "world", angle_deg=0, role="knee_and_hand_support")
    elif template == "LUNGE":
        if c.has(r"\bstep ?ups?\b"):
            add("box", "right_foot", height_m=0.45)
        elif c.has(r"\bbulgarian|\brear foot elevated|\brfe"):
            add("bench", "world", angle_deg=0, role="rear_foot_support")

    return props


def build_pose_cues(template: str, modifiers: Sequence[str], grip: Dict[str, str], stance: Dict[str, str]) -> List[str]:
    cues = [TEMPLATE_INFO[template][1]]
    for m in modifiers:
        cue = MODIFIER_CUES.get(m)
        if cue:
            cues.append(cue)
    if grip["type"] != "none":
        cues.append(
            "Grip: {} ({} width).".format(grip["type"].replace("_", " "), grip["width"])
        )
    if stance["width"] not in ("none",):
        cues.append("Stance width: {}.".format(stance["width"]))
    if stance["laterality"] != "bilateral":
        cues.append("Laterality: {} (right side shown as working side).".format(stance["laterality"].replace("_", " ")))
    return cues


# --------------------------------------------------------------------------------------
# Muscles
# --------------------------------------------------------------------------------------


class MuscleMapper:
    def __init__(self, mapping: Dict):
        self.vertex_groups = set(mapping["vertex_groups"].keys())
        self.groups: Dict[str, Dict] = mapping["muscle_groups"]
        self.alias_to_group: Dict[str, str] = {}
        for key, info in self.groups.items():
            self.alias_to_group[normalize_text(key)] = key
            for alias in info.get("aliases", []):
                self.alias_to_group[normalize_text(alias)] = key
        self.type_secondary: Dict[str, List[str]] = {
            normalize_text(k): v for k, v in mapping.get("type_secondary", {}).items() if not k.startswith("_")
        }
        self.keyword_rules: List[Dict] = mapping.get("name_keyword_rules", [])
        self._validate()

    def _validate(self) -> None:
        def check(names: Sequence[str], where: str) -> None:
            for n in names:
                if n not in self.vertex_groups:
                    raise SystemExit(f"ERROR in muscle_mapping.json: '{n}' ({where}) is not defined in vertex_groups.")

        for key, info in self.groups.items():
            check(info.get("primary", []), f"muscle_groups.{key}.primary")
            check(info.get("secondary", []), f"muscle_groups.{key}.secondary")
        for key, names in self.type_secondary.items():
            check(names, f"type_secondary.{key}")
        for i, rule in enumerate(self.keyword_rules):
            check(rule.get("primary", []), f"name_keyword_rules[{i}].primary")
            check(rule.get("secondary_add", []), f"name_keyword_rules[{i}].secondary_add")
            re.compile(rule["pattern"])

    def canonical_group(self, raw: str) -> Optional[str]:
        return self.alias_to_group.get(normalize_text(raw))

    def parse_also_works(self, raw: str) -> Tuple[List[str], List[str]]:
        """Returns (canonical groups, unknown tokens)."""
        if not raw.strip():
            return [], []
        tokens = [t.strip() for t in re.split(r"[,;/|]|\band\b|&", raw) if t.strip()]
        found: List[str] = []
        unknown: List[str] = []
        for t in tokens:
            g = self.canonical_group(t)
            if g:
                if g not in found:
                    found.append(g)
            else:
                unknown.append(t)
        return found, unknown

    def resolve(self, c: Ctx, raw_type: str, also_works: str) -> Tuple[List[str], List[str], List[str], bool]:
        """Returns (primary, secondary, warnings, type_known)."""
        warnings: List[str] = []
        group = self.groups.get(c.muscle_group)
        if group is None:
            warnings.append(f"Unknown Muscle Group '{c.muscle_group}': no primary muscles assigned.")
            primary: List[str] = []
            default_secondary: List[str] = []
        else:
            primary = list(group.get("primary", []))
            default_secondary = list(group.get("secondary", []))

        type_key = normalize_text(raw_type)
        type_known = type_key in self.type_secondary
        secondary = list(self.type_secondary[type_key]) if type_known else default_secondary

        for rule in self.keyword_rules:
            mg = rule.get("muscle_group", "*")
            if mg != "*" and normalize_text(mg) != normalize_text(c.muscle_group):
                continue
            if not re.search(rule["pattern"], c.name):
                continue
            if "primary" in rule:
                demoted = [m for m in primary if m not in rule["primary"]]
                primary = list(rule["primary"])
                secondary = demoted + secondary
            secondary += rule.get("secondary_add", [])

        also_groups, unknown = self.parse_also_works(also_works)
        for t in unknown:
            warnings.append(f"Unknown 'Also Works' value '{t}' ignored. Add it as an alias in muscle_mapping.json.")
        for g in also_groups:
            secondary += self.groups[g].get("primary", [])

        # De-duplicate, and never list a primary muscle as secondary
        seen = set(primary)
        final_secondary: List[str] = []
        for m in secondary:
            if m not in seen:
                seen.add(m)
                final_secondary.append(m)
        return primary, final_secondary, warnings, type_known


# --------------------------------------------------------------------------------------
# IO
# --------------------------------------------------------------------------------------


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    last_error: Optional[Exception] = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                reader = csv.DictReader(f)
                header = [h.strip() for h in (reader.fieldnames or [])]
                missing = [col for col in REQUIRED_COLUMNS if col not in header]
                if missing:
                    raise SystemExit(
                        "ERROR: CSV is missing columns: {}\nFound columns: {}".format(missing, header)
                    )
                rows = []
                for raw in reader:
                    row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
                    if not any(row.values()):
                        continue
                    rows.append(row)
                return rows
        except UnicodeDecodeError as exc:
            last_error = exc
    raise SystemExit(f"ERROR: could not decode {path} as UTF-8 or Windows-1252: {last_error}")


def make_id(row: Dict[str, str]) -> str:
    raw = row["Exercise ID"]
    if raw.lower().startswith("lib:"):
        raw = raw[4:]
    if not raw:
        raw = row["Exercise"]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_.")
    return safe or "exercise"


def normalize_equipment(raw: str, name_norm: str) -> Tuple[str, Optional[str]]:
    key = raw.strip().lower()
    if key in EQUIPMENT_ALIASES:
        eq = EQUIPMENT_ALIASES[key]
        # Smith machine lifts are posed like barbell lifts; props.py adds the Smith frame.
        if eq in ("other", "machine") and re.search(r"\bsmith\b", name_norm):
            eq = "barbell"
        return eq, None
    return "other", f"Unknown Equipment '{raw}' treated as 'other'. Add it to EQUIPMENT_ALIASES."


def enrich_row(row: Dict[str, str], mapper: MuscleMapper, valid_templates: set) -> Dict:
    name_norm = normalize_text(row["Exercise"])
    equipment, eq_warning = normalize_equipment(row["Equipment"], name_norm)
    canonical_group = mapper.canonical_group(row["Muscle Group"]) or row["Muscle Group"]
    c = Ctx(name=name_norm, type=normalize_text(row["Type"]), muscle_group=canonical_group, equipment=equipment)

    template, base_mods, confidence, matched_rule, warnings = classify_template(c)
    if template not in valid_templates:
        raise SystemExit(f"BUG: rule '{matched_rule}' produced template '{template}' which is not in the schema enum.")
    if eq_warning:
        warnings.append(eq_warning)

    laterality, single_arm, single_leg = guess_laterality(c)
    grip = guess_grip(c, template, base_mods)
    stance = guess_stance(c, template, laterality)
    modifiers = finalize_modifiers(c, template, base_mods, grip, stance, single_arm, single_leg)

    unknown_mods = [m for m in modifiers if m not in KNOWN_MODIFIERS]
    if unknown_mods:
        raise SystemExit(f"BUG: unknown modifiers {unknown_mods} from rule '{matched_rule}'. Add them to KNOWN_MODIFIERS.")

    primary, secondary, muscle_warnings, type_known = mapper.resolve(c, row["Type"], row["Also Works"])
    warnings.extend(muscle_warnings)
    if not type_known and row["Type"]:
        warnings.append(f"Type '{row['Type']}' not in type_secondary; used muscle group default secondaries.")

    sides = "both"
    if laterality == "unilateral_right":
        sides = "right"
    elif laterality == "unilateral_left":
        sides = "left"

    props = build_props(c, template, modifiers, grip, laterality, single_arm)
    cues = build_pose_cues(template, modifiers, grip, stance)

    needs_review = confidence != "high" or any(w for w in warnings if not w.startswith("Type '"))
    return {
        "schema_version": SCHEMA_VERSION,
        "id": make_id(row),
        "library_id": row["Exercise ID"],
        "name": row["Exercise"],
        "equipment": equipment,
        "base_template": template,
        "pose_modifiers": modifiers,
        "stance": stance,
        "grip": grip,
        "pose_cues": cues,
        "joint_deltas": {},
        "custom_joint_overrides": {},
        "muscles": {"primary": primary, "secondary": secondary, "sides": sides},
        "props": props,
        "source": {
            "muscle_group": row["Muscle Group"],
            "type": row["Type"],
            "equipment": row["Equipment"],
            "also_works": row["Also Works"],
            "logged_as": row["Logged As"],
            "current_images": row["Current Images"],
        },
        "enrichment": {
            "template_confidence": confidence,
            "matched_rule": matched_rule,
            "warnings": warnings,
        },
        "review": {"status": "needs_review" if needs_review else "auto", "notes": ""},
    }


def rel(path: Path) -> str:
    """Path relative to the repo root for display, or absolute if outside it."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Enrich the OpenLift exercise CSV with pose/grip/muscle data.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="CSV path (default: data/exercise-database.csv)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON output (default: data/enriched-database.json)")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="CSV review report (default: data/enrichment-report.csv)")
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING, help="Muscle mapping JSON")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Exercise JSON schema")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: input CSV not found: {args.input}", file=sys.stderr)
        return 1

    with args.mapping.open("r", encoding="utf-8") as f:
        mapper = MuscleMapper(json.load(f))
    with args.schema.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    valid_templates = set(schema["properties"]["base_template"]["enum"])

    rows = read_csv_rows(args.input)
    print(f"Read {len(rows)} rows from {rel(args.input)}")

    records: List[Dict] = []
    seen_ids: Dict[str, int] = {}
    for row in rows:
        record = enrich_row(row, mapper, valid_templates)
        base_id = record["id"]
        if base_id in seen_ids:
            seen_ids[base_id] += 1
            record["id"] = f"{base_id}_{seen_ids[base_id]}"
            record["enrichment"]["warnings"].append(f"Duplicate ID '{base_id}' renamed to '{record['id']}'.")
            record["review"]["status"] = "needs_review"
        else:
            seen_ids[base_id] = 1
        records.append(record)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(
            {"schema_version": SCHEMA_VERSION, "count": len(records), "exercises": records},
            f, indent=2, ensure_ascii=False,
        )
        f.write("\n")

    with args.report.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "base_template", "pose_modifiers", "confidence", "matched_rule", "review_status", "warnings"])
        order = {"low": 0, "medium": 1, "high": 2}
        for r in sorted(records, key=lambda r: (order[r["enrichment"]["template_confidence"]], r["id"])):
            writer.writerow([
                r["id"], r["name"], r["base_template"], " ".join(r["pose_modifiers"]),
                r["enrichment"]["template_confidence"], r["enrichment"]["matched_rule"],
                r["review"]["status"], " | ".join(r["enrichment"]["warnings"]),
            ])

    # Summary
    conf = Counter(r["enrichment"]["template_confidence"] for r in records)
    templates = Counter(r["base_template"] for r in records)
    unknown_types = Counter(
        r["source"]["type"] for r in records
        if any(w.startswith("Type '") for w in r["enrichment"]["warnings"])
    )
    print(f"Wrote {len(records)} exercises to {rel(args.output)}")
    print(f"Wrote review report to {rel(args.report)}")
    print("")
    print("Confidence:  high={}  medium={}  low={}".format(conf.get("high", 0), conf.get("medium", 0), conf.get("low", 0)))
    print("Templates:")
    for t, n in sorted(templates.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {t:<24} {n}")
    if unknown_types:
        print("")
        print("Types not in muscle_mapping.json 'type_secondary' (add them for better secondary muscles):")
        for t, n in unknown_types.most_common():
            print(f"  {t!r}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())