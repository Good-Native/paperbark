# Configuration reference

Paperbark's runtime defaults live in TOML. This document is the canonical
reference for every key the loader recognises. The schema is implemented in
[`src/paperbark/config.py`](../src/paperbark/config.py); the starter
template emitted by `paperbark init` is in
[`src/paperbark/init.py`](../src/paperbark/init.py).

## Discovery

The loader looks for a config file in this order and uses the first hit:

1. The path passed to `--config` on any subcommand that accepts it
   (`monitor`, `analyse`, `search`).
2. `./paperbark.toml` (current working directory).
3. `~/.config/paperbark/config.toml`.

If none is found and `--config` was not supplied, the loader returns
`Config.defaults()` and every section gets the documented default.

A directory named `paperbark.toml` (an easy mistake) is skipped during
discovery rather than masking a valid home config.

## Override semantics

Every CLI flag is also a TOML key. CLI flags override TOML at runtime; TOML
values override built-in defaults. The merge runs once per invocation, so an
unset flag falls through to the TOML value (or the built-in default) without
mutating the loaded config.

Boolean flags follow `argparse.BooleanOptionalAction` where they need to
clear a TOML `true` — currently `--stdout` / `--no-stdout` for analyse, and
the mutually exclusive `--ignore-case` / `--case-sensitive` pair for search.

## Duration strings

`[monitor].interval`, `[monitor].analyse_every`, and the corresponding CLI
flags accept either a non-negative integer (interpreted as seconds) or a
string in one of these forms:

| Form  | Meaning                 |
| ----- | ----------------------- |
| `30s` | 30 seconds              |
| `5m`  | 5 minutes (300 seconds) |
| `1h`  | 1 hour (3600 seconds)   |
| `42`  | 42 seconds (plain int)  |

Combined forms (`1h30m`, `1m30s`) are deliberately unsupported —
the bash dispatcher doesn't accept them either and admitting them would
silently widen the contract. Decimals, signs, and unknown suffixes raise
`ConfigError`.

## Schema reference

### `[paperbark]`

| Key    | Type     | Default  | Description                                                                                      |
| ------ | -------- | -------- | ------------------------------------------------------------------------------------------------ |
| `root` | `string` | `"logs"` | Output directory for captured runs. Each run lands in `<root>/YYYYMMDD/HHMM_<slug>_<settings>/`. |

`--root` on `analyse` and `search` overrides this for the invocation.

### `[monitor]`

Cadence, scope, and identity for `paperbark monitor`. Defaults mirror
`reference/logs.sh` so the Python port behaves identically out of the box.

| Key               | Type     | Default | Description                                                                                                                       |
| ----------------- | -------- | ------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `interval`        | duration | `3`     | Seconds (or duration string) between iterations. Must be `> 0`.                                                                   |
| `iterations`      | integer  | `1440`  | Total iterations to run. `0` runs forever (until SIGINT).                                                                         |
| `analyse_every`   | duration | `"5m"`  | Snapshot analyse cadence. `0` disables snapshots entirely.                                                                        |
| `run_id`          | string   | `""`    | Run slug. Empty triggers an auto-generated `<adjective>-<colour>` slug at start.                                                  |
| `cleanup_enabled` | boolean  | `true`  | Run the rotation pass at loop start. Set `false` (or `--no-cleanup`) to disable.                                                  |
| `cleanup_days`    | integer  | `1`     | Rotate run dirs older than `N` days. `0` rotates every older run, including yesterday's.                                          |
| `cleanup_mode`    | string   | `"zip"` | `"zip"` archives each `<app>/raw/` to a sibling `raw.zip` and removes per-iter JSON/CSV; `"delete"` removes the run dir entirely. |

`run_id` validation: letters, numbers, `.`, `_`, `-`; must start with a
letter or number. The same regex (`^[A-Za-z0-9][A-Za-z0-9._-]*$`) applies
on the CLI override path so a hostile `--run-id ../escape` is rejected.

The cleanup pass runs once at loop start before the new run dir is
created. `summary.md`, `time_series.csv`, `events_per_minute.csv`,
`components_per_minute.csv`, `analysis.{json,md}`, and `monitor.log`
all survive a `"zip"` rotation; only the bulky raw and per-iter
artefacts are folded into `raw.zip` / removed. `paperbark.search`
already reads `<app>/raw.zip` transparently, so rotated runs remain
searchable.

CLI flags: `--interval`, `--iterations`, `--analyse-every`, `--run-id`,
`--cleanup` / `--no-cleanup`, `--cleanup-days`, `--cleanup-mode`.

### `[analyse]`

Defaults for `paperbark analyse`. Every field is also a CLI flag.

| Key        | Type             | Default    | Description                                                                                                        |
| ---------- | ---------------- | ---------- | ------------------------------------------------------------------------------------------------------------------ |
| `run`      | string           | `"latest"` | Selector: `"latest"`, `"all"`, `"<date>"`, or `"<date>/<runname>"`.                                                |
| `app`      | string           | `""`       | Comma-separated app filter; empty matches every app under the run.                                                 |
| `keywords` | array of strings | `[]`       | Ad-hoc literal terms added on top of the default probe set.                                                        |
| `regexes`  | array of strings | `[]`       | Ad-hoc regex terms added on top of the default probe set.                                                          |
| `out`      | string           | `""`       | Override output base path (writes `<out>.json` + `<out>.md`). Empty writes the default `<run>/analysis.{json,md}`. |
| `stdout`   | boolean          | `false`    | Also print rendered markdown to stdout in addition to writing files.                                               |

CLI flags: `--run`, `--root`, `--app`, repeatable `--keyword` /
`--regex`, `--out`, `--stdout` / `--no-stdout`.

CLI keyword/regex flags **replace** the TOML default rather than extending
it — this lets you narrow searches without editing the file.

### `[search]`

Defaults for `paperbark search`. Every field is also a CLI flag.

| Key              | Type             | Default    | Description                                                                                      |
| ---------------- | ---------------- | ---------- | ------------------------------------------------------------------------------------------------ |
| `run`            | string           | `"latest"` | Same selector grammar as `[analyse].run`.                                                        |
| `app`            | string           | `""`       | Comma-separated app filter.                                                                      |
| `keywords`       | array of strings | `[]`       | Repeatable literal terms. At least one keyword/regex must be supplied (TOML or CLI) at run time. |
| `regexes`        | array of strings | `[]`       | Repeatable regex terms.                                                                          |
| `case_sensitive` | boolean          | `false`    | Strict matching (default off; case-insensitive).                                                 |
| `max`            | integer          | `0`        | Stop after N total matches. `0` is unlimited. Must be `>= 0`.                                    |

CLI flags: `--run`, `--root`, `--app`, repeatable `--keyword` / `--regex`,
`--case-sensitive` / `--ignore-case` (mutually exclusive), `--max`.

A TOML-supplied `[search].keywords` or `[search].regexes` drives matching
even when no `--keyword` / `--regex` flag is supplied. As with analyse,
CLI keyword/regex flags **replace** the TOML default.

### `[probes]`

Probe toggles plus ad-hoc keyword/regex matchers. Setting any probe to
`false` disables it entirely for the invocation.

| Key              | Type             | Default | Description                                       |
| ---------------- | ---------------- | ------- | ------------------------------------------------- |
| `severity`       | boolean          | `true`  | Severity rollup (info/warn/error/fatal counts).   |
| `panics`         | boolean          | `true`  | Panic and fatal detection.                        |
| `http`           | boolean          | `true`  | HTTP status rollup.                               |
| `latency`        | boolean          | `true`  | Latency probe (p50/p95/p99 plus slowest entries). |
| `heartbeat`      | boolean          | `true`  | Heartbeat / gap detection.                        |
| `process_health` | boolean          | `true`  | Process health regex bucket.                      |
| `autoscaler`     | boolean          | `true`  | Autoscaler events regex bucket.                   |
| `database`       | boolean          | `true`  | Database / external errors regex bucket.          |
| `sentry`         | boolean          | `true`  | Sentry events regex bucket.                       |
| `keywords`       | array of strings | `[]`    | Ad-hoc literal terms folded into the probe set.   |
| `regexes`        | array of strings | `[]`    | Ad-hoc regex terms folded into the probe set.     |

See [`docs/PROBES.md`](PROBES.md) for what each probe matches and what
shape it reports.

### `[probes.patterns]`

Per-probe pattern overrides. Each key is a probe name; each value is an
array of `{label, pattern}` tables. Use these to extend or replace the
built-in regex sets without forking — handy for non-Fly platforms whose
log vocabulary differs from the defaults.

```toml
[probes.patterns]
autoscaler = [
    { label = "reconciling", pattern = "reconciling app" },
    { label = "scale-up",   pattern = "scaling up" },
]
database = [
    { label = "pg-deadlock", pattern = "deadlock detected" },
]
```

Both `label` and `pattern` are required strings; missing or non-string
values raise `ConfigError`.

### `[[sources]]`

An array of tables, one per captured source. Each entry needs a unique
`name` and a `type`. Every other key on the table is treated as a
type-specific option and forwarded to the source constructor.

| Key    | Type   | Required | Description                                                             |
| ------ | ------ | -------- | ----------------------------------------------------------------------- |
| `name` | string | yes      | Unique label; used as the app dir name under each run.                  |
| `type` | string | yes      | One of: `flyctl`, `wrangler`, `kubectl`, `cloudwatch`, `file`, `stdin`. |

#### `flyctl` options

| Key           | Type    | Default | Description                                                                                                                                           |
| ------------- | ------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app`         | string  | —       | Required Fly.io app name.                                                                                                                             |
| `no_tail`     | boolean | `true`  | Run `flyctl logs --no-tail` (one-shot capture; cursor filter handles overlap). The streaming form is intentionally unsupported in v1.                 |
| `samples`     | integer | `400`   | Per-iteration capture window size (`-n` on flyctl). Mirrors `reference/logs.sh`'s `--samples` default; lift on busy apps to avoid dropped lines.      |
| `format_keys` | table   | none    | JSON key overrides for the iteration parser. Each value is a string or list of strings; allowed fields: `timestamp`, `level`, `message`, `component`. |

##### `format_keys` example

For an app whose structured logs use `ts`/`severity`/`text`/`service`
instead of the Fly defaults:

```toml
[[sources]]
name = "api"
type = "flyctl"
app = "fly-api"

[sources.format_keys]
timestamp = "ts"
level = ["severity", "lvl"]
message = "text"
component = "service"
```

Unspecified fields keep their defaults (`time`/`timestamp`/…, `level`,
`msg`/`message`, `component`). Unknown field names are rejected so a
typo can't silently disable detection. Only JSON-keyed lines benefit
from `format_keys` — non-JSON shapes (plain text, regex-matched
formats) are tracked for v0.2.

#### `wrangler`, `kubectl`, `cloudwatch`, `file`, `stdin`

Stubs in v1. They satisfy the `Source` Protocol so the config layer can
name them, but `capture()` raises `NotImplementedError`. See
[`docs/SOURCES.md`](SOURCES.md) for the interface and how to land a real
implementation.

## Validation

The loader fails closed on structural and semantic errors and raises a
typed `ConfigError`. Common cases:

- A non-table where a table is expected (e.g. `[monitor]` as a list).
- A non-string `name` / `type` on a source, or a duplicate `name`.
- A non-bool probe toggle (e.g. `severity = "yes"`).
- A non-positive `[monitor].interval`.
- A negative `[monitor].iterations` or `[search].max`.
- An out-of-pattern `[monitor].run_id`.
- A `[probes.patterns]` entry missing `label` or `pattern`.
- An invalid duration string (`"5"` is fine, `"5seconds"` is not).

A directory at the discovered config path also fails closed rather than
producing a confusing `IsADirectoryError`.

## Run-dir layout (reminder)

The shape below is part of the public contract; downstream tooling
(search across runs, etc.) depends on it. Don't change it without bumping
a major version.

```text
<root>/YYYYMMDD/HHMM_<slug>_<settings>/
├── <app>/raw/<HHMMSSZ>_iter<N>.log   # cursor-filtered captures
├── <app>/<HHMMSSZ>_iter<N>.json      # per-iter aggregator input
├── <app>/<HHMMSSZ>_iter<N>.csv       # flat per-line side-output
├── <app>/.cursor                     # last-seen ISO timestamp
├── <app>/summary.md                  # cumulative aggregator summary
├── <app>/{time_series,events_per_minute,components_per_minute}.csv
├── snapshots/
│   ├── analysis_<HHMMSS>Z.md
│   └── analysis_<HHMMSS>Z.json
├── analysis.md / analysis.json
└── monitor.log
```

After a `cleanup_mode = "zip"` rotation, each older run dir keeps its
`<app>/summary.md` and `<app>/{time_series,events_per_minute,components_per_minute}.csv`,
while the per-iter logs and JSON/CSV files collapse to `<app>/raw.zip`
(transparently read by `paperbark search`).

## Examples

### Minimal Fly.io setup

```toml
[paperbark]
root = "logs"

[[sources]]
name = "web"
type = "flyctl"
app = "my-fly-web"
```

`paperbark monitor` will use the built-in defaults for cadence,
iterations, snapshot analyse cadence, and probe set.

### Two apps, custom cadence, snapshot every 30 seconds

```toml
[paperbark]
root = "logs"

[monitor]
interval = "1s"
iterations = 0
analyse_every = "30s"

[[sources]]
name = "web"
type = "flyctl"
app = "my-fly-web"

[[sources]]
name = "worker"
type = "flyctl"
app = "my-fly-worker"
```

### Probe-set tuning for a non-Fly producer

```toml
[probes]
sentry = false
keywords = ["upstream timeout", "circuit open"]

[probes.patterns]
autoscaler = [
    { label = "k8s-evict",  pattern = "Evicting pod" },
    { label = "k8s-pull",   pattern = "Pulling image" },
]
```

### Fixed run id and ad-hoc analyse keywords

```toml
[paperbark]
root = "logs"

[monitor]
run_id = "incident-pr349"

[analyse]
keywords = ["panic", "5xx"]
stdout = true
```

`paperbark monitor` writes to
`logs/<date>/<HHMM>_incident-pr349_<settings>/`; `paperbark analyse`
re-runs over that capture and prints the rendered markdown to stdout in
addition to writing the JSON + markdown files at the run root. Use
`--no-stdout` once if you want the file output but not the inline dump.
