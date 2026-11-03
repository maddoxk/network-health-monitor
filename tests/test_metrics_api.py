from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from aiohttp.test_utils import TestClient, TestServer
from prometheus_client import CollectorRegistry
from prometheus_client.parser import text_string_to_metric_families

from nethealth.api import State, create_app
from nethealth.config import Config
from nethealth.metrics import Metrics
from nethealth.probes.base import ProbeResult
from tests.conftest import make_target


def parse(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    out: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            out[(s.name, tuple(sorted(s.labels.items())))] = s.value
    return out


def key(name: str, **labels: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (name, tuple(sorted(labels.items())))


def test_metrics_record_success_and_failure() -> None:
    from prometheus_client import generate_latest

    reg = CollectorRegistry()
    m = Metrics(reg)
    m.record(
        ProbeResult("web", "http", True, latency_seconds=0.03, http_status=200, timestamp=1000.0)
    )
    m.record(ProbeResult("db", "tcp", False, error="connect timeout"))
    m.record(ProbeResult("cert", "tls", True, latency_seconds=0.1, tls_expiry_days=42.5))
    m.record(ProbeResult("dns1", "dns", True, latency_seconds=0.02, dns_resolve_seconds=0.02))
    s = parse(generate_latest(reg).decode())

    assert s[key("probe_up", target="web", probe="http")] == 1
    assert s[key("probe_up", target="db", probe="tcp")] == 0
    assert s[key("probe_failures_total", target="db", probe="tcp")] == 1
    assert s[key("probe_http_status_code", target="web")] == 200
    assert s[key("tls_cert_expiry_days", target="cert")] == 42.5
    assert s[key("dns_resolve_seconds", target="dns1")] == 0.02
    assert s[key("probe_last_success_timestamp_seconds", target="web", probe="http")] == 1000.0
    # 0.03 s falls in the 0.05 bucket and every larger one, but not in 0.025.
    assert s[key("probe_latency_seconds_bucket", target="web", probe="http", le="0.025")] == 0
    assert s[key("probe_latency_seconds_bucket", target="web", probe="http", le="0.05")] == 1
    assert s[key("probe_latency_seconds_count", target="web", probe="http")] == 1
    # failed probes must not pollute the latency histogram
    assert key("probe_latency_seconds_count", target="db", probe="tcp") not in s


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:  # type: ignore[type-arg]
    cfg = Config(
        (
            make_target(name="web", type="http", url="http://x.test/"),
            make_target(name="db", host="db.test", port=5432),
            make_target(name="pending", host="p.test", port=1),
        )
    )
    state = State(cfg)
    state.on_result(ProbeResult("web", "http", True, latency_seconds=0.02, http_status=200))
    state.on_result(ProbeResult("db", "tcp", False, error="connect timeout"))
    async with TestClient(TestServer(create_app(state))) as c:
        yield c


async def test_health(client: TestClient) -> None:  # type: ignore[type-arg]
    body = await (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["targets_configured"] == 3 and body["targets_probed"] == 2


async def test_targets(client: TestClient) -> None:  # type: ignore[type-arg]
    body = await (await client.get("/targets")).json()
    assert [t["name"] for t in body] == ["web", "db", "pending"]


async def test_status_includes_unprobed_as_null(client: TestClient) -> None:  # type: ignore[type-arg]
    body = await (await client.get("/status")).json()
    assert body["summary"] == {"up": 1, "down": 1}
    assert body["targets"]["web"]["http_status"] == 200
    assert body["targets"]["db"]["error"] == "connect timeout"
    assert body["targets"]["pending"] is None


async def test_metrics_endpoint_serves_prometheus_text(client: TestClient) -> None:  # type: ignore[type-arg]
    resp = await client.get("/metrics")
    assert resp.status == 200
    assert resp.content_type == "text/plain"
    s = parse(await resp.text())
    assert s[key("probe_up", target="web", probe="http")] == 1
