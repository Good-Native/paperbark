# Paperbark — roadmap

Living document. Update as items land. For project rules and tooling
baseline, see [`CLAUDE.md`](../CLAUDE.md).

## Current state

- **Last verified:** 2026-05-04
- **Latest commit on `main`:** `Reject unknown source option keys (#12)`
  (`a99953b`). PR #13 (in review) adds `docs/PROBES.md`, wires
  `[probes]` toggles and `[probes.patterns]` overrides through to the
  runtime, and retires `reference/`.
- **Repo:** <https://github.com/Good-Native/paperbark>
- **Released:** nothing yet (version stub `0.0.0`).
- **Tests:** 380 passing across 27 test modules; CI has been green on
  every push since the `Land uv.lock and unblock CI` change.

### Implementation status

| #   | Step                                                        | Status                                                  |
| --- | ----------------------------------------------------------- | ------------------------------------------------------- |
| 1   | Port `filter_since.py` → `paperbark.cursor`                 | ✅ done                                                 |
| 2   | Port `analyse_logs.py` → `paperbark.probes/`                | ✅ done                                                 |
| 3   | Port `aggregate_logs.py` → `paperbark.aggregate`            | ✅ done                                                 |
| 4   | Port `process_logs.py` → `paperbark.iteration`              | ✅ done                                                 |
| 5   | Port `search_logs.py` → `paperbark.search` (wired into CLI) | ✅ done (PR #1)                                         |
| 6   | Source interface + flyctl source (stubs for the rest)       | ✅ done                                                 |
| 7   | Format interface + built-in presets                         | ✅ done                                                 |
| 8   | Dispatcher and animator (`rich.live`) replacing `logs.sh`   | ✅ done — long-running loop + `rich.live` ticker landed |
| 9   | `paperbark init` TOML writer                                | ✅ done (PR #6)                                         |
| 10  | Wire `paperbark analyse` over captured runs                 | ✅ done (PR #7)                                         |

An end-to-end live `paperbark monitor` run is now wired: the loop
captures on a fixed cadence, fires snapshot analyses every
`analyse_every` seconds, swaps in the `rich.live` ticker on a TTY
(plain progress lines on non-TTY), and writes the final analysis at
the run root when the loop ends. The `.gitattributes` LF baseline
landed direct-to-`main` in `644a4f4`. PR #9 threaded `[analyse]` and
`[search]` through the TOML loader, so every CLI flag for those
subcommands is also a TOML key. PRs #10 and #11 filled in
`docs/CONFIG.md` and `docs/SOURCES.md`; PR #13 (in review) adds
`docs/PROBES.md`, wires `[probes]` toggles and `[probes.patterns]`
overrides through to the runtime, and retires `reference/`. Remaining
shortlist after PR #13: release prep (PyPI reservation, version bump
from `0.0.0`, Homebrew formula).

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

- **`tzdata`** is now a hard runtime dep on Windows so
  `zoneinfo.ZoneInfo("Australia/Melbourne")` resolves without the system
  zoneinfo database. No-op on Linux/macOS where the OS already ships it.
- **Remote uses HTTPS**, not SSH — the user's local SSH identity isn't
  registered against the `Good-Native` org. Pushes go via `gh`'s
  credential helper. Optional follow-up: register an SSH key.
- **Direct-to-main commits before PR #1** never went through the
  CodeRabbit bot (only the search PR did). The CLI is installed in
  WSL; running `coderabbit review --type committed --base-commit
bf4af64 --config CLAUDE.md` from inside the repo will surface any
  findings on those seven commits without re-opening retroactive PRs.
- **Workflow going forward**: branch + PR per step (matches
  `CONTRIBUTING.md`), so the bot catches issues before they land on
  `main`.

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

1. ~~Port `filter_since.py` → `src/paperbark/cursor.py`.~~ Done.
2. ~~Port `analyse_logs.py` → `src/paperbark/probes/`. Split per-probe
   classes; each behind a TOML toggle.~~ Done. Per-probe TOML toggles
   still pending — they land with the config layer in step 8.
3. ~~Port `aggregate_logs.py` → `src/paperbark/aggregate.py`.~~ Done.
4. ~~Port `process_logs.py` → `src/paperbark/iteration.py`.~~ Done.
5. ~~Port `search_logs.py` → wire into `paperbark search`.~~ Done (PR #1).
6. ~~Source interface (`src/paperbark/sources/__init__.py`) plus the
   flyctl source. Stubs for the others.~~ Done.
7. ~~Format interface plus the built-in presets.~~ Done.
8. **Dispatcher and animator** (`rich.live`) replacing `logs.sh`.
   Lands the TOML config loader (`./paperbark.toml` →
   `~/.config/paperbark/config.toml`), wires `monitor` and `analyse`
   subcommand dispatch into `cli.main`, and composes
   source → cursor filter → iteration → aggregate → probes end to end.
9. **`paperbark init`** TOML writer (template with every key the
   config layer recognises).

Each step lands behind passing CI. Add a `CHANGELOG.md` entry per
user-visible change.

### What was kept vs rebuilt

The bash originals (formerly tracked under `reference/`, also in
`~/Documents/GitHub/hover/scripts/`, MIT-licensed) were retired ahead of
v0.1. The mapping for posterity:

| File                       | Action                                                   |
| -------------------------- | -------------------------------------------------------- |
| `analyse_logs.py`          | Ported directly; well-tested                             |
| `filter_since.py`          | Ported directly; small and correct                       |
| `aggregate_logs.py`        | Ported directly                                          |
| `process_logs.py`          | Ported directly                                          |
| `search_logs.py`           | Ported directly                                          |
| `logs.sh` dispatcher       | Rebuilt as `argparse` + `rich.live`                      |
| Bash banner / kv printing  | Rebuilt with `rich.table` / `rich.panel`                 |
| Background ticker animator | Rebuilt with `threading.Thread` driving `rich.live.Live` |
| Capture loop               | Rebuilt with `subprocess.Popen` + `concurrent.futures`   |

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

## Naming and registries

| Surface  | Name                     | Status                       |
| -------- | ------------------------ | ---------------------------- |
| GitHub   | `Good-Native/paperbark`  | created, public              |
| npm      | `@good-native/paperbark` | scope reserved, package free |
| PyPI     | `paperbark`              | free, not yet reserved       |
| Homebrew | `paperbark`              | free, not yet reserved       |

Reserve PyPI before the first release; reserve Homebrew when a formula
is ready.
