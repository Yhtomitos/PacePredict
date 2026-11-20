"""Validate schema migration, preservation, and failed-publication safety."""

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from scripts.endomondo_combine_datasets import sha256
from scripts.endomondo_flatten_workouts import (
    FIELDS,
    average_heart_rate,
    flatten_archive,
    summarize_workout,
)
from scripts.endomondo_join_workouts import joined_record
from tests.test_combine_datasets import sources


class FlattenTests(unittest.TestCase):
    """Check migration of an already-joined archive without original inputs."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "joined.json.gz"
        hr, meta = sources()
        hr["heart_rate"] = [120, 130, 140]
        meta.update({"timestamp": ["UTC"], "calories": 100, "weather": {}})
        self.row = {"workout_id": 1, "user_id": 2, "hr": hr, "meta": meta}
        with gzip.open(self.source, "wt", encoding="utf-8") as stream:
            stream.write(json.dumps(self.row) + "\n")
        self.manifest = self.source.with_suffix(".build.json")
        self.manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "output_sha256": sha256(self.source),
                    "matched_workouts": 1,
                    "hr_records": 1,
                    "meta_records": 1,
                    "unmatched_hr": 0,
                    "unmatched_meta": 0,
                }
            )
        )

    def test_in_place_migration_summarizes_arrays_and_preserves_count(self):
        report = flatten_archive(self.source, self.source, replace=True)
        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(report["matched_workouts"], 1)
        self.assertEqual(report["output_sha256"], sha256(self.source))
        with gzip.open(self.source, "rt", encoding="utf-8") as stream:
            row = json.loads(next(stream))
            self.assertEqual(list(stream), [])
        self.assertEqual(set(row), set(FIELDS))
        self.assertAlmostEqual(row["average_heart_rate_bpm"], 131.6666667)
        self.assertAlmostEqual(row["distance_miles"], 5 / 1.609344)
        self.assertAlmostEqual(row["pace_min_per_mile"], 6 * 1.609344)
        self.assertIsNone(row["temperature_c"])
        self.assertIsNone(row["relative_humidity_pct"])
        self.assertEqual(row["latitude"], 0)
        self.assertEqual(row["started_at_utc"], "2014-05-13T16:53:20Z")
        self.assertFalse(list(self.source.parent.glob(".combine-*")))

    def test_replacement_requires_explicit_flag(self):
        before = self.source.read_bytes()
        with self.assertRaises(ValueError):
            flatten_archive(self.source, self.source)
        self.assertEqual(self.source.read_bytes(), before)

    def test_bad_count_preserves_existing_data_and_manifest(self):
        report = json.loads(self.manifest.read_text())
        report["matched_workouts"] = 2
        self.manifest.write_text(json.dumps(report))
        before = self.source.read_bytes(), self.manifest.read_bytes()
        with self.assertRaises(ValueError):
            flatten_archive(self.source, self.source, replace=True)
        self.assertEqual(
            before, (self.source.read_bytes(), self.manifest.read_bytes())
        )

    def test_flat_source_and_deterministic_output(self):
        flat = joined_record(self.row["hr"], self.row["meta"])
        with gzip.open(self.source, "wt", encoding="utf-8") as stream:
            stream.write(json.dumps(flat) + "\n")
        report = json.loads(self.manifest.read_text())
        report.update(schema_version=2, output_sha256=sha256(self.source))
        self.manifest.write_text(json.dumps(report))
        before = self.source.read_bytes()
        first = self.source.parent / "first.gz"
        second = self.source.parent / "second.gz"
        flatten_archive(self.source, first)
        flatten_archive(self.source, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(before, self.source.read_bytes())

    def test_invalid_measurements_remain_null(self):
        row = joined_record(self.row["hr"], self.row["meta"])
        row.update(distance=0, ascent=-1, latitude=[91], longitude=[0])
        result = summarize_workout(row)
        for key in (
            "distance_miles",
            "pace_min_per_mile",
            "elevation_gain_m",
            "latitude",
            "longitude",
        ):
            self.assertIsNone(result[key])
        self.assertEqual(result["elevation_loss_m"], 0)

    def test_invalid_hr_arrays_do_not_fabricate_average(self):
        for rates, times in (
            ([120], [1]),
            ([120, 130], [1]),
            ([120, 130], [2, 1]),
            ([120, 130], [1, 1]),
            ([120, None], [1, 2]),
            ([120, True], [1, 2]),
            ([0, 130], [1, 2]),
            ([120, float("nan")], [1, 2]),
        ):
            with self.subTest(rates=rates, times=times):
                self.assertIsNone(
                    average_heart_rate(
                        {"heart_rate": rates, "hr_timestamp": times}
                    )
                )
