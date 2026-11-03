"""Scheduler: runs every target on its own interval with bounded concurrency.

Design: one asyncio task per target (cheap), but a single shared Semaphore caps how
many probes are *in flight* at once so a big config cannot open hundreds of sockets
simultaneously and distort its own measurements.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Callable, Mapping

from nethealth.config import Config, Target
from nethealth.probes.base import Probe, ProbeResult

log = logging.getLogger(__name__)

ResultCallback = Callable[[ProbeResult], None]

RETRY_DELAY_SECONDS = 0.5


async def run_with_retries(
    probe: Probe, target: Target, retry_delay: float = RETRY_DELAY_SECONDS
) -> ProbeResult:
    """Run a probe up to 1 + target.retries times; return the first success or last failure."""
    result: ProbeResult | None = None
    for attempt in range(1, target.retries + 2):
        try:
            result = await probe.run(target)
        except Exception as exc:  # a buggy probe must not kill the scheduler
            log.exception("probe %s crashed", target.name)
            result = ProbeResult(target.name, target.type, False, error=f"internal error: {exc!r}")
        result.attempts = attempt
        if result.ok:
            break
        if attempt <= target.retries:
            await asyncio.sleep(retry_delay)
    assert result is not None
    return result


class Scheduler:
    def __init__(
        self,
        config: Config,
        probes: Mapping[str, Probe],
        on_result: ResultCallback,
        retry_delay: float = RETRY_DELAY_SECONDS,
    ) -> None:
        self._config = config
        self._probes = probes
        self._on_result = on_result
        self._retry_delay = retry_delay
        self._sem = asyncio.Semaphore(config.max_concurrency)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def _loop(self, target: Target) -> None:
        # Random initial delay spreads probes out instead of firing all at t=0 every cycle.
        await self._sleep(random.uniform(0, min(target.interval, 2.0)))
        while not self._stop.is_set():
            started = time.monotonic()
            async with self._sem:
                result = await run_with_retries(
                    self._probes[target.type], target, self._retry_delay
                )
            self._on_result(result)
            await self._sleep(max(0.0, target.interval - (time.monotonic() - started)))

    async def _sleep(self, seconds: float) -> None:
        """Sleep that wakes immediately on shutdown."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._loop(t)) for t in self._config.targets]

    async def stop(self) -> None:
        self._stop.set()
        await asyncio.gather(*self._tasks, return_exceptions=True)
