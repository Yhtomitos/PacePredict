"""Exercise complete source preservation and safe workout-ID joins."""

import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.endomondo_join_workouts import join_workouts, joined_record
from tests.test_combine_datasets import sources
from tests.validate_joined_workouts import validate_joined, validate_sources


class JoinWorkoutsTests(unittest.TestCase):
    """Validate complete joins without applying training-feature filters."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.hr_path, self.meta_path = (
            self.root / "hr.gz",
            self.root / "meta.gz",
        )
        self.output = self.root / "workouts.json.gz"

    @staticmethod
    def write(path, values):
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            for value in values:
                stream.write(repr(value) + "\n")

    def test_full_join_preserves_fields_and_unusable_targets(self):
        hr, meta = sources()
        hr["heart_rate"] = [120, 130, 140]
        hr["speed"] = [0, 10, 11]
        meta.update({"weather": {"type": 3}, "duration": 0, "extra": None})
        self.write(self.hr_path, [hr, {**hr, "id": 2}])
        self.write(self.meta_path, [{**meta, "id": 3}, meta])
        original = self.hr_path.read_bytes(), self.meta_path.read_bytes()
        report = join_workouts(self.hr_path, self.meta_path, self.output)
        with gzip.open(self.output, "rt", encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream]
        self.assertEqual(
            rows,
            [joined_record(hr, meta)],
        )
        self.assertEqual(report["matched_workouts"], 1)
        self.assertEqual(
            validate_joined(self.output),
            {
                "workouts": 1,
                "heart_rate_records": 1,
            },
        )
        self.assertEqual(report["unmatched_hr"], 1)
        self.assertEqual(report["unmatched_meta"], 1)
        self.assertEqual(
            original,
            (
                self.hr_path.read_bytes(),
                self.meta_path.read_bytes(),
            ),
        )
        self.assertFalse(list(self.root.glob(".combine-*")))
        other = self.root / "second.json.gz"
        join_workouts(self.hr_path, self.meta_path, other)
        self.assertEqual(self.output.read_bytes(), other.read_bytes())

    def test_mismatched_users_ids_or_sports_fail(self):
        for key, value in (("id", 9), ("userId", 9), ("sport", "bike")):
            with self.subTest(key=key):
                hr, meta = sources()
                meta[key] = value
                with self.assertRaises(ValueError):
                    joined_record(hr, meta)

    def test_validator_rejects_identity_mismatch(self):
        hr, meta = sources()
        row = joined_record(hr, meta)
        row["userId"] = 9
        with self.assertRaisesRegex(ValueError, "user ID"):
            validate_sources(row)

    def test_flat_fields_meta_precedence_and_distinct_timestamps(self):
        hr, meta = sources()
        hr.update({"gender": "hr value", "calories": 10, "weather": {}})
        meta.update(
            {
                "gender": "meta value",
                "timestamp": ["UTC time"],
                "hydration": 3,
                "calories": 20,
                "weather": {"type": 3},
            }
        )
        before = json.dumps([hr, meta], sort_keys=True)
        row = joined_record(hr, meta)
        self.assertEqual(row["gender"], "meta value")
        self.assertEqual(row["hr_timestamp"], hr["timestamp"])
        self.assertEqual(row["workout_timestamp"], ["UTC time"])
        self.assertEqual(row["altitude"], hr["altitude"])
        for key in (
            "hr",
            "meta",
            "timestamp",
            "calories",
            "hydration",
            "weather",
        ):
            self.assertNotIn(key, row)
        self.assertEqual(before, json.dumps([hr, meta], sort_keys=True))

    def test_duplicates_and_mismatches_do_not_publish(self):
        hr, meta = sources()
        variants = (
            ([hr, hr], [meta], sqlite3.IntegrityError),
            ([hr], [meta, meta], sqlite3.IntegrityError),
            ([hr], [{**meta, "userId": 9}], ValueError),
        )
        for hr_rows, meta_rows, error in variants:
            with self.subTest(error=error):
                self.write(self.hr_path, hr_rows)
                self.write(self.meta_path, meta_rows)
                with self.assertRaises(error):
                    join_workouts(self.hr_path, self.meta_path, self.output)
                self.assertFalse(self.output.exists())

    def test_existing_outputs_and_source_files_are_protected(self):
        hr, meta = sources()
        self.write(self.hr_path, [hr])
        self.write(self.meta_path, [meta])
        with self.assertRaises(ValueError):
            join_workouts(self.hr_path, self.meta_path, self.hr_path)
        self.output.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            join_workouts(self.hr_path, self.meta_path, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
