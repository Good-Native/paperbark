# Contributing to paperbark

Thanks for your interest. This guide covers the bare minimum to get a
local development environment running and a change merged.

## Bootstrap

You'll need [`uv`](https://docs.astral.sh/uv/) and Python 3.11+.

```sh
git clone git@github.com:Good-Native/paperbark.git
cd paperbark
uv sync
pre-commit install
```

`uv sync` creates a virtualenv under `.venv/` and installs the project plus
dev dependencies. `pre-commit install` wires up the formatting and lint
hooks.

## Day-to-day

```sh
# run the CLI from the working tree
uv run paperbark --help

# tests (targeted runs are preferred while iterating)
uv run pytest tests/test_smoke.py
uv run pytest

# type-check
uv run mypy src tests

# lint and format (pre-commit usually handles this)
uv run ruff check .
uv run ruff format .
```

## Branches and commits

- Branch off `main`. Use short descriptive names: `feature/severity-probe`,
  `fix/cursor-timezone`.
- Commit messages: 5–6 words, present tense. Examples: `Add severity
probe toggle`, `Fix cursor filter timezone`. No AI-attribution footers.
- Group related work into single commits where reasonable.
- Use Australian English in code, comments, commit messages, and docs.

## Pre-merge gates

CI runs (and must pass) the following on every push and pull request,
across Python 3.11, 3.12, and 3.13:

- `ruff check`
- `ruff format --check`
- `mypy`
- `pytest`
- `pip-audit`

The same set runs locally via `pre-commit run --all-files`. Don't bypass
gates without explicit maintainer approval.

## Reporting issues

Open an issue on
[GitHub](https://github.com/Good-Native/paperbark/issues). Include the
command you ran, the source you were capturing from, the version of
paperbark, and any output (with secrets redacted).

## Code of conduct

Participation in this project is governed by the
[Contributor Covenant](CODE_OF_CONDUCT.md). By participating, you agree
to abide by its terms.
