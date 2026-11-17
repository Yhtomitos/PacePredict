"""Independently validate the combined dataset and its build manifest.

Input: Combined gzip JSON Lines archive.
Output: Validated counts and missing-field totals, or a validation error.
"""

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

EXPECTED = {
    "workout_id",
    "user_id",
    "started_at_utc",
    "distance_km",
    "temperature_c",
    "relative_humidity_pct",
    "elevation_gain_m",
    "elevation_loss_m",
    "mean_elevation_m",
    "pace_min_per_km",
}
NUMERIC = EXPECTED - {"workout_id", "user_id", "started_at_utc"}


def validate_numbers(row: dict[str, Any]) -> None:
    """Check numeric values and basic physical constraints.

    Input: Schema-checked dictionary.
    Output: None; invalid values raise ValueError.
    """
    for key in NUMERIC:
        value = row[key]
        if value is not None and (
            type(value) not in (int, float) or not math.isfinite(value)
        ):
            raise ValueError(f"Invalid number: {key}")
    for key in ("distance_km", "pace_min_per_km"):
        if row[key] is None or row[key] <= 0:
            raise ValueError(f"Expected positive value: {key}")
    for key in ("elevation_gain_m", "elevation_loss_m"):
        if row[key] is not None and row[key] < 0:
            raise ValueError(f"Expected nonnegative value: {key}")
    humidity = row["relative_humidity_pct"]
    if humidity is not None and not 0 <= humidity <= 100:
        raise ValueError("Humidity outside [0, 100]")


def validate_row(row: dict[str, Any], previous: int) -> int:
    """Validate schema, numeric constraints, and ordered identifiers.

    Input: Decoded row and preceding workout identifier.
    Output: Current identifier; invalid values raise ValueError.
    """
    if set(row) != EXPECTED:
        raise ValueError("Unexpected schema")
    for key in ("workout_id", "user_id"):
        if type(row[key]) is not int or row[key] < 0:
            raise ValueError(f"Invalid identifier: {key}")
    if row["workout_id"] <= previous:
        raise ValueError("Workout IDs must be unique and increasing")
    validate_numbers(row)
    started = row["started_at_utc"]
    if started is not None:
        if not isinstance(started, str) or not started.endswith("Z"):
            raise ValueError("Start time must be UTC")
        datetime.fromisoformat(started.replace("Z", "+00:00"))
    return row["workout_id"]


def validate_combined(path: Path) -> dict[str, Any]:
    """Check every row, gzip integrity, checksum, and reconciled totals.

    Input: Combined archive with adjacent build manifest.
    Output: Record count and null counts for the validated archive.
    """
    manifest = json.loads(path.with_suffix(".build.json").read_text())
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != manifest["output_sha256"]:
        raise ValueError("Archive checksum differs from build manifest")
    previous = -1
    count = 0
    missing: Counter[str] = Counter()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            previous = validate_row(row, previous)
            missing.update(key for key, value in row.items() if value is None)
            count += 1
    if count != manifest["output_records"]:
        raise ValueError("Output count differs from build manifest")
    if count + sum(manifest["rejected"].values()) != manifest["hr_records"]:
        raise ValueError("Input and output counts do not reconcile")
    return {"records": count, "null_counts": dict(missing)}


def main() -> None:
    """Validate from the command line.

    Input: Archive path argument.
    Output: JSON validation results on stdout.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate_combined(args.archive), indent=2))


if __name__ == "__main__":
    main()
