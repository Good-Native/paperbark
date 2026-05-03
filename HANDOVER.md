# Paperbark — handover note

This file hands a fresh Claude Code session the context it needs to pick up
from where the previous session (running inside the Hover worktree) left off.
Delete or fold into a planning doc once the scaffold is committed.

## What this is

A pip/pipx-installable Python CLI that captures, searches, and analyses logs
from many sources (Fly.io, Cloudflare, Kubernetes, CloudWatch, plain files,
stdin). Extracted as an OSS package from `scripts/logs.sh` and its Python
helpers in the Hover repo at `~/Documents/GitHub/hover/scripts/`.

The package belongs to the `Good-Native` family — sibling to `sprout`,
`hover`, `bloom`. Short, single-word, Australian-native names.

## Names and registries

| Surface | Name | Status |
|---|---|---|
| GitHub | `Good-Native/paperbark` | created, public, empty |
| npm | `@good-native/paperbark` | scope reserved, package free |
| PyPI | `paperbark` | free, not yet reserved |
| Homebrew | `paperbark` | free, not yet reserved |

## Tooling stack (confirmed)

- Python target: 3.11+ (CI matrix: 3.11, 3.12, 3.13)
- Build backend: `hatchling`
- Project manager: `uv`
- Lint + format: `ruff` and `ruff format`
- Test runner: `pytest`
- Type checker: `mypy`
- Licence: MIT, copyright "Good-Native"
- Code of conduct: Contributor Covenant 2.1

## What's done

- GitHub org `Good-Native` created
- Repo `Good-Native/paperbark` created (public, empty)
- npm scope `@good-native` registered
- Naming and availability research complete
- `CLAUDE.md` written (project operating guide, Python-flavoured)
- `.claude/settings.json` written (Python toolchain perms; no Hover-specific
  Go/Supabase/Fly entries)
- `.claude/agents/{planner,code-reviewer,security-auditor}.md` written
  (Python-adapted ports of the Hover originals)
- `reference/` populated with the original Hover bash + Python scripts
  (`logs.sh`, `monitor_logs.sh`, `filter_since.py`, `process_logs.py`,
  `aggregate_logs.py`, `analyse_logs.py`, `search_logs.py`, plus the
  `monitor` slash-command doc). See `reference/README.md` for the per-file
  port plan. Delete the directory once v0.1 ships.

## What's next — minimum scaffold

Already on disk (don't recreate): `CLAUDE.md`, `HANDOVER.md`, `.claude/`,
`reference/`. Still to write:

```
paperbark/
├── README.md                       # title, tagline, install + quickstart placeholders
├── LICENSE                         # MIT, copyright Good-Native
├── CHANGELOG.md                    # Keep-a-Changelog skeleton
├── CONTRIBUTING.md                 # short contribution guide; bootstrap = `uv sync && pre-commit install`
├── CODE_OF_CONDUCT.md              # Contributor Covenant 2.1
├── .gitignore                      # Python standard
├── .python-version                 # 3.13 for local dev
├── pyproject.toml                  # hatchling, project metadata, ruff/pytest/mypy config
├── .pre-commit-config.yaml         # ruff + ruff-format + prettier (md/yaml/json)
├── src/
│   └── paperbark/
│       ├── __init__.py             # __version__ stub
│       └── cli.py                  # argparse skeleton: monitor / search / analyse / init
├── tests/
│   └── test_smoke.py               # one passing test so CI is green from day one
└── .github/
    └── workflows/
        └── ci.yml                  # uv + ruff + pytest + pip-audit matrix on 3.11/3.12/3.13
```

**No `scripts/` directory.** Hover's bash scripts (`security-check.sh`,
`format.sh`, `run-tests.sh`, `setup-hooks.sh`) are deliberately not ported.
Modern Python tooling replaces them:

| Hover script | paperbark equivalent |
|---|---|
| `security-check.sh` | CI runs `pip-audit` + `ruff` (security `S` ruleset); Dependabot enabled in repo settings |
| `format.sh` | `pre-commit` hooks (`ruff format`, `ruff check --fix`, `prettier`) |
| `run-tests.sh` | `uv run pytest` (one-liner; no wrapper) |
| `setup-hooks.sh` | `pre-commit install` (one command on first clone) |
| `pr-status-check.sh` / `pr-comment-reply.sh` | Skip until/unless CodeRabbit is installed on the repo |
| `changelog-version.sh` | Defer until release automation is needed |

Console-script entry point in `pyproject.toml`:

```toml
[project.scripts]
paperbark = "paperbark.cli:main"
```

GitHub repo metadata:
- Description: "Configurable cross-source log capture, search, and analysis CLI."
- Topics: `logs`, `observability`, `cli`, `python`, `fly-io`, `monitoring`

### First-push workflow

1. `git init`, write all scaffold files, first commit on `main` with a
   5–6 word plain-English subject (no Conventional Commits prefix), e.g.
   `Initial project scaffold and CI`
2. Add remote: `git remote add origin git@github.com:Good-Native/paperbark.git`
3. **Show user `git remote -v` and the staged tree before pushing.**
4. Push only after the user confirms (per their no-auto-push rule).

## V1 scope

Feature parity with `~/Documents/GitHub/hover/scripts/logs.sh` and its
helpers, with these changes:

- TOML-driven config (`./paperbark.toml` then `~/.config/paperbark/config.toml`).
- Pluggable `Source` layer — flyctl in v1; `wrangler`, `kubectl`, `cloudwatch`,
  `file`, `stdin` scaffolded as interface-conformant stubs but **not** required
  for v1 ship.
- Pluggable `Format` layer — JSON-keys plus named-group regex with presets
  (`apache-combined`, `nginx-default`, `syslog-rfc5424`).
- Pure-Python dispatcher / animator using `rich.live` (replaces the bash
  ticker and banner).
- Preserve the existing run-dir layout, finding shape, and probe set.

### CLI surface

```
paperbark                # default = monitor with config defaults
paperbark monitor [...]
paperbark search --keyword X [--regex Y] [--run latest|all|<id>]
paperbark analyse [--run latest|all|<id>] [--keyword X] [--regex Y]
paperbark init           # write a starter paperbark.toml in cwd
```

CLI flags override TOML; flag names mirror TOML keys where reasonable.

### Probes (port from Hover)

Severity rollup, panics and fatals, HTTP status, latency (p50/p95/p99 plus
slowest entries), heartbeat (gap detection), process health, autoscaler
events, database/external errors, Sentry events, plus ad-hoc keyword/regex.

Each finding shape: `{count, first_seen, last_seen, peak}`. Keep the bounded
LRU dedup in the per-app analyser as a safety net on top of cursor filtering.

Make every probe class config-toggleable. Make probe regex sets
(autoscaler, DB/external, Sentry) config-overridable so a Cloudflare-Worker
user can replace them without forking.

### Output layout (preserve)

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

## What to keep vs rebuild

The originals live in `~/Documents/GitHub/hover/scripts/` (MIT-licensed).

**Port directly:**
- `analyse_logs.py` — probe set; well-tested, mostly as-is
- `filter_since.py` — cursor logic; small and correct
- `aggregate_logs.py` — per-minute time series
- `process_logs.py` — per-iteration JSON summary
- `search_logs.py` — grep across raw or zipped captures

**Rebuild in pure Python:**
- `logs.sh` dispatcher — `argparse` plus `rich.live` ticker
- Bash banner / kv printing — `rich.table` or `rich.panel`
- Background ticker animator — `threading.Thread` driving a `rich.live.Live`
- Capture loop — `subprocess.Popen` with `concurrent.futures`

## Gotchas already handled in the bash version

Look at the merged commits on Hover branch `work/cranky-bhabha-4ea945` for
the full evolution. Key ones to carry across:

- Fly's ANSI-coloured timestamp prefix (`\033[2m2026-…Z\033[0m`) — strip
  before parsing.
- `flyctl logs --no-tail` returns the same recent window every call —
  cursor-filter on the consumer side is mandatory.
- Capture overlap dedup (bounded LRU window) on top of cursor filter as a
  safety net.
- Python child processes catch `KeyboardInterrupt` to exit silently when
  the parent forwards SIGINT through the pipe.
- `dim` SGR (`\033[2m`) renders as a background block in some terminals;
  use bright-black foreground (`\033[90m`) instead.
- VS Code terminal renders Braille spinner glyphs (`⠋⠙⠹…`) too small;
  use rotating quarter-circles (`◐ ◓ ◑ ◒`).

## Out of scope for v1

- Web UI / dashboard.
- Persistent server / agent-mode running as a daemon.
- Cross-run aggregation queries.
- Alerting integrations (Slack, PagerDuty).
- External `Source` plugin loader (interface documented; loader not shipped).

## Project rules

- Australian English in code, comments, commit messages, docs.
- Commit messages: 5–6 words, no AI-attribution footers.
- MIT licence — all ported code from Hover is MIT-compatible.
- No secrets, credentials, or end-user content anywhere in the repo or
  produced output.

## New-session boot prompt

When starting a fresh session in `~/Documents/GitHub/paperbark/`, read
`CLAUDE.md` and this file, confirm the scaffold list with the user, then
proceed to scaffold + commit + show staged tree + wait for push approval.
