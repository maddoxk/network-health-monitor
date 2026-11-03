"""ICMP echo probe (network layer, OSI L3).

Trade-off: raw ICMP sockets need root (or CAP_NET_RAW). We use icmplib with
privileged=False, which opens an unprivileged ICMP *datagram* socket (SOCK_DGRAM).
That works without root on macOS and on Linux when the kernel allows it via
`net.ipv4.ping_group_range` (Docker sets a permissive default on recent versions).
When it is not allowed the probe reports a clear error instead of crashing.
The cost: the kernel, not us, controls the ICMP identifier/sequence, and we get
only summary RTT (no TTL or raw reply inspection).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from icmplib import async_ping
from icmplib.exceptions import ICMPLibError

from nethealth.config import Target
from nethealth.probes.base import ProbeResult

# (host, timeout) -> round-trip time in seconds, or None if no reply.
PingFn = Callable[[str, float], Awaitable[float | None]]


async def _icmplib_ping(host: str, timeout: float) -> float | None:
    result = await async_ping(host, count=1, timeout=timeout, privileged=False)
    return result.avg_rtt / 1000.0 if result.is_alive else None


class IcmpProbe:
    def __init__(self, ping: PingFn = _icmplib_ping) -> None:
        self._ping = ping

    async def run(self, target: Target) -> ProbeResult:
        assert target.host is not None
        try:
            rtt = await self._ping(target.host, target.timeout)
        except (ICMPLibError, OSError) as exc:
            return ProbeResult(target.name, "icmp", False, error=f"icmp error: {exc}")
        if rtt is None:
            return ProbeResult(target.name, "icmp", False, error="no echo reply (timeout)")
        return ProbeResult(target.name, "icmp", True, latency_seconds=rtt)
