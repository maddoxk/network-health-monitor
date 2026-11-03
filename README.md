# network-health-monitor

An asyncio network health monitor. It probes targets listed in a YAML file at several network layers, exposes the results as Prometheus metrics and a small JSON API, and ships with a docker-compose stack (Prometheus + alert rules + a provisioned Grafana dashboard).

It is a learning/portfolio project, not a replacement for [blackbox_exporter](https://github.com/prometheus/blackbox_exporter), which is more complete.

## What it monitors

| Probe | Layer | What it measures |
|-------|-------|------------------|
| `icmp` | L3 network | Echo round-trip time (unprivileged ICMP datagram socket via `icmplib`) |
| `tcp`  | L4 transport | Time for the TCP three-way handshake (about one RTT) |
| `dns`  | L7 application | Resolver query time, optional check that expected records are present (`dnspython`) |
| `http` | L7 application | Status code and end-to-end GET latency (`httpx`) |
| `tls`  | between L4 and L7 | Days until the server certificate expires, after full verification |

Each target has its own `interval`, `timeout`, and `retries`. Concurrency is bounded by a shared `asyncio.Semaphore` (`max_concurrency`). See [`config.example.yaml`](config.example.yaml).

## Quick start

Local (Python 3.12+):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
nethealth --config config.example.yaml --listen 127.0.0.1:8000
curl localhost:8000/status
curl localhost:8000/metrics
```

Full stack (needs Docker):

```bash
docker compose up --build
# monitor    http://localhost:8000   (/health /targets /status /metrics)
# Prometheus http://localhost:9090   (alerts: /alerts)
# Grafana    http://localhost:3000   (admin / admin, demo only; dashboard "Network Health Overview")
```

## Endpoints

- `GET /metrics` Prometheus exposition.
- `GET /health` liveness plus counts of configured and probed targets.
- `GET /targets` the validated configuration.
- `GET /status` latest result per target (`null` until the first probe completes).

## Metrics

| Metric | Type | Labels |
|--------|------|--------|
| `probe_up` | gauge | target, probe |
| `probe_latency_seconds` | histogram (successful probes only) | target, probe |
| `probe_failures_total` | counter | target, probe |
| `probe_last_success_timestamp_seconds` | gauge | target, probe |
| `dns_resolve_seconds` | gauge | target |
| `tls_cert_expiry_days` | gauge | target |
| `probe_http_status_code` | gauge | target |

Alert rules (`deploy/prometheus/alerts.yml`): `TargetDown` (probe failing 2 min), `HighLatency` (mean > 1 s for 5 min), `TlsCertExpiringSoon` (< 14 days), `MonitorStopped`.

## Development and testing

```bash
ruff check . && ruff format --check . && mypy && pytest
```

- Unit tests use fakes (injected ping/resolver/cert-fetch functions, `httpx.MockTransport`, loopback sockets), so they need no internet.
- CI (GitHub Actions) runs ruff, mypy `--strict`, and pytest on Python 3.12 and 3.13, then a `compose-smoke` job that builds the stack, waits for `/health`, and asserts `/metrics`, Prometheus scrape targets and `probe_up`, alert-rule loading, and Grafana datasource/dashboard provisioning (`scripts/smoke_test.py`).
- The five probe types were also run by hand against live targets (1.1.1.1, example.com) from a macOS laptop; that is a manual check, not a benchmark, and no performance numbers are claimed.

## Limitations

- No SNMP (not implemented).
- ICMP uses unprivileged sockets: it works on macOS and on Linux only where `net.ipv4.ping_group_range` allows the user's group (the compose file sets it for the container). Otherwise the probe reports an error. It sends one echo per attempt and reports only RTT (no loss percentage or TTL).
- State is in memory only; restarting the monitor resets `/status`, and the histogram restarts from zero (Prometheus handles counter resets).
- One vantage point: results describe the network path from wherever the monitor runs.
- No authentication on the API; it is meant for localhost or a trusted network. The Grafana password in compose is a demo value.
- HTTP probe follows redirects and issues GET only; no custom headers, body, or content assertions.
- DNS probe does not distinguish UDP vs TCP fallback or measure DNSSEC.

## Design

See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and the networking concepts behind each probe.

## License

MIT
