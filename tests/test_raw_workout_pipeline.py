"""Exercise raw filtering, joining, and summary generation as one pipeline."""

import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.endomondo_process_dataset import FIELDS, build_from_raw, sha256
from tests.workout_fixtures import sources


class RawPipelineTests(unittest.TestCase):
    """Validate the integrated pipeline without accessing real datasets."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.hr = self.root / "hr.gzip"
        self.meta = self.root / "meta.gzip"
        self.output = self.root / "summary.json.gz"
        self.hr_row, self.meta_row = sources()
        self.hr_row["heart_rate"] = [120, 130, 140]
        self.write(self.hr, [self.hr_row])
        self.write(self.meta, [self.meta_row])

    @staticmethod
    def write(path, rows):
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            for row in rows:
                stream.write(repr(row) + "\n")

    def test_filter_join_units_counts_and_determinism(self):
        self.write(
            self.hr,
            [
                self.hr_row,
                {**self.hr_row, "id": 2},
                {**self.hr_row, "id": 3, "sport": "treadmill running"},
            ],
        )
        self.write(
            self.meta,
            [
                {**self.meta_row, "id": 4},
                self.meta_row,
                {**self.meta_row, "id": 3, "sport": "treadmill running"},
                {**self.meta_row, "id": 2, "sport": "bike"},
            ],
        )
        before = self.hr.read_bytes(), self.meta.read_bytes()
        report = build_from_raw(self.hr, self.meta, self.output)
        self.assertEqual(report["raw_hr_records"], 3)
        self.assertEqual(report["raw_meta_records"], 4)
        self.assertEqual(report["hr_records"], 2)
        self.assertEqual(report["meta_records"], 2)
        self.assertEqual(report["matched_workouts"], 1)
        self.assertEqual(report["unmatched_hr"], 1)
        self.assertEqual(report["unmatched_meta"], 1)
        with gzip.open(self.output, "rt", encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream]
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), set(FIELDS))
        self.assertEqual(rows[0]["workout_id"], 1)
        self.assertAlmostEqual(rows[0]["pace_min_per_mile"], 9.656064)
        self.assertAlmostEqual(rows[0]["average_heart_rate_bpm"], 131.6666667)
        self.assertIsNone(rows[0]["temperature_c"])
        self.assertEqual(report["output_sha256"], sha256(self.output))
        second = self.root / "second.gz"
        build_from_raw(self.hr, self.meta, second)
        self.assertEqual(self.output.read_bytes(), second.read_bytes())
        self.assertEqual(
            before, (self.hr.read_bytes(), self.meta.read_bytes())
        )
        self.assertFalse(list(self.root.glob(".combine-*")))

    def test_bad_matches_and_duplicate_ids_leave_no_outputs(self):
        for target, rows, error in (
            (self.meta, [{**self.meta_row, "userId": 9}], ValueError),
            (self.hr, [self.hr_row, self.hr_row], sqlite3.IntegrityError),
            (
                self.meta,
                [self.meta_row, self.meta_row],
                sqlite3.IntegrityError,
            ),
            (self.meta, [{**self.meta_row, "id": 9}], ValueError),
        ):
            with self.subTest(target=target, error=error):
                self.write(self.hr, [self.hr_row])
                self.write(self.meta, [self.meta_row])
                self.write(target, rows)
                with self.assertRaises(error):
                    build_from_raw(self.hr, self.meta, self.output)
                self.assertFalse(self.output.exists())
                self.assertFalse(
                    self.output.with_suffix(".build.json").exists()
                )
                self.assertFalse(list(self.root.glob(".combine-*")))

    def test_existing_outputs_and_sources_protected(self):
        before = self.hr.read_bytes()
        with self.assertRaises(ValueError):
            build_from_raw(self.hr, self.meta, self.hr)
        self.assertEqual(before, self.hr.read_bytes())
        self.output.write_bytes(b"existing")
        with self.assertRaises(ValueError):
            build_from_raw(self.hr, self.meta, self.output)
        self.assertEqual(self.output.read_bytes(), b"existing")

    def test_truncated_source_fails_without_publication(self):
        self.hr.write_bytes(self.hr.read_bytes()[:-8])
        with self.assertRaises(EOFError):
            build_from_raw(self.hr, self.meta, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".combine-*")))
