from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from guardrail_common.graph import PipelineGraph, Segment


class DetectorResult(BaseModel):
    """Uniform result of every detector. Condition rules read these fields."""

    detected: bool = False
    score: float | None = None
    label: str | None = None
    flags: dict[str, Any] = Field(default_factory=dict)
    # Rewritten text for masking detectors; None when the text is unchanged.
    text: str | None = None
    entities: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    latency_ms: float = 0.0


class DetectRequest(BaseModel):
    text: str
    config: dict[str, Any] = Field(default_factory=dict)


class TraceStep(BaseModel):
    node: str
    type: str
    outcome: Literal["pass", "flag", "mask", "block", "error_open", "branch_true", "branch_false"]
    result: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0


class SegmentRequest(BaseModel):
    segment: Segment
    # OpenAI-style chat request body (request phase) or chat completion (response phase).
    body: dict[str, Any]


class SegmentResponse(BaseModel):
    decision: Literal["allow", "block"]
    status: int = 200
    message: str | None = None
    blocked_by: str | None = None
    body: dict[str, Any]
    trace: list[TraceStep] = Field(default_factory=list)
    latency_ms: float = 0.0


class DryRunRequest(BaseModel):
    """Runs every custom/control node of a whole pipeline, skipping native plugins and the LLM."""

    graph: PipelineGraph
    body: dict[str, Any]
