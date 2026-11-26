# PacePredict

PacePredict is a running pace estimator built around a regression model.

## Training data

Retained datasets live under `training-data/model-training/`:

- `raw/endomondoHR.json.gzip` and `raw/endomondoMeta.json.gzip` preserve the
  original unfiltered data locally. They are ignored by Git.
- `processed/final/workouts.json.gz` is the complete outdoor-workout join,
  containing **117,902 workouts**, tracked through Git LFS.
- `processed/final/workouts.json.build.json` records counts and checksums.

Run `git lfs pull` after cloning to download the tracked joined archive.
Read its strict JSON Lines format one object at a time:

```python
import gzip
import json

path = "training-data/model-training/processed/final/workouts.json.gz"
with gzip.open(path, "rt", encoding="utf-8") as source:
    for line in source:
        workout = json.loads(line)
        heart_rate = workout["heart_rate"]
        distance = workout["distance"]
```

Each flat object merges HR and Meta records matched by workout ID and checked
for matching user IDs. Meta values overwrite overlapping HR fields. The HR
timestamp array is `hr_timestamp`; the Meta array is `workout_timestamp`.
Coordinates and sensor arrays are top-level fields. `calories`, `hydration`,
and `weather` are removed. Further cleaning and external temperature/humidity
enrichment remain separate steps.

Separate processed HR/Meta archives and the superseded reduced `running`
artifacts have been removed to save storage. Rebuild inputs are temporary local
intermediates; see [processed data notes](training-data/model-training/processed/README.md)
and [raw source notes](training-data/model-training/raw/README.md).

## Validation and tests

Validate joined identities, counts, checksum, and gzip integrity:

```console
python tests/validate_joined_workouts.py training-data/model-training/processed/final/workouts.json.gz
```

For gzip integrity and extracted-byte counts alone:

```console
python tests/validate_gzip_extraction.py training-data/model-training/processed/final/workouts.json.gz --expected-records 117902
```

Run fixture tests without downloading or modifying real datasets:

```console
python -B -m unittest discover -s tests -v
```

Static-analysis tools and CI instructions are documented in [tests/README.md](tests/README.md).
