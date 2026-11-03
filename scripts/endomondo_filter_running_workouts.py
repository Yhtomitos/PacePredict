"""Filter Endomondo records into a deterministic gzip archive.

The input contains one Python dictionary or JSON object per line. Selected
records retain their original bytes. Use tests/validate_gzip_extraction.py
to independently check extraction against the reported SHA-256 checksum.
"""

import argparse
import ast
import gzip
import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

SPORT = re.compile(rb"['\"]sport['\"]\s*:\s*(['\"])(.*?)\1")
RUNNING = {b"run", b"treadmill running"}
LFS_SIZE_LIMIT = 2 * 1024**3


@dataclass
class Selection:
    """Statistics and source-byte checksum for selected workout records."""

    source_records: int = 0
    kept: Counter[str] = field(default_factory=Counter)
    uncompressed_bytes: int = 0
    dropped_truncated_tail: bool = False
    checksum: str = ""


def sport_of(line: bytes) -> bytes:
    """Read one sport field without constructing large sensor arrays."""
    matches = list(SPORT.finditer(line))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one sport field, found {len(matches)}"
        )
    return matches[0].group(2)


def _valid_tail(line: bytes, allow_truncated_tail: bool) -> bool:
    """Reject malformed terminal records unless recovery was requested."""
    if line.endswith(b"\n"):
        return True
    try:
        ast.literal_eval(line.decode("utf-8"))
    except (SyntaxError, ValueError):
        if not allow_truncated_tail:
            raise
        return False
    return True


def _write_archive(
    source: Path, temporary: Path, allow_truncated_tail: bool
) -> Selection:
    """Write selected records with deterministic gzip metadata."""
    selection = Selection()
    digest = hashlib.sha256()
    with source.open("rb") as incoming, temporary.open("xb") as raw:
        with gzip.GzipFile(
            filename="", mode="wb", compresslevel=1, fileobj=raw, mtime=0
        ) as outgoing:
            for number, line in enumerate(incoming, 1):
                selection.source_records = number
                if not _valid_tail(line, allow_truncated_tail):
                    selection.dropped_truncated_tail = True
                    continue
                sport = sport_of(line)
                if sport in RUNNING:
                    outgoing.write(line)
                    digest.update(line)
                    selection.kept[sport.decode("utf-8")] += 1
                    selection.uncompressed_bytes += len(line)
                if number % 100_000 == 0:
                    print(
                        f"{source.name}: scanned {number:,} records",
                        flush=True,
                    )
    selection.checksum = digest.hexdigest()
    return selection


def _publish_archive(temporary: Path, destination: Path) -> None:
    """Retry transient Windows file locks with bounded exponential backoff."""
    for attempt in range(7):
        try:
            temporary.replace(destination)
            return
        except PermissionError:
            if attempt == 6:
                raise
            time.sleep(0.1 * 2**attempt)


def filter_running(
    source: str | Path,
    destination: str | Path,
    *,
    allow_truncated_tail: bool = False,
) -> dict[str, object]:
    """Filter outdoor and treadmill runs and report source-byte statistics.

    Replace the destination after filtering and checking the archive size.
    Errors leave an existing destination intact. Recovery of a malformed final
    record is opt-in for historical sources that end mid-record.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Source and destination must differ")
    temporary = destination.with_name(destination.name + ".tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    try:
        selection = _write_archive(source, temporary, allow_truncated_tail)
        if not selection.kept:
            raise ValueError("No running workouts found")
        if temporary.stat().st_size > LFS_SIZE_LIMIT:
            raise ValueError(
                "Compressed output exceeds GitHub's 2 GiB LFS limit"
            )
        _publish_archive(temporary, destination)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except PermissionError:
            pass  # A Windows lock must not mask the original error.
        raise
    result: dict[str, object] = {
        "source": str(source),
        "destination": str(destination),
        "source_records": selection.source_records,
        "kept": dict(selection.kept),
        "uncompressed_bytes": selection.uncompressed_bytes,
        "dropped_truncated_tail": selection.dropped_truncated_tail,
        "compressed_bytes": destination.stat().st_size,
        "uncompressed_sha256": selection.checksum,
    }
    print(json.dumps(result), flush=True)
    return result


def main() -> None:
    """Parse source and destination paths and print a JSON filter summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    filter_running(args.source, args.destination)


if __name__ == "__main__":
    main()
