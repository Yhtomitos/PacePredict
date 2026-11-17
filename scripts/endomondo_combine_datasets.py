"""Combine outdoor workouts into a reproducible gzip JSON Lines dataset.

Input: HR and Meta gzip archives containing Python dictionary records.
Output: Combined schema rows, a build report, and local weather lookup records.
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
from collections import Counter
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "training-data/model-training/processed"
MAX_RECORD_BYTES = 8 * 1024 * 1024
FIELDS = (
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
)


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


def route_points(record: dict[str, Any]) -> list[tuple[float, ...]]:
    """Validate aligned numeric route arrays.

    Input: HR dictionary containing coordinate and sensor arrays.
    Output: Validated latitude, longitude, altitude, and timestamp tuples.
    """
    keys = ("latitude", "longitude", "altitude", "timestamp")
    arrays: list[list[Any]] = []
    for key in keys:
        values = record.get(key)
        if not isinstance(values, list):
            raise ValueError("missing_route_arrays")
        arrays.append(values)
    if len({len(values) for values in arrays}) != 1 or len(arrays[0]) < 2:
        raise ValueError("misaligned_or_short_route")
    points: list[tuple[float, ...]] = []
    for values in zip(*arrays, strict=True):
        converted = [numeric(value) for value in values]
        if any(value is None for value in converted):
            raise ValueError("invalid_route_number")
        point = tuple(value for value in converted if value is not None)
        lat, lon, _, timestamp = point
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("invalid_coordinate")
        if points and timestamp < points[-1][3]:
            raise ValueError("nonmonotonic_timestamps")
        points.append(point)
    return points


def route_features(record: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Compute distance-weighted elevation and weather lookup metadata.

    Input: HR record with aligned coordinates, altitude, and Unix timestamps.
    Output: Mean reported elevation in metres and start-location metadata.
    """
    points = route_points(record)
    distance = weighted = 0.0
    for previous, current in zip(points, points[1:], strict=False):
        lat1, lon1 = map(math.radians, previous[:2])
        lat2, lon2 = map(math.radians, current[:2])
        haversine = (
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1)
            * math.cos(lat2)
            * math.sin((lon2 - lon1) / 2) ** 2
        )
        segment = (
            2
            * 6_371_008.8
            * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))
        )
        distance += segment
        weighted += segment * (previous[2] + current[2]) / 2
    if distance <= 0:
        raise ValueError("zero_length_route")
    try:
        started = datetime.fromtimestamp(points[0][3], timezone.utc)
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError("invalid_start_time") from error
    return weighted / distance, {
        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
        "latitude": points[0][0],
        "longitude": points[0][1],
        "route_distance_km": distance / 1000,
        "route_elapsed_seconds": points[-1][3] - points[0][3],
    }


def combine_record(
    hr: dict[str, Any],
    meta: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Construct one schema row while preserving missing feature values.

    Input: HR and Meta dictionaries for the same workout.
    Output: Schema row, weather metadata, and quality flags.
    """
    workout_id, user_id = (
        identifier(hr.get("id")),
        identifier(hr.get("userId")),
    )
    if workout_id != identifier(meta.get("id")):
        raise ValueError("workout_id_mismatch")
    if user_id != identifier(meta.get("userId")):
        raise ValueError("user_id_mismatch")
    if hr.get("sport") != "run" or meta.get("sport") != "run":
        raise ValueError("not_outdoor_running")
    distance, duration = (
        numeric(meta.get("distance")),
        numeric(meta.get("duration")),
    )
    if distance is None or duration is None or distance <= 0 or duration <= 0:
        raise ValueError("invalid_distance_or_duration")
    pace = duration / 60 / distance
    if not math.isfinite(pace):
        raise ValueError("invalid_pace")
    flags = ["weather_pending", "elevation_not_externally_verified"]
    lookup: dict[str, Any] = {"workout_id": workout_id}
    lookup["source_duration_seconds"] = duration
    mean = None
    try:
        mean, route = route_features(hr)
        lookup.update(route)
    except ValueError as error:
        flags.append(str(error))
    gain = numeric(meta.get("ascent"), 0)
    loss = numeric(meta.get("descent"), 0)
    if gain is None:
        flags.append("missing_or_invalid_ascent")
    if loss is None:
        flags.append("missing_or_invalid_descent")
    row: dict[str, Any] = dict(
        zip(
            FIELDS,
            (
                workout_id,
                user_id,
                lookup.get("started_at_utc"),
                distance,
                None,
                None,
                gain,
                loss,
                mean,
                pace,
            ),
            strict=True,
        )
    )
    return row, lookup, flags


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


def index_metadata(connection: sqlite3.Connection, source: Path) -> int:
    """Index compact metadata using a unique workout key.

    Input: Temporary database connection and Meta archive.
    Output: Indexed record count; duplicate IDs fail the build.
    """
    connection.execute("CREATE TABLE meta (id INTEGER PRIMARY KEY, data TEXT)")
    keys = (
        "id",
        "userId",
        "sport",
        "distance",
        "duration",
        "ascent",
        "descent",
    )
    count = 0
    for record in records(source):
        workout_id = identifier(record.get("id"))
        compact = {key: record.get(key) for key in keys}
        connection.execute(
            "INSERT INTO meta VALUES (?, ?)",
            (workout_id, json.dumps(compact)),
        )
        count += 1
        if count % 10_000 == 0:
            print(f"Indexed {count:,} Meta records", flush=True)
    connection.execute("CREATE TABLE rows (id INTEGER PRIMARY KEY, data BLOB)")
    connection.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY)")
    return count


def collect_rows(
    connection: sqlite3.Connection,
    hr_path: Path,
    audit: gzip.GzipFile,
) -> dict[str, Any]:
    """Join source workouts and record every rejection or quality flag.

    Input: Indexed metadata, HR archive, and provenance stream.
    Output: Reconciled counts and quality-flag totals.
    """
    rejected: Counter[str] = Counter()
    flags_count: Counter[str] = Counter()
    scanned = accepted = 0
    for hr in records(hr_path):
        scanned += 1
        workout_id = identifier(hr.get("id"))
        connection.execute("INSERT INTO seen VALUES (?)", (workout_id,))
        match = connection.execute(
            "SELECT data FROM meta WHERE id = ?",
            (workout_id,),
        ).fetchone()
        try:
            if match is None:
                raise ValueError("missing_metadata")
            row, lookup, flags = combine_record(hr, json.loads(match[0]))
        except ValueError as error:
            rejected[str(error)] += 1
            audit.write(
                encoded({"workout_id": workout_id, "rejected": str(error)})
            )
        else:
            connection.execute(
                "INSERT INTO rows VALUES (?, ?)",
                (workout_id, encoded(row)),
            )
            audit.write(encoded({**lookup, "quality_flags": flags}))
            flags_count.update(flags)
            accepted += 1
        if scanned % 5_000 == 0:
            print(f"Joined {scanned:,} HR records", flush=True)
    return {
        "hr_records": scanned,
        "output_records": accepted,
        "rejected": dict(rejected),
        "quality_flags": dict(flags_count),
    }


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


def build_dataset(
    hr_path: Path, meta_path: Path, output: Path
) -> dict[str, Any]:
    """Build a combined archive without modifying source files.

    Input: HR archive, Meta archive, and a new output archive path.
    Output: Published dataset and build report; existing outputs are protected.
    """
    report_path = output.with_suffix(".build.json")
    audit_path = output.with_suffix(".provenance.jsonl.gz")
    destinations = (output, report_path, audit_path)
    sources = {hr_path.resolve(), meta_path.resolve()}
    if any(path.resolve() in sources for path in destinations):
        raise ValueError("Output must differ from input archives")
    if any(path.exists() for path in destinations):
        raise ValueError("Output or companion files already exist")
    if output.resolve().is_relative_to((PROCESSED.parent / "raw").resolve()):
        raise ValueError("Output cannot be inside raw sources")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=".combine-"
    ) as tmp:
        staging = Path(tmp)
        with closing(sqlite3.connect(staging / "index.sqlite")) as connection:
            meta_count = index_metadata(connection, meta_path)
            with gzip_output(staging / "provenance.gz") as audit:
                counts = collect_rows(connection, hr_path, audit)
            with gzip_output(staging / "dataset.gz") as stream:
                for (data,) in connection.execute(
                    "SELECT data FROM rows ORDER BY id"
                ):
                    stream.write(data)
        report = {
            "schema_version": 1,
            "status": "weather_pending",
            "fields": FIELDS,
            "meta_records": meta_count,
            **counts,
            "source_sha256": {
                "hr": sha256(hr_path),
                "meta": sha256(meta_path),
            },
            "output_sha256": sha256(staging / "dataset.gz"),
            "pace_definition": (
                "Meta duration seconds / 60 / distance kilometres; "
                "pause semantics unverified"
            ),
            "elevation_definition": (
                "Meta ascent/descent metres; distance-weighted "
                "HR altitude, uncorrected"
            ),
            "weather_definition": (
                "null pending separate enrichment; not training-ready"
            ),
        }
        (staging / "report.json").write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )
        publish_file(staging / "provenance.gz", audit_path)
        publish_file(staging / "dataset.gz", output)
        publish_file(staging / "report.json", report_path)
    return report


def main() -> None:
    """Run the dataset builder.

    Input: Optional source and output CLI paths.
    Output: Combined archive and a JSON build report on stdout.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hr", type=Path, default=PROCESSED / "endomondoHR.json.gz"
    )
    parser.add_argument(
        "--meta", type=Path, default=PROCESSED / "endomondoMeta.json.gz"
    )
    parser.add_argument(
        "--output", type=Path, default=PROCESSED / "final/running.json.gz"
    )
    args = parser.parse_args()
    print(json.dumps(build_dataset(args.hr, args.meta, args.output), indent=2))


if __name__ == "__main__":
    main()
