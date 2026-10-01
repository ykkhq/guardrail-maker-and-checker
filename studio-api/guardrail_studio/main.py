"""studio-api HTTP API for the Web UI.

  GET    /v1/catalog                         node types (palette + inspector forms)
  POST   /v1/validate                        issues, plus rule fields per detector for the condition editor
  POST   /v1/compile                         AI Gateway entities (format=declarative|gateway)
  POST   /v1/preview/llamaguard              the Llama Guard prompt for a node config
  GET    /v1/pipelines                       saved pipelines
  GET    /v1/pipelines/{slug}                one pipeline graph
  PUT    /v1/pipelines/{slug}                save a graph (canvas positions included)
  DELETE /v1/pipelines/{slug}                delete (and undeploy)
  POST   /v1/pipelines/{slug}/deploy         compile, push, snapshot a version
  GET    /v1/pipelines/{slug}/versions       deployed versions
  POST   /v1/pipelines/{slug}/rollback/{v}   restore a version's graph and redeploy it
  POST   /v1/playground                      run a prompt: live through Kong, or dry-run on the engine
  GET    /v1/status                          Konnect or Gateway / data plane / engine reachability

Environment: GUARDRAIL_DEPLOY_TARGET (konnect|gateway, default konnect),
KONNECT_PAT_FILE or KONNECT_PAT, KONNECT_REGION, KONNECT_CONTROL_PLANE,
KONG_ADMIN_URL, KONG_ADMIN_TOKEN, KONG_WORKSPACE (gateway mode; default guardrail),
GUARDRAIL_ENGINE_URL (URL Kong uses to reach the engine),
ENGINE_URL (URL studio-api uses; defaults to GUARDRAIL_ENGINE_URL), KONG_PROXY_URL, KONG_PUBLIC_URL,
STUDIO_DB, STUDIO_SAMPLES_DIR, STUDIO_CORS_ORIGINS.
"""

from __future__ import annotations

import os
import pathlib
import time
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from guardrail_common import catalog, llamaguard, outputs
from guardrail_common.catalog import CATALOG
from guardrail_common.graph import PipelineGraph
from guardrail_studio.compiler import CompileError, CompileOptions, compile_pipeline, to_declarative, validate
from guardrail_studio.compiler.compile import CHAT_SUFFIX
from guardrail_studio.compiler.gateway import to_gateway_entities
from guardrail_studio.gateway_admin import GatewayAdmin, GatewayError, normalize_workspace
from guardrail_studio.konnect import Konnect, KonnectError, load_token
from guardrail_studio.store import Store

ROOT = pathlib.Path(__file__).resolve().parents[2]


class CompileRequest(BaseModel):
    graph: PipelineGraph
    format: Literal["entities", "declarative", "gateway"] = "entities"


class PlaygroundRequest(BaseModel):
    slug: str
    mode: Literal["live", "dry"] = "dry"
    messages: list[dict[str, Any]] = Field(min_length=1)
    # Dry runs use this unsaved graph from the canvas when given.
    graph: PipelineGraph | None = None


class LlamaGuardPreview(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    phase: Literal["request", "response"] = "request"
    text: str = "<message>"


class SettingsUpdate(BaseModel):
    kong_workspace: str | None = None


class Settings:
    def __init__(self) -> None:
        def env(name: str, default: str = "") -> str:
            return (os.environ.get(name) or "").strip() or default

        self.deploy_target = env("GUARDRAIL_DEPLOY_TARGET", "konnect").lower()
        gateway = self.deploy_target == "gateway"
        self.region = env("KONNECT_REGION", "us")
        self.control_plane = env("KONNECT_CONTROL_PLANE", "guardrail-service")
        self.kong_admin_url = env("KONG_ADMIN_URL", "http://host.docker.internal:8001")
        # Local Enterprise docker defaults to kongadmin; Konnect mode does not use this.
        self.kong_admin_token = env("KONG_ADMIN_TOKEN", "kongadmin" if gateway else "")
        self.kong_workspace = env("KONG_WORKSPACE", "guardrail" if gateway else "default")
        self.kong_service_url = env("KONG_SERVICE_URL", "http://mockbin:8080")
        # DataKit URL must be reachable from the Kong data plane that serves traffic.
        self.kong_engine_url = env(
            "GUARDRAIL_ENGINE_URL",
            "http://host.docker.internal:18080" if gateway else "http://guardrail-engine:8080",
        )
        # studio-api → engine (same compose network).
        self.engine_url = env("ENGINE_URL", "http://guardrail-engine:8080")
        self.kong_proxy_url = env(
            "KONG_PROXY_URL",
            "http://host.docker.internal:8000" if gateway else "http://kong-dp:8000",
        )
        self.kong_public_url = env(
            "KONG_PUBLIC_URL",
            "http://localhost:8000" if gateway else "http://localhost:18000",
        )
        self.db = env("STUDIO_DB", str(ROOT / "studio.db"))
        self.samples = pathlib.Path(env("STUDIO_SAMPLES_DIR", str(ROOT / "samples")))
        self.cors = env("STUDIO_CORS_ORIGINS", "http://localhost:5173").split(",")


def _issues(exc: CompileError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"ok": False, "issues": [i.to_dict() for i in exc.issues]})


def create_app(settings: Settings | None = None, konnect_factory=None,
               gateway_factory=None, http: httpx.Client | None = None) -> FastAPI:
    cfg = settings or Settings()
    store = Store(cfg.db)
    opts = CompileOptions(engine_url=cfg.kong_engine_url)
    client = http or httpx.Client(timeout=60)
    cp_cache: dict[str, str] = {}

    def konnect() -> Konnect:
        if konnect_factory:
            return konnect_factory()
        try:
            return Konnect(load_token(), cfg.region)
        except RuntimeError as exc:
            raise HTTPException(503, f"Konnect not configured: {exc}") from exc

    def gateway() -> GatewayAdmin:
        if gateway_factory:
            return gateway_factory()
        try:
            return GatewayAdmin(
                cfg.kong_admin_url,
                token=cfg.kong_admin_token or None,
                workspace=cfg.kong_workspace,
                service_url=cfg.kong_service_url,
            )
        except RuntimeError as exc:
            raise HTTPException(503, f"Kong Admin not configured: {exc}") from exc

    def cp_id(k: Konnect) -> str:
        if "id" not in cp_cache:
            cp = k.find_control_plane(cfg.control_plane)
            if not cp:
                raise HTTPException(503, f"AI Gateway '{cfg.control_plane}' not found in {cfg.region}")
            cp_cache["id"] = cp["id"]
        return cp_cache["id"]

    # Refresh bundled sample pipelines from samples/*.json on every start.
    if cfg.samples.is_dir():
        for f in sorted(cfg.samples.glob("*.json")):
            store.save(PipelineGraph.model_validate_json(f.read_text()))

    # Persist workspace override from a previous UI set (survives studio-api restart).
    if saved_ws := store.get_meta("kong_workspace"):
        try:
            cfg.kong_workspace = normalize_workspace(saved_ws)
        except ValueError:
            pass

    app = FastAPI(title="guardrail-studio-api", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors, allow_methods=["*"], allow_headers=["*"])

    # --- catalog / compiler ------------------------------------------------------

    engine_cache: dict[str, Any] = {}

    def engine_detectors() -> dict[str, dict] | None:
        """Which custom detectors the engine can run (cached 30 s; None if unreachable)."""
        if engine_cache.get("at", 0) > time.time() - 30:
            return engine_cache["data"]
        try:
            r = client.get(f"{cfg.engine_url}/v1/detectors", timeout=3)
            data = r.json().get("detectors") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            data = None
        engine_cache.update(at=time.time(), data=data)
        return data

    @app.get("/v1/catalog")
    def get_catalog() -> dict:
        det = engine_detectors() or {}
        types = []
        for t in CATALOG.values():
            d = t.to_dict()
            if t.kind == "custom" and t.type in det:
                d["available"] = det[t.type]["available"]
                d["missing"] = det[t.type]["missing"]
            types.append(d)
        return {"node_types": types}

    @app.post("/v1/validate")
    def post_validate(graph: PipelineGraph) -> dict:
        a = validate(graph)
        issues = [i.to_dict() for i in a.issues]
        det = engine_detectors() or {}
        for n in graph.nodes:
            info = det.get(n.type)
            if info and not info["available"]:
                issues.append({"level": "error", "node": n.id, "edge": None,
                               "message": f"the guardrail engine cannot run '{n.type}': missing Python "
                                          f"module(s) {', '.join(info['missing'])} (rebuild the engine image with them)"})
        ok = not any(i["level"] == "error" for i in issues)
        fields, default_rules = {}, {}
        for n in graph.nodes:
            if CATALOG.get(n.type) and CATALOG[n.type].kind == "custom":
                try:
                    fields[n.id] = outputs.result_fields(n.type, n.config)
                    default_rules[n.id] = outputs.default_rule(n.id, n.type, n.config)
                except (TypeError, AttributeError, KeyError):
                    fields[n.id] = []
        return {"ok": ok, "issues": issues, "phases": a.phase, "fields": fields, "default_rules": default_rules}

    @app.post("/v1/compile")
    def post_compile(req: CompileRequest):
        try:
            compiled = compile_pipeline(req.graph, opts)
        except CompileError as exc:
            return _issues(exc)
        if req.format == "declarative":
            result = to_declarative(compiled)
        elif req.format == "gateway":
            result = to_gateway_entities(compiled, service_url=cfg.kong_service_url)
        else:
            result = {"provider": compiled.provider, "model": compiled.model, "policies": compiled.policies}
        return {"ok": True, "result": result, "stages": [s.describe() for s in compiled.stages],
                "warnings": compiled.warnings}

    @app.post("/v1/preview/llamaguard")
    def preview_llamaguard(req: LlamaGuardPreview) -> dict:
        """The exact prompt the engine sends to Llama Guard for this node config."""
        node_cfg = catalog.with_defaults("llamaguard_safety", req.config)
        return {"prompt": llamaguard.build_prompt(req.text, node_cfg, req.phase),
                "codes": llamaguard.codes(node_cfg.get("policy"))}

    # --- pipelines -------------------------------------------------------------------

    @app.get("/v1/pipelines")
    def list_pipelines() -> dict:
        return {"pipelines": store.list()}

    @app.get("/v1/pipelines/{slug}")
    def get_pipeline(slug: str) -> dict:
        g = store.get(slug)
        if not g:
            raise HTTPException(404, f"pipeline '{slug}' not found")
        return g.model_dump(by_alias=True)

    @app.put("/v1/pipelines/{slug}")
    def put_pipeline(slug: str, graph: PipelineGraph) -> dict:
        if graph.slug != slug:
            raise HTTPException(400, "slug in body does not match URL")
        store.save(graph)
        return {"ok": True}

    @app.delete("/v1/pipelines/{slug}")
    def delete_pipeline(slug: str, undeploy: bool = True) -> dict:
        # Always attempt Kong undeploy by slug (versions may be empty after DB reset).
        removed: list[str] = []
        if undeploy:
            if cfg.deploy_target == "gateway":
                try:
                    with gateway() as g:
                        removed = g.undeploy(slug)
                except GatewayError as exc:
                    return JSONResponse(
                        status_code=502,
                        content={"ok": False, "error": str(exc), "gateway": exc.body, "removed": removed},
                    )
            else:
                try:
                    with konnect() as k:
                        removed = k.undeploy(cp_id(k), slug)
                except KonnectError as exc:
                    return JSONResponse(
                        status_code=502,
                        content={"ok": False, "error": str(exc), "konnect": exc.body, "removed": removed},
                    )
        store.delete(slug)
        return {"ok": True, "removed": removed}

    @app.get("/v1/settings")
    def get_settings() -> dict:
        return {
            "deploy_target": cfg.deploy_target,
            "kong_workspace": cfg.kong_workspace if cfg.deploy_target == "gateway" else None,
        }

    @app.put("/v1/settings")
    def put_settings(body: SettingsUpdate) -> dict:
        if body.kong_workspace is not None:
            if cfg.deploy_target != "gateway":
                raise HTTPException(400, "kong_workspace only applies when GUARDRAIL_DEPLOY_TARGET=gateway")
            try:
                ws = normalize_workspace(body.kong_workspace)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            cfg.kong_workspace = ws
            store.set_meta("kong_workspace", ws)
        return {
            "ok": True,
            "deploy_target": cfg.deploy_target,
            "kong_workspace": cfg.kong_workspace if cfg.deploy_target == "gateway" else None,
        }

    def _deploy(graph: PipelineGraph):
        try:
            compiled = compile_pipeline(graph, opts)
        except CompileError as exc:
            return _issues(exc)
        if cfg.deploy_target == "gateway":
            try:
                with gateway() as g:
                    res = g.deploy(compiled)
            except GatewayError as exc:
                return JSONResponse(status_code=502, content={"ok": False, "error": str(exc), "gateway": exc.body})
            version = store.add_version(graph, {"provider": compiled.provider, "model": compiled.model,
                                                "policies": compiled.policies,
                                                "gateway": to_gateway_entities(compiled, service_url=cfg.kong_service_url)})
            return {"ok": True, "version": version, "warnings": compiled.warnings,
                    "stages": [s.describe() for s in compiled.stages],
                    "target": "gateway",
                    "deployed": {"service_id": res.service_id, "route_id": res.route_id, "plugins": res.plugins,
                                 "deleted": res.deleted, "workspace": res.workspace},
                    "endpoint": f"{cfg.kong_public_url}{compiled.endpoint_path}", "model": compiled.model_name}
        try:
            with konnect() as k:
                res = k.deploy(cp_id(k), compiled)
        except KonnectError as exc:
            return JSONResponse(status_code=502, content={"ok": False, "error": str(exc), "konnect": exc.body})
        version = store.add_version(graph, {"provider": compiled.provider, "model": compiled.model,
                                            "policies": compiled.policies})
        return {"ok": True, "version": version, "warnings": compiled.warnings,
                "stages": [s.describe() for s in compiled.stages],
                "target": "konnect",
                "deployed": {"model_id": res.model_id, "provider_id": res.provider_id, "policies": res.policies,
                             "deleted": res.deleted},
                "endpoint": f"{cfg.kong_public_url}{compiled.endpoint_path}", "model": compiled.model_name}

    @app.post("/v1/pipelines/{slug}/deploy")
    def deploy_pipeline(slug: str, graph: PipelineGraph | None = None):
        """Deploy the given graph (saving it first), or the saved one."""
        if graph:
            if graph.slug != slug:
                raise HTTPException(400, "slug in body does not match URL")
            store.save(graph)
        g = graph or store.get(slug)
        if not g:
            raise HTTPException(404, f"pipeline '{slug}' not found")
        return _deploy(g)

    @app.get("/v1/pipelines/{slug}/versions")
    def list_versions(slug: str) -> dict:
        return {"versions": store.versions(slug)}

    @app.post("/v1/pipelines/{slug}/rollback/{version}")
    def rollback(slug: str, version: int):
        g = store.version_graph(slug, version)
        if not g:
            raise HTTPException(404, f"version {version} of '{slug}' not found")
        store.save(g)
        res = _deploy(g)
        if isinstance(res, dict):
            res["graph"] = g.model_dump(by_alias=True)
        return res

    # --- playground --------------------------------------------------------------------

    @app.post("/v1/playground")
    def playground(req: PlaygroundRequest) -> dict:
        graph = req.graph or store.get(req.slug)
        if not graph:
            raise HTTPException(404, f"pipeline '{req.slug}' not found")
        body = {"model": f"gs-{req.slug}", "messages": req.messages}
        out: dict[str, Any] = {"mode": req.mode}

        try:
            r = client.post(f"{cfg.engine_url}/v1/dry-run",
                            json={"graph": graph.model_dump(by_alias=True), "body": body})
            out["trace"] = r.json() if r.status_code == 200 else {"error": r.text}
        except httpx.HTTPError as exc:
            out["trace"] = {"error": f"engine unreachable: {exc}"}

        if req.mode == "live":
            t0 = time.perf_counter()
            try:
                r = client.post(f"{cfg.kong_proxy_url}{opts.route_prefix}/{req.slug}{CHAT_SUFFIX}", json=body)
                try:
                    payload: Any = r.json()
                except ValueError:
                    payload = r.text
                out["live"] = {"status": r.status_code, "body": payload,
                               "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                               "kong_request_id": r.headers.get("x-kong-request-id")}
            except httpx.HTTPError as exc:
                out["live"] = {"status": 0, "body": f"Kong unreachable: {exc}"}
        return out

    @app.get("/v1/status")
    def status() -> dict:
        def probe(url: str) -> bool:
            try:
                return client.get(url, timeout=3).status_code < 500
            except httpx.HTTPError:
                return False

        out: dict[str, Any] = {
            "deploy_target": cfg.deploy_target,
            "engine": {"ok": probe(f"{cfg.engine_url}/healthz"), "url": cfg.engine_url},
            "kong": {"ok": probe(f"{cfg.kong_proxy_url}/"), "url": cfg.kong_proxy_url},
        }
        if cfg.deploy_target == "gateway":
            admin_ok, err = False, None
            try:
                with gateway() as g:
                    admin_ok = g.ready()
            except HTTPException as exc:
                err = exc.detail
            except GatewayError as exc:
                err = f"Admin API returned {exc.status}"
            except httpx.HTTPError as exc:
                err = type(exc).__name__
            out["gateway"] = {
                "ok": admin_ok,
                "admin_url": cfg.kong_admin_url,
                "workspace": cfg.kong_workspace,
                "error": err,
            }
        else:
            konnect_ok, cp, err = False, None, None
            try:
                with konnect() as k:
                    cp = cp_id(k)
                    konnect_ok = True
            except HTTPException as exc:
                err = exc.detail
            except KonnectError as exc:
                err = f"Konnect API returned {exc.status}"
            except httpx.HTTPError as exc:
                err = type(exc).__name__
            out["konnect"] = {"ok": konnect_ok, "region": cfg.region, "control_plane": cfg.control_plane,
                              "control_plane_id": cp, "error": err}
        return out

    return app


def __getattr__(name: str):
    # `uvicorn guardrail_studio.main:app` builds the app lazily, so importing
    # this module (tests) does not open the database.
    if name == "app":
        return create_app()
    raise AttributeError(name)
