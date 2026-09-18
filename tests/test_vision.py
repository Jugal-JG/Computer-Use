import asyncio
from types import SimpleNamespace

import pytest
from PIL import Image

from visual_agent.contracts import Target
from visual_agent.engine import Run
from visual_agent.vision import Box, Unresolved, View


@pytest.mark.parametrize(
    "failure", ["TARGET_NOT_FOUND", "TARGET_AMBIGUOUS", "TARGET_LOW_CONFIDENCE"]
)
async def test_scroll_search_is_bounded_and_preserves_uncertainty(failure):
    class Screen:
        width, height = 500, 391
        base_words = [Box("Savings", 20, 20, 80, 20)]

        def resolve(self, target):
            raise Unresolved(failure)

    class Surface:
        policy = SimpleNamespace(check=lambda *args: None)
        scrolls = 0

        async def scroll(self, delta):
            self.scrolls += 1

    run = object.__new__(Run)
    run.surface = Surface()
    run.owner = "automation"
    run.lock = asyncio.Lock()
    run.evidence = SimpleNamespace(event=lambda *args, **kwargs: None)

    async def observe():
        return Screen()

    async def handle(*args):
        return False

    run.observe, run.handle = observe, handle
    with pytest.raises(Unresolved, match=failure):
        await run.scroll_to_target(Screen(), Target(text="Prepare statement"), None)
    assert run.surface.scrolls == (2 if failure == "TARGET_NOT_FOUND" else 1)


def test_ambiguity_is_not_first_match():
    v = object.__new__(View)
    v.width, v.height, v.foreground = 1280, 800, None
    v.words = [Box("Search", 20, 20, 60, 20, 99), Box("Search", 200, 20, 60, 20, 99)]
    with pytest.raises(Unresolved):
        v.resolve(Target(text="Search"))
    assert v.resolve(Target(text="Search", region=(0, 0, 0.1, 0.2))).x == 20


async def test_scroll_search_continues_after_responsive_reflow():
    target = Target(text="Search")
    expected = Box("Search", 30, 200, 80, 20)
    before = SimpleNamespace(width=1200, height=800, base_words=[Box("Heading", 400, 20, 80, 20)])
    after = SimpleNamespace(
        width=500,
        height=288,
        base_words=[Box("Heading", 20, 20, 80, 20)],
        resolve=lambda target: expected,
    )
    run = object.__new__(Run)
    run.owner, run.lock = "automation", asyncio.Lock()
    run.evidence = SimpleNamespace(event=lambda *args, **kwargs: None)

    async def scroll(delta):
        pass

    async def observe():
        return after

    async def handle(*args):
        return False

    run.surface = SimpleNamespace(policy=SimpleNamespace(check=lambda *args: None), scroll=scroll)
    run.observe, run.handle = observe, handle
    assert await run.scroll_to_target(before, target, None) == (after, expected)


def test_no_fuzzy_identity_matching():
    v = object.__new__(View)
    v.width, v.height, v.foreground = 1280, 800, None
    v.words = [Box("10001", 20, 20, 60, 20, 99)]
    assert v.find("10002") == []


def test_low_confidence_stops():
    v = object.__new__(View)
    v.width, v.height, v.foreground = 1280, 800, None
    v.words = [Box("Search", 20, 20, 60, 20, 49)]
    with pytest.raises(Unresolved):
        v.resolve(Target(text="Search"))


def test_duplicate_text_uses_normalized_geometry_and_margin():
    v = object.__new__(View)
    v.width, v.height, v.foreground = 1000, 500, None
    v.words = [Box("Search", 100, 100, 70, 20, 99), Box("Search", 800, 100, 70, 20, 99)]
    target = Target(text="Search", geometry_hint=(0.78, 0.15, 0.12, 0.1), min_confidence=0.5)
    assert v.resolve(target).x == 800


def test_modal_search_ignores_background_words():
    v = object.__new__(View)
    v.width, v.height = 1000, 500
    v.foreground = Box("foreground", 300, 100, 400, 300)
    v.words = [Box("Search", 100, 100, 70, 20, 99), Box("Search", 450, 200, 70, 20, 99)]
    assert v.resolve(Target(text="Search")).x == 450


def test_geometry_hint_cannot_invent_a_field(monkeypatch):
    v = object.__new__(View)
    v.width, v.height, v.foreground = 1000, 500, None
    v.image = Image.new("RGB", (1000, 500), "white")
    v.words = [Box("Notes", 100, 100, 50, 20, 99)]
    monkeypatch.setattr(v, "_field_rectangles", lambda: [])
    target = Target(
        text="Notes",
        kind="field",
        relationships=("below",),
        geometry_hint=(0.1, 0.3, 0.6, 0.3),
    )
    with pytest.raises(Unresolved, match="TARGET_NOT_FOUND"):
        v.resolve(target)


def test_uniform_dark_screen_is_uncertain_occlusion():
    import io

    image = Image.new("RGB", (640, 480), "#202020")
    data = io.BytesIO()
    image.save(data, format="PNG")
    view = View(data.getvalue())
    assert view.occluded and not view.modal


def test_short_viewport_dark_header_is_not_a_dialog():
    import io

    from PIL import ImageDraw

    picture = Image.new("RGB", (500, 180), "#dce2e7")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 0, 499, 77), fill="#18394c")
    draw.rectangle((20, 100, 480, 179), fill="white")
    data = io.BytesIO()
    picture.save(data, format="PNG")
    view = View(data.getvalue())
    assert not view.modal and not view.occluded


@pytest.mark.parametrize("height", [391, 628, 900])
def test_below_field_survives_desktop_geometry_hint(height):
    v = object.__new__(View)
    v.width, v.height, v.foreground = 500, height, None
    v.image = Image.new("RGB", (500, height), "white")
    v.words = [Box("Statement", 36, 195, 90, 20, 99), Box("month", 132, 195, 55, 20, 99)]
    expected = Box("field", 36, 224, 310, 42)
    v.field_rectangles = [expected, Box("field", 36, 312, 310, 42)]
    target = Target(
        text="Statement month",
        kind="field",
        relationships=("right_of",),
        geometry_hint=(0.39, 0.29, 0.24, 0.05),
        min_confidence=0.5,
        winner_margin=0.1,
    )
    assert v.resolve(target) == expected


def test_equally_plausible_below_fields_remain_ambiguous():
    v = object.__new__(View)
    v.width, v.height, v.foreground = 500, 391, None
    v.image = Image.new("RGB", (500, 391), "white")
    v.words = [Box("Notes", 230, 100, 40, 20, 99)]
    v.field_rectangles = [Box("field", 30, 130, 210, 42), Box("field", 260, 130, 210, 42)]
    with pytest.raises(Unresolved, match="TARGET_AMBIGUOUS"):
        v.resolve(Target(text="Notes", kind="field", relationships=("below",)))
