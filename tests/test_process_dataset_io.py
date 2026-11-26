"""Check parsing, publication, and CLI behavior of the standalone processor."""

import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.endomondo_process_dataset import (
    joined_record,
    main,
    publish_file,
    records,
)
from tests.workout_fixtures import sources


class ProcessorIOTests(unittest.TestCase):
    """Protect the integrated processor's file and parsing boundaries."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_parser_rejects_non_dictionary_and_executable_input(self):
        source = self.root / "raw.gzip"
        for text in ("[]", "dict(id=1)", "{'id':"):
            with self.subTest(text=text):
                with gzip.open(source, "wt", encoding="utf-8") as stream:
                    stream.write(text + "\n")
                with self.assertRaises(ValueError):
                    list(records(source))

    def test_record_size_is_bounded(self):
        source = self.root / "raw.gzip"
        with gzip.open(source, "wt", encoding="utf-8") as stream:
            stream.write("{'id': 1}\n")
        with patch("scripts.endomondo_process_dataset.MAX_RECORD_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "too large"):
                list(records(source))

    def test_failed_copy_preserves_destination_and_cleans_temporary(self):
        source, output = self.root / "source", self.root / "output"
        source.write_bytes(b"new")
        output.write_bytes(b"existing")
        with patch(
            "scripts.endomondo_process_dataset.shutil.copyfileobj",
            side_effect=OSError("copy failed"),
        ):
            with self.assertRaises(OSError):
                publish_file(source, output)
        self.assertEqual(output.read_bytes(), b"existing")
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_merge_precedence_timestamps_and_source_immutability(self):
        hr, meta = sources()
        hr.update(gender="hr", calories=1)
        meta.update(gender="meta", timestamp=["UTC"], weather={})
        row = joined_record(hr, meta)
        self.assertEqual(row["gender"], "meta")
        self.assertEqual(row["hr_timestamp"], hr["timestamp"])
        self.assertEqual(row["workout_timestamp"], ["UTC"])
        self.assertNotIn("calories", row)
        self.assertNotIn("weather", row)
        self.assertEqual(hr["calories"], 1)

    def test_cli_routes_raw_defaults(self):
        with patch("sys.argv", ["processor"]):
            with patch(
                "scripts.endomondo_process_dataset.build_from_raw"
            ) as build:
                main()
        hr, meta, output = build.call_args.args
        self.assertEqual(hr.name, "endomondoHR.json.gzip")
        self.assertEqual(meta.name, "endomondoMeta.json.gzip")
        self.assertEqual(output.name, "planned-workouts.json.gz")
