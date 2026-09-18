import io
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from visual_agent.capability import fixture_artifact
from visual_agent.contracts import Inputs
from visual_agent.discovery import Gemini, ModelUnavailable
from visual_agent.vision import Unresolved


class Events:
    def __init__(self):
        self.rows = []

    def event(self, event, **fields):
        self.rows.append((event, fields))


def fake_run():
    return SimpleNamespace(
        llm_calls=0,
        state="RUNNING",
        inputs=Inputs(member_id="10001", statement_month="2026-08", delivery_method="postal"),
        artifact=fixture_artifact(),
        evidence=Events(),
    )


class FakeView:
    png = b"synthetic-test-image"

    def resolve(self, t):
        raise Unresolved("No candidates in provider unit test")


class LargeView(FakeView):
    def __init__(self):
        image = Image.new("RGB", (1920, 1080), "white")
        data = io.BytesIO()
        image.save(data, format="PNG")
        self.png = data.getvalue()


async def test_transport_diagnostics_preserve_phase_without_logging_secrets(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-secret")

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 45

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            await kwargs["extensions"]["trace"](
                "http11.receive_response_headers.failed", {"exception": "private-header"}
            )
            raise httpx.ReadTimeout("unit-test-secret private-header")

    async def sleep(_):
        pass

    monkeypatch.setattr("visual_agent.discovery.httpx.AsyncClient", Client)
    monkeypatch.setattr("visual_agent.discovery.asyncio.sleep", sleep)
    run = fake_run()
    with pytest.raises(ModelUnavailable):
        await Gemini().propose(run, "test", FakeView(), [])
    timing = [fields for event, fields in run.evidence.rows if event == "model_request_timing"]
    assert len(timing) == 4
    assert all(row["error_type"] == "ReadTimeout" for row in timing)
    assert timing[0]["phases"][0]["phase"] == "http11.receive_response_headers.failed"
    assert "unit-test-secret" not in str(run.evidence.rows)
    assert "private-header" not in str(run.evidence.rows)


@pytest.mark.parametrize("status,expected_calls", [(429, 4), (503, 4), (401, 1), (400, 1)])
async def test_provider_retry_budget(monkeypatch, status, expected_calls):
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-not-a-real-key")

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, *a, **kw):
            return httpx.Response(
                status,
                headers={"Retry-After": "0"},
                json={"error": {"message": "DO_NOT_LOG_THIS"}},
            )

    async def sleep(_):
        pass

    monkeypatch.setattr("visual_agent.discovery.httpx.AsyncClient", Client)
    monkeypatch.setattr("visual_agent.discovery.asyncio.sleep", sleep)
    run = fake_run()
    with pytest.raises(ModelUnavailable):
        await Gemini().propose(run, "test", FakeView(), [])
    assert run.llm_calls == expected_calls
    assert "unit-test-not-a-real-key" not in str(run.evidence.rows)
    assert "DO_NOT_LOG_THIS" not in str(run.evidence.rows)


async def test_retry_after_exceeding_budget_stops(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-not-a-real-key")

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, *a, **kw):
            return httpx.Response(429, headers={"Retry-After": "3600"})

    monkeypatch.setattr("visual_agent.discovery.httpx.AsyncClient", Client)
    run = fake_run()
    with pytest.raises(ModelUnavailable):
        await Gemini().propose(run, "test", FakeView(), [])
    assert run.llm_calls == 1


async def test_current_interactions_response_and_no_store(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-not-a-real-key")
    payload = {
        "done": False,
        "reason": "Populate member input",
        "current_screen": "Member Search",
        "step": {
            "action": "type_text",
            "target": {"text": "Member ID", "kind": "field", "relationships": ["right_of"]},
            "input": "member_id",
        },
    }
    import json

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kw):
            assert url.endswith("/interactions")
            assert kw["json"]["store"] is False
            assert kw["json"]["response_format"]["schema"]["type"] == "object"
            instruction = json.loads(kw["json"]["input"][0]["text"])
            assert "candidates" not in instruction
            assert "supported_screen_headings" not in instruction
            assert "Open savings" not in str(instruction)
            assert "current_screen" in kw["json"]["response_format"]["schema"]["properties"]
            return httpx.Response(
                200,
                json={
                    "steps": [
                        {"type": "thought", "signature": "not-logged"},
                        {
                            "type": "model_output",
                            "content": [{"type": "text", "text": json.dumps(payload)}],
                        },
                    ],
                    "usage": {"total_tokens": 10},
                },
            )

    monkeypatch.setattr("visual_agent.discovery.httpx.AsyncClient", Client)
    run = fake_run()
    assert await Gemini().propose(run, "test", FakeView(), []) == payload
    assert "not-logged" not in str(run.evidence.rows)


async def test_model_image_is_downsampled_for_target_selection(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-not-a-real-key")
    seen = {}

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, _url, **kw):
            import base64
            import json

            seen["image"] = base64.b64decode(kw["json"]["input"][1]["data"])
            return httpx.Response(
                200,
                json={
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(
                                        {"done": True, "reason": "done", "step": None}
                                    ),
                                }
                            ],
                        }
                    ]
                },
            )

    monkeypatch.setattr("visual_agent.discovery.httpx.AsyncClient", Client)
    run = fake_run()
    await Gemini().propose(run, "test", LargeView(), [])
    image = Image.open(io.BytesIO(seen["image"]))
    assert image.size == (960, 540)
