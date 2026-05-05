# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `paperbark monitor` now prints a startup banner to the terminal — bash
  parity with the reference's bracketed `── slug ──` block above the
  ticker. Lists the run dir, configured sources, interval, iterations
  (with duration hint), and snapshot cadence. Rich-styled with a TTY,
  plain ASCII to stderr otherwise. Backed by a new
  `dispatcher.MonitorStart` dataclass and `on_start` callback so other
  consumers can hook in.

### Changed

- `paperbark monitor` no longer emits a stderr warning when a source's
  parse rate falls below 50%. The bash reference never warned, and the
  threshold false-positived on healthy mixed-format sources (apps that
  interleave JSON records with plain keepalives or platform notices).
  The diagnostic line stays in `monitor.log` so genuine silent-failure
  cases can still be traced after the fact.

## [0.1.1] - 2026-05-05

### Added

- `[[sources]]` (flyctl) accepts a `samples` integer (default `400`) that
  caps the number of lines kept from each `flyctl logs --no-tail` window.
  `flyctl logs` itself has no native flag for this (`-n` is the short
  form of `--no-tail`), so the bound is enforced inside `capture()` via
  a bounded `deque` — same behaviour as the bash dispatcher's
  `flyctl logs … | tail -n <samples>` pipe. v0.1.0 quietly used flyctl's
  built-in window (~100 lines), which dropped messages between
  iterations on busy apps.
- `[[sources]]` (flyctl) accepts a `format_keys` table for per-field JSON
  key overrides (`timestamp`, `level`, `message`, `component`). Each value
  may be a string or a list of strings. The iteration parser threads the
  override through `summarise_log_file` / `summarise_lines` so apps that
  emit structured logs under non-default keys parse correctly without
  forking. Non-JSON formats (regex presets) remain on the v0.2 list.
- `[monitor]` gains `cleanup_enabled`, `cleanup_days`, and `cleanup_mode`
  (`"zip"` / `"delete"`); the loop now rotates older run dirs at start,
  matching the bash dispatcher. CLI flags: `--cleanup` / `--no-cleanup`,
  `--cleanup-days N`, `--cleanup-mode {zip,delete}`. `"zip"` archives each
  `<app>/raw/` to a sibling `raw.zip` and removes the per-iter
  `*_iter*.{json,csv}` artefacts; summaries and time-series CSVs are
  preserved. `paperbark.search` already reads `raw.zip` transparently.
- `paperbark monitor` now emits a one-time stderr warning (and a per-iter
  line in `monitor.log`) when a source's parse rate drops below 50% —
  the format-mismatch case where probes downstream see a heavily
  depleted record set with no other diagnostic. Threshold: at least
  five captured lines and ≤50% parsed; smoke-tested live against the
  hover-analysis Fly app (19/100 parsed → warning).
- `paperbark search` now strips ANSI escape sequences from matched lines
  by default so piped/redirected output stays readable. New `--keep-ansi`
  flag preserves them for TTY-aware viewers.
- Per-iteration capture again writes the flat
  `<YYYYMMDDTHHMMSSZ>_iter<N>.csv` side-output alongside the matching
  `.json` (timestamp/level/component/message/extras columns). The
  `iteration` module already supported the sink path; v0.1.0 simply
  never passed it. Bash-parity restoration.
- 24 new tests covering the cleanup pass (`zip`/`delete` modes,
  retention window, idempotency, missing-root no-op, invalid mode,
  zip-content verification), parse-rate warning, `samples` knob,
  `format_keys` validation, search ANSI handling (default + opt-out +
  TOML drives + `--no-keep-ansi` clears TOML).

### Changed

- Per-iteration filenames revert to the bash-dispatcher shape:
  `<YYYYMMDDTHHMMSSZ>_iter<N>.{log,json,csv}` (timestamp first, no
  zero-padded iter index). v0.1.0 used `iter_<NNNN>_<YYYYMMDDTHHMMSSZ>`,
  which silently broke downstream tools that relied on the documented
  run-dir contract. The `<HHMMSS>Z` snapshot suffix in `snapshots/` is
  unchanged.
- The `External errors and timeouts` probe heading replaces
  `Database / external`. The toggle key stays `database` (config
  back-compat) but the heading matches what the default pattern set
  actually catches: generic Go context timeouts and outbound HTTP
  failures, not just DB driver errors. Pattern set is unchanged; users
  who want a DB-only matcher can override under
  `[probes.patterns].database`.
- `FlyctlSource.capture()` now buffers flyctl's output through a
  `deque(maxlen=samples)` and yields the last N lines, matching
  `reference/logs.sh`'s `flyctl logs … | tail -n $SAMPLES` pipe.
  `samples` defaults to `400`.

### Fixed

- `paperbark monitor` would silently swallow format mismatches: a source
  whose every line failed JSON parsing showed up as healthy (`summary.md`
  rendered with `Parse success rate: 0.0%` but every probe section read
  `(no matches)` with no other signal). The dispatcher now surfaces a
  warning once per affected app per run.

## [0.1.0] - 2026-05-04

### Added

- `paperbark.probes.default_probes` now honours
  `paperbark.config.ProbesConfig`. `[probes]` toggles drop the named
  probe from the constructed set, `[probes].keywords` and
  `[probes].regexes` fold into the trailing `Ad-hoc keywords` bucket
  alongside any `--keyword` / `--regex` extras, and
  `[probes.patterns].<probe>` entries replace the built-in regex set
  for that probe (overrides do not extend — copy the defaults across to
  extend). Threaded through `paperbark.analyse` and the dispatcher's
  snapshot path so both one-shot analyse runs and monitor snapshots
  pick up the configured set. Previously `[probes]` was parsed and
  validated but never read at runtime, so the user-visible behaviour
  matched only the documented surface in `docs/CONFIG.md`. Eleven new
  tests in `tests/test_probes_config.py`.
- `docs/PROBES.md`: probe-layer reference. Documents the `Probe`
  Protocol, the canonical record, every built-in probe (toggle name,
  default regex set or finding shape), the `[probes]` /
  `[probes.patterns]` surface, and the four-step "add a new probe"
  walkthrough. README now links to it instead of marking it
  forthcoming.

### Changed

- Retired the `reference/` bash originals (formerly carried as a port
  source under MIT). The migration is complete; the port lives in
  `src/paperbark/`. Ruff's `extend-exclude = ["reference"]` is gone with
  it. `docs/ROADMAP.md` updates the "what was kept vs rebuilt" table to
  past tense.
- `CODE_OF_CONDUCT.md`: replace the placeholder enforcement address with
  `support@goodnative.co`.

### Documentation

- `docs/SOURCES.md`: `Source` interface reference. Documents the Protocol
  contract (name attr, `capture(*, since="")`, no per-call state), the
  mandatory cursor-filter chokepoint, the registry, the built-in source
  list (real flyctl + five stubs), per-source options, and a step-by-step
  walkthrough of adding a new source (module → registry → dispatcher
  branch → docs → tests). README now links to it instead of marking it
  forthcoming. New `tests/test_docs_sources.py` round-trips every
  ` ```toml ` block in the doc through `from_dict` so doc examples can't
  drift from the loader.
- `docs/CONFIG.md`: full TOML schema reference. Documents discovery order,
  override semantics, duration-string grammar, every key under
  `[paperbark]` / `[monitor]` / `[analyse]` / `[search]` / `[probes]` /
  `[probes.patterns]` / `[[sources]]`, the validation surface, the
  run-dir layout contract, and four worked examples. README now links to
  it instead of marking it forthcoming.

### Fixed

- `paperbark analyse`: `--stdout` is now `argparse.BooleanOptionalAction`,
  so a `[analyse].stdout = true` in TOML can be cleared at the CLI with
  `--no-stdout`. The previous `store_true`/`default=None` shape only let
  the flag re-affirm `true`, breaking the documented "flags override TOML
  at runtime" contract for that field.
- `paperbark search`: `--ignore-case` is now wired through. Pre-fix it set a
  separate `args.ignore_case` dest that `paperbark.search.run` never read,
  so the flag was inert; that became user-visible once
  `[search].case_sensitive` landed in the TOML loader (a TOML `true` plus a
  CLI `--ignore-case` would have left matching case-sensitive). The CLI now
  exposes `--ignore-case` and `--case-sensitive` as a mutually exclusive
  pair sharing the `case_sensitive` dest, with a parser-level `default=None`
  so either flag overrides the TOML value at runtime.

### Added

- `paperbark.config`: new `[analyse]` and `[search]` tables. `AnalyseConfig`
  and `SearchConfig` carry every CLI flag of their respective subcommands as
  TOML keys (`run`, `app`, `keywords`, `regexes`, `out`, `stdout` for
  analyse; `run`, `app`, `keywords`, `regexes`, `case_sensitive`, `max` for
  search). CLI flags override TOML values at runtime; `--root` overrides
  `[paperbark].root`. The `paperbark init` starter template documents both
  sections at their default values, and a TOML-supplied `[search].keywords`
  now drives matching with no `--keyword` flag required (previously search
  exited 2 in that scenario). Search `--max` validation matches the TOML
  loader (`>= 0`; `0` = unlimited). Thirty-one new unit tests across
  `tests/test_config.py`, `tests/test_cli_analyse.py`,
  `tests/test_cli_search.py`.
- Repository-wide `.gitattributes` (LF normalisation) so prettier and ruff
  don't rewrite every text file on Windows checkouts.
- `paperbark monitor` is now long-running. The dispatcher's
  `run_monitor_loop` repeats `run_iteration` on a fixed cadence until the
  iteration cap is reached or the user interrupts; SIGINT flips a
  `threading.Event` so the in-flight iteration finishes cleanly, the
  final aggregate + analyse still write, and exit 130 propagates. Snapshot
  analyses fire every `analyse_every` seconds into
  `<run>/snapshots/analysis_<HHMMSSZ>.{md,json}` (set 0 to disable). Every
  significant event lands in `<run>/monitor.log`. New CLI flags:
  `--interval`, `--iterations`, `--run-id`, `--analyse-every`. All four
  mirror the `[monitor]` TOML section so config and flags express the
  same surface; flags override TOML at runtime.
- `paperbark.animator`: `rich.live`-driven ticker that renders elapsed
  time, iteration counter, captured-line total, and time-until-next
  snapshot. Spinner uses the bash dispatcher's `◐ ◓ ◑ ◒` quarter-circles
  (Braille glyphs render too small in some terminals). Pure
  `render_status` lets tests pin the line without a TTY; the
  `MonitorAnimator` context manager owns the redraw thread and ticks
  elapsed/snapshot fields between state publishes so the line stays
  alive during slow flyctl captures. Eight unit tests.
- `paperbark.duration`: shared `parse_duration` /
  `format_elapsed` helpers consumed by the loop, the animator, and the
  `[monitor]` config section. Accepts the same shorthand as the bash
  dispatcher (`30s`, `5m`, `1h`, plain seconds); rejects combined forms.
  Twelve parse cases plus elapsed-format coverage.
- `paperbark.config`: new `[monitor]` table. `MonitorConfig` carries
  `interval`, `iterations`, `analyse_every`, `run_id` with defaults that
  match `reference/logs.sh` (3s cadence, 1440 iterations, 5-minute
  snapshots, auto-generated slug). `run_id` is validated against the
  same path-safety regex as the bash dispatcher.
- `paperbark.dispatcher`: `random_slug()` (auto-generated
  `<adjective>-<colour>` run identifiers) and `settings_suffix()` (the
  `<interval>_<duration>` half of the run-dir name). Both ported from
  `reference/logs.sh`; pools and rounding match the bash exactly so a
  Python-emitted run name lines up with anything downstream tooling
  built around the bash version.
- `paperbark.analyse`: `paperbark analyse` is now wired end to end. Replays
  every captured raw line through `paperbark.probes.parse_line` and the
  `default_probes()` set, then writes `analysis.json` and `analysis.md`
  at the run root (or at `--out <base>` when supplied). Reuses
  `paperbark.search.resolve_runs` and `paperbark.search.iter_lines` for
  run discovery and capture reading. Carries the reference's bounded LRU
  dedup window (50,000 lines) as a safety net on top of cursor filtering.
  CLI flags: `--run`, `--root`, `--app`, repeatable `--keyword` /
  `--regex`, `--out`, `--stdout` (matches the reference contract).
  `--run all` writes one report per run; `--run all --out <base>` is
  rejected with exit 2. `KeyboardInterrupt` → exit 130. Fifteen unit
  tests covering JSON shape, probe wiring, ad-hoc keyword bucketing,
  `--out` redirection, multi-run loops, app filter, dedup, and CLI
  dispatch.
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

- `paperbark.dispatcher`: `build_source` now raises `DispatcherError`
  for any key in `SourceConfig.options` the target type doesn't
  recognise (mirrors the existing missing-`app` pattern). Previously
  unknown keys were silently dropped, so a typo like `[[sources]] appp
= "..."` was a quiet no-op. `docs/SOURCES.md` drops the "silently
  dropped" caveat. Three new dispatcher tests cover the flyctl typo,
  alphabetical ordering of multiple offenders, and stub-source
  rejection.
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
