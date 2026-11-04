# PacePredict
PacePredict is a running pace estimator that works off a trained regression model.

## Training data

Datasets are separated by processing stage under `training-data/model-training/`:

- `raw/endomondoHR.json.gzip` and `raw/endomondoMeta.json.gzip` contain the original,
  unfiltered records. They stay local and are ignored by Git; the HR archive
  exceeds GitHub's per-file limit. See [raw source notes](training-data/model-training/raw/README.md).
- `processed/endomondoHR.json.gz` and `processed/endomondoMeta.json.gz` contain
  outdoor runs only (`sport == "run"`), excluding treadmill running. These gzip
  archives are tracked by Git LFS. Run `git lfs pull` after cloning to download
  them. See [processed data notes](training-data/model-training/processed/README.md).

Records retain the source's line-delimited Python dictionary syntax. Read them
one at a time to avoid loading the whole dataset into memory:

```python
import ast
import gzip

path = "training-data/model-training/processed/endomondoHR.json.gz"
with gzip.open(path, "rt", encoding="utf-8") as source:
    for line in source:
        workout = ast.literal_eval(line)
        # Process the running workout here.
```

To regenerate processed datasets, first extract the raw `.gzip` archives to
`raw/endomondoHR.json` and `raw/endomondoMeta.json`, then run:

```console
python scripts/endomondo_filter_running_workouts.py training-data/model-training/raw/endomondoHR.json
python scripts/endomondo_filter_running_workouts.py training-data/model-training/raw/endomondoMeta.json
```

The output defaults to `training-data/model-training/processed/<source-name>.gz`.
The script creates the directory if needed and preserves the raw input. An
optional second argument selects a different output path; writing filtered
output into the repository's `raw` directory is rejected.

The filter writes a deterministic gzip archive and prints a JSON summary with
the selected record counts and an `uncompressed_sha256` checksum. It does not
decompress the archive again during filtering.

## Extraction validation and tests

Validation tools and automated tests live in `tests/`. Check a gzip archive
without writing an extracted copy to disk:

```console
python tests/validate_gzip_extraction.py training-data/model-training/processed/endomondoHR.json.gz
```

To also check that extraction matches the filter's selected source bytes, pass
its `uncompressed_sha256` value and the sum of its `kept` record counts:

```console
python tests/validate_gzip_extraction.py running.json.gz --expected-sha256 HASH --expected-records COUNT
```

Replace `HASH` and `COUNT` with the filter summary values. The validator streams
the archive, checks gzip integrity, and reports its extracted SHA-256, byte count,
and record count. It exits with a nonzero status if extraction fails or an
expected value differs. It does not validate individual workout fields.

Run the small fixture tests without downloading or modifying the real datasets:

```console
python -B -m unittest discover -s tests -v
```
