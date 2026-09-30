"""CLI entry point: ``python -m edge_voice``.

Starts the relay and blocks until interrupted. The dashboard is served
separately by the Vite dev server (``npm run dev`` in ``dashboard/``); for a
venue with no reliable network, build the bundle once with ``npm run build``
and serve ``dist/`` from any static file server.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from dataclasses import replace

from edge_voice.asr.service import build_engine
from edge_voice.config import AppConfig
from edge_voice.relay import TelemetryRelay

LOGGER = logging.getLogger("edge_voice")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edge_voice", description="Edge Voice Activator relay")
    parser.add_argument("--host", help="bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, help="bind port (default 8765)")
    parser.add_argument(
        "--no-asr",
        action="store_true",
        help="run without speech recognition; every other panel keeps working",
    )
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # PyAV and ctranslate2 are chatty at INFO on model load.
    logging.getLogger("av").setLevel(logging.WARNING)


async def _amain(argv: list[str] | None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    config = AppConfig.from_env()
    relay_overrides: dict[str, object] = {}
    if args.host:
        relay_overrides["host"] = args.host
    if args.port:
        relay_overrides["port"] = args.port
    if relay_overrides:
        config = replace(config, relay=replace(config.relay, **relay_overrides))
    if args.no_asr:
        config = replace(config, asr=replace(config.asr, engine="none"))

    engine = build_engine(config.asr)
    relay = TelemetryRelay(config, engine)
    heartbeat = relay.start_heartbeat()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError, ValueError):
            loop.add_signal_handler(getattr(signal, signal_name), stop.set)

    serve_task = asyncio.create_task(relay.serve_forever())
    await stop.wait()

    LOGGER.info("Shutting down")
    relay.request_stop()
    heartbeat.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat
    serve_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serve_task
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(_amain(argv))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
