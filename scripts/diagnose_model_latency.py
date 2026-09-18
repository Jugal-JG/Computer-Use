"""Measure request phases without logging credentials, prompts, or responses."""

import argparse
import asyncio
import json
import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from dotenv import load_dotenv

from visual_agent.contracts import Inputs
from visual_agent.discovery import Gemini
from visual_agent.vision import View


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--samples", type=int, default=2)
    args = parser.parse_args()
    load_dotenv()
    model = Gemini()
    original_client = httpx.AsyncClient
    bodies = []

    def capture(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"steps": [{"type": "model_output", "content": [{"type": "text", "text": "{}"}]}]},
        )

    run = SimpleNamespace(
        inputs=Inputs(member_id="10001", statement_month="2026-08", delivery_method="postal"),
        llm_calls=0,
        evidence=SimpleNamespace(event=lambda *a, **kw: None),
    )
    view = View(Path(args.image).read_bytes())
    with patch(
        "visual_agent.discovery.httpx.AsyncClient",
        side_effect=lambda **kw: original_client(transport=httpx.MockTransport(capture), **kw),
    ):
        await model.propose(
            run, "Prepare a savings statement request and stop at review.", view, []
        )
    tiny = {
        "model": model.model,
        "store": False,
        "input": "Reply with the single word OK.",
        "generation_config": {"max_output_tokens": 32, "thinking_level": "low"},
    }
    started = time.perf_counter()
    addresses = await asyncio.to_thread(
        socket.getaddrinfo, "generativelanguage.googleapis.com", 443, type=socket.SOCK_STREAM
    )
    print(
        json.dumps(
            {
                "event": "environment",
                "model": model.model,
                "dns_ms": round((time.perf_counter() - started) * 1000, 1),
                "address_families": sorted({a[0].name for a in addresses}),
                "proxy_env_present": [
                    k for k in os.environ if k.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
                ],
            }
        ),
        flush=True,
    )
    for sample in range(args.samples):
        for name, body in (("tiny_text", tiny), ("discovery_payload", bodies[0])):
            phases = []
            started = time.perf_counter()

            async def trace(event, info):
                phases.append(
                    {"phase": event, "ms": round((time.perf_counter() - started) * 1000, 1)}
                )

            print(
                json.dumps({"event": "request_start", "case": name, "sample": sample + 1}),
                flush=True,
            )
            # Match the application's fresh-client-per-decision behavior.
            async with original_client(timeout=45) as client:
                result = {"case": name, "sample": sample + 1}
                try:
                    response = await client.post(
                        "https://generativelanguage.googleapis.com/v1beta/interactions",
                        headers={"x-goog-api-key": model.key},
                        json=body,
                        extensions={"trace": trace},
                    )
                    result["status"] = response.status_code
                    result["usage"] = response.json().get("usage", {})
                except httpx.TransportError as exc:
                    result["error_type"] = type(exc).__name__
                result["seconds"] = round(time.perf_counter() - started, 3)
                result["phases"] = phases
                print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
