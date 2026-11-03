"""TCP connect probe (transport layer, OSI L4).

Measures the time for the three-way handshake: we send SYN, the peer answers SYN-ACK,
and connect() returns once our ACK is sent. That is about one network round trip, so
connect time is a good proxy for RTT without needing ICMP privileges.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from nethealth.config import Target
from nethealth.probes.base import ProbeResult


class TcpProbe:
    async def run(self, target: Target) -> ProbeResult:
        assert target.host is not None and target.port is not None
        start = time.perf_counter()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(target.host, target.port), timeout=target.timeout
            )
        except TimeoutError:
            return ProbeResult(target.name, "tcp", False, error="connect timeout")
        except OSError as exc:  # refused, unreachable, DNS failure (gaierror) ...
            return ProbeResult(target.name, "tcp", False, error=f"connect failed: {exc}")
        elapsed = time.perf_counter() - start
        writer.close()
        with contextlib.suppress(OSError):  # teardown errors do not matter
            await writer.wait_closed()
        return ProbeResult(target.name, "tcp", True, latency_seconds=elapsed)
