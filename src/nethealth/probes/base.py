"""Shared types for all probes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from nethealth.config import Target


@dataclass
class ProbeResult:
    """Outcome of one probe run (after retries). Probes never raise; failures land here."""

    target: str
    probe: str
    ok: bool
    latency_seconds: float | None = None
    error: str | None = None
    attempts: int = 1
    timestamp: float = field(default_factory=time.time)
    # Probe-specific extras; None when the probe type does not produce them.
    http_status: int | None = None
    dns_resolve_seconds: float | None = None
    tls_expiry_days: float | None = None


class Probe(Protocol):
    """A probe performs ONE attempt. Retries and concurrency live in the scheduler."""

    async def run(self, target: Target) -> ProbeResult: ...
