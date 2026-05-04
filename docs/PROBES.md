# Probes reference

Probes are the analysis layer of paperbark. Each probe consumes a stream of
[`CanonicalRecord`](../src/paperbark/probes/_record.py)s — the
source-agnostic shape every captured line is parsed into — and emits a
JSON-serialisable report at finalisation. The set is configurable: toggle
individual probes off, override their regex sets, or fold ad-hoc keywords
in, all without forking. See
[`src/paperbark/probes/`](../src/paperbark/probes/) for the implementation
and [`docs/CONFIG.md`](CONFIG.md#probes) for the TOML keys.

## The probe contract

Every probe implements the [`Probe`](../src/paperbark/probes/_base.py)
Protocol — a `name` attribute, a `feed(record)` method, and a `report()`
method. Implementations must:

- Be cheap to instantiate (probes are rebuilt on every analyse run).
- Hold no I/O resources.
- Tolerate records arriving slightly out of timestamp order (the
  cursor filter de-duplicates before probes see anything, but
  per-iteration capture order is not strictly monotonic across
  multi-app runs).

`feed` is called once per canonical record; `report` is called once at the
end. Findings — the per-label rollups every regex-bucket-style probe emits
— share the project-wide shape:

```json
{
  "label": "<bucket label>",
  "count": 42,
  "first_seen": "2026-05-03T14:30:01+00:00",
  "last_seen": "2026-05-03T14:35:12+00:00",
  "peak": "2026-05-03T14:32",
  "peak_count": 11,
  "samples": ["...up to three short raw-line samples..."]
}
```

The four keys `count`, `first_seen`, `last_seen`, and `peak` are the
public contract; `peak_count` and `samples` are conveniences. Non-bucket
probes (`Latency`, `Heartbeat`) emit shapes documented per-probe below.

## The canonical record

Probes never branch on the source. The format adapter turns each raw line
into:

| Field         | Type            | Notes                                                                         |
| ------------- | --------------- | ----------------------------------------------------------------------------- |
| `timestamp`   | `str`           | ISO-8601 with offset; empty when no timestamp could be extracted.             |
| `level`       | `str`           | Lower-cased; one of the known levels or empty.                                |
| `message`     | `str`           |                                                                               |
| `component`   | `str`           |                                                                               |
| `status`      | `str`           | Three-digit HTTP status, or empty.                                            |
| `duration_ms` | `float \| None` | Explicit `*_ms` keys honoured; bare `duration` is read as Go-style nanoseconds. |
| `raw_line`    | `str`           | Original line; regex-style probes match against this, not the parsed JSON.    |

`parse_line` lives in
[`src/paperbark/probes/_record.py`](../src/paperbark/probes/_record.py).
The format layer is small enough to extend for non-Fly producers; do that
rather than thread source-specific shapes into individual probes.

## Built-in probe set

Probes report in the order listed below. Toggle each one with a
`[probes]` key (see [`CONFIG.md`](CONFIG.md#probes)).

### `Severity` — toggle `severity`

Counts records per severity level. Known levels (in canonical order):
`debug`, `info`, `warn`, `error`, `fatal`. Any non-empty level outside
that set rolls up under `unknown-level` so typos and bespoke severities
are visible without polluting the rollup.

Reports `findings` keyed by level, each with the standard
`{count, first_seen, last_seen, peak, ...}` shape.

### `Panics & fatals` — toggle `panics`

Buckets `panic:` and `fatal[: |error:]` lines by their first-line cause
(matched on the raw line, case-insensitive). The cause is trimmed to the
first 120 characters so a long panic message becomes a stable bucket
label rather than blowing up the report. The top ten causes by count are
returned.

### `HTTP status` — toggle `http`

Two views of the same traffic: per-class buckets (`2xx`, `3xx`, `4xx`,
`5xx`) and explicit per-code buckets for the codes operators most often
need to triage on (`429`, `499`, `500`, `502`, `503`, `504`). Findings are
sorted by label so adjacent class/code rows are easy to scan.

### `Latency` — toggle `latency`

Records every `record.duration_ms` and reports `samples`, `p50_ms`,
`p95_ms`, `p99_ms`, `max_ms`, `mean_ms`, plus the ten slowest entries
(timestamp + trimmed line). Values < 0 ms or > 1 hour are rejected as
parsing artefacts.

When no duration field was seen, the report carries
`{"findings": [], "note": "no duration fields seen"}` — the empty
findings list keeps the JSON shape consistent across runs.

### `Heartbeat` — toggle `heartbeat`

Detects minutes inside a run window where info-level traffic dropped to
zero mid-flight — a reliable signal that an app stopped emitting healthy
chatter even when it didn't crash outright.

Reports `median_info_per_minute`, `first_minute` / `last_minute` of the
observed window, and up to twenty `gap_minutes` (the first and last
observed minutes are skipped because they are typically partial windows
caused by capture starting or stopping mid-minute). The gap detector also
synthesises minutes that fell between two observed minutes with no log
lines at all, so a fully silent stretch still surfaces.

When no timestamped traffic was captured the report is
`{"findings": [], "note": "no timestamped traffic"}`.

### `Process health` — toggle `process_health`

Fly-flavoured process lifecycle vocabulary on the raw line. Default
labels:

| Label                 | Pattern                                  |
| --------------------- | ---------------------------------------- |
| `starting machine`    | `starting machine`                       |
| `stopping machine`    | `stopping machine`                       |
| `exited with code`    | `exited with code\s+\d+`                 |
| `out of memory`       | `out of memory\|oom[- ]?killed`          |
| `killed by signal`    | `killed by signal\|signal:\s*killed`     |
| `health check failed` | `health check.*fail`                     |
| `restart`             | `\brestart(ing)?\b`                      |

Replace the entire set under
`[probes.patterns].process_health` for non-Fly platforms.

### `Autoscaler` — toggle `autoscaler`

| Label          | Pattern                                          |
| -------------- | ------------------------------------------------ |
| `reconciling`  | `"msg":\s*"reconciling"\|reconciling\s+app`      |
| `scale up`     | `scal(e\|ing)\s*up\|adding machine`              |
| `scale down`   | `scal(e\|ing)\s*down\|removing machine`          |
| `target=N`     | `target\s*[=:]\s*\d+\|"target":\s*\{`            |
| `queue depth`  | `queue[_ ]depth\|backlog\s*[=:]\s*\d+`           |
| `no-op`        | `no scale change\|already at target`             |

Replace under `[probes.patterns].autoscaler`.

### `Database / external` — toggle `database`

| Label                       | Pattern                       |
| --------------------------- | ----------------------------- |
| `pgx error`                 | `\bpgx\b.*error\|pgx:.*`      |
| `pq error`                  | `\bpq:\s`                     |
| `connection refused`        | `connection refused`          |
| `context deadline exceeded` | `context deadline exceeded`   |
| `i/o timeout`               | `i/o timeout`                 |
| `connection reset`          | `connection reset`            |
| `too many connections`      | `too many connections`        |

Replace under `[probes.patterns].database`.

### `Sentry` — toggle `sentry`

| Label         | Pattern                                  |
| ------------- | ---------------------------------------- |
| `event sent`  | `sentry.*event\b\|event sent to sentry`  |
| `send failed` | `sentry.*(?:fail\|error)`                |

Replace under `[probes.patterns].sentry`.

### Ad-hoc keywords (always trailing, never toggled)

When `[probes].keywords`, `[probes].regexes`, `--keyword`, or `--regex`
is non-empty, paperbark appends an `Ad-hoc keywords` regex-bucket probe.
Literal keywords are escaped before compilation; regex strings are passed
through unchanged. CLI flags **append to** TOML-supplied terms (they do
not replace), so a `[probes].regexes = ["panic"]` plus `--regex 5xx` runs
both.

Labels are `keyword:<term>` or `regex:<term>` so the source of each
finding stays visible in the JSON report.

## Configuring probes

The TOML schema is the source of truth — see
[`CONFIG.md`](CONFIG.md#probes). Three things to remember:

- **Toggles win.** Setting `process_health = false` drops the probe
  entirely, even if `[probes.patterns].process_health` supplies an
  override.
- **`[probes.patterns]` overrides replace, they do not extend.** When
  you supply an override list for a probe, only those labels run for that
  probe. Copy the built-in labels across if you want to extend the set.
- **CLI flags append to TOML probe terms.** `[probes].keywords` and
  `[probes].regexes` are folded into the same `Ad-hoc keywords` bucket
  as `--keyword` / `--regex`; CLI flags don't replace TOML values for
  this surface (they do for `[analyse].keywords` / `[search].keywords`,
  which is a different code path).

A non-toggle string in `[probes]` (e.g. `severity = "yes"`) raises
`ConfigError` rather than coercing to a boolean.

## Adding a new probe

A new probe usually means one new file under
[`src/paperbark/probes/`](../src/paperbark/probes/) and three small
edits elsewhere. The end-to-end shape:

1. **Implement.** Add `src/paperbark/probes/<name>.py` with a class
   that satisfies the `Probe` Protocol — a `name` attribute, a
   `feed(record)` method, and a `report()` method that returns a
   JSON-serialisable dict containing at minimum `name` and either
   `findings` or the probe-specific keys (see `Latency` /
   `Heartbeat` for non-bucket shapes). Reuse
   [`Bucket`](../src/paperbark/probes/_bucket.py) for the standard
   `{count, first_seen, last_seen, peak, ...}` rollup so the output
   shape stays consistent.
2. **Register.** Import the class in
   [`src/paperbark/probes/__init__.py`](../src/paperbark/probes/__init__.py),
   add it to `__all__`, and append a construction step inside
   `default_probes()` guarded by `cfg.is_enabled("<toggle>")`. Pick
   a stable position in the report order — once a probe ships it
   becomes part of the public report shape downstream tooling reads.
3. **Add the toggle.** Add the new key to `PROBE_NAMES` and the
   matching `bool` field on `ProbesConfig` in
   [`src/paperbark/config.py`](../src/paperbark/config.py); the
   loader handles `[probes].<name> = false` automatically once it is
   in the tuple.
4. **Document.** Add a section here, and a row to the
   `[probes]` table in [`CONFIG.md`](CONFIG.md#probes).
5. **Test.** A unit test under `tests/test_probes_<name>.py` exercising
   `feed` / `report`, plus a row in `tests/test_probes_config.py` if the
   probe accepts pattern overrides via `[probes.patterns]`.

If the probe is a regex-bucket variant — match a list of `(label,
pattern)` entries against the raw line and roll up findings — reuse
[`RegexBucketProbe`](../src/paperbark/probes/regex_bucket.py) instead
of writing a new class. Add a tuple to `_REGEX_PROBES` in
`paperbark/probes/__init__.py`; that wires the toggle, default
patterns, and `[probes.patterns]` override hook in one step.

## Provenance

The default probe set, regex vocabularies, and finding shape are ported
from `scripts/process_logs.py` in
[`Good-Native/hover`](https://github.com/Good-Native/hover) (MIT). The
TOML toggle/override surface is new; the finding shape is unchanged so
search-across-runs tooling that consumes the bash output continues to
work against paperbark's reports.
