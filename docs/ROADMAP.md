# Paperbark — roadmap

Living document. Update as items land. For project rules and tooling
baseline, see [`CLAUDE.md`](../CLAUDE.md).

## Current state

- **Last verified:** 2026-05-03
- **Latest commit:** `Initial project scaffold and CI` on `main`
- **Repo:** <https://github.com/Good-Native/paperbark>
- **Released:** nothing yet (version stub `0.0.0`)

### Scaffold (done)

- Project metadata in `pyproject.toml` (hatchling, ruff, pytest, mypy);
  console-script entry point `paperbark = paperbark.cli:main`.
- Pre-commit hooks (`ruff`, `ruff format`, `prettier` for md/yaml/json).
- GitHub Actions CI matrix on Python 3.11 / 3.12 / 3.13: `ruff check`,
  `ruff format --check`, `mypy`, `pytest`, `pip-audit`.
- argparse CLI skeleton with `monitor`, `search`, `analyse`, `init`
  subcommands (all stub out with a "not yet implemented" notice and
  exit non-zero).
- Smoke tests so CI is green from day one.
- `LICENSE` (MIT), `CHANGELOG.md` (Keep-a-Changelog), `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1, adopted by reference).

### Open operational notes

- **CI status** for the initial-scaffold push was not verified by the
  session that pushed it. Before starting feature work, run
  `gh run list --limit 3` and fix anything red.
- **Remote uses HTTPS**, not SSH — the user's local SSH identity isn't
  registered against the `Good-Native` org. Pushes go via `gh`'s
  credential helper. Optional follow-up: register an SSH key.
- **Code-of-conduct contact** is a placeholder
  (`conduct@good-native.dev`). Replace before announcing publicly.
- **`uv.lock`** is not committed yet; will be generated and tracked the
  first time `uv sync` runs against real dependencies.

## V1 scope

Feature parity with `~/Documents/GitHub/hover/scripts/logs.sh` and its
helpers, with these architectural changes:

- **TOML-driven config**: `./paperbark.toml` then
  `~/.config/paperbark/config.toml`. Every CLI flag must also be
  expressible as a TOML key. Flags override TOML at runtime.
- **Pluggable `Source` layer**: flyctl-backed source ships in v1;
  `wrangler`, `kubectl`, `cloudwatch`, `file`, `stdin` land as
  interface-conformant stubs but are **not** required for v1 ship.
- **Pluggable `Format` layer**: JSON-keys plus named-group regex with
  presets (`apache-combined`, `nginx-default`, `syslog-rfc5424`).
- **Pure-Python dispatcher / animator** using `rich.live`, replacing the
  bash ticker and banner.
- **Preserve the run-dir layout, finding shape, and probe set** from the
  Hover originals (downstream tooling depends on them).

### CLI surface

```
paperbark                # default = monitor with config defaults
paperbark monitor [...]
paperbark search --keyword X [--regex Y] [--run latest|all|<id>]
paperbark analyse [--run latest|all|<id>] [--keyword X] [--regex Y]
paperbark init           # write a starter paperbark.toml in cwd
```

### Probes (port from Hover)

Severity rollup, panics and fatals, HTTP status, latency
(p50/p95/p99 plus slowest entries), heartbeat (gap detection), process
health, autoscaler events, database/external errors, Sentry events,
plus ad-hoc keyword/regex.

Each finding shape: `{count, first_seen, last_seen, peak}`. Keep the
bounded LRU dedup in the per-app analyser as a safety net on top of
cursor filtering.

Make every probe class config-toggleable. Make probe regex sets
(autoscaler, DB/external, Sentry) config-overridable so a
Cloudflare-Worker user can replace them without forking.

### Output layout (preserve — public contract)

```
logs/YYYYMMDD/HHMM_<slug>_<settings>/
├── <app>/raw/*.log         # cursor-filtered captures
├── <app>/.cursor           # last-seen ISO timestamp
├── snapshots/
│   ├── analysis_<HHMMSS>Z.md
│   └── analysis_<HHMMSS>Z.json
├── analysis.md / analysis.json
└── monitor.log
```

Don't change the shape without a major-version bump (per `CLAUDE.md`).

## Implementation plan

Suggested ordering, smallest and most-tested first:

1. **Port `filter_since.py`** → `src/paperbark/cursor.py`. Small and
   correct. Cover with tests against fixtures from `reference/`.
2. **Port `analyse_logs.py`** → `src/paperbark/probes/`. Split per-probe
   classes; each behind a TOML toggle.
3. **Port `aggregate_logs.py`** → `src/paperbark/aggregate.py`.
4. **Port `process_logs.py`** → `src/paperbark/iteration.py`.
5. **Port `search_logs.py`** → wire into `paperbark search`.
6. **Source interface** (`src/paperbark/sources/__init__.py`) plus the
   flyctl source. Stubs for the others.
7. **Format interface** plus the built-in presets.
8. **Dispatcher and animator** (`rich.live`) replacing `logs.sh`.
9. **`paperbark init`** TOML writer.

Each step lands behind passing CI. Add a `CHANGELOG.md` entry per
user-visible change.

### What to keep vs rebuild

Originals live in `reference/` (also in
`~/Documents/GitHub/hover/scripts/`, MIT-licensed).

| File | Action |
|---|---|
| `analyse_logs.py` | Port directly; well-tested |
| `filter_since.py` | Port directly; small and correct |
| `aggregate_logs.py` | Port directly |
| `process_logs.py` | Port directly |
| `search_logs.py` | Port directly |
| `logs.sh` dispatcher | Rebuild as `argparse` + `rich.live` |
| Bash banner / kv printing | Rebuild with `rich.table` / `rich.panel` |
| Background ticker animator | Rebuild with `threading.Thread` driving `rich.live.Live` |
| Capture loop | Rebuild with `subprocess.Popen` + `concurrent.futures` |

Delete `reference/` once v0.1 ships.

## Gotchas already handled in the bash version

Carry these into the Python port:

- Fly's ANSI-coloured timestamp prefix (`\033[2m2026-…Z\033[0m`) — strip
  before parsing.
- `flyctl logs --no-tail` returns the same recent window every call —
  cursor-filter on the consumer side is mandatory.
- Capture overlap dedup (bounded LRU window) on top of cursor filter as
  a safety net.
- Python child processes catch `KeyboardInterrupt` to exit silently when
  the parent forwards SIGINT through the pipe.
- `dim` SGR (`\033[2m`) renders as a background block in some
  terminals; use bright-black foreground (`\033[90m`) instead.
- VS Code terminal renders Braille spinner glyphs (`⠋⠙⠹…`) too small;
  use rotating quarter-circles (`◐ ◓ ◑ ◒`).

## Out of scope for v1

- Web UI / dashboard.
- Persistent server / agent-mode running as a daemon.
- Cross-run aggregation queries.
- Alerting integrations (Slack, PagerDuty).
- External `Source` plugin loader (interface documented; loader not
  shipped).

## Beyond v1 (parking lot)

- External plugin loader for third-party `Source` and `Format` modules.
- Cross-run search and aggregation queries.
- Optional alert sinks (Slack, PagerDuty).
- Homebrew formula and PyPI release automation.
- `docs/SOURCES.md`, `docs/PROBES.md`, `docs/CONFIG.md` — currently
  referenced from `README.md` and `CLAUDE.md` but not yet written.

## Naming and registries

| Surface | Name | Status |
|---|---|---|
| GitHub | `Good-Native/paperbark` | created, public |
| npm | `@good-native/paperbark` | scope reserved, package free |
| PyPI | `paperbark` | free, not yet reserved |
| Homebrew | `paperbark` | free, not yet reserved |

Reserve PyPI before the first release; reserve Homebrew when a formula
is ready.
