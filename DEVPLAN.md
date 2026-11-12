# Final running dataset development plan

Status: planning only. No dataset construction or weather requests have started.

## Objective and scope

Build one validated row per outdoor workout to predict average running pace
from distance, temperature, relative humidity, elevation gain, elevation loss,
and average route elevation. Keep user identifiers for grouping and auditing,
not as a default model input. Do not reintroduce running-history features.

The user-requested behaviour guideline is `C:\home\AGENT_BEHAVIOUR_OUTLINE.md`.
It requests this plan, alternatives, task-specific branches, and tests before
commits. It does not authorize implementing or publishing the pipeline now.

## Starting evidence

- Existing processed archives contain outdoor runs (`sport == "run"`).
- A read-only scan found 117,902 unique HR workout IDs, 347,558 unique Meta
  workout IDs, and 117,902 shared workout IDs. This is a candidate pool,
  not the final accepted row count.
- HR samples provide aligned altitude, latitude, longitude, and timestamp
  arrays. Meta samples provide distance, duration, ascent, and descent.
- The earlier 200-record samples showed missing ascent/descent and negative
  altitudes. Negative elevation alone is not evidence of corruption; validate
  reference level, location, consistency, and abrupt changes.
- Shared workout IDs still need user-ID agreement and record validation.
- Original archives in `raw/` and existing processed archives stay unchanged.

## Implementation alternatives

| Option | Advantages | Technical debt / cost |
| --- | --- | --- |
| Streaming Python + SQLite index + gzip JSON Lines (recommended) | Standard library only; bounded memory; inspectable records; disk-backed joins and restartable stages | Explicit schema checks; less efficient column selection than Parquet |
| Streaming columnar processing + Parquet | Typed columns; compact analytical reads; convenient training integration | Adds pinned runtime packages and schema/version management |
| In-memory dataframe join + CSV | Short initial implementation; familiar inspection tools | Memory pressure from route arrays; weak CSV typing; likely streaming rewrite |

Use the recommended option for version 1. Reconsider Parquet if measured
training or storage requirements justify an additional dependency.

## Proposed row contract

Names and units below are proposed until the source audit confirms conversions.
One row describes a completed workout used as a proxy for a planned route.

| Field | Role | Definition |
| --- | --- | --- |
| `workout_id` | Metadata | Stable source workout ID; unique per accepted row |
| `user_id` | Metadata | Source user ID; used for grouping, not fitness estimation |
| `started_at_utc` | Metadata | Validated workout start in UTC |
| `distance_km` | Input | Completed distance as proxy for planned distance |
| `temperature_c` | Input | Historical 2 m temperature matched to start location/time |
| `relative_humidity_pct` | Input | Historical 2 m relative humidity matched the same way |
| `elevation_gain_m` | Input | Validated Meta ascent, or a documented route-derived fallback |
| `elevation_loss_m` | Input | Validated Meta descent, or a documented route-derived fallback |
| `mean_elevation_m` | Input | Distance-weighted route elevation above sea level |
| `pace_min_per_km` | Target | Validated duration in minutes divided by distance in km |

Store source provenance, weather cache keys, calculation version, elevation
method, and quality flags in a keyed companion artifact. Keep coordinates out
of the training feature matrix; retain lookup coordinates in local staging only
when needed for reproducibility. Do not commit location caches by default.

## High-level tasks and actionable subtasks

### 1. Audit sources and settle the data contract

- Before implementation, fetch/pull the relevant origin branch with a clean
  working tree; create a feature branch from the current integration base.
  Never implement directly on `main`.
- Stream both archives with `ast.literal_eval`; bound record size and reject
  malformed objects. Never use `eval` or read an entire archive into memory.
- Measure field availability, value types, finite numeric values, duplicate
  identifiers, user-ID agreement, route array lengths, and timestamp ordering.
- Confirm distance, duration, altitude, ascent, and descent units against source
  documentation and cross-check against coordinate distances and timestamps.
- Establish whether `duration` represents elapsed or moving time. Do not infer
  pause semantics solely from the first/last GPS timestamps.
- Produce an audit report with rejection categories and distributions. Select
  documented GPS/elevation plausibility thresholds from this evidence.
- Unit tests: malformed records, incorrect types, duplicate IDs, non-finite
  values, inconsistent users, missing fields, and unit conversions.
- Smoke test: a tiny pair of fixture archives produces predictable audit counts.

### 2. Join workouts and construct non-weather features

- Index compact Meta fields in temporary SQLite storage; stream HR records and
  join on workout ID, checking user-ID agreement before accepting a pair.
- Reject zero/negative distance or duration. Define the target's pause policy
  explicitly and record it in the dataset version metadata.
- Validate coordinates and timestamp alignment. Compute horizontal segment
  distances; exclude zero-distance segments from elevation weighting.
- Calculate mean elevation as the sum of each segment's horizontal distance
  multiplied by its endpoint-mean elevation, divided by total valid distance.
- Prefer validated Meta ascent/descent initially. Audit their agreement with
  route-derived estimates; do not sum raw noisy altitude differences blindly.
- Treat absent/unusable elevation as missing, never as zero. Compare dropping
  affected rows with a separately flagged, validated route-derived fallback.
  External elevation correction is a separate scope decision if needed.
- Unit tests: flat routes, known uphill/downhill routes, irregular sampling,
  valid below-sea-level routes, spikes, repeated coordinates, and missing data.
- Smoke test: joined fixtures produce the expected inputs and pace target.

### 3. Enrich weather through a separate script

- Use the Open-Meteo Historical Weather API with `temperature_2m` and
  `relative_humidity_2m`, explicit Celsius units, and UTC timestamps.
- Initially use the first valid coordinate and nearest hourly value to workout
  start, with a documented tie rule and maximum acceptable time difference.
  Fetch adjacent dates when necessary. Do not silently substitute daily means.
- Select and record an explicit reanalysis model supported across the audited
  date range. Historical reanalysis is an estimate, not a route measurement.
- Cache requests by exact coordinates, date range, model, units, and variables.
  Consider spatial bucketing only after quantifying its approximation error.
- Review current service terms, attribution, usage limits, and batching before
  scheduling a full run. Estimate unique requests from the staging manifest.
- Use timeouts, bounded retries, exponential backoff with jitter for rate limits
  and transient failures, and respect server retry guidance. Resume from cache.
- Validate response units, coordinate/time coverage, finite temperatures, and
  humidity in [0, 100]. Retain failed lookups with reasons for later retry.
- Preserve retrieval time, model, returned coordinates, and matching method.
- Unit tests: UTC boundaries, hour ties, missing hours, null values, malformed
  responses, cache hits, rate limiting, retry exhaustion, and restart behaviour.
- Smoke test: mocked HTTP responses enrich a fixture without network access.
- Separately run a small opt-in live check before bulk enrichment.

### 4. Publish and independently validate the final dataset

- Proposed output directory: `training-data/model-training/processed/final/`.
  Keep staging and weather caches local, outside published feature artifacts.
- Write deterministic gzip JSON Lines with a fixed field schema and stable
  workout-ID order. Use temporary files and atomic publication.
- Record source archive hashes, configuration/version, accepted/rejected counts,
  rejection reasons, output checksum, units, and weather provenance in a build
  manifest. Do not recreate deprecated per-source summary JSON files.
- Keep incomplete rows in a keyed rejection/retry report. Initially publish
  complete validated rows only; report the resulting selection bias and losses.
- Independently verify gzip integrity, schema, uniqueness, feature ranges,
  target calculations, counts, provenance references, and source preservation.
- Unit tests: deterministic output, interrupted publication, invalid schema,
  corruption, duplicate rows, and reconciliation of accepted/rejected counts.
- Smoke test: tiny source archives plus a fixed weather cache reproduce a
  known final artifact and checksum without touching the real datasets.

### 5. Prepare leakage-aware training handoff and automation

- Define deterministic user-disjoint train/validation/test partitions for
  evaluating predictions for new users. Keep user IDs out of model features.
- Check repeated workouts/routes across partitions and document any remaining
  dependence. Add a chronological evaluation if future-run prediction for
  existing users becomes a separate objective.
- Fit imputers, scaling, and other learned transformations on training rows
  only. Do not use target duration to select weather inputs or route features.
- Document the completed-route proxy and the difference between historical
  weather at training time and forecast/user-supplied weather at prediction time.
- Extend tests under `tests/` and existing GitHub Actions. Normal PR checks use
  small fixtures and mocked weather, never full archives or bulk API calls.
- Retain Ruff, Pyright, Bandit, Semgrep, pip-audit, and CodeQL; no additional
  static-analysis tool is currently needed. Add source paths to their coverage.
- Document and pin any new runtime dependencies in the existing manifest;
  the recommended initial implementation requires no new runtime packages.
- Suggested branches: dataset-contract, dataset-features, weather-enrichment,
  and dataset-publication. Integrate in order through reviewable pull requests.
- Before each commit, run available static analysis, unit tests, and relevant
  smoke tests; require passing results. Use concise bulleted commit messages.
- Follow the guideline's description/Input/Output docstring convention for new
  functions, keeping individual explanatory comments concise.

## Decisions to resolve during the audit

- Confirm source units and duration semantics before fixing the pace target.
- Select data-quality thresholds and the missing-elevation fallback policy.
- Confirm the weather model and measure the start-location approximation.
- Finalize retention of cache/provenance artifacts and published output tracking.
- Set reproducible split proportions once per-user coverage is measured.

## Completion criteria

- Every published row has the six agreed inputs, one valid pace target, and
  traceable workout metadata. No historical fitness metrics are introduced.
- Accepted/rejected counts reconcile with the candidate population; no final
  row-count promise is made before validation and weather matching.
- A cached rerun is reproducible, bounded in memory, and preserves source files.
- Fixture tests and existing analysis checks pass; bulk execution remains an
  explicit subsequent task after planning.

## External reference

- [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api):
  hourly historical temperature/humidity, reanalysis options, units, and timing.
  Recheck service limits and terms before bulk execution.
