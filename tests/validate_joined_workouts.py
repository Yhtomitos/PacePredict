"""Validate a complete joined archive independently of its join implementation.

Input: Joined gzip JSON Lines archive and adjacent build manifest.
Output: Validated counts; schema, identifier, or checksum errors raise.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


def validate_joined(path: Path) -> dict[str, Any]:
    """Check identities, uniqueness, gzip integrity, and counts.

    Input: Joined archive path.
    Output: Workout count and records containing heart-rate arrays.
    """
    report = json.loads(path.with_suffix(".build.json").read_text())
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != report["output_sha256"]:
        raise ValueError("Joined archive checksum mismatch")
    seen: set[int] = set()
    heart_rate_records = 0
    with gzip.open(path, "rt", encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            if set(row) != {"workout_id", "user_id", "hr", "meta"}:
                raise ValueError("Unexpected joined schema")
            validate_sources(row)
            if row["workout_id"] in seen:
                raise ValueError("Duplicate joined workout")
            seen.add(row["workout_id"])
            heart_rate_records += isinstance(row["hr"].get("heart_rate"), list)
    matches = len(seen)
    if matches != report["matched_workouts"]:
        raise ValueError("Joined count differs from build report")
    for name in ("hr", "meta"):
        if matches + report[f"unmatched_{name}"] != report[f"{name}_records"]:
            raise ValueError("Joined and unmatched counts do not reconcile")
    return {"workouts": matches, "heart_rate_records": heart_rate_records}


def validate_sources(row: dict[str, Any]) -> None:
    """Check embedded record identity without imposing feature cleaning.

    Input: Joined workout object.
    Output: None; inconsistent embedded dictionaries raise ValueError.
    """
    for key in ("hr", "meta"):
        record = row[key]
        if not isinstance(record, dict):
            raise ValueError("Expected embedded source dictionary")
        if record.get("id") != row["workout_id"]:
            raise ValueError("Embedded workout ID mismatch")
        if record.get("userId") != row["user_id"]:
            raise ValueError("Embedded user ID mismatch")
        if record.get("sport") != "run":
            raise ValueError("Embedded workout is not outdoor running")
    for key in ("workout_id", "user_id"):
        if type(row[key]) is not int or row[key] < 0:
            raise ValueError("Invalid joined identifier")


def main() -> None:
    """Run independent validation from the command line.

    Input: Joined archive path argument.
    Output: Validation status on stdout.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    validate_joined(args.archive)
    print("Validation succeeded")


if __name__ == "__main__":
    main()
