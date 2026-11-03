"""Entry point: `nethealth --config targets.yaml --listen 0.0.0.0:8000`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from aiohttp import web

from nethealth.api import State, create_app
from nethealth.config import ConfigError, load_config
from nethealth.probes import default_probes
from nethealth.scheduler import Scheduler


async def serve(config_path: str, host: str, port: int) -> None:
    state = State(load_config(config_path))
    scheduler = Scheduler(state.config, default_probes(), state.on_result)
    runner = web.AppRunner(create_app(state))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    scheduler.start()
    logging.info("serving on %s:%d with %d targets", host, port, len(state.config.targets))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await scheduler.stop()
    await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(prog="nethealth", description=__doc__)
    parser.add_argument("--config", required=True, help="path to YAML config")
    parser.add_argument(
        "--listen", default="127.0.0.1:8000", help="host:port (default %(default)s)"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    host, _, port = args.listen.rpartition(":")
    try:
        asyncio.run(serve(args.config, host or "127.0.0.1", int(port)))
    except ConfigError as exc:
        sys.exit(f"config error: {exc}")


if __name__ == "__main__":
    main()
