"""Filter, join, and summarize raw Endomondo archives in one command.

Input: Raw HR/Meta gzip archives, or a joined archive for legacy migration.
Output: Schema-version-3 summary archive; weather awaits enrichment.
"""

import argparse
import ast
import gzip
import hashlib
import json
import math
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "training-data/model-training/processed"
MAX_RECORD_BYTES = 8 * 1024 * 1024
REMOVED_FIELDS = {"calories", "hydration", "weather"}


def records(path: Path) -> Iterator[dict[str, Any]]:
    """Read bounded source records, rejecting malformed input.

    Input: Path to a gzip archive.
    Output: Iterator of dictionaries; malformed input raises ValueError.
    """
    with gzip.open(path, "rb") as stream:
        number = 0
        while line := stream.readline(MAX_RECORD_BYTES + 1):
            number += 1
            if len(line) > MAX_RECORD_BYTES:
                raise ValueError(f"{path.name}:{number}: record too large")
            try:
                value = ast.literal_eval(line.decode("utf-8"))
            except (SyntaxError, ValueError, UnicodeError) as error:
                raise ValueError(
                    f"{path.name}:{number}: invalid dictionary"
                ) from error
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}:{number}: expected dictionary")
            yield value


def identifier(value: Any) -> int:
    """Validate a source identifier.

    Input: Source identifier value.
    Output: Nonnegative integer, or ValueError.
    """
    if type(value) is not int or value < 0:
        raise ValueError("invalid_identifier")
    return value


def numeric(value: Any, minimum: float | None = None) -> float | None:
    """Normalize optional numeric values without accepting booleans.

    Input: Source value and optional inclusive minimum.
    Output: Finite float or None.
    """
    if type(value) not in (int, float):
        return None
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        return None
    return result


@contextmanager
def gzip_output(path: Path) -> Iterator[gzip.GzipFile]:
    """Write deterministic gzip bytes.

    Input: New staging path.
    Output: Writable binary gzip stream.
    """
    with path.open("xb") as raw:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw,
            mtime=0,
            compresslevel=6,
        ) as stream:
            yield stream


def encoded(value: Any) -> bytes:
    """Serialize strict JSON Lines.

    Input: JSON-compatible value.
    Output: UTF-8 line without NaN or infinity.
    """
    return (
        json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n"
    ).encode()


def sha256(path: Path) -> str:
    """Hash a file without reading it all at once.

    Input: File path.
    Output: SHA-256 hexadecimal digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def publish_file(source: Path, destination: Path) -> None:
    """Publish bytes using permissions inherited from the destination folder.

    Input: Private staging file and final destination path.
    Output: Atomically replaced destination; temporary sibling is cleaned up.
    """
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        # Moving from a private Windows temp directory retains its private ACL.
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def joined_record(hr: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Flatten matching records, giving Meta precedence except for timestamps.

    Input: HR and Meta records for an outdoor workout.
    Output: Flat object with renamed timestamps and unwanted fields removed.
    """
    workout_id = identifier(hr.get("id"))
    user_id = identifier(hr.get("userId"))
    if workout_id != identifier(meta.get("id")):
        raise ValueError("Workout IDs differ")
    if user_id != identifier(meta.get("userId")):
        raise ValueError("User IDs differ")
    if hr.get("sport") != "run" or meta.get("sport") != "run":
        raise ValueError("Expected outdoor running records")
    result = {**hr, **meta}
    for key in REMOVED_FIELDS | {"timestamp"}:
        result.pop(key, None)
    result.update(
        {
            "workout_id": workout_id,
            "user_id": user_id,
            "hr_timestamp": hr.get("timestamp"),
            "workout_timestamp": meta.get("timestamp"),
        }
    )
    return result


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


def index_raw_hr(
    connection: sqlite3.Connection, source: Path
) -> dict[str, int]:
    """Filter raw HR records into a temporary disk-backed join index.

    Input: SQLite connection and original gzip dictionary-lines archive.
    Output: Raw and selected counts; duplicate outdoor IDs fail the build.
    """
    connection.execute(
        "CREATE TABLE hr (id INTEGER PRIMARY KEY, data BLOB, matched INTEGER)"
    )
    scanned = selected = 0
    for row in records(source):
        scanned += 1
        if row.get("sport") == "run":
            connection.execute(
                "INSERT INTO hr VALUES (?, ?, 0)",
                (identifier(row.get("id")), encoded(row)),
            )
            selected += 1
        if scanned % 10_000 == 0:
            print(f"Scanned {scanned:,} raw HR records", flush=True)
    connection.commit()
    connection.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY)")
    return {"raw_hr_records": scanned, "hr_records": selected}


def summarize_matches(
    connection: sqlite3.Connection, source: Path, destination: Path
) -> dict[str, int]:
    """Filter Meta, join by workout ID, and write final summaries directly.

    Input: HR index, raw Meta gzip archive, and new staging output.
    Output: Reconciled counts; mismatched users or duplicate IDs fail.
    """
    scanned = selected = matched = 0
    with gzip_output(destination) as stream:
        for meta in records(source):
            scanned += 1
            if meta.get("sport") != "run":
                continue
            selected += 1
            workout_id = identifier(meta.get("id"))
            connection.execute("INSERT INTO seen VALUES (?)", (workout_id,))
            match = connection.execute(
                "SELECT data FROM hr WHERE id = ?", (workout_id,)
            ).fetchone()
            if match is not None:
                row = joined_record(json.loads(match[0]), meta)
                stream.write(encoded(summarize_workout(row)))
                connection.execute(
                    "UPDATE hr SET matched = 1 WHERE id = ?", (workout_id,)
                )
                matched += 1
            if selected % 10_000 == 0:
                print(f"Scanned {selected:,} outdoor Meta runs", flush=True)
    unmatched = connection.execute(
        "SELECT count(*) FROM hr WHERE matched = 0"
    ).fetchone()[0]
    return {
        "raw_meta_records": scanned,
        "meta_records": selected,
        "matched_workouts": matched,
        "unmatched_hr": unmatched,
        "unmatched_meta": selected - matched,
    }


def build_from_raw(
    hr_path: Path, meta_path: Path, output: Path
) -> dict[str, Any]:
    """Build the current schema from both original compressed datasets.

    Input: Raw gzip archives and a new output path outside raw storage.
    Output: Summary gzip and manifest; temporary index is always cleaned up.
    """
    report_path = output.with_suffix(".build.json")
    sources = {hr_path.resolve(), meta_path.resolve()}
    if len(sources) != 2:
        raise ValueError("HR and Meta sources must differ")
    for path in (output, report_path):
        if path.resolve() in sources or path.exists():
            raise ValueError("Destination must be new and differ from sources")
        if path.resolve().is_relative_to((PROCESSED.parent / "raw").resolve()):
            raise ValueError("Cannot publish inside raw source storage")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=".combine-"
    ) as directory:
        staging = Path(directory)
        with closing(sqlite3.connect(staging / "index.sqlite")) as connection:
            counts = index_raw_hr(connection, hr_path)
            counts.update(
                summarize_matches(connection, meta_path, staging / "data.gz")
            )
        if not counts["matched_workouts"]:
            raise ValueError("No matching outdoor running workouts")
        report = {
            "schema_version": 3,
            "status": "weather_pending",
            "required_fields": FIELDS,
            **counts,
            "source_sha256": {
                "hr": sha256(hr_path),
                "meta": sha256(meta_path),
            },
            "output_sha256": sha256(staging / "data.gz"),
            "record_order": "Outdoor Meta source order; inner join by ID",
            "filter": "sport == run in both sources; treadmill excluded",
            "overlap_precedence": "Meta overwrites HR except timestamps",
            "pace_definition": (
                "Meta seconds / 60 / (Meta kilometres / 1.609344)"
            ),
            "heart_rate_definition": "Trapezoidal time-weighted measured BPM",
            "coordinate_definition": "First valid paired coordinates",
            "missing_values": "Null; no imputation or measurement filtering",
            "weather_definition": "Null pending separate enrichment",
        }
        (staging / "report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        publish_file(staging / "data.gz", output)
        publish_file(staging / "report.json", report_path)
    return report


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
    """Build from raw sources or migrate a joined archive from the CLI.

    Input: Optional --hr/--meta/--output or legacy joined source argument.
    Output: Summary gzip archive and reproducibility manifest.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        nargs="?",
        help="Optional existing joined archive to migrate",
    )
    raw = PROCESSED.parent / "raw"
    parser.add_argument("--hr", type=Path)
    parser.add_argument("--meta", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROCESSED / "final/planned-workouts.json.gz",
    )
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.source is not None:
        if args.hr or args.meta:
            parser.error("Use either a joined source or --hr/--meta")
        flatten_archive(args.source, args.output, replace=args.replace)
    else:
        if args.replace:
            parser.error(
                "Raw builds require a new output; --replace is migration-only"
            )
        build_from_raw(
            args.hr or raw / "endomondoHR.json.gzip",
            args.meta or raw / "endomondoMeta.json.gzip",
            args.output,
        )
    print("Flattening succeeded")


if __name__ == "__main__":
    main()
