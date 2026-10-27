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
from pathlib import Path


SPORT = re.compile(rb"['\"]sport['\"]\s*:\s*(['\"])(.*?)\1")
RUNNING = {b"run", b"treadmill running"}
LFS_SIZE_LIMIT = 2 * 1024 ** 3
PROGRESS_INTERVAL = 100_000
REPLACE_ATTEMPTS = 7


def sport_of(line: bytes) -> bytes:
    """Return the sport label, requiring exactly one sport field.

    The source uses both single and double quotes. Matching the field avoids
    constructing large sensor arrays solely to decide whether to keep a row.
    Missing or ambiguous sport fields raise ValueError.
    """
    matches = list(SPORT.finditer(line))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one sport field, found {len(matches)}"
        )
    return matches[0].group(2)


def filter_running(
    source: str | Path,
    destination: str | Path,
    *,
    allow_truncated_tail: bool = False,
) -> dict[str, object]:
    """Write outdoor and treadmill runs to a gzip file and return statistics.

    Args:
        source: Path to uncompressed, line-delimited Endomondo records.
        destination: Output gzip path, replaced after filtering succeeds.
        allow_truncated_tail: Discard an invalid final record that has no
            trailing newline.
            This recovery option is intended for historical truncated sources.

    Returns:
        Record counts, compressed and uncompressed sizes, and the SHA-256 of
        selected source bytes. Independent extraction validation belongs in
        tests/validate_gzip_extraction.py. The filter does not reread output.

    Raises:
        ValueError: Paths coincide, sport fields are ambiguous, no runs are
            found, or the compressed archive exceeds the 2 GiB LFS limit.
        FileExistsError: A temporary output from another operation exists.
        SyntaxError: The final record is invalid and recovery is disabled.
        OSError: Reading, writing, or publishing the archive fails.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Source and destination must differ")
    temporary = destination.with_name(destination.name + ".tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    kept = Counter()
    digest = hashlib.sha256()
    read_count = kept_bytes = 0
    dropped_truncated_tail = False
    try:
        with source.open("rb") as incoming, temporary.open("xb") as raw:
            # Fixed metadata makes repeated archives reproducible.
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=1,
                fileobj=raw,
                mtime=0,
            ) as outgoing:
                for read_count, line in enumerate(incoming, 1):
                    if not line.endswith(b"\n"):
                        try:
                            ast.literal_eval(line.decode("utf-8"))
                        except (SyntaxError, ValueError):
                            if not allow_truncated_tail:
                                raise
                            dropped_truncated_tail = True
                            continue
                    sport = sport_of(line)
                    if sport in RUNNING:
                        outgoing.write(line)
                        digest.update(line)
                        kept[sport.decode("utf-8")] += 1
                        kept_bytes += len(line)
                    if read_count % PROGRESS_INTERVAL == 0:
                        print(
                            f"{source.name}: scanned {read_count:,} records",
                            flush=True,
                        )
        if not kept:
            raise ValueError("No running workouts found")
        if temporary.stat().st_size > LFS_SIZE_LIMIT:
            raise ValueError(
                "Compressed output exceeds GitHub's 2 GiB LFS limit"
            )
        # Windows scanners can briefly lock a newly written archive.
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                temporary.replace(destination)
                break
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.1 * 2**attempt)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except PermissionError:
            # Preserve the original failure if cleanup is temporarily blocked.
            pass
        raise
    result = {
        "source": str(source),
        "destination": str(destination),
        "source_records": read_count,
        "kept": dict(kept),
        "uncompressed_bytes": kept_bytes,
        "dropped_truncated_tail": dropped_truncated_tail,
        "compressed_bytes": destination.stat().st_size,
        "uncompressed_sha256": digest.hexdigest(),
    }
    print(json.dumps(result), flush=True)
    return result


def main() -> None:
    """Parse paths, filter records, and print a JSON summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Uncompressed source file")
    parser.add_argument("destination", type=Path, help="Output gzip archive")
    args = parser.parse_args()
    filter_running(args.source, args.destination)


if __name__ == "__main__":
    main()
