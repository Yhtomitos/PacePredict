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

## Combined schema dataset

Build a single gzip JSON Lines archive with the proposed pace-prediction schema:

```console
python scripts/endomondo_combine_datasets.py
python tests/validate_combined_dataset.py training-data/model-training/processed/final/running.json.gz
```

The output is `final/running.json.gz`. Unlike the source dictionary syntax,
each decompressed line is strict JSON and can be read with `json.loads`.
Workouts are joined by workout ID, checked for matching user IDs and outdoor
running, and written in ascending workout-ID order. Meta-only workouts lack
HR route data and are not added to this matched dataset.

| Field | Role / unit |
| --- | --- |
| `workout_id` | Metadata: unique workout identifier |
| `user_id` | Metadata: runner identifier, not a default training feature |
| `started_at_utc` | Metadata: route start in UTC, or null if unusable |
| `distance_km` | Input: recorded distance as a proxy for planned distance |
| `temperature_c` | Input: null until separate weather enrichment |
| `relative_humidity_pct` | Input: null until separate weather enrichment |
| `elevation_gain_m` | Input: Meta ascent, or null when missing/invalid |
| `elevation_loss_m` | Input: Meta descent, or null when missing/invalid |
| `mean_elevation_m` | Input: distance-weighted reported HR altitude, or null |
| `pace_min_per_km` | Target: Meta duration / 60 / distance |

Distance is interpreted as kilometres, duration as seconds, and elevation as
metres. Duration is used as recorded; pause/moving-time semantics have not been
verified. This target must not be labelled as moving pace without further
source verification. Elevation is uncorrected source data; its absolute reference
and GPS quality still need auditing before treating it as verified altitude
above sea level. Negative elevations are retained rather than assumed invalid.

Mean elevation weights each segment's endpoint-average altitude by horizontal
great-circle distance. Repeated positions have zero weight. Invalid coordinates,
non-finite values, misaligned arrays, backwards timestamps, and zero-length
routes produce null elevation and a quality flag. Ascent/descent are not
recalculated by summing noisy altitude differences.

The combined archive is an intermediate dataset, **not training-ready**: weather
is deliberately null, and missing route/elevation features remain explicit.
Invalid distance/duration or mismatched records are rejected with reasons.

The current build contains **117,839 rows**, excluding 63 HR workouts with
invalid distance or duration. Mean elevation/start time is null for 122 rows;
gain is null for 262 rows and loss for 264. All rows have null weather values.
The compressed archive is 5,753,511 bytes. Its schema, gzip integrity, checksum,
unique IDs, counts, and all pace targets were independently checked.

Basic validity is not physical plausibility: observed pace ranges from roughly
0.0034 to 60,013.6 min/km, and mean reported elevation reaches 12,526.6 m.
These source outliers remain in this combined intermediate archive. Choose and
document plausibility thresholds before constructing a training-ready subset;
this build does not silently invent pace or elevation cutoffs.

Two companion artifacts support validation and later enrichment:

- `final/running.json.build.json`: source and output SHA-256 hashes, counts,
  feature definitions, rejection reasons, and quality-flag totals.
- `final/running.json.provenance.jsonl.gz`: local per-workout quality flags,
  recorded duration, and route start coordinates/time for weather lookup.
  This file is ignored by Git and is separate from the training feature matrix.

The builder uses only the standard library, a temporary SQLite index, bounded
record reads, deterministic gzip settings, and new output paths. It refuses to
overwrite existing outputs or write into raw storage. To build another version,
pass `--output` with a new filename. Original archives are preserved.

The upstream [FitRec dataset page](https://cseweb.ucsd.edu/~jmcauley/datasets/fitrec.html)
describes academic-use and redistribution restrictions; keep generated data
local unless the intended distribution is permitted.
