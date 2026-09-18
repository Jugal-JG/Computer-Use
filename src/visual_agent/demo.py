"""Evaluation harness. Scenario configuration never enters the execution engine."""

import asyncio
import json
import socket
from pathlib import Path

import uvicorn

from .contracts import Artifact, Inputs
from .engine import Run
from .policy import Policy
from .surface import BrowserSurface


async def suite(artifact_path, directory):
    # Only this harness imports the proxy; the actual automation cannot access it.
    from .proxy import create_app

    raw = Path(artifact_path).read_text(encoding="utf-8")
    if json.loads(raw).get("schema_version") == "1.0":
        raise SystemExit("Migrate the schema-v1 artifact with 'visual-agent migrate-v1'.")
    artifact = Artifact.model_validate_json(raw)
    cases = {
        "normal": "REVIEW_READY",
        "not_found": "MEMBER_NOT_FOUND",
        "validation": "VALIDATION_REJECTED",
        "permission": "ACCESS_DENIED",
        "slow": "REVIEW_READY",
        "notice": "REVIEW_READY",
        "app_error": "APP_UNAVAILABLE",
    }
    summary = []
    for scenario, expected in cases.items():
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(create_app(scenario), log_level="error", access_log=False)
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            await asyncio.sleep(0.02)
        surface = BrowserSurface(Policy(origin=f"http://127.0.0.1:{port}"))
        run = None
        try:
            await surface.start()
            run = Run(
                surface,
                artifact,
                Inputs(member_id="10002", statement_month="2026-07", delivery_method="electronic"),
                Path(directory) / scenario,
                intervention_timeout=1,
            )
            result = await run.replay()
            record = {
                "scenario": scenario,
                "expected": expected,
                "actual": result.code,
                "passed": result.code == expected,
                "llm_calls": result.llm_calls,
                "evidence_dir": result.evidence_dir,
            }
            summary.append(record)
            print(json.dumps(record), flush=True)
        finally:
            await surface.close()
            server.should_exit = True
            await task
            if run:
                run.evidence.close()
    Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(directory) / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if all(r["passed"] for r in summary) else 1
