"""Scripted provider tests the compiler/executor, not LLM reasoning evidence."""

import asyncio
import json
import socket

import pytest
import uvicorn

from visual_agent.capability import discovery_draft
from visual_agent.contracts import Artifact, Inputs
from visual_agent.discovery import discover
from visual_agent.engine import Run
from visual_agent.policy import Policy
from visual_agent.proxy import create_app
from visual_agent.surface import BrowserSurface


class RenamedUI:
    """Test-only UI variant: names absent from the authored capability catalog."""

    def __init__(self):
        self.app = create_app()

    async def __call__(self, scope, receive, send):
        async def renamed(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (k, v) for k, v in message["headers"] if k.lower() != b"content-length"
                ]
            if message["type"] == "http.response.body":
                body = message.get("body", b"")
                for old, new in {
                    b"Member Search": b"Client Lookup",
                    b"Member ID": b"Client Code",
                    b">Search<": b">Find client<",
                    b"Member Details": b"Client Summary",
                    b"Open savings": b"Explore deposits",
                    b"Savings Account": b"Deposit Overview",
                    b"Prepare statement": b"Draft a document",
                    b"Statement Request": b"Document Options",
                    b"Statement month": b"Period",
                    b"Delivery method": b"Dispatch channel",
                    b"Review request": b"Preview document",
                    b"Statement Review": b"Document Preview",
                    b"Masked account": b"Account reference",
                }.items():
                    body = body.replace(old, new)
                message["body"] = body
            await send(message)

        await self.app(scope, receive, renamed)


async def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.02)
    return server, task, port


class ScriptedObserver:
    model = "scripted-ui-test"
    genuine = False

    def __init__(self):
        self.index = 0

    async def propose(self, run, goal, view, history):
        actions = [
            ("Client Lookup", "type_text", "Client Code", "member_id"),
            ("Client Lookup", "click", "Find client", None),
            ("Client Summary", "click", "Explore deposits", None),
            ("Deposit Overview", "click", "Draft a document", None),
            ("Document Options", "type_text", "Period", "statement_month"),
            ("Document Options", "type_text", "Dispatch channel", "delivery_method"),
            ("Document Options", "click", "Preview document", None),
        ]
        if self.index == 0:
            assert run.artifact.targets == {} and run.artifact.steps == []
        if self.index < len(actions):
            screen, action, label, input_name = actions[self.index]
            self.index += 1
            return {
                "done": False,
                "reason": "scripted integration test",
                "current_screen": screen,
                "step": {
                    "action": action,
                    "input": input_name,
                    "target": {
                        "text": label,
                        "kind": "field" if input_name else "text",
                        "relationships": ["right_of", "below"] if input_name else [],
                    },
                },
            }
        return {
            "done": True,
            "reason": "scripted integration test",
            "current_screen": "Document Preview",
            "extractions": {
                name: {"text": label, "kind": "value", "relationships": ["right_of"]}
                for name, label in {
                    "masked_account": "Account reference",
                    "statement_month": "Period",
                    "delivery_method": "Dispatch channel",
                }.items()
            },
        }


@pytest.mark.parametrize("resize_during_entry", [False, True])
async def test_unknown_labels_compile_and_replay_without_model(
    tmp_path, monkeypatch, resize_during_entry
):
    server, task, port = await serve(RenamedUI())
    inputs = Inputs(member_id="10001", statement_month="2026-08", delivery_method="postal")
    artifact_path = tmp_path / "learned.json"
    try:
        for mode in ("discover", "replay"):

            class ResizeSurface(BrowserSurface):
                resized = False

                async def click(self, x, y):
                    await super().click(x, y)
                    if resize_during_entry and run.step == "step_5" and not self.resized:
                        self.resized = True
                        await self.page.set_viewport_size({"width": 500, "height": 700})

            surface = ResizeSurface(Policy(origin=f"http://127.0.0.1:{port}"))
            run = None
            try:
                await surface.start()
                artifact = (
                    discovery_draft()
                    if mode == "discover"
                    else Artifact.model_validate_json(artifact_path.read_text())
                )
                if mode == "replay":
                    inputs = Inputs(
                        member_id="10002", statement_month="2026-07", delivery_method="electronic"
                    )

                    async def forbidden(*args, **kwargs):
                        raise AssertionError("Replay called model")

                    monkeypatch.setattr("visual_agent.discovery.Gemini.propose", forbidden)
                run = Run(surface, artifact, inputs, tmp_path / mode, intervention_timeout=1)
                result = (
                    await discover(run, "test", artifact_path, ScriptedObserver())
                    if mode == "discover"
                    else await run.replay()
                )
                assert result.status == "success", result.model_dump()
                assert result.outputs.masked_account == "****" + inputs.member_id[-4:]
                assert result.llm_calls == 0  # scripted provider, never claim real LLM evidence
            finally:
                await surface.close()
                if run:
                    run.evidence.close()
        saved = json.loads(artifact_path.read_text())
        assert saved["schema_version"] == "3.0"
        assert len(saved["steps"]) == 7
        assert saved["entry_condition"] == "Client Lookup"
        assert saved["success_condition"] == "Document Preview"
        assert saved["provenance"]["genuine_discovery"] is False
        assert "Find client" in str(saved["targets"])
        assert "10001" not in artifact_path.read_text()
    finally:
        server.should_exit = True
        await task
