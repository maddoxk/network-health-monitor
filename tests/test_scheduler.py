from __future__ import annotations

import asyncio

from nethealth.config import Config, Target
from nethealth.probes.base import ProbeResult
from nethealth.scheduler import Scheduler, run_with_retries
from tests.conftest import make_target


class FlakyProbe:
    """Fails the first `fail_times` calls, then succeeds."""

    def __init__(self, fail_times: int) -> None:
        self.calls = 0
        self.fail_times = fail_times

    async def run(self, target: Target) -> ProbeResult:
        self.calls += 1
        ok = self.calls > self.fail_times
        return ProbeResult(target.name, target.type, ok, error=None if ok else "boom")


async def test_retry_until_success() -> None:
    probe = FlakyProbe(fail_times=2)
    r = await run_with_retries(probe, make_target(retries=3), retry_delay=0)
    assert r.ok and r.attempts == 3 and probe.calls == 3


async def test_retries_exhausted_returns_last_failure() -> None:
    probe = FlakyProbe(fail_times=99)
    r = await run_with_retries(probe, make_target(retries=2), retry_delay=0)
    assert not r.ok and r.attempts == 3


async def test_crashing_probe_becomes_failed_result() -> None:
    class Crash:
        async def run(self, target: Target) -> ProbeResult:
            raise RuntimeError("bug")

    r = await run_with_retries(Crash(), make_target(), retry_delay=0)
    assert not r.ok and "internal error" in (r.error or "")


async def test_semaphore_bounds_concurrency() -> None:
    in_flight = 0
    peak = 0

    class Slow:
        async def run(self, target: Target) -> ProbeResult:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return ProbeResult(target.name, target.type, True)

    targets = tuple(make_target(name=f"t{i}", interval=0.2, timeout=0.1) for i in range(8))
    results: list[ProbeResult] = []
    sched = Scheduler(Config(targets, max_concurrency=2), {"tcp": Slow()}, results.append)
    sched.start()
    await asyncio.sleep(0.6)
    await sched.stop()
    assert len(results) >= 8  # every target ran at least once
    assert peak == 2
