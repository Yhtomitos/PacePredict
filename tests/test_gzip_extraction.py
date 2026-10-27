"""Exercise filtering and independent gzip extraction on small local fixtures.

Run from the repository root with: python -m unittest discover -s tests -v
"""

import ast
import contextlib
import gzip
import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.endomondo_filter_running_workouts import filter_running
from tests.validate_gzip_extraction import validate_gzip


class GzipExtractionTests(unittest.TestCase):
    """Verify source-byte preservation, extraction, and failure handling."""

    def setUp(self) -> None:
        """Create disposable source and destination paths for each test."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / "workouts.json"
        self.archive = self.directory / "running.json.gz"
        self.selected = (
            b"{'sport': 'run', 'id': 2, 'heart_rate': [100, 120]}\n"
            b'{"sport": "treadmill running", "id": 3}\n'
        )
        self.source.write_bytes(
            b"{'sport': 'bike', 'id': 1}\n"
            + self.selected
            + b"{'sport': 'walk', 'id': 4}\n"
        )

    def filter_fixture(self, **kwargs) -> dict[str, object]:
        """Filter the fixture and suppress progress output in test logs."""
        with contextlib.redirect_stdout(io.StringIO()):
            return filter_running(self.source, self.archive, **kwargs)

    def test_filtered_records_extract_without_byte_changes(self) -> None:
        """Round-trip both input syntaxes and check independent totals."""
        summary = self.filter_fixture()
        extracted = gzip.decompress(self.archive.read_bytes())
        self.assertEqual(extracted, self.selected)
        workouts = [
            ast.literal_eval(line.decode())
            for line in extracted.splitlines()
        ]
        self.assertEqual([row["id"] for row in workouts], [2, 3])
        result = validate_gzip(
            self.archive,
            expected_sha256=summary["uncompressed_sha256"],
            expected_records=2,
        )
        self.assertEqual(result["uncompressed_bytes"], len(self.selected))
        self.assertEqual(
            result["uncompressed_sha256"],
            hashlib.sha256(self.selected).hexdigest(),
        )

    def test_filter_does_not_decompress_output(self) -> None:
        """Keep extraction validation independent from the filtering pass."""
        failure = AssertionError("Unexpected reread")
        with patch("gzip.open", side_effect=failure):
            self.filter_fixture()
        validate_gzip(self.archive, expected_records=2)

    def test_valid_final_record_without_newline(self) -> None:
        """Preserve a valid final record without a trailing newline."""
        self.source.write_bytes(self.selected.rstrip(b"\n"))
        self.filter_fixture()
        with patch("tests.validate_gzip_extraction.CHUNK_SIZE", 7):
            result = validate_gzip(self.archive, expected_records=2)
        self.assertEqual(result["uncompressed_bytes"], len(self.selected) - 1)

    def test_truncated_input_preserves_existing_destination(self) -> None:
        """A failed filter must leave the previous archive intact."""
        self.filter_fixture()
        original = self.archive.read_bytes()
        self.source.write_bytes(self.selected + b"{'sport': 'run', 'id':")
        with self.assertRaises(SyntaxError):
            self.filter_fixture()
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertFalse(self.archive.with_suffix(".gz.tmp").exists())

    def test_explicit_truncated_tail_recovery(self) -> None:
        """Recovery drops only the incomplete terminal record."""
        self.source.write_bytes(self.selected + b"{'sport': 'run', 'id':")
        summary = self.filter_fixture(allow_truncated_tail=True)
        self.assertTrue(summary["dropped_truncated_tail"])
        self.assertEqual(
            gzip.decompress(self.archive.read_bytes()), self.selected
        )

    def test_wrong_checksum_and_count_are_rejected(self) -> None:
        """A valid archive can still contain the wrong dataset."""
        self.filter_fixture()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            validate_gzip(self.archive, expected_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "expected 3"):
            validate_gzip(self.archive, expected_records=3)

    def test_corrupt_crc_is_rejected(self) -> None:
        """Reading through EOF detects damaged gzip integrity metadata."""
        self.filter_fixture()
        damaged = bytearray(self.archive.read_bytes())
        damaged[-8] ^= 1
        self.archive.write_bytes(damaged)
        with self.assertRaises(gzip.BadGzipFile):
            validate_gzip(self.archive)

    def test_truncated_gzip_is_rejected(self) -> None:
        """An incomplete archive cannot pass extraction validation."""
        self.filter_fixture()
        self.archive.write_bytes(self.archive.read_bytes()[:-4])
        with self.assertRaises(EOFError):
            validate_gzip(self.archive)

    def test_cli_reports_failure_with_nonzero_exit(self) -> None:
        """Return a failure status for use in automated checks."""
        self.filter_fixture()
        validator = Path(__file__).with_name("validate_gzip_extraction.py")
        result = subprocess.run(
            [
                sys.executable,
                str(validator),
                str(self.archive),
                "--expected-records",
                "3",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Validation failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
