from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Inputs(Strict):
    member_id: str = Field(pattern=r"^\d{5}$")
    statement_month: str = Field(pattern=r"^2026-(0[1-9]|1[0-2])$")
    delivery_method: Literal["postal", "electronic"]


class Target(Strict):
    text: str = Field(min_length=1, max_length=100)
    kind: Literal["text", "field", "value"] = "text"
    region: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    relationships: tuple[
        Literal[
            "right_of", "left_of", "above", "below", "same_row", "same_column", "inside_container"
        ],
        ...,
    ] = ()
    anchor_text: str | None = Field(default=None, min_length=1, max_length=100)
    geometry_hint: tuple[float, float, float, float] | None = None
    min_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    winner_margin: float = Field(default=0.12, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def valid_region(self):
        x1, y1, x2, y2 = self.region
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("Region must use normalized coordinates")
        if self.geometry_hint:
            x, y, w, h = self.geometry_hint
            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
                raise ValueError("Geometry hint must use normalized coordinates")
            if x + w > 1 or y + h > 1:
                raise ValueError("Geometry hint outside viewport")
        if self.anchor_text and not self.relationships:
            raise ValueError("An anchor requires at least one relationship")
        return self


class Condition(Strict):
    kind: Literal["text", "field_input"] = "text"
    text: str
    target: str | None = None
    input: Literal["member_id", "statement_month", "delivery_method"] | None = None


class Step(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    action: Literal["click", "type_text"]
    target: str
    input: Literal["member_id", "statement_month", "delivery_method"] | None = None
    pre: str
    post: Condition
    timeout_ms: int = Field(default=5000, ge=500, le=15000)

    @model_validator(mode="after")
    def typed_action(self):
        if (self.action == "type_text") != (self.input is not None):
            raise ValueError("Only type_text must specify an input reference")
        if self.post.kind == "field_input" and (not self.post.target or not self.post.input):
            raise ValueError("Field checkpoint needs target and input")
        return self


class Handler(Strict):
    code: str
    text: str
    response: Literal["business_outcome", "recover", "intervention", "failure"]
    target: str | None = None
    locator: Target | None = None
    max_attempts: int = Field(default=1, ge=1, le=2)


class Extraction(Strict):
    label: str
    parser: Literal["masked_account", "month", "delivery"]
    target: Target | None = None


class Capability(Strict):
    """Shared runtime contract; a draft is not yet a replayable artifact."""

    schema_version: Literal["2.0", "3.0"] = "2.0"
    capability_id: Literal["prepare_statement_request"] = "prepare_statement_request"
    capability_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    description: str
    input_schema: dict
    output_schema: dict
    extractions: dict[str, Extraction]
    profile: Literal["member-services-v1"] = "member-services-v1"
    discovery_viewport: tuple[int, int] | None = None
    entry_condition: str = "Member Search"
    targets: dict[str, Target]
    steps: list[Step] = Field(default_factory=list, max_length=30)
    handlers: list[Handler]
    success_condition: str = "Statement Review"
    provenance: dict

    @model_validator(mode="after")
    def references(self):
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate step IDs")
        if self.discovery_viewport and (
            self.discovery_viewport[0] < 320 or self.discovery_viewport[1] < 240
        ):
            raise ValueError("Discovery viewport is implausibly small")
        if (
            self.input_schema != Inputs.model_json_schema()
            or self.output_schema != Outputs.model_json_schema()
        ):
            raise ValueError("Unsupported input/output contract")
        for name, parser in {
            "masked_account": "masked_account",
            "statement_month": "month",
            "delivery_method": "delivery",
        }.items():
            if name in self.extractions and self.extractions[name].parser != parser:
                raise ValueError("Output parser mismatch")
        for s in self.steps:
            if s.target not in self.targets or (
                s.post.target and s.post.target not in self.targets
            ):
                raise ValueError("Unknown target reference")
        for h in self.handlers:
            if h.response == "recover" and not h.locator and h.target not in self.targets:
                raise ValueError("Recovery must refer to a declared target")
        return self

    def digest(self):
        data = self.model_dump()
        if self.schema_version == "2.0":
            # Optional v3 extensions must not change hashes pinned by v2 evidence.
            for handler in data["handlers"]:
                if handler.get("locator") is None:
                    handler.pop("locator", None)
            for extraction in data["extractions"].values():
                if extraction.get("target") is None:
                    extraction.pop("target", None)
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class Artifact(Capability):
    steps: list[Step] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def complete(self):
        if set(self.extractions) != {"masked_account", "statement_month", "delivery_method"}:
            raise ValueError("Every declared data output needs an extraction binding")
        if self.schema_version == "3.0" and any(
            e.target is None or e.target.kind not in {"field", "value"}
            for e in self.extractions.values()
        ):
            raise ValueError("Version 3 requires resolved output locators")
        return self


class DiscoveryAction(Strict):
    action: Literal["click", "type_text"]
    target: Target
    input: Literal["member_id", "statement_month", "delivery_method"] | None = None

    def compile(self, step_id, target_id, current_screen):
        """Clicks receive their final postcondition after the next observation."""
        return Step(
            id=step_id,
            action=self.action,
            target=target_id,
            input=self.input,
            pre=current_screen,
            post=Condition(
                kind="field_input" if self.action == "type_text" else "text",
                text=current_screen,
                target=target_id if self.action == "type_text" else None,
                input=self.input,
            ),
            timeout_ms=10000,
        )

    @model_validator(mode="after")
    def valid_action(self):
        if self.action == "type_text":
            if self.target.kind != "field" or self.input is None:
                raise ValueError("Typing requires a field and an input reference")
        elif self.target.kind != "text" or self.input is not None:
            raise ValueError("Click requires visible text and no input reference")
        return self


class DiscoveryProposal(Strict):
    done: bool = Field(strict=True)
    reason: str = Field(max_length=200)
    current_screen: str = Field(min_length=1, max_length=100)
    step: DiscoveryAction | None = None
    scroll: Literal["up", "down"] | None = None
    extractions: dict[str, Target] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exclusive(self):
        if sum((self.done, self.step is not None, self.scroll is not None)) > 1:
            raise ValueError("Choose completion, one action, or one scroll")
        if self.extractions and not self.done:
            raise ValueError("Extraction locators belong to completion")
        return self


class Outputs(Strict):
    masked_account: str = Field(pattern=r"^\*{4}\d{4}$")
    statement_month: str
    delivery_method: Literal["postal", "electronic"]
    review_ready: Literal[True] = True


class Result(Strict):
    run_id: str
    session_id: str
    artifact_hash: str
    status: Literal["success", "business_outcome", "failure"]
    code: str
    step: str | None = None
    expected: str | None = None
    observed: str | None = None
    outputs: Outputs | None = None
    llm_calls: int = 0
    evidence_dir: str
