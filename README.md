# PacePredict
PacePredict is a running pace estimator that works off a trained regression model.

## Training data

`training-data/raw/endomondoHR.json.gz` and
`training-data/raw/endomondoMeta.json.gz` contain only workouts whose `sport`
is `run` or `treadmill running`. They are compressed with gzip and stored with
Git LFS. Install Git LFS and run `git lfs pull` after cloning to download them.

Records retain the source's line-delimited Python dictionary syntax. Read them
one at a time to avoid loading the whole dataset into memory:

```python
import ast
import gzip

with gzip.open("training-data/raw/endomondoHR.json.gz", "rt", encoding="utf-8") as source:
    for line in source:
        workout = ast.literal_eval(line)
        # Process the running workout here.
```

To filter another uncompressed source file while preserving each selected record:

```console
python scripts/filter_running_workouts.py source.json running.json.gz
```
