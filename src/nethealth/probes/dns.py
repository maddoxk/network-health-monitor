"""DNS probe (application layer, OSI L7, usually over UDP/53).

We time a single query/response with caching disabled (dnspython's Resolver has no
cache unless you attach one), so the number reflects the resolver round trip rather
than a local cache hit. If `expect_records` is set, the answer must contain them all.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import dns.asyncresolver
import dns.exception
import dns.resolver

from nethealth.config import Target
from nethealth.probes.base import ProbeResult

# (target) -> list of answer strings. Raising dns exceptions signals failure.
ResolveFn = Callable[[Target], Awaitable[list[str]]]


async def _dnspython_resolve(target: Target) -> list[str]:
    assert target.query is not None
    resolver = dns.asyncresolver.Resolver(configure=target.nameserver is None)
    if target.nameserver:
        resolver.nameservers = [target.nameserver]
    resolver.lifetime = target.timeout
    answer = await resolver.resolve(target.query, target.record_type)
    return [rdata.to_text() for rdata in answer]


class DnsProbe:
    def __init__(self, resolve: ResolveFn = _dnspython_resolve) -> None:
        self._resolve = resolve

    async def run(self, target: Target) -> ProbeResult:
        start = time.perf_counter()
        try:
            records = await self._resolve(target)
        except dns.resolver.NXDOMAIN:
            return ProbeResult(target.name, "dns", False, error="NXDOMAIN")
        except dns.exception.Timeout:
            return ProbeResult(target.name, "dns", False, error="dns timeout")
        except dns.exception.DNSException as exc:
            return ProbeResult(target.name, "dns", False, error=f"dns error: {exc}")
        elapsed = time.perf_counter() - start

        missing = [r for r in target.expect_records if r not in records]
        if missing:
            return ProbeResult(
                target.name,
                "dns",
                False,
                latency_seconds=elapsed,
                dns_resolve_seconds=elapsed,
                error=f"missing expected records {missing}; got {records}",
            )
        return ProbeResult(
            target.name, "dns", True, latency_seconds=elapsed, dns_resolve_seconds=elapsed
        )
