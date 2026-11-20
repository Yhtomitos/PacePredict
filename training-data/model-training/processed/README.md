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

## Rebuilding when needed

Existing schema-version-1 nested archives can be migrated without regenerating
large source intermediates. The following explicit command replaces the joined
archive and updates its manifest after conversion succeeds:

```console
python -m scripts.endomondo_flatten_workouts training-data/model-training/processed/final/workouts.json.gz --output training-data/model-training/processed/final/workouts.json.gz --replace
```

The migration verifies the old checksum, workout/user identities, uniqueness,
and row count, then publishes from temporary storage. It refuses an already-flat
source. Fresh joins from the source inputs also emit flattened schema version 2.

Regeneration is optional and recreates large temporary inputs. The filter
currently expects uncompressed Python-dictionary records. Extract the original
raw `.gzip` archives to their corresponding `.json` names before filtering.
Process one source at a time and delete its extracted copy after validation
when storage is limited. Keep the original compressed raw files unchanged.

```console
python scripts/endomondo_filter_running_workouts.py training-data/model-training/raw/endomondoHR.json
python scripts/endomondo_filter_running_workouts.py training-data/model-training/raw/endomondoMeta.json
```

The outputs default to `processed/endomondoHR.json.gz` (117,902 rows) and
`processed/endomondoMeta.json.gz` (347,558 rows). They are now ignored local
intermediates, rather than retained Git LFS datasets. Use the extraction
validator with the filter's checksum and count before using regenerated inputs.

Once both inputs exist, build to a new output name because the builder protects
existing outputs:

```console
python -m scripts.endomondo_join_workouts --output training-data/model-training/processed/final/workouts-rebuilt.json.gz
```

After independently validating the replacement archive, remove redundant
intermediate copies again. The standard-library builder uses disk-backed
SQLite storage, deterministic gzip compression level 1, and publication with
normal destination-folder permissions.

The reduced-feature builder and its tests remain available as optional tooling,
but its outputs are no longer retained datasets. Their validators apply only
if those optional outputs are explicitly regenerated.

The upstream [FitRec dataset page](https://cseweb.ucsd.edu/~jmcauley/datasets/fitrec.html)
describes academic-use and redistribution restrictions.
