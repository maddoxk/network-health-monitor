"""Prometheus metrics.

We use our own CollectorRegistry (not the global default) so tests can create isolated
instances and the /metrics output contains only our series, not Python process noise.

Naming follows Prometheus conventions: base units in the name (_seconds, _days),
`_total` for counters, and a small, bounded set of labels (target, probe). Labels must
never hold unbounded values such as error strings, or cardinality explodes.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from nethealth.probes.base import ProbeResult

# Buckets chosen for network latencies: 1 ms (LAN) up to 5 s (the probe timeout range).
LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class Metrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        labels = ["target", "probe"]
        self.up = Gauge(
            "probe_up", "1 if the last probe succeeded, else 0", labels, registry=registry
        )
        self.latency = Histogram(
            "probe_latency_seconds",
            "Latency of successful probe attempts",
            labels,
            buckets=LATENCY_BUCKETS,
            registry=registry,
        )
        self.failures = Counter(
            "probe_failures",  # exposed as probe_failures_total
            "Probe runs that failed after all retries",
            labels,
            registry=registry,
        )
        self.last_success = Gauge(
            "probe_last_success_timestamp_seconds",
            "Unix time of the last successful probe",
            labels,
            registry=registry,
        )
        self.dns_resolve = Gauge(
            "dns_resolve_seconds",
            "Duration of the last DNS resolution",
            ["target"],
            registry=registry,
        )
        self.tls_expiry = Gauge(
            "tls_cert_expiry_days",
            "Days until the TLS certificate expires (negative if expired)",
            ["target"],
            registry=registry,
        )
        self.http_status = Gauge(
            "probe_http_status_code",
            "HTTP status code of the last response",
            ["target"],
            registry=registry,
        )

    def record(self, result: ProbeResult) -> None:
        t, p = result.target, result.probe
        self.up.labels(t, p).set(1 if result.ok else 0)
        if result.ok:
            if result.latency_seconds is not None:
                self.latency.labels(t, p).observe(result.latency_seconds)
            self.last_success.labels(t, p).set(result.timestamp)
        else:
            self.failures.labels(t, p).inc()
        # Extras are set even on failure when known (e.g. an expired cert, a 503).
        if result.dns_resolve_seconds is not None:
            self.dns_resolve.labels(t).set(result.dns_resolve_seconds)
        if result.tls_expiry_days is not None:
            self.tls_expiry.labels(t).set(result.tls_expiry_days)
        if result.http_status is not None:
            self.http_status.labels(t).set(result.http_status)
