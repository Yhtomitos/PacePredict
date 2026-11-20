"""Summarize joined workouts into the planned modeling schema.

Input: Nested or flat joined gzip archive and its build manifest.
Output: Schema-version-3 summary archive; weather awaits enrichment.
"""

import argparse
import gzip
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .endomondo_combine_datasets import (
    PROCESSED,
    encoded,
    identifier,
    numeric,
    publish_file,
    sha256,
)
from .endomondo_join_workouts import joined_record

FIELDS = (
    "workout_id",
    "user_id",
    "started_at_utc",
    "distance_miles",
    "latitude",
    "longitude",
    "elevation_gain_m",
    "elevation_loss_m",
    "average_heart_rate_bpm",
    "temperature_c",
    "relative_humidity_pct",
    "pace_min_per_mile",
)
KM_PER_MILE = 1.609344


def average_heart_rate(row: dict[str, Any]) -> float | None:
    """Calculate trapezoidal time-weighted heart rate, without gap imputation.

    Input: Aligned BPM and Unix-second arrays.
    Output: Mean BPM, or None for invalid/missing samples or time intervals.
    """
    rates, times = row.get("heart_rate"), row.get("hr_timestamp")
    if not isinstance(rates, list) or not isinstance(times, list):
        return None
    if len(rates) != len(times) or len(rates) < 2:
        return None
    bpm = [numeric(value, 0) for value in rates]
    seconds = [numeric(value) for value in times]
    total = weighted = 0.0
    for index in range(1, len(rates)):
        left, right = bpm[index - 1], bpm[index]
        start, end = seconds[index - 1], seconds[index]
        if left is None or right is None or start is None or end is None:
            return None
        if left <= 0 or right <= 0 or end <= start:
            return None
        total += end - start
        weighted += (end - start) * (left + right) / 2
    return numeric(weighted / total, 0)


def start_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Extract the first valid coordinate pair and first HR sample time.

    Input: Flat joined workout with original arrays.
    Output: Scalar start metadata; unavailable values remain null.
    """
    result: dict[str, Any] = dict.fromkeys(
        ("latitude", "longitude", "started_at_utc")
    )
    latitudes, longitudes = row.get("latitude"), row.get("longitude")
    if isinstance(latitudes, list) and isinstance(longitudes, list):
        for lat, lon in zip(latitudes, longitudes, strict=False):
            lat, lon = numeric(lat), numeric(lon)
            if lat is not None and lon is not None:
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    result.update(latitude=lat, longitude=lon)
                    break
    times = row.get("hr_timestamp")
    timestamp = (
        numeric(times[0]) if isinstance(times, list) and times else None
    )
    if timestamp is not None:
        try:
            result["started_at_utc"] = (
                datetime.fromtimestamp(timestamp, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (ValueError, OverflowError, OSError):
            pass
    return result


def summarize_workout(row: dict[str, Any]) -> dict[str, Any]:
    """Build scalar features without using pace as an input.

    Input: Flat outdoor workout; source distance is km and duration seconds.
    Output: Planned schema with null invalid values and pending weather.
    """
    if row.get("sport") != "run":
        raise ValueError("Expected outdoor running records")
    distance = numeric(row.get("distance"), 0)
    miles = distance / KM_PER_MILE if distance and distance > 0 else None
    duration = numeric(row.get("duration"), 0)
    pace = duration / 60 / miles if duration and miles else None
    return {
        "workout_id": identifier(row.get("workout_id")),
        "user_id": identifier(row.get("user_id")),
        **start_metadata(row),
        "distance_miles": miles,
        "elevation_gain_m": numeric(row.get("ascent"), 0),
        "elevation_loss_m": numeric(row.get("descent"), 0),
        "average_heart_rate_bpm": average_heart_rate(row),
        "temperature_c": None,
        "relative_humidity_pct": None,
        "pace_min_per_mile": numeric(pace, 0),
    }


def flatten_archive(
    source: Path,
    output: Path,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Summarize a verified joined archive without mutating raw sources.

    Input: Joined archive, output path, and explicit replacement permission.
    Output: Summary archive and manifest; conversion failures preserve input.
    """
    report_path = output.with_suffix(".build.json")
    if (output.exists() or report_path.exists()) and not replace:
        raise ValueError(
            "Output exists; replacement requires explicit permission"
        )
    if output.resolve().is_relative_to((PROCESSED.parent / "raw").resolve()):
        raise ValueError("Cannot write inside raw source storage")
    report = json.loads(source.with_suffix(".build.json").read_text())
    if report.get("schema_version") not in (1, 2):
        raise ValueError("Expected joined schema version 1 or 2")
    previous_hash = sha256(source)
    if previous_hash != report["output_sha256"]:
        raise ValueError("Source checksum differs from manifest")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=".combine-"
    ) as tmp:
        staging = Path(tmp)
        count = write_flattened(source, staging / "data.gz")
        if count != report["matched_workouts"]:
            raise ValueError("Migrated count differs from source manifest")
        report.pop("fields", None)
        report.pop("weather", None)
        report.pop("removed_fields", None)
        report.pop("overlap_precedence", None)
        report.update(
            {
                "schema_version": 3,
                "status": "weather_pending",
                "required_fields": FIELDS,
                "pace_definition": "duration seconds / 60 / distance miles",
                "heart_rate_definition": (
                    "Trapezoidal BPM average; invalid arrays yield null; "
                    "measured during workout, unavailable before a planned run"
                ),
                "coordinate_definition": "First valid paired HR coordinates",
                "missing_values": "Null; no imputation or row removal",
                "weather_definition": "Null pending separate enrichment",
                "flattened_from_sha256": previous_hash,
                "output_sha256": sha256(staging / "data.gz"),
            }
        )
        (staging / "report.json").write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )
        publish_file(staging / "data.gz", output)
        publish_file(staging / "report.json", report_path)
    return report


def write_flattened(source: Path, destination: Path) -> int:
    """Stream nested or flat joined records into scalar summaries.

    Input: Joined gzip source and new temporary destination path.
    Output: Written row count; duplicate or inconsistent IDs fail conversion.
    """
    seen: set[int] = set()
    with gzip.open(source, "rt", encoding="utf-8") as incoming:
        with destination.open("xb") as raw:
            with gzip.GzipFile(
                fileobj=raw,
                filename="",
                mode="wb",
                mtime=0,
                compresslevel=1,
            ) as outgoing:
                for line in incoming:
                    original = json.loads(line)
                    row = (
                        joined_record(original["hr"], original["meta"])
                        if "hr" in original
                        else original
                    )
                    if (
                        row["workout_id"] != original["workout_id"]
                        or row["user_id"] != original["user_id"]
                    ):
                        raise ValueError("Source identifier aliases differ")
                    if row["workout_id"] in seen:
                        raise ValueError("Duplicate workout ID")
                    seen.add(row["workout_id"])
                    outgoing.write(encoded(summarize_workout(row)))
                    if len(seen) % 10_000 == 0:
                        print(f"Flattened {len(seen):,} workouts", flush=True)
    return len(seen)


def main() -> None:
    """Flatten an existing joined archive from the command line.

    Input: Source/output paths and optional --replace flag.
    Output: Flattened archive and updated build manifest.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    flatten_archive(args.source, args.output, replace=args.replace)
    print("Flattening succeeded")


if __name__ == "__main__":
    main()
