"""Validate feature calculations and a complete fixture dataset build."""

import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.endomondo_combine_datasets import (
    FIELDS,
    build_dataset,
    combine_record,
    publish_file,
    records,
    route_features,
)
from tests.validate_combined_dataset import validate_combined, validate_row


def sources() -> tuple[dict, dict]:
    """Create known route and metadata fixtures.

    Input: None.
    Output: Matching HR and Meta dictionaries.
    """
    hr = {
        "id": 1,
        "userId": 2,
        "sport": "run",
        "latitude": [0, 0, 0],
        "longitude": [0, 0.01, 0.03],
        "altitude": [0, 10, 30],
        "timestamp": [1_400_000_000, 1_400_000_010, 1_400_000_030],
    }
    meta = {
        "id": 1,
        "userId": 2,
        "sport": "run",
        "distance": 5,
        "duration": 1800,
        "ascent": 30,
        "descent": 0,
    }
    return hr, meta


class FeatureTests(unittest.TestCase):
    """Exercise route calculations and schema decisions."""

    def test_known_features(self):
        row, lookup, flags = combine_record(*sources())
        self.assertEqual(set(row), set(FIELDS))
        self.assertAlmostEqual(row["mean_elevation_m"], 15)
        self.assertEqual(row["pace_min_per_km"], 6)
        self.assertEqual(row["elevation_loss_m"], 0)
        self.assertIsNone(row["temperature_c"])
        self.assertIsNone(row["relative_humidity_pct"])
        self.assertEqual(lookup["source_duration_seconds"], 1800)
        self.assertIn("weather_pending", flags)

    def test_below_sea_level_and_stationary_segment(self):
        hr, _ = sources()
        hr["altitude"] = [-30, -20, -10]
        hr["longitude"] = [0, 0, 0.03]
        mean, _ = route_features(hr)
        self.assertAlmostEqual(mean, -15)

    def test_invalid_routes(self):
        cases = {
            "altitude": [[1], [0, None, 1], [0, float("inf"), 1]],
            "latitude": [[0, 91, 0]],
            "timestamp": [[3, 2, 1]],
            "longitude": [[0, 0, 0]],
        }
        for key, variants in cases.items():
            for value in variants:
                with self.subTest(key=key, value=value):
                    hr, meta = sources()
                    hr[key] = value
                    row, _, flags = combine_record(hr, meta)
                    self.assertIsNone(row["mean_elevation_m"])
                    self.assertGreater(len(flags), 2)

    def test_missing_ascent_is_not_zero(self):
        hr, meta = sources()
        meta["ascent"] = None
        meta["descent"] = -1
        row, _, flags = combine_record(hr, meta)
        self.assertIsNone(row["elevation_gain_m"])
        self.assertIsNone(row["elevation_loss_m"])
        self.assertIn("missing_or_invalid_ascent", flags)

    def test_invalid_pairs_and_targets(self):
        for key, value in (
            ("id", 3),
            ("userId", 7),
            ("sport", "treadmill running"),
            ("distance", 0),
            ("duration", -1),
            ("duration", True),
            ("distance", float("nan")),
        ):
            with self.subTest(key=key, value=value):
                hr, meta = sources()
                meta[key] = value
                with self.assertRaises(ValueError):
                    combine_record(hr, meta)


class BuildTests(unittest.TestCase):
    """Run source-protection and end-to-end fixture checks."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.hr_path = self.root / "hr.gz"
        self.meta_path = self.root / "meta.gz"
        self.output = self.root / "final.json.gz"
        hr, meta = sources()
        self.write(self.hr_path, [hr])
        self.write(self.meta_path, [meta])

    @staticmethod
    def write(path, values):
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            for value in values:
                stream.write(repr(value) + "\n")

    def test_smoke_determinism_and_source_preservation(self):
        originals = [
            path.read_bytes() for path in (self.hr_path, self.meta_path)
        ]
        report = build_dataset(self.hr_path, self.meta_path, self.output)
        other = self.root / "second.json.gz"
        build_dataset(self.hr_path, self.meta_path, other)
        self.assertEqual(self.output.read_bytes(), other.read_bytes())
        self.assertEqual(report["output_records"], 1)
        self.assertEqual(report["hr_records"], 1)
        self.assertEqual(validate_combined(self.output)["records"], 1)
        with gzip.open(self.output, "rt", encoding="utf-8") as stream:
            row = json.loads(next(stream))
            self.assertEqual(row["pace_min_per_km"], 6)
            self.assertEqual(list(stream), [])
        self.assertEqual(
            originals,
            [path.read_bytes() for path in (self.hr_path, self.meta_path)],
        )
        self.assertFalse(list(self.root.glob(".combine-*")))

    def test_missing_match_and_invalid_target_reconcile(self):
        hr, meta = sources()
        second = {**hr, "id": 3}
        meta["duration"] = 0
        self.write(self.hr_path, [hr, second])
        self.write(self.meta_path, [meta])
        report = build_dataset(self.hr_path, self.meta_path, self.output)
        self.assertEqual(report["output_records"], 0)
        self.assertEqual(
            report["rejected"],
            {"invalid_distance_or_duration": 1, "missing_metadata": 1},
        )

    def test_source_order_does_not_change_dataset(self):
        hr, meta = sources()
        other_hr, other_meta = {**hr, "id": 3}, {**meta, "id": 3}
        self.write(self.hr_path, [other_hr, hr])
        self.write(self.meta_path, [other_meta, meta])
        build_dataset(self.hr_path, self.meta_path, self.output)
        self.assertEqual(validate_combined(self.output)["records"], 2)
        self.write(self.hr_path, [hr, other_hr])
        self.write(self.meta_path, [meta, other_meta])
        other = self.root / "ordered.json.gz"
        build_dataset(self.hr_path, self.meta_path, other)
        self.assertEqual(self.output.read_bytes(), other.read_bytes())

    def test_duplicate_ids_fail_without_publishing(self):
        hr, meta = sources()
        for target, value in ((self.hr_path, hr), (self.meta_path, meta)):
            with self.subTest(target=target):
                self.write(target, [value, value])
                with self.assertRaises(sqlite3.IntegrityError):
                    build_dataset(self.hr_path, self.meta_path, self.output)
                self.assertFalse(self.output.exists())
                self.write(target, [value])

    def test_source_and_existing_output_are_protected(self):
        with self.assertRaises(ValueError):
            build_dataset(self.hr_path, self.meta_path, self.hr_path)
        self.output.write_bytes(b"existing")
        with self.assertRaises(ValueError):
            build_dataset(self.hr_path, self.meta_path, self.output)
        self.assertEqual(self.output.read_bytes(), b"existing")

    def test_malformed_record_fails(self):
        self.write(self.hr_path, ["not a dictionary"])
        with self.assertRaises(ValueError):
            list(records(self.hr_path))

    def test_publication_copies_to_destination_folder(self):
        destination = self.root / "published.gz"
        publish_file(self.hr_path, destination)
        self.assertEqual(destination.read_bytes(), self.hr_path.read_bytes())
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_failed_publication_preserves_existing_file(self):
        destination = self.root / "published.gz"
        destination.write_bytes(b"existing")
        with patch(
            "scripts.endomondo_combine_datasets.shutil.copyfileobj",
            side_effect=OSError("copy failed"),
        ):
            with self.assertRaises(OSError):
                publish_file(self.hr_path, destination)
        self.assertEqual(destination.read_bytes(), b"existing")
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_validator_rejects_modified_archive(self):
        build_dataset(self.hr_path, self.meta_path, self.output)
        self.output.write_bytes(self.output.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            validate_combined(self.output)

    def test_validator_rejects_duplicate_and_nonfinite_values(self):
        row, _, _ = combine_record(*sources())
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_row(row, 1)
        row["mean_elevation_m"] = float("nan")
        with self.assertRaisesRegex(ValueError, "Invalid number"):
            validate_row(row, -1)


if __name__ == "__main__":
    unittest.main()
