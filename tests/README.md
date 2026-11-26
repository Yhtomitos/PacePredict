# Tests and analysis tools

Tool configuration and pinned development dependencies live in `tests/tooling/`.
Semgrep rules and their deliberately unsafe fixtures live in `tests/semgrep/`.
GitHub requires workflow entry points under `.github/workflows/`.

| Check | Configuration | Purpose |
| --- | --- | --- |
| Ruff | `tooling/ruff.toml` | Lint, imports, Pyflakes, Bugbear, complexity (maximum 10), formatting |
| Pyright | `tooling/pyrightconfig.json` | Static type checks targeting Python 3.10 |
| Bandit | `tooling/bandit.toml` | Filesystem, parsing, subprocess, and Python security checks |
| Semgrep | `semgrep/invariants.yml` | Custom data-processing and archive rules |
| pip-audit | `tooling/requirements.txt` | Vulnerabilities in resolved runtime and development dependencies |
| CodeQL | `tooling/codeql.yml` | Python security-extended queries in GitHub |

## Local checks

From the repository root, create a virtual environment:

```console
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` in PowerShell or
`source .venv/bin/activate` on Linux/macOS. Then run:

```console
python -m pip install -r tests/tooling/requirements.txt
python -B -m unittest discover -s tests -v
python -m ruff check --config tests/tooling/ruff.toml main.py scripts tests
python -m ruff format --config tests/tooling/ruff.toml --check main.py scripts tests
python -m pyright --project tests/tooling/pyrightconfig.json
python -m bandit -c tests/tooling/bandit.toml -r main.py scripts tests
python -m pip_audit -r tests/tooling/requirements.txt --strict
```

The processor fixture tests cover raw filtering, workout-ID joins, summary
units, null handling, malformed and oversized records, gzip truncation,
determinism, source protection, and temporary-file cleanup. They never modify
real datasets. Shared synthetic records live in `workout_fixtures.py`.
Legacy joined migration has separate fixtures in `test_flatten_workouts.py`.
The independent legacy validators remain available for existing archives;
they do not validate schema-version-3 summaries.

For the full pipeline, run one command from the repository root:

```console
python -m scripts.endomondo_process_dataset
```

Defaults read `raw/endomondoHR.json.gzip` and `raw/endomondoMeta.json.gzip`
under `training-data/model-training/` and create
`processed/final/planned-workouts.json.gz` plus its build manifest.
Override paths with `--hr`, `--meta`, and `--output`. Both raw inputs must be
gzip dictionary-lines archives. Only `sport == "run"` is selected in both;
treadmill records are excluded. Matching uses workout IDs and verifies user
IDs, with Meta precedence for overlapping fields. A temporary SQLite index
keeps the join out of memory and is removed on success or failure. No filtered
or joined intermediate archives are retained. Raw files and existing outputs
are protected; choose a new output path to rebuild.

`test_raw_workout_pipeline.py` tests the full pipeline with tiny raw fixtures,
including counts, filtering, matching, duplicate IDs, source preservation,
determinism, truncation, and cleanup. Legacy joined migration remains available:

```console
python -m scripts.endomondo_process_dataset training-data/model-training/processed/final/workouts.json.gz --output training-data/model-training/processed/final/planned-workouts.json.gz
```

Summary fields are `workout_id`, `user_id`, `started_at_utc`, `distance_miles`,
`latitude`, `longitude`, `elevation_gain_m`, `elevation_loss_m`,
`average_heart_rate_bpm`, `temperature_c`, `relative_humidity_pct`, and
`pace_min_per_mile`. Coordinates are the first valid paired HR coordinates;
start time is the first HR timestamp converted to UTC. Source distance is
interpreted as kilometres, duration as seconds, and ascent/descent as metres.
Invalid/missing features and targets remain null; rows are not dropped.
Heart rate requires aligned, positive samples and strictly increasing times;
its trapezoidal average includes recording gaps without gap correction.
Weather remains null for separate enrichment. Measured average heart rate is
retrospective and must not be assumed available before a planned run.
Full arrays stay in the input archive when using a distinct output path.

Keep application-code fixes on their relevant feature branch. This test branch
is based on the filtering branch so these checks can exercise its current code.
CI checks formatting; it does not rewrite source files.

`tooling/runtime-requirements.txt` is currently empty because the runtime uses
only Python's standard library. Add pinned runtime packages there when needed;
the development manifest includes it so pip-audit also checks those packages.

## Semgrep

Use a separate Linux environment, including WSL, for Semgrep:

```console
python3 -m venv .venv-semgrep
source .venv-semgrep/bin/activate
python -m pip install -r tests/tooling/requirements-semgrep.txt
semgrep --test tests/semgrep --metrics=off
semgrep scan --config tests/semgrep/invariants.yml --error --metrics=off --no-git-ignore --exclude tests/semgrep --exclude training-data main.py scripts tests/*.py
```

The five rules detect executable parsing, unsafe deserialization, unbounded reads
inside gzip contexts, bulk archive extraction, and opening an input path for
writing. Rules cover their tested explicit patterns, not every possible alias
or data-flow variant. Runtime validation is still needed.

`semgrep/invariants.py` contains deliberately unsafe positive examples and safe
negative examples. Never execute or import it. Ruff, Pyright, Bandit, CodeQL,
and normal Semgrep scans exclude it; `semgrep --test` validates the annotations.
The CLI subprocess test uses narrow Bandit exceptions for its fixed local
interpreter invocation; subprocess checks remain active elsewhere.

Semgrep is installed separately from the audited runtime/development toolchain.
A local audit of Semgrep 1.182.1 found advisories in its upstream-pinned
`mcp==1.29.0` and `opentelemetry-api~=1.37.0` dependencies. Their patched releases
are outside Semgrep's declared constraints. These advisories are not suppressed
in pip-audit; that CI audit targets the runtime/development manifest, not this
separate security-tool environment. Dependabot tracks new Semgrep releases.

## GitHub Actions

- `checks.yml` runs fixture tests on Python 3.10, 3.12, and 3.14, plus quality
  and Semgrep jobs on Python 3.12. Findings fail their checks.
- `codeql.yml` runs security-extended Python queries and uploads code-scanning
  alerts. Findings are reviewed in GitHub's Security tab; repository code-scanning
  protection can be configured to block merges with outstanding alerts.
- Both workflows run on pushes, pull requests, manual dispatch, and weekly
  schedules. Scheduled runs use the default branch.
- Checkouts skip Git LFS downloads and do not persist credentials. Actions are
  pinned by commit SHA. Dependabot proposes weekly dependency/action updates.
- The workflows activate once committed and pushed. CodeQL needs code scanning
  enabled for this repository; its advanced setup must not conflict with an
  existing CodeQL default setup. Set required checks in repository branch rules
  if merges must wait for CI.

References: [Ruff](https://docs.astral.sh/ruff/),
[Pyright](https://github.com/microsoft/pyright/blob/main/docs/configuration.md),
[Bandit](https://bandit.readthedocs.io/),
[Semgrep rule tests](https://semgrep.dev/docs/writing-rules/testing-rules),
[pip-audit](https://github.com/pypa/pip-audit),
[CodeQL](https://github.com/github/codeql-action).
