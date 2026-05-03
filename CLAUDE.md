# CLAUDE.md

Last reviewed: 2026-05-03

This file is the project operating guide for Claude Code (desktop/CLI) in this
repository.

## Hard requirements

- Use Australian English in code comments, commit messages, user-facing text,
  and generated docs.
- Preserve existing behaviour unless explicitly asked to change it.
- Ask at most one clarifying question when ambiguity materially affects
  correctness or safety.
- Ask for explicit confirmation before destructive steps (force pushes,
  history rewrites, secret/config changes, dependency removals).
- Do not expose, invent, or log secrets, credentials, tokens, or end-user
  log content captured by the tool.
- Keep edits scoped and incremental.
- If a safety limit is reached in a tool, pause and continue with the best
  available path.

## Technical baseline

- Language: Python 3.11+. CI matrix runs 3.11, 3.12, 3.13.
- Build backend: `hatchling`.
- Project manager: `uv` (use `uv sync`, `uv run`, `uv add`, etc.).
- Lint + format: `ruff` and `ruff format`. Run on every touched file.
- Test runner: `pytest`. Prefer targeted runs (`pytest tests/test_foo.py`)
  before broader sweeps.
- Type checker: `mypy`. Treat type errors on touched files as blockers.
- Licence: MIT.

## Code navigation

- Prefer symbol-aware navigation (LSP / pyright) for Python code when
  available.
- Use `grep` / `glob` for non-Python files (TOML, YAML, Markdown, shell).

## Project-specific rules

**Source abstraction:** Every new log source implements the `Source`
interface in `src/paperbark/sources/__init__.py`. A source must yield raw
log lines newer than the supplied cursor, and must not retain state across
calls. Add a row to the source table in `README.md` when introducing one.

**Format abstraction:** Probes consume a canonical record
(`{timestamp, level, message, component, status, duration_ms, raw_line}`).
Source/format details stop at the boundary; probe code never branches on
source type.

**Probe contract:** Findings keep the shape
`{count, first_seen, last_seen, peak}`. New probes go behind a config toggle
under `[probes]` in TOML so users can disable them without forking. Regex
sets used by probes are config-overridable for the same reason.

**TOML config is the source of truth for defaults.** Every CLI flag must
also be expressible as a TOML key. Flags override TOML at runtime.

**Run-dir layout is part of the public contract.** Do not change the
shape of `logs/YYYYMMDD/HHMM_<slug>_<settings>/` without bumping a major
version — downstream tooling (search across runs, etc.) depends on it.

**Cursor filter is mandatory.** Every source's captured output must pass
through the cursor filter; per-iteration capture overlap is otherwise
guaranteed for at least one source (Fly's `--no-tail`).

## Instruction loading

- `CLAUDE.md` (this file) and optional `CLAUDE.local.md` are read in the
  project scope.
- Agent role files live under `.claude/agents/*.md` with YAML frontmatter
  (name, description, optional tools/model). None are mandatory yet; add
  specialists here as the project grows.

## Work approach

- For small tasks: minimal read / plan / implement.
- For large changes: confirm scope, prepare a staged plan, then implement
  in bounded increments.
- Report blockers clearly with concrete risk and proposed mitigation.

## Automated review gates

- CI (GitHub Actions) runs `ruff check`, `ruff format --check`, `mypy`,
  `pytest`, and `pip-audit` across the Python matrix. Treat all five as
  mandatory pre-merge gates.
- Pre-commit hooks (`ruff`, `ruff format`, `prettier` for md/yaml/json)
  run on every commit; CI enforces the same set with `pre-commit run
--all-files`.
- If a change risks failing a gate, call it out before implementation.
- Do not recommend or request bypasses unless explicitly approved by
  project maintainers.

**No bash wrapper scripts.** This repo deliberately does not carry
`security-check.sh`, `format.sh`, `run-tests.sh`, or `setup-hooks.sh` from
the Hover lineage. The Python ecosystem covers their roles via
`pre-commit`, `pip-audit`, `ruff`, and `uv run pytest` directly. Resist
adding wrapper scripts unless something genuinely doesn't fit a config
file.

## Commit style

- 5–6 words, descriptive, present tense. Examples: `Add severity probe
toggle`, `Fix cursor filter timezone`, `Port aggregate logs to Python`.
- No AI-attribution footers (`Co-Authored-By: Claude`, `Generated with`,
  etc.).
- Group related work into single commits where reasonable; avoid chains
  of fix-the-fix commits.

## Source-of-truth docs

For detailed rules and onboarding, build out (or maintain):

- `README.md` — install, quickstart for Fly + at least one other source,
  TOML reference, custom-probe how-to.
- `CHANGELOG.md` — Keep-a-Changelog format.
- `CONTRIBUTING.md` — branching, commits, gates.
- `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1.
- `docs/SOURCES.md` — interface, built-in sources, external-plugin notes.
- `docs/PROBES.md` — built-in probes and how to add a new one.
- `docs/CONFIG.md` — TOML reference, every key documented.

## Origin / provenance

The probe set, cursor filter, aggregator, and run-dir layout are ported
from `scripts/` in `Good-Native/hover` (MIT-licensed). The bash dispatcher
and animator are rebuilt rather than ported. See `docs/ROADMAP.md` for
the migration map and v1 plan.
