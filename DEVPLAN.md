# PacePredict: two-model research development plan

Status: planning only for modelling. The complete workout join exists; neither
model has been implemented, trained, or evaluated. Preserve both tracks.

This plan governs current modelling decisions. Dataset build history remains
in [DEVPLAN_Process_Dataset.md](DEVPLAN_Process_Dataset.md). The portable
manuscript handoff is [INTERIM_RESEARCH_CONTEXT.txt](INTERIM_RESEARCH_CONTEXT.txt).

## Objective

Compare two approaches for personalized running pace estimates and prediction
intervals using workout context: temperature, humidity, elevation gain/loss,
distance, and usable historical heart-rate information. Average route elevation
is an additional candidate retained from the earlier schema.

The learned feature represents workout context, not a validated fitness score,
effort category, or perceived-exertion label. The goal is narrower prediction
intervals at comparable measured coverage, not narrow ranges in isolation.

## Separate model tracks

| Track | Planned approach | Advantages | Costs / technical debt |
| --- | --- | --- | --- |
| A | Distance-weighted nearest-neighbor regression using scaled Euclidean distance | Transparent similar-run examples; direct local weighting; simple baseline | Sensitive to feature scaling, distance weights, sparse history, and outliers; interval calibration required |
| B | Gaussian-mixture workout-context representation followed by tree-based regression | Soft context memberships; models interactions downstream | Cluster selection/stability and inference design add complexity; interval calibration required |

Recommended sequence: implement A first as a transparent benchmark, then B.
This is a development order, not a decision to discard either model.

The user described B as "Gaussian clustering + random tree regression".
Gaussian clustering is provisionally interpreted as a Gaussian mixture model
(GMM). The exact tree estimator is not settled: random forest regression is a
provisional recommended implementation, not a confirmed replacement for a
single randomized tree or an Extra Trees ensemble. Confirm before implementing.

## Shared foundation and leakage constraints

- Start from `training-data/model-training/processed/final/workouts.json.gz`:
  117,902 matched outdoor workouts, with complete `hr` and `meta` dictionaries.
- Audit/clean distance, duration, sensor quality, route geometry, elevation,
  duplicates, missing values, and source units. Freeze explicit thresholds.
- Set a single target pace definition; Meta duration pause semantics remain
  unverified. Recorded completed distance is only a proxy for a planned route.
- Enrich weather separately through Open-Meteo using coordinates and UTC time.
  Preserve cache/provenance and apply bounded exponential backoff on rate limits.
- Derive heart-rate summaries with sampling/time alignment and signal-quality
  checks; do not assume raw BPM is directly comparable across runners.
- Define a recent-history window and minimum usable history from validation;
  28 days is a candidate, not an established or mandatory setting.
- User ID is a grouping/lookup key, not a numeric fitness feature. Do not
  assume workouts from different runners with similar conditions imply the
  same achievable pace.
- Use only data available before the target workout starts in the primary
  prospective task. Its measured heart rate, actual pace, duration, and final
  elapsed time are unavailable inputs. Do not match neighbors using target pace.
- Keep actual target-workout HR experiments explicitly retrospective if studied.
  They must not be reported as pre-run prediction performance.
- Earlier exclusion of vague historical averages is superseded only by explicit,
  time-bounded, missing-aware history definitions for these model experiments.

## Actionable development tasks

### 1. Freeze the experiment contract and prepare common features

- Document pre-run inference inputs and which heart-rate representation is
  available for both tracks. A prior-workout HR profile is the proposed default;
  do not silently substitute unknown target-workout HR.
- Specify HR units, time weighting, quality rejection, history cutoffs, route
  elevation definitions, weather matching, missingness, and fallback behaviour.
- Create a shared feature builder and data manifest so both models use the same
  accepted cohort, target definition, outer splits, and information budget.
- Audit per-user and per-date coverage before choosing history requirements.
- Tests: cutoff boundaries, no target/future-row access, weighted summaries,
  unit conversions, missing history, malformed sensors, and deterministic rows.
- Smoke test: joined fixture plus mocked weather yields known as-of features.

### 2. Implement Model A: weighted workout similarity

- Standardize or robustly scale candidate features using training data only.
- Compare consistently defined pre-run feature vectors for historical training
  cases and the query. Include only the available historical HR profile or an
  explicitly supplied intended HR variable with matching training semantics.
- Use weighted Euclidean distance `d_i = sqrt(sum_j a_j (z_j-z_ij)^2)`.
- Select eligible earlier workouts, initially from the same runner, and compute
  an inverse-distance-weighted pace estimate. Handle exact matches explicitly.
- Tune neighbor count/radius, feature weights, optional recency weights, and
  sparse-history fallback using validation data only.
- Estimate a candidate range from weighted neighbor pace quantiles; calibrate
  and evaluate it rather than calling raw quantiles a guaranteed interval.
- Record neighbor IDs, distances, and normalized contributions for explanation.
- Tests: scaling, distance calculations, zero-distance ties, weights summing to
  one, no eligible neighbors, chronological eligibility, and interval ordering.
- Smoke test: known fixture neighbors yield the expected weighted pace/range.

### 3. Implement Model B: GMM context feature + tree regression

- Fit a scaled GMM on eligible training workouts using observed historical HR,
  environmental conditions, distance, elevation gain/loss, and optional mean
  elevation. Do not include the target pace as a clustering input.
- Treat membership probabilities as candidate context features. They express
  model membership, not calibrated effort/fitness probabilities.
- Resolve prospective inference explicitly. Recommended starting design: assign
  contexts to prior completed workouts, then summarize recent memberships for
  each target runner as of prediction time. Combine that profile with the planned
  route/weather inputs in the regressor. Never use the target's observed HR to
  assign its prospective context.
- Fit the selected tree regressor; provisionally compare random forest with the
  same regressor without GMM features. Preserve the tree-estimator decision above.
- Tune GMM component count, covariance structure, regularization, history
  aggregation, and tree parameters in inner training/validation folds.
- Compare soft membership features with hard cluster assignments as an ablation.
- Add a separately calibrated prediction interval. Spread across forest trees
  alone must not be presented as a validated future-outcome interval.
- Tests: reproducible fits, membership dimensions/sums, transform without refit,
  absent history, target/future HR exclusion, and persisted pipeline consistency.
- Smoke test: train and predict on a tiny fixture without real data or API calls.

### 4. Evaluate both tracks fairly

- Primary task: future workouts for existing runners using chronological
  train/validation/calibration/test partitions with sufficient prior history.
- Secondary task: user-disjoint evaluation for unseen runners, documenting the
  information actually available and the separate sparse-history fallback.
- Fit imputers, scaling, GMMs, neighbor indexes, regressors, and calibration only
  on their assigned training/calibration partitions. Use forward validation;
  do not tune choices on the final test set.
- Compare simple median/recent-history pace baselines, A, B, and tree regression
  on the same inputs without GMM. These are controls, not replacement tracks.
- Ablate weather, terrain, HR history, recency, and GMM features individually.
- Report MAE/RMSE, median absolute error, per-user errors, prediction-interval
  coverage/width, interval score, and sparse-history fallback frequency.
- Choose nominal coverage levels in advance (80%/90% are candidates). Compare
  widths only alongside coverage, including weather, terrain, and user subgroups.
- Report cluster stability and association with weather/user identity; a high
  silhouette score does not establish improved pace prediction.
- Account for repeated workouts and runner dependence in uncertainty estimates.
  Time dependence/distribution shifts limit simple calibration guarantees.
- Tests: disjoint partitions, reproducible splits, coverage/width calculations,
  no preprocessing fit on held-out data, and subgroup-count reconciliation.
- Smoke test: one fixed miniature experiment produces reproducible metrics.

### 5. Reproducibility, CI, and interim reporting

- Develop shared preparation, Model A, Model B, and evaluation on separate
  feature branches. Never implement directly on `main`.
- Keep tests/configuration in `tests/` and extend the existing GitHub Actions.
  Normal CI uses fixtures and mocked weather, not bulk archives or network calls.
- Retain Ruff, Pyright, Bandit, Semgrep, pip-audit, and CodeQL. Pin modelling
  dependencies when introduced; no new package is installed by this plan.
- Likely dependencies: scikit-learn plus its numerical dependencies. Select
  supported versions and document any additional interval-calibration package.
- Save split IDs, source hashes, parameters, seeds, feature versions, fitted
  preprocessing, model artifacts, and evaluation manifests. Avoid duplicate data.
- Distinguish completed dataset engineering from proposed model development in
  the interim paper. Do not invent performance, significance, or novelty claims.
- Preserve both tracks even if an initial experiment favours one; document the
  comparison and reasons for later changes rather than silently dropping a track.

## Research handoff and decisions still open

- `INTERIM_RESEARCH_CONTEXT.txt` is self-contained context for another session;
  it includes verified dataset facts, proposed methods, references, and caveats.
- Confirm tree estimator, prediction timing/HR representation, history window,
  quality thresholds, feature weighting, GMM size, interval calibration, and
  conference venue/template before treating them as settled methodology.

## Official implementation references

- https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.KNeighborsRegressor.html
- https://scikit-learn.org/stable/modules/generated/sklearn.mixture.GaussianMixture.html
- https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html
