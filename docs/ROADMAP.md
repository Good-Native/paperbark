# Paperbark — roadmap

Living document. States overall progress and forward plans. Versions,
dates, PR numbers, and commit hashes deliberately live elsewhere
([`CHANGELOG.md`](../CHANGELOG.md), `git log`, GitHub releases) so this
file doesn't drift. For project rules and tooling baseline, see
[`CLAUDE.md`](../CLAUDE.md).

## Current state

Paperbark is shipped to PyPI and runs end to end. `paperbark monitor`
captures on a fixed cadence, fires snapshot analyses, swaps in a
`rich.live` ticker on a TTY (plain progress lines off-TTY), and writes
a final analysis at the run root when the loop ends. Every CLI flag
is mirrored as a TOML key; flags override TOML at runtime. CI is green
across the supported Python matrix.

Repo: <https://github.com/Good-Native/paperbark>.

### Implementation status

| Step                                                   | Status  |
| ------------------------------------------------------ | ------- |
| Cursor filter (`paperbark.cursor`)                     | done    |
| Probes (`paperbark.probes`)                            | done    |
| Aggregate (`paperbark.aggregate`)                      | done    |
| Iteration (`paperbark.iteration`)                      | done    |
| Search (`paperbark.search`, wired into CLI)            | done    |
| Source interface + flyctl source                       | done    |
| Format interface + built-in regex presets              | done    |
| Dispatcher and `rich.live` animator                    | done    |
| `paperbark init` TOML writer (with manifest detection) | done    |
| `paperbark analyse` over captured runs                 | done    |
| Real `file`, `stdin`, `wrangler` sources               | done    |
| Format-aware cursor filter                             | done    |
| Real `kubectl` source                                  | planned |
| Real `cloudwatch` source                               | planned |
| Per-source probe overrides                             | planned |

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

Every probe class is config-toggleable. Probe regex sets (autoscaler,
DB/external, Sentry) are config-overridable so a Cloudflare-Worker user
can replace them without forking.

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

### What was kept vs rebuilt

The bash originals (formerly `reference/`, also in
`~/Documents/GitHub/hover/scripts/`, MIT-licensed) were retired ahead of
v1. The mapping for posterity:

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

## Forward plans

Near-term, in roughly the order they're likely to land:

- Real `kubectl` source (wraps `kubectl logs` with namespace/container
  selection).
- Real `cloudwatch` source (AWS SDK `filter_log_events` against one
  log group per `[[sources]]`).
- Per-source probe overrides (today probe toggles and
  `[probes.patterns]` are global).
- Custom inline `RegexFormat` definitions in TOML (today only the
  bundled presets are selectable via `format = "<preset>"`).

## Beyond v1 (parking lot)

- External plugin loader for third-party `Source` and `Format` modules.
- Cross-run search and aggregation queries.
- Optional alert sinks (Slack, PagerDuty).
- Homebrew formula.

## Naming and registries

- **GitHub:** `Good-Native/paperbark` (public).
- **PyPI:** `paperbark` (published; auto-release on merge to `main`
  with a fresh `[Unreleased]` changelog entry).
- **npm:** `@good-native/paperbark` scope reserved; package free for
  any future companion package.
- **Homebrew:** `paperbark` free; reserve when a formula is ready.
