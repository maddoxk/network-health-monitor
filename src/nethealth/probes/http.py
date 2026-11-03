"""HTTP(S) probe (application layer, OSI L7).

Latency is total time to receive the response headers and body of a GET, which
includes DNS lookup + TCP connect + TLS handshake (for https) + server think time.
That is deliberately an end-to-end number; the other probes isolate the lower layers.
"""

from __future__ import annotations

import time

import httpx

from nethealth.config import Target
from nethealth.probes.base import ProbeResult


class HttpProbe:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # `transport` lets tests inject httpx.MockTransport instead of using the network.
        self._transport = transport

    async def run(self, target: Target) -> ProbeResult:
        assert target.url is not None
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=target.timeout,
                follow_redirects=True,
                verify=target.verify_tls,
                transport=self._transport,
            ) as client:
                response = await client.get(target.url)
        except httpx.TimeoutException:
            return ProbeResult(target.name, "http", False, error="http timeout")
        except httpx.HTTPError as exc:
            return ProbeResult(
                target.name, "http", False, error=f"http error: {type(exc).__name__}: {exc}"
            )
        elapsed = time.perf_counter() - start

        status = response.status_code
        ok = status in target.expect_status if target.expect_status else status < 400
        return ProbeResult(
            target.name,
            "http",
            ok,
            latency_seconds=elapsed,
            http_status=status,
            error=None if ok else f"unexpected status {status}",
        )
