"""Keep running workouts from line-delimited Endomondo data in a gzip file."""

import argparse
import ast
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re
import time


SPORT = re.compile(rb"['\"]sport['\"]\s*:\s*(['\"])(.*?)\1")
RUNNING = {b"run", b"treadmill running"}


def sport_of(line):
    matches = list(SPORT.finditer(line))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one sport field, found {len(matches)}")
    return matches[0].group(2)


def filter_running(source, destination, *, allow_truncated_tail=False):
    """Preserve selected records byte for byte; verify before publishing output."""
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
            with gzip.GzipFile(filename="", mode="wb", compresslevel=1,
                               fileobj=raw, mtime=0) as outgoing:
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
                    if read_count % 100000 == 0:
                        print(f"{source.name}: scanned {read_count:,} records", flush=True)
        if not kept:
            raise ValueError("No running workouts found")
        verified = hashlib.sha256()
        with gzip.open(temporary, "rb") as check:
            while chunk := check.read(8 * 1024 * 1024):
                verified.update(chunk)
        if verified.digest() != digest.digest():
            raise ValueError("Compressed output does not match selected records")
        if temporary.stat().st_size > 2147483648:
            raise ValueError("Compressed output exceeds GitHub's 2 GiB LFS limit")
        for attempt in range(7):
            try:
                temporary.replace(destination)
                break
            except PermissionError:
                if attempt == 6:
                    raise
                time.sleep(0.1 * 2 ** attempt)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except PermissionError:
            pass
        raise
    result = {"source": str(source), "destination": str(destination),
              "source_records": read_count, "kept": dict(kept),
              "uncompressed_bytes": kept_bytes,
              "dropped_truncated_tail": dropped_truncated_tail,
              "compressed_bytes": destination.stat().st_size,
              "uncompressed_sha256": digest.hexdigest()}
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    filter_running(args.source, args.destination)
