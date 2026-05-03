# reference/

Source material for paperbark. These are the original bash + Python scripts
from `Good-Native/hover` (`scripts/` directory) that paperbark is being
extracted from. MIT-licensed in their original repo.

Kept here verbatim during the v0 port so we have one place to diff against
while building the Python equivalents. **Delete this directory once v0.1
ships and these are no longer needed.**

## What's here

| File | Role | Port plan |
|---|---|---|
| `logs.sh` | Bash dispatcher: monitor / search / analyse subcommands | **Rebuild** in pure Python (`argparse` + `rich.live` ticker) |
| `monitor_logs.sh` | Legacy thin wrapper that forwards to `logs.sh monitor` | Drop |
| `filter_since.py` | Cursor filter — drops lines older than the saved per-app timestamp | Port directly |
| `process_logs.py` | Raw log → per-iteration JSON summary | Port directly |
| `aggregate_logs.py` | Iteration JSONs → per-minute time series | Port directly |
| `analyse_logs.py` | Raw + aggregated → probe report (`analysis.md`/`json`) | Port mostly as-is; lift hardcoded keys + regex sets into TOML config |
| `search_logs.py` | Grep across captured raw logs (live or zipped) | Port directly |
| `monitor-command.md` | The Hover Claude Code slash-command that documents `logs.sh` usage | Reference for the new `paperbark` CLI help text and quickstart |

## Provenance

Original commit history lives on Hover branch `work/cranky-bhabha-4ea945`
(merged to `main` as PR #373). Every notable correctness or UX bug we hit
is captured there as a discrete commit — useful when porting.

## Gotchas already handled

Carry these across when porting:

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
- VS Code terminal renders Braille spinner glyphs (`⠋⠙⠹…`) too small to
  be noticeable; use rotating quarter-circles (`◐ ◓ ◑ ◒`).
