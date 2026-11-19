# Processed training data

## Retained dataset

`final/workouts.json.gz` is the single retained processed dataset. It contains
**117,902 complete matched outdoor workouts**, compressed to 1,164,614,265 bytes.
`final/workouts.json.build.json` is its build manifest, not another dataset.

Each decompressed line is strict JSON with four top-level fields:

```json
{"workout_id": 123, "user_id": 456, "hr": {"id": 123, "userId": 456}, "meta": {"id": 123, "userId": 456}}
```

This shortened example shows structure only. Actual `hr` and `meta` objects
retain every source field: heart-rate samples, timestamps, latitude, longitude,
altitude, optional speed, distance, duration, ascent/descent, original weather,
and other metadata. Separate namespaces preserve different timestamp formats
and conflicting source values without silently overwriting either.

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
arrays, consistent embedded identities, reconciled counts, checksum, and gzip
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
