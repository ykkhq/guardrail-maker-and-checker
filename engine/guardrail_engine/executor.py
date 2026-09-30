"""Runs a segment of custom/control nodes over a chat body.

Semantics
- Nodes run in topological order; a node runs only if an incoming edge from a
  node that ran (or the segment entry) is active.
- `condition` activates only its "true" or "false" out-edges.
- `block` stops with decision=block.
- Detectors: on error, fail_mode "closed" blocks and "open" continues.
  On detection, on_detect "block" blocks, "mask" rewrites the text and
  continues, and "flag" only records the result.
"""

from __future__ import annotations

import time
from typing import Any

from guardrail_common import catalog
from guardrail_common.conditions import eval_condition
from guardrail_common.graph import Segment, topo_order
from guardrail_engine.chat import ChatDoc
from guardrail_engine.detectors import Registry
from guardrail_engine.models import DetectorResult, SegmentResponse, TraceStep


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


def run_detector(registry: Registry, node_type: str, doc: ChatDoc, config: dict[str, Any], transforms: bool) -> DetectorResult:
    """Masking detectors run on every guarded text; checks run on the primary text."""
    t0 = time.perf_counter()
    detector = registry.get(node_type)
    try:
        if transforms and config.get("on_detect") == "mask":
            merged = DetectorResult()
            texts = doc.texts()
            for text in texts:
                r = detector.detect(text, config)
                merged.detected |= r.detected
                merged.entities.extend(r.entities)
                if r.score is not None:
                    merged.score = max(merged.score or 0.0, r.score)
                merged.label = merged.label or r.label
            if merged.detected:
                # Second pass rewrites in place; detectors are deterministic.
                doc.rewrite(lambda t: detector.detect(t, config).text)
            result = merged
        else:
            result = detector.detect(doc.primary_text(), config)
    except Exception as exc:  # noqa: BLE001 - any detector failure is handled by fail_mode
        result = DetectorResult(error=f"{type(exc).__name__}: {exc}")
    result.latency_ms = _ms(t0)
    return result


def run_segment(segment: Segment, body: dict[str, Any], registry: Registry) -> SegmentResponse:
    t_start = time.perf_counter()
    doc = ChatDoc(body, segment.phase)
    nodes = {n.id: n for n in segment.nodes}
    edges = [e for e in segment.edges if e.source in nodes]
    order = topo_order(list(nodes), edges)

    # `entry` is either the first segment node, or the node just before the
    # segment (prompt_in / a native plugin), whose out-edges start the segment.
    if segment.entry in nodes:
        active: set[str] = {segment.entry}
    else:
        active = {e.target for e in segment.edges if e.source == segment.entry and e.target in nodes}
        if not active:
            raise ValueError(f"segment entry '{segment.entry}' has no edges into the segment")
    results: dict[str, dict[str, Any]] = {}
    trace: list[TraceStep] = []

    def block(node_id: str, status: int, message: str) -> SegmentResponse:
        return SegmentResponse(
            decision="block", status=status, message=message, blocked_by=node_id,
            body=doc.body, trace=trace, latency_ms=_ms(t_start),
        )

    def activate(node_id: str, handle: str | None = None) -> None:
        for e in edges:
            if e.source == node_id and (handle is None or e.source_handle == handle):
                active.add(e.target)

    for node_id in order:
        if node_id not in active:
            continue
        node = nodes[node_id]
        nt = catalog.get(node.type)
        cfg = catalog.with_defaults(node.type, node.config)

        if node.type == "block":
            trace.append(TraceStep(node=node_id, type=node.type, outcome="block"))
            return block(node_id, cfg["status"], cfg["message"])

        if node.type == "condition":
            t0 = time.perf_counter()
            hit = eval_condition(cfg, results)
            results[node_id] = {"detected": hit}
            trace.append(TraceStep(node=node_id, type=node.type, outcome="branch_true" if hit else "branch_false",
                                   result=results[node_id], latency_ms=_ms(t0)))
            activate(node_id, "true" if hit else "false")
            continue

        if nt.kind != "custom":
            raise ValueError(f"node '{node_id}' of kind '{nt.kind}' cannot run in the engine")

        res = run_detector(registry, node.type, doc, cfg, nt.transforms_text)
        results[node_id] = res.model_dump()
        step = TraceStep(node=node_id, type=node.type, outcome="pass", result=res.model_dump(exclude={"text"}),
                         latency_ms=res.latency_ms)
        trace.append(step)

        if res.error:
            if cfg.get("fail_mode", "closed") == "closed":
                step.outcome = "block"
                return block(node_id, 503, f"Guardrail '{node_id}' unavailable.")
            step.outcome = "error_open"
        elif res.detected:
            action = cfg.get("on_detect", "block")
            if action == "block":
                step.outcome = "block"
                return block(node_id, cfg.get("block_status", 403), cfg.get("block_message", "Request blocked by guardrail policy."))
            step.outcome = "mask" if action == "mask" else "flag"
        activate(node_id)

    return SegmentResponse(decision="allow", body=doc.body, trace=trace, latency_ms=_ms(t_start))
