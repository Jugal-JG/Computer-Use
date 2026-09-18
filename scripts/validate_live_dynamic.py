"""Run real Gemini discovery and replay against an isolated synthetic app.

Usage: python scripts/validate_live_dynamic.py --output .runs/dynamic-validation
Creates a new directory; never replaces existing submission evidence.
"""

import argparse
import asyncio
import socket
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from visual_agent.capability import discovery_draft
from visual_agent.contracts import Artifact, Inputs
from visual_agent.discovery import Gemini, discover
from visual_agent.engine import Run
from visual_agent.policy import Policy
from visual_agent.proxy import create_app
from visual_agent.surface import BrowserSurface


async def main(directory):
    load_dotenv()
    model = Gemini()
    directory.mkdir(parents=True, exist_ok=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.02)
    try:
        for mode in ("discovery", "replay"):
            surface = BrowserSurface(Policy(origin=f"http://127.0.0.1:{port}"))
            run = None
            try:
                await surface.start()
                artifact = (
                    discovery_draft()
                    if mode == "discovery"
                    else Artifact.model_validate_json(
                        (directory / "capability.json").read_text(encoding="utf-8")
                    )
                )
                inputs = Inputs(
                    member_id="10001" if mode == "discovery" else "10002",
                    statement_month="2026-08" if mode == "discovery" else "2026-07",
                    delivery_method="postal" if mode == "discovery" else "electronic",
                )
                run = Run(surface, artifact, inputs, directory / mode, intervention_timeout=1)
                result = (
                    await discover(
                        run,
                        "Find the specified member, prepare a savings statement request using "
                        "the supplied inputs, and stop at the review page without submitting.",
                        directory / "capability.json",
                        model,
                    )
                    if mode == "discovery"
                    else await run.replay()
                )
                print(
                    f"{mode}: {result.status} {result.code}; model calls={result.llm_calls}",
                    flush=True,
                )
                if result.status != "success":
                    return 1
                if mode == "replay" and result.llm_calls != 0:
                    raise AssertionError("Replay made model calls")
            finally:
                await surface.close()
                if run:
                    run.evidence.close()
    finally:
        server.should_exit = True
        await task
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args().output)))
