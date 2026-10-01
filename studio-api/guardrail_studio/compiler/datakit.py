"""Builds the DataKit flow that hands a segment to guardrail-engine.

Per phase (prefix REQ_ or RESP_):

  SEGMENT      static  segment spec (nodes/edges) inlined, so the engine is stateless
  PAYLOAD      jq      {segment, body: request.body | service_response.body}
  GUARD        call    POST {engine}/v1/segments/execute
  BODY         jq      .body  -> service_request.body | response.body (masked text)
  BLOCK_BODY   jq      OpenAI-style error body
  BLOCKED_<s>  jq      .decision == "block" and .status == <s>
  GATE_<s>     branch  then: [EXIT_<s>]
  EXIT_<s>     exit    status <s>

DataKit exit status is static, so there is one exit per status the segment can
return: each block node, each blocking detector's block_status, and 503 for
fail-closed detector errors.
"""

from __future__ import annotations

from typing import Any

from guardrail_common import catalog
from guardrail_common.graph import Segment

REQUEST, RESPONSE = catalog.REQUEST, catalog.RESPONSE

_IO = {
    REQUEST: {"prefix": "REQ", "read": "request.body", "write": "service_request.body"},
    RESPONSE: {"prefix": "RESP", "read": "service_response.body", "write": "response.body"},
}


def possible_statuses(segment: Segment) -> list[int]:
    statuses: set[int] = set()
    for n in segment.nodes:
        cfg = catalog.with_defaults(n.type, n.config)
        if n.type == "block":
            statuses.add(cfg["status"])
        elif catalog.get(n.type).kind == "custom":
            if cfg.get("on_detect") == "block":
                statuses.add(cfg["block_status"])
            if cfg.get("fail_mode") == "closed":
                statuses.add(503)
    return sorted(statuses)


def segment_nodes(segment: Segment, engine_url: str, timeout_ms: int = 10000) -> list[dict[str, Any]]:
    io = _IO[segment.phase]
    p = io["prefix"]
    # UI-only fields (canvas position, label) stay out of the Kong config.
    spec = segment.model_dump(by_alias=True, exclude_none=True,
                              exclude={"nodes": {"__all__": {"position", "label"}}})
    # Konnect rejects an empty object in static values; the engine defaults config to {}.
    for n in spec["nodes"]:
        if not n.get("config"):
            n.pop("config", None)
    nodes: list[dict[str, Any]] = [
        {"name": f"{p}_SEGMENT", "type": "static", "values": {"segment": spec}},
        {
            "name": f"{p}_PAYLOAD",
            "type": "jq",
            "inputs": {"body": io["read"], "segment": f"{p}_SEGMENT.segment"},
            "jq": "{segment: .segment, body: .body}",
        },
        {
            "name": f"{p}_GUARD",
            "type": "call",
            "method": "POST",
            "url": f"{engine_url.rstrip('/')}/v1/segments/execute",
            "timeout": timeout_ms,
            "inputs": {"body": f"{p}_PAYLOAD"},
        },
        {"name": f"{p}_BODY", "type": "jq", "input": f"{p}_GUARD.body", "jq": ".body", "output": io["write"]},
        {
            "name": f"{p}_BLOCK_BODY",
            "type": "jq",
            "input": f"{p}_GUARD.body",
            "jq": '{error: {type: "guardrail_blocked", message: .message, node: .blocked_by}}',
        },
    ]
    for s in possible_statuses(segment):
        nodes += [
            {"name": f"{p}_BLOCKED_{s}", "type": "jq", "input": f"{p}_GUARD.body",
             "jq": f'.decision == "block" and .status == {s}'},
            # `then`/`else` list the nodes to schedule; `outputs.*` is for data wiring.
            {"name": f"{p}_GATE_{s}", "type": "branch", "input": f"{p}_BLOCKED_{s}", "then": [f"{p}_EXIT_{s}"]},
            {"name": f"{p}_EXIT_{s}", "type": "exit", "inputs": {"body": f"{p}_BLOCK_BODY"}, "status": s},
        ]
    return nodes


def datakit_config(segments: list[Segment], engine_url: str, debug: bool = False,
                   timeout_ms: int = 10000) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for seg in segments:
        nodes += segment_nodes(seg, engine_url, timeout_ms)
    return {"debug": debug, "nodes": nodes}
