#!/usr/bin/env python3
"""
OpenLift - split data/enriched-database.json into exercises/<id>.json.

Safe to re-run. Manual work is never lost:
  - custom_joint_overrides, joint_deltas and review.notes are carried over from
    the existing file if they are non-empty.
  - Files with review.status == "approved" are not touched at all.
  - --force overwrites everything (including approved files and manual fields).

Validates every record against schema/exercise.schema.json. Uses the
`jsonschema` package if installed (pip install jsonschema); otherwise runs a
built-in check of required fields and enums.

Pure standard library. Python 3.9+. Does NOT need Blender.

Usage (from the repo root):
    python3 pipeline/generate_jsons.py
    python3 pipeline/generate_jsons.py --only Barbell_Bench_Press_-_Medium_Grip
    python3 pipeline/generate_jsons.py --prune      # also delete JSONs no longer in the CSV
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "enriched-database.json"
DEFAULT_OUT_DIR = REPO_ROOT / "exercises"
DEFAULT_SCHEMA = REPO_ROOT / "schema" / "exercise.schema.json"

# Fields hand-edited by humans. Kept from the existing file when non-empty.
PRESERVED_FIELDS = ("joint_deltas", "custom_joint_overrides")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------


class Validator:
    def __init__(self, schema: Dict[str, Any]):
        self.schema = schema
        self._full = None
        try:
            import jsonschema  # type: ignore

            cls = jsonschema.validators.validator_for(schema)
            cls.check_schema(schema)
            self._full = cls(schema)
            try:
                from importlib.metadata import version

                self.mode = "jsonschema " + version("jsonschema")
            except Exception:
                self.mode = "jsonschema"
        except ImportError:
            self.mode = "built-in (install 'jsonschema' for full validation)"

    def errors(self, record: Dict[str, Any]) -> List[str]:
        if self._full is not None:
            return [
                "{}: {}".format("/".join(str(p) for p in e.absolute_path) or "<root>", e.message)
                for e in self._full.iter_errors(record)
            ]
        return self._basic_errors(record)

    def _basic_errors(self, record: Dict[str, Any]) -> List[str]:
        props = self.schema["properties"]
        defs = self.schema["$defs"]
        errs: List[str] = []
        for key in self.schema["required"]:
            if key not in record:
                errs.append(f"missing required field '{key}'")
        for key in record:
            if key not in props:
                errs.append(f"unknown field '{key}'")
        for key in ("equipment", "base_template"):
            if key in record and record[key] not in props[key]["enum"]:
                errs.append(f"{key}: '{record[key]}' is not one of the allowed values")
        for key in ("stance", "grip"):
            for sub, spec in props[key]["properties"].items():
                val = record.get(key, {}).get(sub)
                if "enum" in spec and val not in spec["enum"]:
                    errs.append(f"{key}.{sub}: '{val}' is not one of {spec['enum']}")
        muscle_keys = set(defs["muscleKey"]["enum"])
        for tier in ("primary", "secondary"):
            for m in record.get("muscles", {}).get(tier, []):
                if m not in muscle_keys:
                    errs.append(f"muscles.{tier}: unknown muscle '{m}'")
        bones = set(defs["standardBone"]["enum"])
        for field in ("joint_deltas", "custom_joint_overrides"):
            for bone, value in record.get(field, {}).items():
                if bone not in bones:
                    errs.append(f"{field}: unknown bone '{bone}' (use Standard Anatomical Names)")
                rot = value.get("rotation_deg") if isinstance(value, dict) else None
                if rot is not None and (not isinstance(rot, list) or len(rot) != 3):
                    errs.append(f"{field}.{bone}.rotation_deg must be [x, y, z]")
        prop_types = set(defs["prop"]["properties"]["type"]["enum"])
        attach_points = set(defs["prop"]["properties"]["attach"]["enum"])
        for i, p in enumerate(record.get("props", [])):
            if p.get("type") not in prop_types:
                errs.append(f"props[{i}].type '{p.get('type')}' is not allowed")
            if p.get("attach") not in attach_points:
                errs.append(f"props[{i}].attach '{p.get('attach')}' is not allowed")
        return errs


# --------------------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------------------


def merge_with_existing(new: Dict[str, Any], existing: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(new)
    for field in PRESERVED_FIELDS:
        if existing.get(field):
            merged[field] = existing[field]
    old_review = existing.get("review") or {}
    review = dict(merged.get("review") or {"status": "auto", "notes": ""})
    if old_review.get("notes"):
        review["notes"] = old_review["notes"]
    # Keep a human decision (rejected) rather than resetting it to auto/needs_review.
    if old_review.get("status") == "rejected":
        review["status"] = "rejected"
    merged["review"] = review
    return merged


def dump(record: Dict[str, Any]) -> str:
    return json.dumps(record, indent=2, ensure_ascii=False) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Write one JSON file per exercise into exercises/.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Enriched JSON (default: data/enriched-database.json)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output folder (default: exercises/)")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Exercise JSON schema")
    parser.add_argument("--only", action="append", default=[], metavar="ID", help="Only write this exercise ID (repeatable)")
    parser.add_argument("--force", action="store_true", help="Overwrite everything, including approved files and manual fields")
    parser.add_argument("--prune", action="store_true", help="Delete exercise JSONs whose ID is no longer in the enriched data")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: {rel(args.input)} not found. Run: python3 pipeline/enrich_csv.py", file=sys.stderr)
        return 1

    with args.input.open("r", encoding="utf-8") as f:
        data = json.load(f)
    with args.schema.open("r", encoding="utf-8") as f:
        validator = Validator(json.load(f))

    records: List[Dict[str, Any]] = data["exercises"]
    if args.only:
        wanted = set(args.only)
        records = [r for r in records if r["id"] in wanted]
        missing = wanted - {r["id"] for r in records}
        if missing:
            print(f"ERROR: IDs not found in {rel(args.input)}: {sorted(missing)}", file=sys.stderr)
            return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Validating with: {validator.mode}")

    counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped_approved": 0, "invalid": 0, "pruned": 0}
    invalid: Dict[str, List[str]] = {}

    for record in records:
        path = args.out_dir / f"{record['id']}.json"
        final = record
        if path.exists() and not args.force:
            try:
                with path.open("r", encoding="utf-8") as f:
                    existing = json.load(f)
            except json.JSONDecodeError as exc:
                print(f"ERROR: {rel(path)} is not valid JSON ({exc}). Fix or delete it, then re-run.", file=sys.stderr)
                return 1
            if (existing.get("review") or {}).get("status") == "approved":
                counts["skipped_approved"] += 1
                continue
            final = merge_with_existing(record, existing)

        errs = validator.errors(final)
        if errs:
            invalid[record["id"]] = errs
            counts["invalid"] += 1
            continue

        text = dump(final)
        if path.exists():
            if path.read_text(encoding="utf-8") == text:
                counts["unchanged"] += 1
                continue
            counts["updated"] += 1
        else:
            counts["created"] += 1
        if not args.dry_run:
            path.write_text(text, encoding="utf-8", newline="\n")

    if args.prune and not args.only:
        keep = {f"{r['id']}.json" for r in data["exercises"]}
        for path in sorted(args.out_dir.glob("*.json")):
            if path.name not in keep:
                counts["pruned"] += 1
                print(f"  prune: {rel(path)}")
                if not args.dry_run:
                    path.unlink()

    prefix = "[dry run] " if args.dry_run else ""
    print(
        "{}created={created}  updated={updated}  unchanged={unchanged}  skipped_approved={skipped_approved}  "
        "invalid={invalid}  pruned={pruned}".format(prefix, **counts)
    )
    print(f"{prefix}Output folder: {rel(args.out_dir)}/")

    if invalid:
        print("", file=sys.stderr)
        print("INVALID RECORDS (not written):", file=sys.stderr)
        for ex_id, errs in invalid.items():
            print(f"  {ex_id}", file=sys.stderr)
            for e in errs:
                print(f"    - {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())