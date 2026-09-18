import asyncio
import socket
from pathlib import Path

import pytest
import uvicorn

from visual_agent.capability import fixture_artifact
from visual_agent.contracts import Inputs, Target
from visual_agent.engine import Run
from visual_agent.policy import Policy, PolicyDenied
from visual_agent.proxy import create_app
from visual_agent.surface import BrowserSurface


async def start_proxy(scenario):
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
    return server, task, port


@pytest.mark.parametrize("resize_when", ["focus", "typing"])
@pytest.mark.parametrize("native_window", [False, True])
async def test_discovered_artifact_resize_during_field_entry(
    resize_when, native_window, monkeypatch
):
    import os

    from visual_agent.contracts import Artifact
    from visual_agent.discovery import Gemini

    async def forbidden(*args, **kwargs):
        raise AssertionError("Replay invoked model")

    monkeypatch.setattr(Gemini, "propose", forbidden)
    artifact = Artifact.model_validate_json(
        Path(
            os.environ.get("RESIZE_TEST_ARTIFACT", "evidence/dynamic-v3/capability.json")
        ).read_text(encoding="utf-8")
    )
    server, task, port = await start_proxy("normal")

    class ResizingSurface(BrowserSurface):
        resized = False

        async def resize(self):
            self.resized = True
            if native_window:
                session = await self.context.new_cdp_session(self.page)
                window = await session.send("Browser.getWindowForTarget")
                await session.send(
                    "Browser.setWindowBounds",
                    {
                        "windowId": window["windowId"],
                        "bounds": {"width": 500, "height": 375},
                    },
                )
                await session.detach()
            else:
                await self.page.set_viewport_size({"width": 500, "height": 288})

        async def click(self, x, y):
            await super().click(x, y)
            if resize_when == "focus" and run.step == artifact.steps[4].id and not self.resized:
                await self.resize()

        async def type_text(self, value):
            if resize_when == "typing" and value == "2026-07" and not self.resized:
                await self.resize()
            await super().type_text(value)

    surface = ResizingSurface(
        Policy(origin=f"http://127.0.0.1:{port}"),
        viewport=(1264, 705),
        headed=native_window,
        window_following=native_window,
    )
    run = None
    try:
        await surface.start()
        run = Run(
            surface,
            artifact,
            Inputs(member_id="10002", statement_month="2026-07", delivery_method="electronic"),
            Path(".runs/tests/resize-discovered") / resize_when,
            intervention_timeout=1,
        )
        result = await run.replay()
        assert (result.status, result.code) == ("success", "REVIEW_READY")
        assert surface.resized and result.llm_calls == 0
        assert result.outputs.statement_month == "2026-07"
        assert result.outputs.delivery_method == "electronic"
    finally:
        await surface.close()
        server.should_exit = True
        await task
        if run:
            run.evidence.close()


@pytest.mark.parametrize(
    "scenario,status,code",
    [
        ("normal", "success", "REVIEW_READY"),
        ("not_found", "business_outcome", "MEMBER_NOT_FOUND"),
        ("validation", "business_outcome", "VALIDATION_REJECTED"),
        ("permission", "business_outcome", "ACCESS_DENIED"),
        ("slow", "success", "REVIEW_READY"),
        ("notice", "success", "REVIEW_READY"),
        ("app_error", "failure", "APP_UNAVAILABLE"),
    ],
)
@pytest.mark.parametrize("viewport", [(1280, 800), (500, 270)])
async def test_replay_scenarios(scenario, status, code, monkeypatch, viewport):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    # If anyone accidentally wires the model into replay, fail loudly.
    from visual_agent.discovery import Gemini

    async def forbidden(*args, **kwargs):
        raise AssertionError("Replay invoked model")

    monkeypatch.setattr(Gemini, "propose", forbidden)
    server, task, port = await start_proxy(scenario)
    surface = BrowserSurface(Policy(origin=f"http://127.0.0.1:{port}"), viewport=viewport)
    run = None
    try:
        await surface.start()
        run = Run(
            surface,
            fixture_artifact(),
            Inputs(
                member_id="10002",
                statement_month="2026-07",
                delivery_method="electronic",
            ),
            Path(".runs/tests") / scenario,
            intervention_timeout=1,
        )
        result = await run.replay()
        if result.code != code and run.last_view:
            # Synthetic fixture screenshot for development QA, never real records.
            (run.evidence.directory / "synthetic-debug.png").write_bytes(run.last_view.png)
            print([(w.text, w.confidence) for w in run.last_view.words])
        assert (result.status, result.code) == (status, code)
        assert result.llm_calls == 0
        if status == "success":
            assert result.outputs.masked_account == "****0002"
    finally:
        await surface.close()
        server.should_exit = True
        await task
        if run:
            run.evidence.close()


@pytest.mark.parametrize(
    "scenario,button",
    [
        ("unknown_dialog", "Acknowledge and continue"),
        ("oversized_dialog", "Acknowledge and continue"),
        ("session_expired", "Restore training session"),
    ],
)
@pytest.mark.parametrize("viewport", [(1280, 800), (500, 400)])
async def test_handoff_same_session(scenario, button, viewport):
    server, task, port = await start_proxy(scenario)
    surface = BrowserSurface(Policy(origin=f"http://127.0.0.1:{port}"), viewport=viewport)
    run = None
    try:
        await surface.start()
        session = surface.session_id
        run = Run(
            surface,
            fixture_artifact(),
            Inputs(member_id="10001", statement_month="2026-08", delivery_method="postal"),
            Path(".runs/tests") / scenario,
            intervention_timeout=30,
            operator_kind="simulated_test_operator",
        )
        running = asyncio.create_task(run.replay())
        for _ in range(300):
            if run.state == "WAITING_FOR_HUMAN" or running.done():
                break
            await asyncio.sleep(0.1)
        assert run.state == "WAITING_FOR_HUMAN"
        stale = run.generation
        await run.take_control(stale)
        with pytest.raises(PolicyDenied):
            await run.human_action({"generation": stale, "kind": "click", "x": 1, "y": 1})
        with pytest.raises(PolicyDenied):
            await run.resume(run.generation)
        view = await run.observe()
        b = view.resolve(Target(text=button))
        await run.human_action(
            {
                "generation": run.generation,
                "kind": "click",
                "x": b.center[0],
                "y": b.center[1],
            }
        )
        await asyncio.sleep(0.2)
        await run.resume(run.generation)
        result = await running
        assert result.status == "success" and result.session_id == session
        events = (run.evidence.directory / "events.jsonl").read_text()
        assert "human_action" in events and "resume_verified" in events
    finally:
        await surface.close()
        server.should_exit = True
        await task
        if run:
            run.evidence.close()


async def test_browser_loss_is_structured():
    server, task, port = await start_proxy("normal")
    surface = BrowserSurface(Policy(origin=f"http://127.0.0.1:{port}"))
    run = None
    try:
        await surface.start()
        run = Run(
            surface,
            fixture_artifact(),
            Inputs(member_id="10001", statement_month="2026-08", delivery_method="postal"),
            Path(".runs/tests/browser_loss"),
            intervention_timeout=1,
        )
        await surface.close()
        result = await run.replay()
        assert result.status == "failure" and result.code == "SESSION_LOST"
    finally:
        server.should_exit = True
        await task
        if run:
            run.evidence.close()


@pytest.mark.parametrize(
    "viewport", [(800, 900), (1024, 768), (1366, 768), (1440, 900), (1920, 1080)]
)
async def test_replay_is_viewport_independent(viewport):
    server, task, port = await start_proxy("normal")
    surface = BrowserSurface(Policy(origin=f"http://127.0.0.1:{port}"), viewport=viewport)
    run = None
    try:
        await surface.start()
        run = Run(
            surface,
            fixture_artifact(),
            Inputs(member_id="10002", statement_month="2026-07", delivery_method="electronic"),
            Path(".runs/tests/responsive") / f"{viewport[0]}x{viewport[1]}",
            intervention_timeout=1,
        )
        result = await run.replay()
        if result.status != "success" and run.last_view:
            (run.evidence.directory / "synthetic-debug.png").write_bytes(run.last_view.png)
        assert result.status == "success" and result.llm_calls == 0
    finally:
        await surface.close()
        server.should_exit = True
        await task
        if run:
            run.evidence.close()


async def test_narrow_request_fields_after_desktop_discovery():
    server, task, port = await start_proxy("normal")
    surface = BrowserSurface(Policy(origin=f"http://127.0.0.1:{port}"))
    run = None
    try:
        await surface.start()
        artifact = fixture_artifact()
        for name, y in [("month", 0.29), ("delivery", 0.36)]:
            artifact.targets[name].geometry_hint = (0.39, y, 0.24, 0.05)
        run = Run(
            surface,
            artifact,
            Inputs(member_id="10002", statement_month="2026-07", delivery_method="electronic"),
            Path(".runs/tests/responsive/narrow-request"),
            intervention_timeout=1,
        )
        for step in artifact.steps[:3]:
            await run.execute_step(step)
        await surface.page.set_viewport_size({"width": 500, "height": 250})
        await run.execute_step(artifact.steps[3])
        await surface.page.set_viewport_size({"width": 500, "height": 391})
        for step in artifact.steps[4:6]:
            await run.execute_step(step)
        assert run.last_view.read_field(artifact.targets["month"]) == "2026-07"
        assert run.last_view.read_field(artifact.targets["delivery"]) == "electronic"
        await run.execute_step(artifact.steps[6])
        assert run.last_view.has("Statement Review")
        assert run.llm_calls == 0
    finally:
        await surface.close()
        server.should_exit = True
        await task
        if run:
            run.evidence.close()


@pytest.mark.parametrize("window_following", [False, True])
async def test_resize_between_replay_steps(window_following):
    server, task, port = await start_proxy("normal")

    class ResizingSurface(BrowserSurface):
        clicks = 0

        async def click(self, x, y):
            await super().click(x, y)
            self.clicks += 1
            if self.clicks == 2:
                if self.window_following:
                    session = await self.context.new_cdp_session(self.page)
                    window = await session.send("Browser.getWindowForTarget")
                    await session.send(
                        "Browser.setWindowBounds",
                        {
                            "windowId": window["windowId"],
                            "bounds": {"width": 1440, "height": 1000},
                        },
                    )
                    await session.detach()
                else:
                    await self.page.set_viewport_size({"width": 1440, "height": 900})

    surface = ResizingSurface(
        Policy(origin=f"http://127.0.0.1:{port}"),
        viewport=(1024, 768),
        headed=window_following,
        window_following=window_following,
    )
    run = None
    try:
        await surface.start()
        await surface.observe()
        initial_dimensions = surface.dimensions
        run = Run(
            surface,
            fixture_artifact(),
            Inputs(member_id="10002", statement_month="2026-07", delivery_method="electronic"),
            Path(".runs/tests/responsive/resize-during-run"),
            intervention_timeout=1,
        )
        result = await run.replay()
        if result.status != "success" and run.last_view:
            (run.evidence.directory / "synthetic-debug.png").write_bytes(run.last_view.png)
        assert result.status == "success" and result.llm_calls == 0
        assert surface.dimensions != initial_dimensions
        if window_following:
            assert surface.page.viewport_size is None
    finally:
        await surface.close()
        server.should_exit = True
        await task
        if run:
            run.evidence.close()


async def test_device_scale_factor_is_translated_to_browser_coordinates():
    server, task, port = await start_proxy("normal")
    surface = BrowserSurface(
        Policy(origin=f"http://127.0.0.1:{port}"),
        viewport=(1024, 768),
        device_scale_factor=1.5,
    )
    run = None
    try:
        await surface.start()
        run = Run(
            surface,
            fixture_artifact(),
            Inputs(member_id="10002", statement_month="2026-07", delivery_method="electronic"),
            Path(".runs/tests/responsive/device-scale"),
            intervention_timeout=1,
        )
        result = await run.replay()
        if result.status != "success" and run.last_view:
            (run.evidence.directory / "synthetic-debug.png").write_bytes(run.last_view.png)
        assert result.status == "success" and result.llm_calls == 0
    finally:
        await surface.close()
        server.should_exit = True
        await task
        if run:
            run.evidence.close()


async def test_operator_endpoint_auth_and_stale_control(tmp_path):
    from types import SimpleNamespace

    import httpx

    from visual_agent.operator import create_operator

    run = Run(
        SimpleNamespace(session_id="test-session"),
        fixture_artifact(),
        Inputs(member_id="10001", statement_month="2026-08", delivery_method="postal"),
        tmp_path,
    )
    app, token = create_operator(run)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/state")).status_code == 401
            headers = {"Authorization": "Bearer " + token}
            assert (await client.get("/state", headers=headers)).status_code == 200
            # No intervention is active, so takeover is forbidden even with a valid token.
            assert (
                await client.post("/take", headers=headers, json={"generation": 0})
            ).status_code == 409
    finally:
        run.evidence.close()
