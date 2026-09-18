"""Statement task contract and reviewed error policy; no discovery action catalog."""

from .contracts import Capability, Handler, Inputs, Outputs, Target

HANDLERS = [
    Handler(code="MEMBER_NOT_FOUND", text="Member not found", response="business_outcome"),
    Handler(
        code="VALIDATION_REJECTED",
        text="Validation rejected",
        response="business_outcome",
    ),
    Handler(code="ACCESS_DENIED", text="Permission denied", response="business_outcome"),
    Handler(code="APP_UNAVAILABLE", text="Application unavailable", response="failure"),
    Handler(code="KNOWN_NOTICE", text="Service notice", response="recover", target="notice"),
    Handler(code="SESSION_EXPIRED", text="Session expired", response="intervention"),
]


def discovery_draft():
    """Task contract and reviewed error policy, with no workflow or action targets."""
    handlers = [h.model_copy(deep=True) for h in HANDLERS]
    for handler in handlers:
        if handler.target:
            handler.locator = Target(text="Dismiss notice")
            handler.target = None
    return Capability(
        schema_version="3.0",
        description="Prepare a statement request and verify its review without submitting.",
        input_schema=Inputs.model_json_schema(),
        output_schema=Outputs.model_json_schema(),
        targets={},
        steps=[],
        extractions={},
        handlers=handlers,
        entry_condition="",
        success_condition="",
        provenance={"source": "discovery_draft"},
    )


def fixture_artifact():
    """Compatibility entry point for the explicitly authored offline fixture."""
    from .fixtures import fixture_artifact as authored_fixture

    return authored_fixture()
