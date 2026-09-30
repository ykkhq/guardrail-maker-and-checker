"""guardrail-engine HTTP API.

  POST /v1/segments/execute   called by the compiled Kong DataKit plugin
  POST /v1/dry-run            runs a whole pipeline's custom/control nodes (UI playground)
  POST /v1/detect/{type}      runs a single detector
  GET  /v1/detectors          detector types: installed (available), loaded, missing modules
  GET  /healthz

Set GUARDRAIL_PRELOAD=presidio_pii,sentiment_kasuhara to load models at startup.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from guardrail_common import catalog
from guardrail_common.graph import Segment
from guardrail_engine.detectors import Registry, default_registry
from guardrail_engine.executor import run_detector, run_segment
from guardrail_engine.chat import ChatDoc
from guardrail_engine.models import DetectRequest, DetectorResult, DryRunRequest, SegmentRequest, SegmentResponse


def create_app(registry: Registry | None = None) -> FastAPI:
    reg = registry or default_registry()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        preload = [t for t in os.environ.get("GUARDRAIL_PRELOAD", "").split(",") if t]
        reg.preload(preload)
        yield

    app = FastAPI(title="guardrail-engine", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/v1/detectors")
    def detectors() -> dict:
        return {"loaded": reg.status(), "detectors": reg.availability()}

    @app.post("/v1/detect/{node_type}", response_model=DetectorResult)
    def detect(node_type: str, req: DetectRequest) -> DetectorResult:
        try:
            nt = catalog.get(node_type)
            reg.get(node_type)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        cfg = catalog.with_defaults(node_type, req.config)
        doc = ChatDoc({"messages": [{"role": "user", "content": req.text}]})
        res = run_detector(reg, node_type, doc, cfg, nt.transforms_text)
        if nt.transforms_text and res.detected and cfg.get("on_detect") == "mask":
            res.text = doc.primary_text()
        return res

    # Sync endpoints run in FastAPI's threadpool, so CPU-bound models don't block the loop.
    @app.post("/v1/segments/execute", response_model=SegmentResponse)
    def execute_segment(req: SegmentRequest) -> SegmentResponse:
        try:
            return run_segment(req.segment, req.body, reg)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/dry-run", response_model=SegmentResponse)
    def dry_run(req: DryRunRequest) -> SegmentResponse:
        """Run the request-phase custom/control nodes of a whole pipeline.

        Native plugins and the LLM only run on Kong, so this previews the
        engine part of the flow. Edges through native nodes are bridged so the
        path stays connected.
        """
        g = req.graph
        try:
            kinds = {n.id: catalog.get(n.type).kind for n in g.nodes}
        except KeyError as exc:
            raise HTTPException(422, str(exc)) from exc
        start = next((n for n in g.nodes if n.type == "prompt_in"), None)
        if start is None:
            raise HTTPException(422, "graph has no prompt_in node")
        engine_ids = {i for i, k in kinds.items() if k in ("custom", "control")}
        succ = g.successors()

        # For every edge leaving prompt_in or an engine node, walk through
        # native nodes until the next engine node; stop at endpoints (LLM).
        bridged = []
        for src in [start.id, *engine_ids]:
            for e in succ.get(src, []):
                stack, seen = [e.target], set()
                while stack:
                    t = stack.pop()
                    if t in seen or kinds.get(t) == "endpoint":
                        continue
                    seen.add(t)
                    if t in engine_ids:
                        bridged.append(e.model_copy(update={"target": t}))
                    else:
                        stack.extend(x.target for x in succ.get(t, []))
        if not any(e.source == start.id for e in bridged):
            return SegmentResponse(decision="allow", body=req.body)
        segment = Segment(entry=start.id, nodes=[n for n in g.nodes if n.id in engine_ids], edges=bridged)
        try:
            return run_segment(segment, req.body, reg)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app


app = create_app()
