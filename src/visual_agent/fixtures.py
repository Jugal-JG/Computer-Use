"""Authored offline test data. Never imported by the discovery path."""

from .capability import HANDLERS
from .contracts import Artifact, Condition, Extraction, Inputs, Outputs, Step, Target

TARGETS = {
    "member_id": Target(
        text="Member ID",
        kind="field",
        region=(0.12, 0.10, 0.98, 0.90),
        relationships=("right_of", "same_row", "below"),
        geometry_hint=(0.39, 0.22, 0.25, 0.055),
        min_confidence=0.50,
        winner_margin=0.10,
    ),
    "search": Target(
        text="Search",
        region=(0.12, 0.10, 0.98, 0.90),
        relationships=("below",),
        anchor_text="Member ID",
        geometry_hint=(0.18, 0.30, 0.09, 0.06),
    ),
    "savings": Target(text="Open savings"),
    "prepare": Target(text="Prepare statement"),
    "month": Target(
        text="Statement month",
        kind="field",
        relationships=("right_of", "same_row", "below"),
        min_confidence=0.50,
        winner_margin=0.10,
    ),
    "delivery": Target(
        text="Delivery method",
        kind="field",
        relationships=("right_of", "same_row", "below"),
        min_confidence=0.50,
        winner_margin=0.10,
    ),
    "review": Target(text="Review request"),
    "notice": Target(text="Dismiss notice"),
}


def make_artifact(steps, provenance, discovery_viewport=(1280, 800)):
    return Artifact(
        discovery_viewport=discovery_viewport,
        description="Prepare a statement request and verify its review screen without submitting.",
        input_schema=Inputs.model_json_schema(),
        output_schema=Outputs.model_json_schema(),
        extractions={
            "masked_account": Extraction(label="Masked account", parser="masked_account"),
            "statement_month": Extraction(label="Statement month", parser="month"),
            "delivery_method": Extraction(label="Delivery method", parser="delivery"),
        },
        targets=TARGETS,
        steps=steps,
        handlers=HANDLERS,
        provenance=provenance,
    )


def fixture_artifact():
    steps = [
        Step(
            id="enter_member",
            action="type_text",
            target="member_id",
            input="member_id",
            pre="Member Search",
            post=Condition(
                kind="field_input",
                text="Member Search",
                target="member_id",
                input="member_id",
            ),
        ),
        Step(
            id="search",
            action="click",
            target="search",
            pre="Member Search",
            post=Condition(text="Member Details"),
            timeout_ms=10000,
        ),
        Step(
            id="open_savings",
            action="click",
            target="savings",
            pre="Member Details",
            post=Condition(text="Savings Account"),
        ),
        Step(
            id="prepare",
            action="click",
            target="prepare",
            pre="Savings Account",
            post=Condition(text="Statement Request"),
        ),
        Step(
            id="month",
            action="type_text",
            target="month",
            input="statement_month",
            pre="Statement Request",
            post=Condition(
                kind="field_input",
                text="Statement Request",
                target="month",
                input="statement_month",
            ),
        ),
        Step(
            id="delivery",
            action="type_text",
            target="delivery",
            input="delivery_method",
            pre="Statement Request",
            post=Condition(
                kind="field_input",
                text="Statement Request",
                target="delivery",
                input="delivery_method",
            ),
        ),
        Step(
            id="review",
            action="click",
            target="review",
            pre="Statement Request",
            post=Condition(text="Statement Review"),
        ),
    ]
    return make_artifact(
        steps,
        {
            "source": "authored_test_fixture",
            "genuine_discovery": False,
            "handler_source": "human_authored_and_tested",
        },
    )
