# OpenLift - Phase 6 kickoff prompt for Claude Code

Paste everything below the line into Claude Code, started in the repo root.

---

You are taking over OpenLift as pipeline lead. Phases 1-5 are done: the pipeline builds GLBs and
thumbnails, and the golden sample (`Barbell_Bench_Press_-_Medium_Grip`) passed the gate in
`docs/TESTING.md`. Your job now is to run the **full library** (every exercise in `exercises/`),
find what looks wrong, fix it, and ship a clean release.

Before doing anything else, read `CLAUDE.md` (project rules, architecture, pitfalls), then
`docs/QA_BACKLOG.md` and `docs/TESTING.md`. Follow CLAUDE.md's rules throughout, especially: data-first
fixes, verify every visual fix with a render, keep the golden sample passing, no `WARNING:` lines,
log every bug in the backlog. I am not a programmer: tell me in plain language what you did, what
you found, and what you need from me. Ask before deleting outputs, re-prepping the body, or pushing.

Work through the stages below in order. Finish each stage, give me a short report (what ran, counts,
problems, decisions you need from me), and wait for my "go" before the next stage.

## Stage 0 - Health check (no changes)

1. `git status` and `git log --oneline -5`: confirm a clean tree on `main` and that Phase 5 is committed.
   Create a working branch `phase6-batch`.
2. Run `blender --background assets/base_body.blend --python pipeline/validate_rig.py`. It must end with
   `RESULT: OK` and report a limb offset ≤ 5 cm.
3. Rebuild the golden sample with thumbnails, look at the three PNGs yourself, and confirm it still matches
   `docs/TESTING.md` §5.
4. Count the exercises in `exercises/`, and summarise `data/enrichment-report.csv`: how many high /
   medium / low confidence, top 10 base templates, and the most common warnings.

## Stage 1 - Batch tooling (small, tested additions)

Add these, each with a quick test, in separate commits:

1. `tools/merge_reports.py` (plain Python): merges every `dist/build_report*.json` into
   `dist/build_summary.json` and prints totals (ok / failed / warnings) plus a list of failed and warned
   exercises. `build.py` overwrites `dist/build_report.json` each run, so copy it to
   `dist/build_report_<batch>.json` after each batch.
2. `tools/contact_sheet.py`: makes review sheets (e.g. 4x5 grid, hero view by default, exercise id under
   each tile, gray background) from `dist/thumbnails/`, ordered by a list of ids or by
   `enrichment-report.csv` confidence. Use Pillow (`py -m pip install pillow`) or Blender's image API -
   your choice, but document how to run it.
3. `pipeline/make_manifest.py` (plain Python): writes `dist/manifest.json`, one entry per built
   exercise with id, name, muscle group, equipment, base template, primary/secondary muscles, and the
   relative paths of the GLB, camera JSON and thumbnails, plus file sizes. Apps will load this.

## Stage 2 - Pilot batch (50 exercises)

1. Pick 50 exercises that cover every `base_template` and every `equipment` type (spread across low,
   medium and high confidence). Build them with `--thumbnails`.
2. Make contact sheets and review every tile yourself. Classify problems into families (e.g. "all cable
   rows: handle too far from the hands", "all stretches: placeholder pose").
3. Report to me: the sheets you looked at, the problem families with counts and example ids, and your
   proposed fix per family (which template / modifier / rule / prop builder you would change).
   Do not fix yet - wait for my go.

## Stage 3 - Fix loop (repeat until the pilot is clean)

For each approved family:
1. Add a `QA-16+` entry to `docs/QA_BACKLOG.md`.
2. Fix it data-first (rule, modifier, variant, prop builder). Use per-exercise
   `custom_joint_overrides` only for true one-offs.
3. Rebuild the affected pilot exercises plus the golden sample with thumbnails, compare before/after
   sheets, and mark the backlog entry FIXED with the evidence.
4. Commit (`QA-NN: <summary>`).

The pilot is clean when: 0 failed, 0 warnings, and every tile passes a visual check (body posed
plausibly, hands on the equipment, equipment not through the body, highlighted muscles visible in the
hero view, nothing cropped).

## Stage 4 - Full batch

1. GLBs first (fast): `blender --background --python build.py -- --all --skip-existing`. If Blender
   crashes or is interrupted, just rerun the same command - `--skip-existing` resumes.
2. Thumbnails: `blender --background --python build.py -- --all --thumbnails --skip-existing`. Expect
   roughly 3-6 s per exercise with EEVEE on a GPU; run it in chunks with `--limit 200` if you prefer,
   copying the report after each chunk.
3. Merge reports. Every failure and warning gets triaged: fix the family (back to Stage 3) or record it in
   the backlog with the reason it is acceptable.
4. Review contact sheets for **all** low- and medium-confidence exercises, and a random 10% sample of the
   high-confidence ones. Report problem families to me as in Stage 2.

## Stage 5 - Release

1. Regenerate `dist/manifest.json`. Check every GLB is < 2 MB and opens (spot-check 5 in
   https://gltf-viewer.donmccurdy.com - ask me to do this if you cannot open a browser).
2. Run the full `docs/TESTING.md` gate one last time (golden sample + QA regression batch).
3. Write `docs/RELEASE_NOTES.md`: counts, known limitations (link backlog ids), how apps should use
   `manifest.json`, `dist/camera/*.json` (orthographic front/side, 85 mm hero) and the glTF extras
   (`openlift_muscles`, `openlift_tier`).
4. Zip `dist/glb`, `dist/camera`, `dist/thumbnails`, `dist/manifest.json` into `dist/openlift-v1.0.zip`.
5. Ask me before merging `phase6-batch` into `main`, pushing, or creating a GitHub release
   (`gh release create v1.0 dist/openlift-v1.0.zip --notes-file docs/RELEASE_NOTES.md`).

## Known open items you may pick up after the release

- Front-squat front rack: elbows lower than a textbook rack (needs a "fingers under the bar" hand pose).
- `--gender female`: needs `assets/base_body_female.blend` made like the male body (HANDOFF.md Part C,
  gender 0.0), then `prep_mesh.py` on it. All code paths already take `--gender`.
- Stretch / mobility exercises use placeholder poses (flagged low confidence by `enrich_csv.py`).
