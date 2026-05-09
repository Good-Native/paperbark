# paperbark

Configurable cross-source log capture, search, and analysis CLI.

Paperbark captures logs from many sources (Fly.io, Cloudflare, Kubernetes,
CloudWatch, plain files, stdin), runs a configurable set of probes over
them, and writes a stable run-directory layout that downstream tooling can
search across.

> Status: v0.1.1 released to PyPI. The probe, format, source (flyctl),
> iteration, aggregate, cursor-filter, search, dispatcher, and analyse
> layers are all wired up; `paperbark monitor` runs end to end on a
> configurable cadence with a `rich.live` ticker. See
> [docs/ROADMAP.md](docs/ROADMAP.md) for current status.

## Install

```sh
# pipx (recommended for CLI use)
pipx install paperbark

# or uv
uv tool install paperbark
```

For local development, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Quickstart

```sh
# write a starter config in the current directory
paperbark init

# add at least one source — uncomment the [[sources]] block in
# paperbark.toml and point `app` at your Fly app. monitor exits with
# "no sources configured" until you do.

# capture and analyse using config defaults (3s cadence, ~72 minutes)
paperbark monitor

# custom cadence, fixed run id, snapshots every 30s
paperbark monitor --interval 1s --run-id incident-pr349 --analyse-every 30s

# capture forever; press Ctrl+C to write the final report and exit
paperbark monitor --iterations 0

# search across captured runs
paperbark search --keyword "panic"

# re-run analysis over an existing run
paperbark analyse --run latest
```

## Configuration

Paperbark reads `./paperbark.toml` first, then
`~/.config/paperbark/config.toml`. Every CLI flag is also expressible as a
TOML key; flags override TOML at runtime. See [`docs/CONFIG.md`](docs/CONFIG.md)
for the full schema reference.

## Sources

| Source                               | Status                         |
| ------------------------------------ | ------------------------------ |
| Fly.io (`flyctl logs`)               | implemented                    |
| Cloudflare Workers (`wrangler tail`) | stub (interface only, post-v1) |
| Kubernetes (`kubectl logs`)          | stub (interface only, post-v1) |
| AWS CloudWatch                       | stub (interface only, post-v1) |
| Plain files                          | implemented                    |
| stdin                                | implemented                    |

See [`docs/SOURCES.md`](docs/SOURCES.md) for the `Source` interface and
how to add a new one.

## Log payload formats

Today: **JSON-keyed payloads only**. Per-source `format_keys` lets you
remap the canonical field names (timestamp / level / message / component)
to whatever JSON keys your app emits — see
[`docs/CONFIG.md`](docs/CONFIG.md#flyctl-options).

Coming in v0.2: **regex named-group formats** for non-JSON shapes
(pipe-delimited, syslog, Apache combined, nginx default, or any custom
pattern). The format layer already ships three presets
(`apache-combined`, `nginx-default`, `syslog-rfc5424`); they're not yet
wired into iteration. Until then, non-JSON sources will trip the
format-mismatch warning and probes will produce no findings.

## Probes

Severity rollup, panics and fatals, HTTP status, latency (p50/p95/p99),
heartbeat gap detection, process health, autoscaler events,
database/external errors, Sentry events, plus ad-hoc keyword and regex
matches. Each probe is config-toggleable; regex sets are
config-overridable. See [`docs/PROBES.md`](docs/PROBES.md) for the full
list, finding shapes, and how to add one.

## Licence

MIT — see [LICENSE](LICENSE).
