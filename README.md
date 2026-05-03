# paperbark

Configurable cross-source log capture, search, and analysis CLI.

Paperbark captures logs from many sources (Fly.io, Cloudflare, Kubernetes,
CloudWatch, plain files, stdin), runs a configurable set of probes over
them, and writes a stable run-directory layout that downstream tooling can
search across.

> Status: pre-alpha. Scaffold only. No releases yet.

## Install

Once published:

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

# capture and analyse using config defaults
paperbark monitor

# search across captured runs
paperbark search --keyword "panic"

# re-run analysis over an existing run
paperbark analyse --run latest
```

## Configuration

Paperbark reads `./paperbark.toml` first, then
`~/.config/paperbark/config.toml`. Every CLI flag is also expressible as a
TOML key; flags override TOML at runtime. Full reference will live in
`docs/CONFIG.md`.

## Sources

| Source | Status |
|---|---|
| Fly.io (`flyctl logs`) | planned for v1 |
| Cloudflare Workers (`wrangler tail`) | scaffolded, post-v1 |
| Kubernetes (`kubectl logs`) | scaffolded, post-v1 |
| AWS CloudWatch | scaffolded, post-v1 |
| Plain files | scaffolded, post-v1 |
| stdin | scaffolded, post-v1 |

See `docs/SOURCES.md` (forthcoming) for the `Source` interface and how to
add a new one.

## Probes

Severity rollup, panics and fatals, HTTP status, latency (p50/p95/p99),
heartbeat gap detection, process health, autoscaler events,
database/external errors, Sentry events, plus ad-hoc keyword and regex
matches. Each probe is config-toggleable; regex sets are
config-overridable. See `docs/PROBES.md` (forthcoming) for the full list
and how to add one.

## Licence

MIT — see [LICENSE](LICENSE).
