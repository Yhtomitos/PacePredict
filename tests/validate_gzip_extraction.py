"""Stream a gzip archive to verify extraction, integrity, and optional totals.

The validator streams the dataset without writing an extracted copy to disk.
Reading to EOF checks gzip headers, its CRC, and the uncompressed size trailer.
Compare the resulting SHA-256 with the filter's summary to verify record bytes.
"""

import argparse
import gzip
import hashlib
import json
import zlib
from pathlib import Path


CHUNK_SIZE = 8 * 1024 * 1024


def validate_gzip(
    archive: str | Path,
    *,
    expected_sha256: str | None = None,
    expected_records: int | None = None,
) -> dict[str, object]:
    """Validate a line-delimited gzip archive without extracting it to disk.

    Args:
        archive: Gzip archive to read through to EOF.
        expected_sha256: Optional hash of the original uncompressed bytes.
        expected_records: Optional count of newline-delimited records. A final
            nonempty line counts as a record even without a trailing newline.

    Returns:
        Archive path, extracted byte count, record count, and SHA-256 checksum.

    Raises:
        ValueError: An expected checksum or record count does not match.
        OSError: The archive is missing, unreadable, or has invalid gzip data.
        EOFError: A compressed stream is truncated.
        zlib.error: Compressed payload data is corrupt.

    This checks archive integrity and byte identity, not workout fields.
    Without an expected checksum, it cannot establish equivalence to a source.
    """
    archive = Path(archive)
    digest = hashlib.sha256()
    uncompressed_bytes = records = 0
    last_byte = b""
    with gzip.open(archive, "rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
            uncompressed_bytes += len(chunk)
            records += chunk.count(b"\n")
            last_byte = chunk[-1:]
    if last_byte and last_byte != b"\n":
        records += 1
    checksum = digest.hexdigest()
    if expected_sha256 is not None:
        if checksum != expected_sha256.lower():
            raise ValueError(
                "Extracted SHA-256 does not match expected checksum"
            )
    if expected_records is not None and records != expected_records:
        raise ValueError(
            f"Extracted {records} records; expected {expected_records}"
        )
    return {
        "archive": str(archive),
        "uncompressed_bytes": uncompressed_bytes,
        "records": records,
        "uncompressed_sha256": checksum,
    }


def main() -> None:
    """Validate an archive from the command line and report failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Gzip archive to validate")
    parser.add_argument(
        "--expected-sha256", help="Hash from the filter summary"
    )
    parser.add_argument(
        "--expected-records", type=int, help="Expected row count"
    )
    args = parser.parse_args()
    try:
        result = validate_gzip(
            args.archive,
            expected_sha256=args.expected_sha256,
            expected_records=args.expected_records,
        )
    except (OSError, EOFError, ValueError, zlib.error) as error:
        parser.exit(1, f"Validation failed: {error}\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
