from types import SimpleNamespace

from visual_agent.capability import fixture_artifact
from visual_agent.contracts import Inputs, Outputs
from visual_agent.discovery import discover, fingerprint
from visual_agent.engine import StopRun
from visual_agent.policy import Policy
from visual_agent.vision import Box


def test_fingerprint_ignores_field_contents_but_not_layout_changes():
    class View:
        width, height = 1000, 600

        def __init__(self, value, label_x=40):
            self.base_words = [
                Box("Member ID", label_x, 100, 80, 20),
                Box(value, 180, 100, 60, 20),
            ]

        def _field_rectangles(self):
            return [Box("field", 170, 90, 200, 42)]

    assert fingerprint(View("10001"), ("10001", "10002")) == fingerprint(
        View("10002"), ("10001", "10002")
    )
    assert fingerprint(View("10001")) != fingerprint(View("10001", label_x=400))


async def test_stale_proposal_never_dispatches(tmp_path):
    events = []

    class View:
        def __init__(self, x):
            self.words = [Box("Member", x, 100, 50, 20)]

        def has(self, text):
            return text in {"Member Search", "SYNTHETIC DATA"}

    class Run:
        artifact = fixture_artifact()
        id = "unit-test"
        llm_calls = 0
        interventions = 0
        inputs = Inputs(member_id="10001", statement_month="2026-08", delivery_method="postal")
        dispatched = 0
        count = 0
        evidence = SimpleNamespace(event=lambda event, **fields: events.append(event))

        async def observe(self):
            self.count += 1
            return View(10 if self.count == 1 else 100)

        async def handle(self, *a):
            return False

        async def execute_step(self, *a):
            self.dispatched += 1

        async def extract(self):
            return Outputs(
                masked_account="****0001", statement_month="2026-08", delivery_method="postal"
            )

        def finish(self, status, code, *args):
            return code

    class Model:
        model = "test-double"
        genuine = False
        count = 0

        async def propose(self, *args):
            self.count += 1
            if self.count == 1:
                return {
                    "done": False,
                    "reason": "Fill input",
                    "current_screen": "Member Search",
                    "step": {
                        "action": "type_text",
                        "target": {
                            "text": "Member ID",
                            "kind": "field",
                            "relationships": ["below"],
                        },
                        "input": "member_id",
                    },
                }
            return {
                "done": True,
                "reason": "Test termination",
                "step": None,
                "current_screen": "Member Search",
            }

    run = Run()
    result = await discover(run, "test", tmp_path / "should-not-exist.json", Model())
    assert result == "NO_DISCOVERED_STEPS"
    assert run.dispatched == 0 and "stale_model_proposal_discarded" in events
    assert not (tmp_path / "should-not-exist.json").exists()


async def test_handoff_after_click_does_not_require_previous_screen(tmp_path):
    class Screen:
        width, height = 1000, 700
        words = [Box("Start", 20, 20, 60, 20)]

        def has(self, text):
            return text in {"Start", "SYNTHETIC DATA"}

        def resolve(self, target):
            return Box("Proceed", 100, 100, 70, 20)

    class Run:
        inputs = Inputs(member_id="10001", statement_month="2026-08", delivery_method="postal")
        surface = SimpleNamespace(policy=Policy())
        evidence = SimpleNamespace(event=lambda *a, **kw: None)
        clicked = False

        async def observe(self):
            return Screen()

        async def handle(self, view, condition):
            if self.clicked:
                assert condition.text == "SYNTHETIC DATA"
                raise StopRun("HANDOFF_CONTEXT_VERIFIED")
            return False

        async def execute_step(self, step, *, verify_post):
            assert verify_post is False
            self.clicked = True

        def finish(self, status, code):
            return code

    class Model:
        async def propose(self, *args):
            return {
                "done": False,
                "reason": "test",
                "current_screen": "Start",
                "step": {"action": "click", "target": {"text": "Proceed"}},
            }

    result = await discover(Run(), "test", tmp_path / "unused.json", Model())
    assert result == "HANDOFF_CONTEXT_VERIFIED"
