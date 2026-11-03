"""HTTP surface: /metrics for Prometheus plus a small JSON API for humans and scripts."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from nethealth.config import Config
from nethealth.metrics import Metrics
from nethealth.probes.base import ProbeResult


class State:
    """Latest result per target. Single event loop, so a plain dict needs no locking."""

    def __init__(self, config: Config, registry: CollectorRegistry | None = None) -> None:
        self.config = config
        self.registry = registry or CollectorRegistry()
        self.metrics = Metrics(self.registry)
        self.latest: dict[str, ProbeResult] = {}
        self.started = time.time()

    def on_result(self, result: ProbeResult) -> None:
        self.latest[result.target] = result
        self.metrics.record(result)


STATE_KEY = web.AppKey("state", State)


def _result_json(result: ProbeResult) -> dict[str, Any]:
    data = asdict(result)
    data["last_checked"] = data.pop("timestamp")
    return data


async def health(request: web.Request) -> web.Response:
    state = request.app[STATE_KEY]
    return web.json_response(
        {
            "status": "ok",
            "uptime_seconds": round(time.time() - state.started, 1),
            "targets_configured": len(state.config.targets),
            "targets_probed": len(state.latest),
        }
    )


async def targets(request: web.Request) -> web.Response:
    state = request.app[STATE_KEY]
    return web.json_response([asdict(t) for t in state.config.targets])


async def status(request: web.Request) -> web.Response:
    state = request.app[STATE_KEY]
    body = {
        t.name: _result_json(state.latest[t.name]) if t.name in state.latest else None
        for t in state.config.targets
    }
    ok = sum(1 for r in state.latest.values() if r.ok)
    return web.json_response(
        {"summary": {"up": ok, "down": len(state.latest) - ok}, "targets": body}
    )


async def metrics(request: web.Request) -> web.Response:
    state = request.app[STATE_KEY]
    # aiohttp wants content_type without the charset parameter.
    return web.Response(
        body=generate_latest(state.registry), content_type=CONTENT_TYPE_LATEST.split(";")[0]
    )


def create_app(state: State) -> web.Application:
    app = web.Application()
    app[STATE_KEY] = state
    app.add_routes(
        [
            web.get("/health", health),
            web.get("/targets", targets),
            web.get("/status", status),
            web.get("/metrics", metrics),
        ]
    )
    return app
