"""Join complete HR and Meta records by workout ID without feature filtering.

Input: Outdoor-running HR and Meta gzip source archives.
Output: One gzip JSON Lines file retaining both records for every match.
"""

import argparse
import gzip
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from .endomondo_combine_datasets import (
    PROCESSED,
    encoded,
    identifier,
    publish_file,
    records,
    sha256,
)


def joined_record(hr: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Preserve complete matching source dictionaries without field collisions.

    Input: HR and Meta records for an outdoor workout.
    Output: Identifiers and original source dictionaries; mismatches raise.
    """
    workout_id = identifier(hr.get("id"))
    user_id = identifier(hr.get("userId"))
    if workout_id != identifier(meta.get("id")):
        raise ValueError("Workout IDs differ")
    if user_id != identifier(meta.get("userId")):
        raise ValueError("User IDs differ")
    if hr.get("sport") != "run" or meta.get("sport") != "run":
        raise ValueError("Expected outdoor running records")
    return {
        "workout_id": workout_id,
        "user_id": user_id,
        "hr": hr,
        "meta": meta,
    }


def index_hr(connection: sqlite3.Connection, path: Path) -> int:
    """Index complete HR records in temporary disk storage.

    Input: SQLite connection and HR archive path.
    Output: Indexed count; duplicate workout IDs fail explicitly.
    """
    connection.execute(
        "CREATE TABLE hr (id INTEGER PRIMARY KEY, data BLOB, matched INTEGER)"
    )
    count = 0
    for record in records(path):
        workout_id = identifier(record.get("id"))
        connection.execute(
            "INSERT INTO hr VALUES (?, ?, 0)",
            (workout_id, encoded(record)),
        )
        count += 1
        if count % 5_000 == 0:
            print(f"Indexed {count:,} complete HR records", flush=True)
    connection.commit()
    connection.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY)")
    return count


def write_matches(
    connection: sqlite3.Connection,
    meta_path: Path,
    output: Path,
) -> dict[str, int]:
    """Stream Meta records and write complete matched workout objects.

    Input: Indexed HR records, Meta archive, and temporary gzip path.
    Output: Counts including unmatched records; mismatches fail publication.
    """
    meta_count = matches = 0
    with output.open("xb") as raw:
        with gzip.GzipFile(
            fileobj=raw,
            filename="",
            mode="wb",
            mtime=0,
            compresslevel=1,
        ) as stream:
            for meta in records(meta_path):
                meta_count += 1
                workout_id = identifier(meta.get("id"))
                connection.execute(
                    "INSERT INTO seen VALUES (?)", (workout_id,)
                )
                match = connection.execute(
                    "SELECT data FROM hr WHERE id = ?",
                    (workout_id,),
                ).fetchone()
                if match is not None:
                    row = joined_record(json.loads(match[0]), meta)
                    stream.write(encoded(row))
                    connection.execute(
                        "UPDATE hr SET matched = 1 WHERE id = ?",
                        (workout_id,),
                    )
                    matches += 1
                if meta_count % 10_000 == 0:
                    print(
                        f"Scanned {meta_count:,} Meta records; "
                        f"joined {matches:,} workouts",
                        flush=True,
                    )
    unmatched = connection.execute(
        "SELECT count(*) FROM hr WHERE matched = 0"
    ).fetchone()[0]
    return {
        "meta_records": meta_count,
        "matched_workouts": matches,
        "unmatched_hr": unmatched,
        "unmatched_meta": meta_count - matches,
    }


def join_workouts(
    hr_path: Path,
    meta_path: Path,
    output: Path,
) -> dict[str, Any]:
    """Build a complete matched archive while preserving existing data.

    Input: Source archives and a new output filename outside raw storage.
    Output: Joined archive plus an adjacent reproducibility manifest.
    """
    report_path = output.with_suffix(".build.json")
    sources = {hr_path.resolve(), meta_path.resolve()}
    for destination in (output, report_path):
        if destination.resolve() in sources or destination.exists():
            raise ValueError("Destination must be new and differ from sources")
        if destination.resolve().is_relative_to(
            (PROCESSED.parent / "raw").resolve()
        ):
            raise ValueError("Cannot publish inside raw source storage")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".combine-",
        dir=output.parent,
    ) as directory:
        staging = Path(directory)
        with closing(sqlite3.connect(staging / "join.sqlite")) as connection:
            hr_count = index_hr(connection, hr_path)
            counts = write_matches(connection, meta_path, staging / "data.gz")
        report = {
            "schema_version": 1,
            "status": "joined_unfiltered",
            "fields": ["workout_id", "user_id", "hr", "meta"],
            "hr_records": hr_count,
            **counts,
            "source_sha256": {
                "hr": sha256(hr_path),
                "meta": sha256(meta_path),
            },
            "output_sha256": sha256(staging / "data.gz"),
            "record_order": (
                "Meta source order; join by workout_id, not line number"
            ),
            "weather": (
                "Original Meta weather preserved; no external enrichment"
            ),
            "cleaning": (
                "No pace, duration, elevation, or heart-rate filtering"
            ),
        }
        (staging / "report.json").write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )
        publish_file(staging / "data.gz", output)
        publish_file(staging / "report.json", report_path)
    return report


def main() -> None:
    """Build the joined archive from command-line source paths.

    Input: Optional HR, Meta, and output arguments.
    Output: Joined gzip dataset and JSON build counts.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hr", type=Path, default=PROCESSED / "endomondoHR.json.gz"
    )
    parser.add_argument(
        "--meta", type=Path, default=PROCESSED / "endomondoMeta.json.gz"
    )
    parser.add_argument(
        "--output", type=Path, default=PROCESSED / "final/workouts.json.gz"
    )
    args = parser.parse_args()
    print(json.dumps(join_workouts(args.hr, args.meta, args.output), indent=2))


if __name__ == "__main__":
    main()
