# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial project scaffold: `pyproject.toml` (hatchling, ruff, pytest, mypy),
  pre-commit configuration, GitHub Actions CI matrix on Python 3.11/3.12/3.13,
  argparse-based CLI skeleton (`monitor`, `search`, `analyse`, `init`),
  smoke test, MIT licence, contributor guide, and Contributor Covenant 2.1
  code of conduct.
- `paperbark.cursor`: cursor-based dedup filter (port of
  `reference/filter_since.py`). Strips ANSI prefixes, keeps lines newer than
  a stored cursor, preserves multi-line records when their header is kept,
  and persists the new cursor only when it advances. Eleven unit tests.
- `paperbark.aggregate`: time-series rollup ported from
  `reference/aggregate_logs.py`. `merge_iteration` is pure (input
  payload + state in, mutated state out); `aggregate(run_dir)` orchestrates
  fingerprinted incremental ingestion (mtime+size), atomic state save via
  `.aggregate_data.json`, and the four CSV / markdown outputs (time
  series, events per minute, components per minute, summary). Detects
  rewritten files (same name, new fingerprint) and forces a cold rebuild
  rather than double-counting. Sixteen unit tests.
- Runtime dependency on `tzdata` for Windows targets so
  `zoneinfo.ZoneInfo("Australia/Melbourne")` resolves without a system
  zoneinfo database.
- `paperbark.probes`: probe layer ported from `reference/analyse_logs.py`.
  Adds `CanonicalRecord` plus a `parse_line` mapper (the format-layer
  boundary), a `Bucket` accumulator, and nine probe classes one file each:
  `SeverityProbe`, `PanicProbe`, `HTTPStatusProbe`, `LatencyProbe`,
  `HeartbeatProbe`, plus `RegexBucketProbe` for the four regex-driven
  probes (Process health, Autoscaler, Database / external, Sentry) and
  ad-hoc keyword/regex terms. `default_probes()` returns the full set in
  reporting order; per-probe TOML toggles will land with the config layer.
  Forty-five unit tests.

### Changed

- CI: pin `UV_PYTHON` per matrix entry and pass `--all-extras` to every
  `uv run` so dev dependencies survive `uv run`'s implicit re-sync.
- CI: audit the exported requirements file (`uv export --no-emit-project`)
  rather than the editable install, so `pip-audit --strict` does not fail
  on the project's own unreleased package.
- Ruff: exclude `reference/` from lint and format checks; the directory
  carries pre-port scripts that will be deleted before v0.1.

### Fixed

- CI: commit `uv.lock` so `astral-sh/setup-uv@v3` can resolve its cache
  dependency glob.
