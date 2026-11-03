"""TLS certificate probe (presentation/application layer, between L4 and L7).

Opens a TCP connection, completes the TLS handshake with full certificate and
hostname verification, then reads the leaf certificate's notAfter date and reports
days remaining. A verification failure (expired, wrong host, untrusted CA) is a
failed probe with the reason attached.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
import time
from collections.abc import Awaitable, Callable

from nethealth.config import Target
from nethealth.probes.base import ProbeResult

SECONDS_PER_DAY = 86400.0

# (target) -> notAfter as epoch seconds. Raises ssl/OS errors on failure.
FetchNotAfterFn = Callable[[Target], Awaitable[float]]


def days_until(not_after_epoch: float, now: float | None = None) -> float:
    return (not_after_epoch - (time.time() if now is None else now)) / SECONDS_PER_DAY


def not_after_from_cert(cert: dict[str, object]) -> float:
    """Convert the dict returned by SSLSocket.getpeercert() into epoch seconds."""
    raw = cert.get("notAfter")
    if not isinstance(raw, str):
        raise ssl.SSLError("peer certificate has no notAfter field")
    return float(ssl.cert_time_to_seconds(raw))


async def _fetch_not_after(target: Target) -> float:
    assert target.host is not None and target.port is not None
    context = ssl.create_default_context()
    _, writer = await asyncio.open_connection(
        target.host, target.port, ssl=context, server_hostname=target.host
    )
    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        cert = ssl_obj.getpeercert() if ssl_obj else None
        if not cert:
            raise ssl.SSLError("no peer certificate")
        return not_after_from_cert(cert)
    finally:
        writer.close()
        with contextlib.suppress(OSError):  # teardown errors do not matter
            await writer.wait_closed()


class TlsProbe:
    def __init__(self, fetch_not_after: FetchNotAfterFn = _fetch_not_after) -> None:
        self._fetch = fetch_not_after

    async def run(self, target: Target) -> ProbeResult:
        start = time.perf_counter()
        try:
            not_after = await asyncio.wait_for(self._fetch(target), timeout=target.timeout)
        except TimeoutError:
            return ProbeResult(target.name, "tls", False, error="tls handshake timeout")
        except (ssl.SSLError, OSError) as exc:  # SSLCertVerificationError is an SSLError
            return ProbeResult(target.name, "tls", False, error=f"tls error: {exc}")
        elapsed = time.perf_counter() - start
        days = days_until(not_after)
        return ProbeResult(
            target.name,
            "tls",
            days > 0,
            latency_seconds=elapsed,
            tls_expiry_days=days,
            error=None if days > 0 else "certificate expired",
        )
