import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from visual_agent.capability import discovery_draft
from visual_agent.contracts import Artifact, Inputs, Target
from visual_agent.discovery import ground_target
from visual_agent.policy import Policy, PolicyDenied
from visual_agent.vision import Box, Unresolved, View


def test_empty_discovery_and_non_replayable_draft():
    draft = discovery_draft()
    assert draft.targets == {} and draft.steps == [] and draft.extractions == {}
    assert draft.entry_condition == draft.success_condition == ""
    with pytest.raises(ValueError):
        Artifact.model_validate(draft.model_dump())


def test_dynamic_policy_and_optional_label_allowlist():
    Policy().check("click", "Explore deposits")
    with pytest.raises(PolicyDenied):
        Policy().check("click", "Transfer funds")
    with pytest.raises(PolicyDenied):
        Policy(allowed_targets=[]).check("click", "Explore deposits")


def test_model_cannot_weaken_confidence_or_compile_input_values():
    inputs = Inputs(member_id="10001", statement_month="2026-08", delivery_method="postal")
    view = object.__new__(View)
    view.width, view.height, view.foreground = 1000, 500, None
    view.words = [Box("Explore", 100, 100, 70, 20, 71)]
    with pytest.raises(Unresolved):
        ground_target(Target(text="Explore", min_confidence=0, winner_margin=0), view, inputs)
    with pytest.raises(ValueError, match="NON_STATIC_LOCATOR"):
        ground_target(Target(text="10001"), view, inputs)


@pytest.mark.parametrize("kind", ["field", "value"])
@pytest.mark.parametrize(
    "relationship,x,y",
    [
        ("right_of", 410, 200),
        ("left_of", 70, 200),
        ("below", 270, 250),
        ("above", 270, 140),
    ],
)
def test_one_reader_resolves_and_reads_all_sides(kind, relationship, x, y):
    # Real Tesseract and OpenCV; no expected value is supplied to the locator.
    image = Image.new("RGB", (800, 500), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font = ImageFont.truetype("DejaVuSans.ttf", 24)
    draw.text((280, 205), "Reference", font=font, fill="black")
    if kind == "field":
        draw.rectangle((x, y, x + 185, y + 42), outline="black", width=2)
    draw.text((x + 12, y + 7), "AB123", font=font, fill="black")
    png = io.BytesIO()
    image.save(png, format="PNG")
    view = View(png.getvalue())
    target = Target(
        text="Reference",
        kind=kind,
        relationships=(relationship,),
        geometry_hint=(x / 800, y / 500, 185 / 800, 42 / 500),
    )
    assert view.read_value(target) == "AB123"


def test_read_box_invalid_bounds_and_ambiguity_are_not_guessed():
    view = object.__new__(View)
    view.width, view.height, view.foreground = 800, 500, None
    view.image = Image.new("RGB", (800, 500), "white")
    with pytest.raises(Unresolved, match="VALUE_REGION_INVALID"):
        view.read_box(Box("", 5, 5, 4, 4), inset=5)
    view.words = [
        Box("Reference", 380, 100, 40, 20),
        Box("one", 310, 140, 50, 20),
        Box("two", 440, 140, 50, 20),
    ]
    with pytest.raises(Unresolved, match="TARGET_AMBIGUOUS"):
        view.resolve(
            Target(text="Reference", kind="value", relationships=("below",), min_confidence=0.5)
        )


def test_read_field_uses_newly_resolved_box(monkeypatch):
    view = object.__new__(View)
    current = Box("field", 100, 200, 200, 40)
    monkeypatch.setattr(view, "resolve", lambda target: current)
    calls = []
    monkeypatch.setattr(view, "read_box", lambda box, **kw: calls.append(box) or "typed")
    assert view.read_field(Target(text="Label", kind="field")) == "typed"
    assert calls == [current]


def test_dark_action_button_cannot_be_used_as_input_field():
    image = Image.new("RGB", (800, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 180, 330, 225), fill="#24546b", outline="black", width=2)
    png = io.BytesIO()
    image.save(png, format="PNG")
    view = View(png.getvalue())
    assert not any(
        100 <= b.center[0] <= 330 and 180 <= b.center[1] <= 225 for b in view._field_rectangles()
    )
