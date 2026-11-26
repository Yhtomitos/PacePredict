# Processed training data

## Retained dataset

`final/workouts.json.gz` is the single retained processed dataset. It contains
**117,902 matched outdoor workouts** in flattened schema version 2.
The current gzip archive is 1,155,506,001 bytes; its manifest records the new
checksum and the previous nested archive's checksum for migration provenance.
`final/workouts.json.build.json` is its build manifest, not another dataset.

Each decompressed line is a flat JSON object. Abbreviated illustrative schema:

```json
{"workout_id": 123, "user_id": 456, "id": 123, "userId": 456, "sport": "run", "heart_rate": [120, 130], "hr_timestamp": [1400000000, 1400000010], "workout_timestamp": ["2014-05-13T16:53:20Z"], "distance": 5.0, "duration": 1800.0, "ascent": 30.0, "descent": 25.0}
```

The source dictionaries are merged at the top level, with Meta values taking
precedence for shared names. Both timestamp arrays are preserved without format
conversion: Meta `timestamp` becomes `workout_timestamp`, and HR `timestamp`
becomes `hr_timestamp`. No `hr`/`meta` containers or generic `timestamp` remain.
The source `id`/`userId` fields and existing `workout_id`/`user_id` aliases are
retained and checked for agreement. `calories`, `hydration`, and `weather` are
removed from the processed records. Heart rate, coordinates, altitude, optional
speed, distance, duration, ascent/descent, and other retained fields stay intact.
Original raw archives still preserve all source fields and conflicting values.

Rows follow Meta source order. The inner join requires matching workout and
user IDs and outdoor running (`sport == "run"`); duplicate IDs or inconsistent
matches stop publication. All 117,902 HR workouts matched. The 229,656 Meta-only
workouts are excluded and counted in the manifest. Original raw Meta data still
preserves those records.

No additional cleaning for pace, duration, elevation, or heart rate was applied.
The archive includes the 63 records previously excluded from reduced features
for invalid distance/duration. There are no inferred effort labels or external
weather values. Treat this as source material for cleaning and clustering,
not as a training-ready feature matrix.

Independent validation confirmed unique matched IDs, complete heart-rate
arrays, consistent identifier aliases, reconciled counts, checksum, and gzip
integrity. Validate it again with:

```console
python tests/validate_joined_workouts.py training-data/model-training/processed/final/workouts.json.gz
```

## Removed redundant artifacts

The separate `endomondoHR.json.gz` and `endomondoMeta.json.gz` outdoor archives
were temporary inputs for the join. The older `final/running.json.gz`, its build
report, and its local provenance archive were superseded by the complete join.
Removing these five files reclaimed 1,506,284,738 bytes (about 1.40 GiB) from the
working folder. Original raw archives and the joined archive are retained.

Historical Git/LFS objects are separate from working files; this cleanup does
not purge repository history or remote LFS storage.

## Rebuilding the current modeling schema

The single processor reads both original gzip archives, filters outdoor runs,
joins by workout ID, checks user IDs, and writes schema-version-3 summaries:

```console
python -m scripts.endomondo_process_dataset
```

It defaults to the original archives in `../raw/` and a new
`final/planned-workouts.json.gz` with an adjacent build manifest. Override with
`--hr`, `--meta`, and `--output`. Existing outputs and raw storage are protected.
A temporary disk-backed SQLite index is removed after success or failure;
no decompressed sources or intermediate datasets are retained.

The summary contains IDs, UTC start time, distance in miles, start coordinates,
elevation gain/loss in metres, time-weighted average heart rate, temperature,
humidity, and average pace in minutes per mile. Weather awaits enrichment;
invalid measurements remain null. Measured heart rate is retrospective.
The retained version-2 archive described above is unchanged by this code update.

Optional migration from a previously joined version-1 or version-2 archive:

```console
python -m scripts.endomondo_process_dataset final/workouts.json.gz --output final/summary.json.gz
```

Paths in this example are relative to the current working directory. Use full
repository-relative paths when running from the repository root. Migration
checks the source manifest/checksum; only migration accepts `--replace`.
The former separate filter, combine, join, and flatten scripts were removed.

The upstream [FitRec dataset page](https://cseweb.ucsd.edu/~jmcauley/datasets/fitrec.html)
describes academic-use and redistribution restrictions.
