# Source reference

A **source** is one upstream system paperbark captures log lines from
(Fly.io, Cloudflare Workers, a file on disk, etc.). The interface is
deliberately small: a source yields raw lines, and that's it. Cursor
filtering, dedup, parsing, probing, and aggregation all happen
downstream so source authors can ignore them.

The interface lives in
[`src/paperbark/sources/__init__.py`](../src/paperbark/sources/__init__.py).
Built-in sources sit alongside it under `src/paperbark/sources/`.

## Status

| Source                  | Module                                                    | Status                   |
| ----------------------- | --------------------------------------------------------- | ------------------------ |
| Fly.io (`flyctl logs`)  | [`flyctl.py`](../src/paperbark/sources/flyctl.py)         | implemented              |
| Cloudflare (`wrangler`) | [`wrangler.py`](../src/paperbark/sources/wrangler.py)     | implemented              |
| Kubernetes (`kubectl`)  | [`kubectl.py`](../src/paperbark/sources/kubectl.py)       | stub (raises on capture) |
| AWS CloudWatch          | [`cloudwatch.py`](../src/paperbark/sources/cloudwatch.py) | stub (raises on capture) |
| Plain files             | [`file.py`](../src/paperbark/sources/file.py)             | implemented              |
| stdin                   | [`stdin.py`](../src/paperbark/sources/stdin.py)           | implemented              |

Stubs satisfy the `Source` Protocol so the config layer can resolve a
`type = "kubectl"` (etc.) entry at parse time, but
`capture()` raises `NotImplementedError` until a real implementation
lands. This keeps `paperbark init` and the TOML loader honest — a typo
in `type` fails fast — without forcing every adapter to ship in v1.

## The `Source` Protocol

```python
from collections.abc import Iterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class Source(Protocol):
    name: str

    def capture(self, *, since: str = "") -> Iterator[str]:
        ...
```

Two members and a contract:

- **`name`** is a class attribute: a short identifier (`"flyctl"`,
  `"wrangler"`) used by the registry and matched against `type` in
  TOML. Per-instance labels (the dir name under each run) come from the
  TOML `name` field on each `[[sources]]` entry, not from this
  attribute.
- **`capture(since="")`** returns an iterator of raw lines. Each call
  starts a fresh capture; the source must not retain state across
  calls. Yielding lazily is fine and encouraged — the dispatcher
  drains the iterator and writes lines through to the cursor filter.
- **`since`** is an advisory ISO-8601 timestamp the source may pass to
  the upstream tool when it supports a native `--since` (or
  equivalent). It is a hint, not a guarantee: the cursor filter
  downstream will trim anything older regardless. Sources whose
  upstream does not accept a since flag (e.g. `flyctl logs`) should
  ignore it without erroring.

## Mandatory cursor filter

Every source's output flows through
[`paperbark.cursor`](../src/paperbark/cursor.py) before it reaches
disk. This is non-negotiable: at least one source (`flyctl logs
--no-tail`) returns the same recent window on every call, so per-run
overlap is guaranteed. Source authors **never** dedup their own output
— the cursor filter is the single chokepoint, and double-filtering
would just hide bugs.

In practice this means a source is allowed to emit duplicate lines
across iterations. The cursor records the last-seen ISO timestamp per
app under `<run>/<app>/.cursor` and skips anything that doesn't
strictly advance it.

## Registry

`registered_sources()` in `src/paperbark/sources/__init__.py` returns
the `name → class` map the config layer uses to resolve `type`. A
source is wired up by:

1. Adding the module under `src/paperbark/sources/`.
2. Importing the class at the top of `src/paperbark/sources/__init__.py`.
3. Adding it to the `__all__` list and to `registered_sources()`.

Construction is handled by
[`paperbark.dispatcher.build_source`](../src/paperbark/dispatcher.py),
which dispatches on `spec.type` and forwards the relevant options from
each `[[sources]]` table to the constructor. New sources need a branch
there too — there is intentionally no plugin-style auto-discovery in
v1, so every supported source is visible from one switch.

## Built-in sources

### `flyctl`

Wraps `flyctl logs --no-tail` for one Fly.io app per `[[sources]]`
entry. Each `capture()` call runs a fresh subprocess and yields stdout
line by line.

| Option        | Type    | Default  | Description                                                                                                                                                                                                                  |
| ------------- | ------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app`         | string  | —        | Required Fly.io app name. Passed as `flyctl logs -a <app>`.                                                                                                                                                                  |
| `no_tail`     | boolean | `true`   | Run with `--no-tail`. Streaming `flyctl logs` (without `--no-tail`) is intentionally unsupported in v1; cursor filtering assumes the one-shot capture pattern.                                                               |
| `samples`     | integer | `400`    | Per-iteration capture window size, passed as `-n <samples>`. Mirrors `reference/logs.sh`'s `--samples` default; raise it on busy apps to avoid dropped lines.                                                                |
| `format`      | string  | `"json"` | Named-group regex preset for non-JSON payloads. One of: `json` (default), `apache-combined`, `nginx-default`, `syslog-rfc5424`. See [`docs/CONFIG.md`](CONFIG.md#flyctl-options) for the format/cursor compatibility matrix. |
| `format_keys` | table   | none     | Optional per-field JSON key overrides forwarded to the iteration parser. See [`docs/CONFIG.md`](CONFIG.md#flyctl-options) for shape and worked examples. JSON-only — rejected when combined with a non-`json` `format`.      |

Notes:

- The `since` parameter is silently ignored — `flyctl logs` has no
  native `--since` flag, so the cursor filter handles bounding alone.
- Fly prefixes lines with an ANSI-coloured timestamp
  (`\033[2m…\033[0m`). The cursor filter strips these before parsing;
  the source itself emits raw lines.
- The runner subprocess is terminated cleanly on early consumer exit
  (a `break` or generator close), with a 5-second grace before SIGKILL.

### `file`

Reads a single text file from disk and yields its lines. Each
`capture()` call re-opens the file and streams it from the start —
the source is stateless across calls (per the project's source
contract), and the cursor filter handles cross-iteration dedup.

| Option        | Type   | Default   | Description                                                                                                                                    |
| ------------- | ------ | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`        | string | —         | Required path to the log file. Existence is checked at capture time, not at config load — log files often appear and disappear under rotation. |
| `encoding`    | string | `"utf-8"` | Text encoding to decode the file with. Undecodable bytes are replaced with `U+FFFD` so a stray byte never aborts a capture.                    |
| `format`      | string | `"json"`  | Same regex-preset selector as `flyctl`; see [`docs/CONFIG.md`](CONFIG.md#flyctl-options).                                                      |
| `format_keys` | table  | none      | JSON-keys overrides; rejected when combined with a non-`json` `format`.                                                                        |

Notes:

- The `since` advisory parameter is silently ignored — the source has
  no upstream query to forward it to, and cursor filtering bounds
  output regardless.
- Cursor filtering keys on a leading ISO-8601 timestamp by default.
  Log shapes that lead with a timestamp (Fly-style JSON-with-prefix,
  syslog emitted with a leading TS) dedup correctly across iterations
  out of the box. For non-leading-TS shapes (Apache combined, nginx
  default, RFC 5424's `<PRI>1` prefix), set `format` to the matching
  preset on the `[[sources]]` entry — the cursor filter then advances
  from the timestamp the format extracts, so those shapes also flow
  end-to-end through `paperbark monitor`.
- Log rotation is the source's responsibility, not paperbark's. If the
  file is replaced (e.g. by `logrotate create`) between iterations the
  next `capture()` reads the new file from the start; the cursor filter
  may still drop replays whose timestamps overlap the cursor.

### `stdin`

Reads lines from `sys.stdin` and yields them. Stdin capture happens
during a `paperbark monitor` run only — `analyse` and `search` read
existing run artefacts and never consume stdin. Intended for piping
pre-captured logs into a one-shot `paperbark monitor` run, e.g.:

```sh
cat app.log | paperbark monitor --iterations 1
```

| Option        | Type   | Default  | Description                                                                                                       |
| ------------- | ------ | -------- | ----------------------------------------------------------------------------------------------------------------- |
| `format`      | string | `"json"` | Same regex-preset selector as `flyctl`; see [`docs/CONFIG.md`](CONFIG.md#flyctl-options).                         |
| `format_keys` | table  | none     | JSON-keys overrides; rejected when combined with a non-`json` `format`.                                           |

Notes:

- `since` is silently ignored — stdin has no upstream query to forward
  it to, and cursor filtering bounds output regardless.
- A piped stdin is a single-use stream owned by the parent process. The
  first `capture()` drains it; subsequent calls yield nothing rather
  than re-raising. Long-running monitor loops over a stdin pipe
  therefore see one productive iteration followed by empty ones, which
  matches the typical one-shot use.
- There is intentionally no `encoding` knob in v0.2 — `sys.stdin` uses
  whatever Python wired up at process start (`PYTHONIOENCODING` and the
  system locale settle this). For byte-level robustness or a custom
  encoding, prefer the `file` source — it owns the underlying handle
  and applies `errors="replace"` safely.

### `wrangler`

Wraps `wrangler tail <worker> --format=json` for one Cloudflare
Worker per `[[sources]]` entry. `wrangler tail` is a live stream
with no `--no-tail` equivalent, so the source bounds each iteration
by wall-clock time (`samples_window_seconds`, default 5 s) instead
of by a snapshot window.

| Option                   | Type    | Default | Description                                                                                                                                                                                                          |
| ------------------------ | ------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `worker`                 | string  | —       | Required Cloudflare Worker name. Passed as `wrangler tail <worker> --format=json`.                                                                                                                                   |
| `account_id`             | string  | none    | Cloudflare account ID. Set via the `CLOUDFLARE_ACCOUNT_ID` env var on the spawned subprocess. Required when the operator's wrangler login covers more than one account — wrangler refuses to pick one in non-interactive mode and the subprocess exits with a loud error otherwise. |
| `samples_window_seconds` | number  | `5`     | Per-iteration capture window. After this many seconds the source SIGTERMs the wrangler subprocess and yields whatever events it collected.                                                                            |
| `samples`                | integer | `400`   | Per-iteration line cap. Mirrors `flyctl`'s `samples` semantics (bounded `deque`); a busy Worker can't blow memory mid-iteration.                                                                                      |
| `format`                 | string  | `"json"`| Same regex-preset selector as `flyctl`. Rare for wrangler (output is always JSON) but kept for parity. The cursor-filter caveat applies here too.                                                                     |
| `format_keys`            | table   | none    | JSON-keys overrides; rejected when combined with a non-`json` `format`. Defaults to `{ component = "scriptName" }` if unset, so the canonical record's `component` field is populated automatically.                  |

Notes:

- **Pretty-printed JSON, not NDJSON.** Wrangler 4.x emits one
  pretty-printed JSON object per Worker invocation, with no
  delimiter between events. The source streams stdout into
  `json.JSONDecoder.raw_decode` and yields one parsed dict per
  top-level object — robust against indented payloads and strings
  that contain braces.
- **Leading ISO timestamp injection.** Wrangler payloads have no
  leading timestamp, which would otherwise cause the cursor filter
  to drop every line. The source converts `eventTimestamp` (ms
  epoch) to ISO-8601 and prepends it to each yielded line so the
  cursor filter's default leading-ISO path accepts the output.
- **Severity mapping.** A synthetic `level` key is injected from
  Cloudflare's `outcome` field: `ok` → `info`,
  `exception` / `exceededCpu` → `error`,
  `canceled` / `unknown` → `warn`. Anything else maps to `warn` so
  unknown future outcomes still surface. Operators who want a
  different mapping can override `format_keys.level` to point at
  their own field.
- **Per-event vs per-log emission.** Each wrangler event carries a
  `logs[]` array (Worker `console.log` output). v0.2 emits one
  canonical line per request; per-log expansion is a follow-up
  toggle.
- **Cleanup.** Mirrors flyctl's lifecycle: `terminate()` →
  `wait(timeout=5)` → `kill()` if still alive.
- The `since` parameter is silently ignored — `wrangler tail` has
  no `--since` equivalent; cursor filtering downstream handles
  bounding.

### Stubs (`kubectl`, `cloudwatch`)

These two are placeholders: `capture()` raises
`NotImplementedError`. They exist so a config that names one of
these `type`s validates and resolves at parse time, and so the
registry and dispatcher round-trip tests cover the eventual real
implementation paths.

The expected shape when these land:

- **`kubectl`** wraps `kubectl logs <pod>` with namespace/container
  selectors. Will likely accept `since` natively.
- **`cloudwatch`** uses the AWS SDK's `filter_log_events` against one
  log group per source. `since` maps to `startTime`.

## Configuration

Sources are declared as an array of tables in `paperbark.toml`. Each
entry needs a unique `name` (the dir slug under each run) and a `type`
(matched against the registry):

```toml
[[sources]]
name = "web"
type = "flyctl"
app = "my-fly-web"

[[sources]]
name = "worker"
type = "flyctl"
app = "my-fly-worker"
```

Every key beyond `name` and `type` lands in `SourceConfig.options`.
The dispatcher's `build_source` switch picks only the keys it knows
about for each type and raises `DispatcherError` on any unrecognised
key, so a typo in an option name fails loudly at startup rather than
becoming a silent no-op. Required options are validated in the
matching `build_source` branch as well (the way `flyctl` checks `app`
before constructing `FlyctlSource`); the source constructor may keep
its own check as defence-in-depth, but the dispatcher is the
canonical contract surface. See [`docs/CONFIG.md`](CONFIG.md) for the
full schema, including validation rules.

## Adding a new source

The interface is small enough that a new adapter is normally one
module plus one dispatcher branch.

1. **Write the source class.** New file under
   `src/paperbark/sources/`. Set `name` as a class attribute and
   implement `capture(*, since="")`. Yield lines lazily; don't dedup.

   ```python
   from collections.abc import Iterator


   class JournaldSource:
       name = "journald"

       def __init__(self, *, unit: str) -> None:
           if not unit:
               raise ValueError("JournaldSource requires a unit name")
           self.unit = unit

       def capture(self, *, since: str = "") -> Iterator[str]:
           cmd = ["journalctl", "-u", self.unit, "--no-pager"]
           if since:
               cmd += ["--since", since]
           # ... run subprocess, yield stdout lines ...
   ```

2. **Register it.** In `src/paperbark/sources/__init__.py`, import the
   class, add it to `__all__`, and add a `JournaldSource.name:
JournaldSource` entry to `registered_sources()`.

3. **Wire the dispatcher.** Add a branch to
   `paperbark.dispatcher.build_source` that calls
   `_reject_unknown_options(spec, frozenset({...}))` with the keys the
   source accepts, then pulls those keys off `spec.options` and
   constructs the class. Raise `DispatcherError` for missing required
   options (mirror the `flyctl` branch).

4. **Document it.** Add a row to the table at the top of this file,
   add a section under "Built-in sources" with the option table, and
   update the sources block in
   [`docs/CONFIG.md`](CONFIG.md#sources) plus the README sources
   table.

5. **Test it.** Cover the registry, the constructor's input
   validation, and the command shape (or upstream-call shape).
   Existing patterns live in
   [`tests/test_sources.py`](../tests/test_sources.py).

A new source must not break the cursor-filter assumption: lines need
to be parseable through the format layer to a record with a usable
timestamp. If the upstream emits something exotic, a matching format
preset is the right place for the parsing rules — not the source.

## External plugin loader

Loading third-party `Source` classes from arbitrary install paths is
explicitly out of scope for v1 (see [`docs/ROADMAP.md`](ROADMAP.md)).
The Protocol is intentionally documented so the plugin loader, when
it lands, can be a thin layer on top of what's already here without
disturbing existing sources.
