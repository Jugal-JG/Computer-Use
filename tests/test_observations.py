from types import SimpleNamespace

from visual_agent.capability import fixture_artifact
from visual_agent.contracts import Inputs
from visual_agent.engine import Run


async def test_identical_capture_reuses_ocr_but_changed_capture_invalidates_it(
    monkeypatch, tmp_path
):
    calls = []

    def perceive(png):
        calls.append(png)
        return SimpleNamespace(png=png, width=1000, height=700)

    class Surface:
        session_id = "cache-test"
        png = b"first-frame"

        async def observe(self):
            return self.png

    monkeypatch.setattr("visual_agent.engine.View", perceive)
    surface = Surface()
    run = Run(
        surface,
        fixture_artifact(),
        Inputs(member_id="10002", statement_month="2026-07", delivery_method="electronic"),
        tmp_path,
    )
    try:
        first = await run.observe()
        assert await run.observe() is first
        surface.png = b"changed-frame"
        assert await run.observe() is not first
        assert calls == [b"first-frame", b"changed-frame"]
    finally:
        run.evidence.close()
