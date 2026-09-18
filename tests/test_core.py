import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from visual_agent.capability import fixture_artifact
from visual_agent.contracts import Artifact, DiscoveryAction, Inputs, Target
from visual_agent.evidence import scrub
from visual_agent.policy import Policy, PolicyDenied


def test_contract_roundtrip_and_hash():
    a = fixture_artifact()
    assert Artifact.model_validate_json(a.model_dump_json()).digest() == a.digest()
    assert "10001" not in a.model_dump_json()
    b = a.model_copy(deep=True)
    b.capability_version = "1.0.1"
    assert a.digest() != b.digest()


def test_compiler_derives_field_verification():
    action = DiscoveryAction(
        action="type_text",
        target=Target(text="Client Code", kind="field", relationships=("below",)),
        input="member_id",
    )
    step = action.compile("enter_member", "learned_field", "Client Lookup")
    assert step.post.kind == "field_input"
    assert step.post.target == step.target and step.post.input == step.input
    assert step.post.text == step.pre


def test_schema_rejects_unknown_and_invalid_references():
    a = fixture_artifact().model_dump()
    a["schema_version"] = "1.0"
    with pytest.raises(ValidationError):
        Artifact.model_validate(a)
    a = fixture_artifact().model_dump()
    a["steps"][0]["target"] = "missing"
    with pytest.raises(ValidationError):
        Artifact.model_validate(a)
    with pytest.raises(ValidationError):
        Inputs(member_id="bad", statement_month="2026-13", delivery_method="postal")


def test_target_regions_are_normalized():
    a = fixture_artifact()
    assert a.schema_version == "2.0"
    assert all(0 <= value <= 1 for target in a.targets.values() for value in target.region)


def test_policy_boundaries():
    p = Policy()
    with pytest.raises(ValidationError):
        Policy(origin="https://bank.example")
    for url in [
        "http://evil.test/",
        "http://127.0.0.1:8765.evil.test/",
        "http://127.0.0.1:8765/submit",
        "http://127.0.0.1:8766/",
        "http://user@127.0.0.1:8765/",
    ]:
        assert not p.allows_url(url)
    assert p.allows_url("http://127.0.0.1:8765/search?member_id=10001")
    with pytest.raises(PolicyDenied):
        p.check("click", "Submit request")
    with pytest.raises(PolicyDenied):
        p.check("key", key="Control+L")
    with pytest.raises(PolicyDenied):
        p.check("click", "Restore training session")
    p.check("click", "Restore training session", human=True)


def test_redaction():
    s = scrub({"member": "10001", "api_key": "SECRET", "email": "a@example.com"})
    assert (
        "10001" not in json.dumps(s)
        and "SECRET" not in json.dumps(s)
        and "a@example.com" not in json.dumps(s)
    )


def test_visual_boundary():
    root = Path(__file__).parents[1] / "src" / "visual_agent"
    code = (root / "surface.py").read_text()
    for forbidden in [
        ".locator(",
        ".evaluate(",
        ".get_by_",
        ".content(",
        ".inner_text(",
        ".query_selector(",
        ".frame_locator(",
    ]:
        assert forbidden not in code
    for filename in ["engine.py", "surface.py", "vision.py", "discovery.py"]:
        assert "import proxy" not in (root / filename).read_text()
    assert "discovery" not in (root / "engine.py").read_text()
