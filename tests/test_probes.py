"""Probe tests. No external network: fakes, MockTransport, and loopback sockets only."""

from __future__ import annotations

import asyncio
import ssl
import time

import dns.exception
import dns.resolver
import httpx
import pytest

from nethealth.config import Target
from nethealth.probes.dns import DnsProbe
from nethealth.probes.http import HttpProbe
from nethealth.probes.icmp import IcmpProbe
from nethealth.probes.tcp import TcpProbe
from nethealth.probes.tls import TlsProbe, days_until, not_after_from_cert
from tests.conftest import make_target

# ---------- ICMP ----------


async def test_icmp_success() -> None:
    async def ping(host: str, timeout: float) -> float | None:
        assert host == "10.0.0.1"
        return 0.012

    r = await IcmpProbe(ping).run(make_target(type="icmp", host="10.0.0.1"))
    assert r.ok and r.latency_seconds == 0.012


async def test_icmp_no_reply() -> None:
    async def ping(host: str, timeout: float) -> float | None:
        return None

    r = await IcmpProbe(ping).run(make_target(type="icmp", host="10.0.0.1"))
    assert not r.ok and r.latency_seconds is None and "timeout" in (r.error or "")


async def test_icmp_permission_error_is_a_failed_result_not_a_crash() -> None:
    async def ping(host: str, timeout: float) -> float | None:
        raise PermissionError("ICMP sockets not allowed")

    r = await IcmpProbe(ping).run(make_target(type="icmp", host="10.0.0.1"))
    assert not r.ok and "not allowed" in (r.error or "")


# ---------- TCP (real loopback server) ----------


async def test_tcp_connect_success_and_refused() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        ok = await TcpProbe().run(make_target(host="127.0.0.1", port=port))
        assert ok.ok and ok.latency_seconds is not None and ok.latency_seconds < 1
    finally:
        server.close()
        await server.wait_closed()

    refused = await TcpProbe().run(make_target(host="127.0.0.1", port=port))  # now closed
    assert not refused.ok and "connect failed" in (refused.error or "")


async def test_tcp_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(asyncio, "open_connection", hang)
    t0 = time.perf_counter()
    r = await TcpProbe().run(make_target(host="10.255.255.1", port=9, timeout=0.05))
    assert not r.ok and r.error == "connect timeout"
    assert time.perf_counter() - t0 < 1


# ---------- DNS ----------


def dns_target(**kw: object) -> Target:
    return make_target(type="dns", query="example.com", **kw)


async def test_dns_success_with_expected_records() -> None:
    async def resolve(t: Target) -> list[str]:
        return ["93.184.216.34", "93.184.216.35"]

    r = await DnsProbe(resolve).run(dns_target(expect_records=("93.184.216.34",)))
    assert r.ok and r.dns_resolve_seconds is not None and r.latency_seconds is not None


async def test_dns_missing_expected_record_fails_but_reports_timing() -> None:
    async def resolve(t: Target) -> list[str]:
        return ["1.1.1.1"]

    r = await DnsProbe(resolve).run(dns_target(expect_records=("9.9.9.9",)))
    assert not r.ok and "missing expected" in (r.error or "")
    assert r.dns_resolve_seconds is not None


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (dns.resolver.NXDOMAIN(), "NXDOMAIN"),
        (dns.exception.Timeout(), "dns timeout"),
        (dns.resolver.NoAnswer(), "dns error"),
    ],
)
async def test_dns_failures(exc: Exception, expected: str) -> None:
    async def resolve(t: Target) -> list[str]:
        raise exc

    r = await DnsProbe(resolve).run(dns_target())
    assert not r.ok and expected in (r.error or "")


# ---------- HTTP (httpx.MockTransport) ----------


def http_target(**kw: object) -> Target:
    return make_target(type="http", url="http://svc.test/", **kw)


async def test_http_ok_default_accepts_below_400() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(204))
    r = await HttpProbe(transport).run(http_target())
    assert r.ok and r.http_status == 204 and r.latency_seconds is not None


async def test_http_unexpected_status() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    r = await HttpProbe(transport).run(http_target())
    assert not r.ok and r.http_status == 503 and "503" in (r.error or "")


async def test_http_expect_status_list() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    r = await HttpProbe(transport).run(http_target(expect_status=(404,)))
    assert r.ok


async def test_http_timeout_and_connect_error() -> None:
    def timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    def refused(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    r1 = await HttpProbe(httpx.MockTransport(timeout)).run(http_target())
    r2 = await HttpProbe(httpx.MockTransport(refused)).run(http_target())
    assert r1.error == "http timeout"
    assert not r2.ok and "ConnectError" in (r2.error or "")


# ---------- TLS ----------


def tls_target() -> Target:
    return make_target(type="tls", host="example.test", port=443)


async def test_tls_days_remaining() -> None:
    async def fetch(t: Target) -> float:
        return time.time() + 20 * 86400

    r = await TlsProbe(fetch).run(tls_target())
    assert r.ok and r.tls_expiry_days is not None and 19.9 < r.tls_expiry_days < 20.1


async def test_tls_expired_cert_is_failure_with_negative_days() -> None:
    async def fetch(t: Target) -> float:
        return time.time() - 86400

    r = await TlsProbe(fetch).run(tls_target())
    assert not r.ok and r.tls_expiry_days is not None and r.tls_expiry_days < 0


async def test_tls_verification_error_is_failure() -> None:
    async def fetch(t: Target) -> float:
        raise ssl.SSLCertVerificationError("hostname mismatch")

    r = await TlsProbe(fetch).run(tls_target())
    assert not r.ok and "hostname mismatch" in (r.error or "")


def test_not_after_parsing_and_days_until() -> None:
    # Format returned by ssl.SSLSocket.getpeercert()
    epoch = not_after_from_cert({"notAfter": "Jan  1 00:00:00 2030 GMT"})
    assert days_until(epoch, now=epoch - 86400 * 3) == pytest.approx(3.0)
    with pytest.raises(ssl.SSLError):
        not_after_from_cert({})
