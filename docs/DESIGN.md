# Design notes

This document explains how the monitor is built and, more importantly, what each probe actually measures. Reading it should let you answer "what does this number mean?" in an interview.

## 1. Architecture

```
config.yaml --> config.py (validate) --> Scheduler --asyncio tasks--> Probe.run(target) --> ProbeResult
                                                                                          |
                                                                  State.on_result <-------+
                                                                    |            |
                                                              Metrics (Prometheus)   latest dict
                                                                    |            |
                                                               GET /metrics   GET /status
```

- **config.py** parses YAML into frozen dataclasses. Validation is hand-written so each error names the target and the field. Defaults can be set once under `defaults:` and overridden per target.
- **probes/** one class per probe, each with `async def run(target) -> ProbeResult`. A probe makes ONE attempt and never raises: every failure (timeout, refused, NXDOMAIN, bad cert) becomes `ProbeResult(ok=False, error=...)`. That keeps the scheduler simple and makes failures first-class data.
- **scheduler.py** starts one asyncio task per target. Retries wrap `probe.run`. A shared `asyncio.Semaphore(max_concurrency)` limits how many probes run at once. Why: a large config would otherwise open many sockets simultaneously, and local CPU/socket contention would inflate the very latencies we are measuring. Initial start times are jittered so probes do not all fire at the same instant every cycle.
- **metrics.py / api.py** `State.on_result` is the single sink: it stores the latest result and updates Prometheus metrics. All of this runs on one event loop, so no locks are needed.
- **Dependency injection for tests.** Each probe takes its backend as a constructor argument (a ping function, a resolver function, an `httpx` transport, a cert-fetching function). Tests pass fakes, so they are fast and deterministic, and they can force the failure modes (timeout, NXDOMAIN, expired cert) that are hard to produce on a real network.

### Trade-offs

- *Pull model.* The monitor exposes `/metrics` and Prometheus scrapes it, rather than the monitor pushing. The monitor stays stateless about storage; Prometheus owns retention and alerting.
- *Gauge `probe_up` plus histogram latency.* `probe_up` answers "is it working right now?" (alert on it). The histogram answers "how fast?" over time and supports `histogram_quantile`. Only successful attempts are observed, otherwise a 5 s timeout would pollute the latency distribution; failures are counted separately in `probe_failures_total`.
- *Bounded labels.* Labels are `target` and `probe`. Error strings are NOT labels (unbounded cardinality); they appear in `/status` instead.
- *In-memory state.* Simplicity over durability. Prometheus is the history.

## 2. What each probe measures

OSI is a teaching model; real stacks are TCP/IP. The mapping below is approximate and shows where each measurement sits.

### ICMP echo (L3, network layer)
Sends an ICMP Echo Request; the peer's IP stack answers with an Echo Reply. No transport or application involved, so it measures the IP path plus the peer's kernel. Caveats: many networks rate-limit or drop ICMP, so "ping fails" does not always mean "host down", and a host can answer ping while its service is dead. Sending raw ICMP normally needs root; we use an unprivileged ICMP datagram socket (`SOCK_DGRAM`, `IPPROTO_ICMP`), where the kernel manages the identifier field. On Linux this needs `net.ipv4.ping_group_range` to include your group.

### TCP connect (L4, transport layer)
`connect()` triggers the three-way handshake: client sends SYN, server replies SYN-ACK, client sends ACK. The call returns when the SYN-ACK arrives (and our ACK is sent). So the measured time is roughly **one round trip**: SYN out, SYN-ACK back. That is why TCP connect time is a good approximation of RTT, and it works through firewalls that block ICMP. It additionally proves that something is listening on the port and the firewall lets us through. It is slightly above pure RTT because the server's kernel processes the SYN and our process has to be scheduled to notice the completion. Note it includes DNS lookup time if you pass a hostname (name resolution happens before connect); use an IP address to isolate the handshake.

### DNS (L7, application layer over UDP/53)
A stub resolver sends a query (usually one UDP datagram) and waits for the response. We time that single exchange and optionally verify the answer contains expected records. Concepts to know:
- **Caching.** Real resolvers (OS, router, recursive server like 1.1.1.1) cache answers for the record's TTL. The first query for a name walks the hierarchy (root, TLD, authoritative) and can take tens to hundreds of ms; a repeat is typically a few ms from cache. Our probe uses dnspython without a local cache, but if the *recursive server* has the answer cached you still measure a cache hit. So a low steady number mostly reflects the resolver's cache, and occasional spikes (when the TTL expires) are normal. To measure the authoritative path you would query the authoritative nameserver directly (set `nameserver:` to it).
- **NXDOMAIN** means the name does not exist; a **timeout** means no response (packet loss, server down); these are different failures and reported differently.
- Choosing `nameserver:` tests a specific resolver; omitting it tests the system's configured one (what your applications actually use).

### HTTP(S) (L7, application layer)
A GET request. Total latency includes DNS lookup, TCP handshake, TLS handshake (https), the request/response round trip, and server processing. It is the user-visible number but a poor diagnostic on its own; the lower-layer probes let you tell *which* component is slow. We follow redirects and treat status < 400 as success unless `expect_status` is set.

### TLS certificate (between L4 and L7)
Completes a TCP connection and a TLS handshake with default verification (chain of trust, hostname match, validity period), then reads the leaf certificate's `notAfter`. We report days remaining. A failed verification (wrong host, expired, untrusted issuer) is a failed probe with the reason. Alerting at 14 days gives time to fix automated renewal (for example ACME/Let's Encrypt certificates last 90 days and renew early).

## 3. Using the probes together to localise a fault

| ICMP | TCP | DNS | HTTP | Likely story |
|------|-----|-----|------|--------------|
| ok | ok | ok | slow/failing | Application or server problem |
| ok | fail | ok | fail | Service down or port filtered |
| fail | ok | ok | ok | ICMP filtered (not an outage) |
| ok | ok | fail | fail (by name) | DNS problem; reachable by IP |
| ok | ok | ok | ok but TLS days low | Renewal pipeline broken |

## 4. Prometheus and Grafana pieces

- Prometheus scrapes `monitor:8000/metrics` every 5 s (demo value; 15 to 60 s is typical in production).
- `alerts.yml`: `TargetDown` uses `probe_up == 0` with `for: 2m` to ignore single blips; `HighLatency` divides the rate of the histogram's `_sum` by `_count` to get a mean over 5 minutes; `TlsCertExpiringSoon` is a plain threshold on the gauge.
- Grafana is provisioned from files (datasource and dashboard JSON) so the stack is reproducible with no clicking. The dashboard panels: status, mean latency, p95 from `histogram_quantile`, DNS time, cert days remaining, HTTP status, failures per minute.
- Alertmanager is not included, so alerts are visible in Prometheus's UI but not delivered anywhere.

## 5. What I would do next

SNMP polling (for example interface counters from an `snmpd` container), per-probe packet-loss measurement with multiple ICMP echoes, persistence of last state, and authentication on the API.
