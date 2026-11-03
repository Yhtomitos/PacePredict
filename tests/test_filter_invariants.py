"""Check gzip filtering without downloading training datasets."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.endomondo_filter_running_workouts import filter_running


class FilterRunningTests(unittest.TestCase):
    """Check source-byte identity and protection from data loss."""

    def setUp(self) -> None:
        """Create independent temporary inputs and outputs."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / "source.json"
        self.destination = Path(temporary.name) / "running.json.gz"
        self.selected = (
            b"{'sport': 'run', 'id': 2}\n"
            b'{"sport": "treadmill running", "id": 3}\n'
        )
        self.source.write_bytes(
            b"{'sport': 'bike'}\n" + self.selected + b"{'sport': 'walk'}\n"
        )

    def filter_fixture(self, **kwargs: bool) -> dict[str, object]:
        """Filter the fixture while suppressing progress output."""
        with contextlib.redirect_stdout(io.StringIO()):
            return filter_running(self.source, self.destination, **kwargs)

    def test_deterministic_archive(self) -> None:
        """Repeated inputs produce identical gzip bytes."""
        self.filter_fixture()
        original = self.destination.read_bytes()
        self.filter_fixture()
        self.assertEqual(self.destination.read_bytes(), original)

    def test_no_runs(self) -> None:
        """Do not publish an empty selection."""
        self.source.write_bytes(b"{'sport': 'bike'}\n")
        with self.assertRaisesRegex(ValueError, "No running"):
            self.filter_fixture()
        self.assertFalse(self.destination.exists())

    def test_no_in_place_writes(self) -> None:
        """Protect the source when paths coincide."""
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "must differ"):
            filter_running(self.source, self.source)
        self.assertEqual(self.source.read_bytes(), original)

    def test_size_guard(self) -> None:
        """Oversized output must not replace the existing destination."""
        self.filter_fixture()
        original = self.destination.read_bytes()
        with patch(
            "scripts.endomondo_filter_running_workouts.LFS_SIZE_LIMIT", 1
        ):
            with self.assertRaisesRegex(ValueError, "LFS limit"):
                self.filter_fixture()
        self.assertEqual(self.destination.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
