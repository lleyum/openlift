# OpenLift QA Backlog

Visual bugs found in render reviews. Each item has a target phase and a proposed fix.
Update the **Status** column when an item is fixed, and add the render that proves it.

Status values: `OPEN`, `IN PROGRESS`, `FIXED (Phase N)`, `WONTFIX (reason)`.

| ID | Area | Bug | Target | Status |
|---|---|---|---|---|
| QA-01 | Grip | Fingers stay nearly flat instead of wrapping bars/handles | Phase 5 | OPEN |
| QA-02 | Grip | Dumbbells stay vertical on lateral raises instead of horizontal | Phase 5 | OPEN |
| QA-03 | Rig | Armpits stretch/tear on `STANDING_OVERHEAD_PUSH` | Phase 5 | OPEN |
| QA-04 | Props | Goblet squat: weight floats in front of the chest, not in the hands | Phase 5 | OPEN |
| QA-05 | Props | Barbell step-ups: hands do not reach the back-racked bar | Phase 5 | OPEN |
| QA-06 | Props | One-arm dumbbell row: dumbbell not seated in the hand | Phase 5 | OPEN |
| QA-07 | Pose | EZ-bar skullcrusher: bar clips the face | Phase 5 | OPEN |
| QA-08 | Muscles | Glute highlight hidden under the procedural shorts | Phase 4 | FIXED (Phase 4) |
| QA-09 | Props | Leg press machine frame reads as stray diagonal bars from above | Phase 5 | OPEN |
| QA-10 | Pose | Barbell hip thrust renders as a floor glute bridge (no bench under the shoulders) | Phase 5 | OPEN |
| QA-11 | Thumbnails | 45 cm plates can hide the body in the side view (hip thrust, curl) | Phase 5 | OPEN |

---

## QA-01 Fingers do not wrap the bar (`HANDS_CLOSED`)

**Seen in:** bench press, barbell curl, pull-ups (Phase 3 cloud renders and local `preview_pose.py`).

**Cause:** every closed grip in `schema/base_templates.json` → `grips` flexes each finger joint 60° and the thumb 25°. With 3 joints per finger that is not quite a fist around a 28 mm bar.

**Proposed fix:**
1. Add a `HANDS_CLOSED` modifier (mode `add`) to `base_templates.json`: `LeftFingers`/`RightFingers` `[25, 0, 0]`, `LeftThumb`/`RightThumb` `[20, 0, 0]`, `idle_sides` respected.
2. Emit it from `pipeline/enrich_csv.py` `finalize_modifiers()` whenever `grip.type` is `pronated`, `supinated`, `neutral` or `mixed`; add it to `KNOWN_MODIFIERS`.
3. Re-run `enrich_csv.py` and `generate_jsons.py` (manual overrides are preserved).

**Acceptance:** close-up render of the bench press grip shows fingertips touching the palm side of the bar; no fingers pass through the bar.

## QA-02 Dumbbells vertical on lateral raises (`WRISTS_PRONATED`)

**Seen in:** Side Lateral Raise (Phase 3 sheet B).

**Cause:** `guess_grip()` gives dumbbells a `pronated` grip for raises, which twists the forearm 80°. In a lateral raise the arm is abducted, so the dumbbell handle ends up pointing up/down instead of front-to-back.

**Proposed fix:**
1. Add a `WRISTS_PRONATED` modifier (mode `add`): `LeftUpperArm`/`RightUpperArm` `[0, 30, 0]` (internal rotation) and `LeftForearm`/`RightForearm` `[0, 60, 0]`, tuned so the dumbbell axis is horizontal and front-to-back at the top of the raise.
2. Emit it from `enrich_csv.py` for `ARMS_LATERAL_RAISE` when equipment is `dumbbell`, `kettlebell` or `cable`, and set that exercise's grip to `neutral` so the twist is not applied twice.

**Acceptance:** front render of Side Lateral Raise shows both dumbbell handles horizontal, pointing toward the camera.

## QA-03 Armpit tearing on overhead presses

**Seen in:** `STANDING_OVERHEAD_PUSH` (template sheet), `ARMS_OVERHEAD` exercises.

**Cause:** upper arm flexion of 170° pushes the MPFB Game Engine weights past their range; the armpit vertices are split between `upperarm_*`, `clavicle_*` and `spine_03` with hard transitions.

**Proposed fix:**
1. `base_templates.json`: `STANDING_OVERHEAD_PUSH` and `ARMS_OVERHEAD` upper arms `[165, 0, 12]`; add `LeftShoulder`/`RightShoulder` `[0, 0, 15]` (clavicle elevation) so part of the lift comes from the shoulder girdle, as in real anatomy.
2. Weight smoothing in `pipeline/prep_mesh.py` (new step, runs once): for vertices within 8 cm of each `UpperArm` head, run 3 iterations of neighbour-averaging on the `upperarm_*`, `clavicle_*`, `spine_03` weights, then normalize. Use `bpy.ops.object.vertex_group_smooth` on a selection or an equivalent bmesh loop.
3. Re-run `prep_mesh.py` from the backup.

**Acceptance:** front and side renders of Standing Military Press show a continuous armpit with no stretched triangles.

## QA-04 Goblet squat weight offset

**Seen in:** Goblet Squat (Phase 3 sheet C).

**Cause:** `props.build_kettlebell()` (`attach: hands`) centers the handle between the two grip points and hangs the bell straight down; with `ARMS_GOBLET` the hands are close together at the chest, so the bell sits in front of the sternum.

**Proposed fix:** for `ARMS_GOBLET`, place the handle at the grip midpoint moved 3 cm toward the palms, and hang the bell along the forearms' average direction (wrist → elbow) instead of world-down, so it rests against the hands/chest. Same rule for a dumbbell held vertically (goblet with dumbbell).

**Acceptance:** side render shows the bell touching both palms, no gap between the hands and the handle.

## QA-05 Barbell step-ups: hands not on the back-racked bar

**Seen in:** Barbell Step Ups (Phase 3 sheet D).

**Cause:** `ARMS_BACK_RACK` was tuned for `SQUAT` (torso leaning forward). In `LUNGE` the torso is upright, so the same joint values leave the hands above and in front of the bar.

**Proposed fix:** add a `LUNGE` (and `STANDING_NEUTRAL`) variant to `ARMS_BACK_RACK` in `base_templates.json`, roughly `UpperArm [-10, -75, 72]`, `Forearm [120, 0, 0]`; then verify that `grip_frame()` centers on the bar within 3 cm. Add an automatic check in `props.build_barbell()` that prints a warning when either grip center is more than 6 cm from the bar axis.

**Acceptance:** both hands visibly wrap the bar behind the neck in the front and side renders.

## QA-06 One-arm dumbbell row: dumbbell not in the hand

**Seen in:** One-Arm Dumbbell Row (Phase 3 sheet D).

**Cause:** the grip center comes from `index_01`/`pinky_01` knuckle heads plus a fixed 2.8 cm palm offset. With the arm hanging and `neutral` grip at the bottom of a row the fingers are only 60° flexed (QA-01), so the handle sits outside the fist.

**Proposed fix:** fix QA-01 first. Then compute the grip center from the curled fingers instead of a fixed offset: midpoint between the knuckle line and the `middle_02` head (after posing). Apply to all hand-held props.

**Acceptance:** the dumbbell handle passes through the closed fist in a close-up render.

## QA-07 EZ-bar skullcrusher clips the face

**Seen in:** EZ-Bar Skullcrusher (Phase 3 sheet D).

**Cause:** `ARMS_SKULLCRUSHER` uses upper arm `[110, 0, 6]` and forearm `[105, 0, 0]`; the bar ends up inside the head.

**Proposed fix:** upper arm `[120, 0, 8]` (elbows further back over the head), forearm `[95, 0, 0]`. Add a generic clearance check in `props.py`: if any hand-held prop vertex is inside the body (BVH ray parity test), print a warning naming the exercise so it shows up in the build report.

**Acceptance:** side render shows the bar 3-6 cm above the forehead.

## QA-08 Glute highlight hidden by shorts (FIXED in Phase 4)

**Cause:** muscle shells sit 2 mm off the skin; the shorts sit 6-10 mm off the skin.

**Fix:** `pipeline/muscles.py` measures, for every shell vertex, the distance to the posed shorts. Vertices covered by the shorts are pushed out to sit above the shorts (`COVERED_OFFSET_M`), so glute, upper hamstring, upper quad and adductor highlights stay visible.

**Acceptance:** Barbell Hip Thrust / glute exercise renders show red glutes over the black shorts.

**Verified:** Phase 4 cloud build, `Barbell_Hip_Thrust_hero.png`: gluteus maximus shell visible over the shorts.

## QA-09 Leg press frame looks wrong from above

**Seen in:** `Leg_Press_hero.png` (Phase 4 cloud build).

**Cause:** `props.build_machine_frame()` places two posts and floor rails behind the body using the torso's horizontal forward axis. For a 45-degree reclined body the "behind" side is under the seat, so the rails and posts read as stray diagonals in elevated views.

**Proposed fix:** give `LEG_PRESS` its own frame: two rails running from under the seat up to the footplate along the sled direction (the shin axis), joined to the footplate posts. Skip the generic upright frame for this template.

**Acceptance:** the hero view shows a recognisable sled (rails from seat to footplate).

## QA-10 Hip thrust has no bench

**Seen in:** Barbell Hip Thrust (Phase 3 sheet C, Phase 4 build).

**Cause:** `HIPS_BRIDGED` is a floor bridge (shoulders on the floor), so `props.build_bench(role=upper_back_support)` finds no height and skips the bench by design.

**Proposed fix:** add an enrich rule (`\bhip thrusts?\b` → modifier `HIPS_THRUST_BENCH` instead of `HIPS_BRIDGED`): Root `[-90, 0, 0]` (trunk level, shoulders at bench height), thighs in line with the trunk, knees about 90 degrees, feet planted. The bench is then fitted under the upper back automatically.

**Acceptance:** side view shows the shoulder blades on a bench, hips level with the shoulders, shins vertical.

## QA-11 Plates hide the body in side thumbnails

**Seen in:** `Barbell_Hip_Thrust_side.png`, `Barbell_Curl` side view.

**Cause:** the side camera looks straight along the barbell; a 45 cm plate sits between the camera and the body.

**Proposed fix:** in `thumbnails.compute_cameras()`, if more than 30% of the body's projected bounding box is covered by prop geometry in a view (ray test on a 16x16 grid), rotate that view's azimuth by up to +/- 25 degrees until coverage drops below 30%. Record the final azimuth in the camera JSON (already stored as `azimuth_deg`).

**Acceptance:** side thumbnails of barbell exercises show the torso and the highlighted muscle.
