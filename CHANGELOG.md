# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `paperbark.dispatcher`: composes source → cursor filter → iteration
  summary → aggregate end to end. `build_source(spec)` and
  `build_sources(config)` resolve `SourceConfig` entries to `Source`
  instances (real `flyctl`, stubs for the rest); `new_run_dir(root)`
  creates `<root>/<YYYYMMDD>/<HHMM>/`; `capture_iteration(...)` runs one
  capture, dedupes against `<app>/.cursor`, writes the raw log and the
  iteration JSON; `run_iteration(...)` coordinates across every built
  source and refreshes per-app aggregate output; `run_monitor(config)`
  is the top-level one-iteration entry. Twenty unit tests; injectable
  clock and source-list keep all paths deterministic.
- `cli.main` now dispatches the `monitor` subcommand. Loads the TOML
  config (explicit `--config` or discovery), runs one iteration, and
  prints the run directory. `ConfigError` and `DispatcherError` surface
  as exit 2 with a single-line stderr message; `KeyboardInterrupt` →
  exit 130. Iteration loop and `rich.live` ticker land in the next PR.
- Initial project scaffold: `pyproject.toml` (hatchling, ruff, pytest, mypy),
  pre-commit configuration, GitHub Actions CI matrix on Python 3.11/3.12/3.13,
  argparse-based CLI skeleton (`monitor`, `search`, `analyse`, `init`),
  smoke test, MIT licence, contributor guide, and Contributor Covenant 2.1
  code of conduct.
- `paperbark.cursor`: cursor-based dedup filter (port of
  `reference/filter_since.py`). Strips ANSI prefixes, keeps lines newer than
  a stored cursor, preserves multi-line records when their header is kept,
  and persists the new cursor only when it advances. Eleven unit tests.
- `paperbark.search`: search subcommand ported from
  `reference/search_logs.py` (PR #1). `resolve_runs` maps the `--run`
  selector (`latest` / `all` / date / prefix) to one or more run
  directories, with fail-closed handling of empty / stripped-empty
  selectors. `iter_lines` reads both `<app>/raw/*.log` and
  `<app>/raw.zip` and tolerates corrupt archives or unreadable members
  with a stderr warning rather than aborting the whole run.
  `search_runs` prints matches with `[run][app][source]` prefixes and
  emits per-app/per-run/global counts on stderr. Run-dir discovery is
  restricted to the canonical `HHMM_*` shape so stray sibling
  directories don't poison `--run latest`. CLI gains repeatable
  `--keyword` / `--regex`, `--app` (comma list), `--root`,
  `--ignore-case` / `--case-sensitive`, `--max`. `cli.main` now
  dispatches the `search` subcommand (and maps `KeyboardInterrupt` to
  exit 130); `monitor` / `analyse` / `init` remain stubs. Twenty-seven
  unit tests.
- `paperbark.formats`: format layer with `Format` Protocol, configurable
  `JsonKeysFormat` (default Fly-style JSON keys, custom keys for non-Fly
  producers), and `RegexFormat` for named-group line shapes with
  optional `strptime` timestamps. Three presets bundled:
  `apache-combined`, `nginx-default`, `syslog-rfc5424` (the syslog
  preset derives level from RFC 5424 priority severity). Thirteen unit
  tests covering protocol conformance, custom keys, named-group
  extraction, and each preset against canonical example lines.
- `paperbark.sources`: source layer with `Source` Protocol and registry.
  Real implementation for `flyctl` (subprocess wrapping `flyctl logs
--no-tail`); injectable runner for testability. Stubs for `wrangler`,
  `kubectl`, `cloudwatch`, `file`, `stdin` — each conforms to the
  Protocol and raises `NotImplementedError` on `capture()`. Twelve unit
  tests.
- `paperbark.iteration`: per-iteration log processor ported from
  `reference/process_logs.py`. `summarise_lines(iter)` is pure (no I/O,
  feeds optional flat-row sink); `summarise_log_file(raw_path)` adds
  the file plumbing and optional flat-CSV side-output. Output shape is
  the contract `paperbark.aggregate.merge_iteration` consumes — verified
  by a round-trip test. Thirteen unit tests.
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

- `paperbark.search`: normalise run-prefix output and `--run` selector
  comparison to forward slashes so Windows and POSIX produce identical
  matched-line prefixes, and a Windows operator passing
  `20260503\1430` matches the same runs as `20260503/1430`.
- `paperbark.cli`: refresh module docstring — `search` is now a real
  dispatch, no longer a pure scaffold.
- `README.md`: status line now points at `docs/ROADMAP.md` instead of
  saying "Scaffold only", and the source table reflects landed
  flyctl + the five remaining stubs.

### Changed (earlier)

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
