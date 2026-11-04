# Processed training data

`endomondoHR.json.gz` and `endomondoMeta.json.gz` contain only records with
`sport == "run"`. These generated gzip archives are stored with Git LFS.

Regenerate from the repository root:

```console
python scripts/endomondo_filter_running_workouts.py training-data/model-training/raw/endomondoHR.json
python scripts/endomondo_filter_running_workouts.py training-data/model-training/raw/endomondoMeta.json
```

Each command preserves its raw input and defaults to this output directory.
Use `tests/validate_gzip_extraction.py` with the emitted SHA-256 and kept-record
count to verify extraction independently. The heart-rate archive should contain
117,902 outdoor runs, and the metadata archive should contain 347,558.
