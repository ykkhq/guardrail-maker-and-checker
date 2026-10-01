import httpx
import pytest
from fastapi.testclient import TestClient

from guardrail_studio.konnect import Konnect
from guardrail_studio.main import Settings, create_app

from studio_fakes import GW, FakeKonnect


class FakeCPKonnect(Konnect):
    def find_control_plane(self, name):
        return {"id": GW, "name": name}


@pytest.fixture
def env(tmp_path):
    fake = FakeKonnect()
    calls: list[str] = []

    def upstream(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path == "/v1/detectors":
            return httpx.Response(200, json={"detectors": {
                "laya_classify": {"available": False, "loaded": False, "missing": ["laya"]},
                "regex_pii": {"available": True, "loaded": False, "missing": []},
            }})
        if req.url.path == "/v1/dry-run":
            return httpx.Response(200, json={"decision": "allow", "trace": [], "body": {}})
        if req.url.path.startswith("/pipelines/"):
            return httpx.Response(403, json={"error": {"node": "safety"}}, headers={"x-kong-request-id": "r1"})
        return httpx.Response(200, json={})

    settings = Settings()
    settings.db = str(tmp_path / "studio.db")
    settings.engine_url, settings.kong_proxy_url = "http://engine", "http://kong"
    app = create_app(
        settings,
        konnect_factory=lambda: FakeCPKonnect("tok", transport=httpx.MockTransport(fake.handler)),
        http=httpx.Client(transport=httpx.MockTransport(upstream)),
    )
    return TestClient(app), fake, calls


def test_catalog_lists_all_kinds(env):
    kinds = {t["kind"] for t in env[0].get("/v1/catalog").json()["node_types"]}
    assert kinds == {"endpoint", "native", "custom", "control"}


def test_samples_are_seeded(env):
    assert [p["slug"] for p in env[0].get("/v1/pipelines").json()["pipelines"]] == [
        "jp-support", "lm-studio-support", "lm-studio-support-kr",
    ]


def test_save_keeps_canvas_positions(env, jp_support_dict):
    client = env[0]
    jp_support_dict["nodes"][0]["position"] = {"x": 10, "y": 20}
    assert client.put("/v1/pipelines/jp-support", json=jp_support_dict).status_code == 200
    assert client.get("/v1/pipelines/jp-support").json()["nodes"][0]["position"] == {"x": 10.0, "y": 20.0}


def test_compile_declarative(env, jp_support_dict):
    r = env[0].post("/v1/compile", json={"graph": jp_support_dict, "format": "declarative"})
    assert r.status_code == 200
    assert r.json()["result"]["ai_gateway_models"][0]["config"]["route"]["paths"] == ["/pipelines/jp-support"]


def test_compile_invalid_returns_issues(env, jp_support_dict):
    jp_support_dict["edges"] = []
    r = env[0].post("/v1/compile", json={"graph": jp_support_dict})
    assert r.status_code == 422 and r.json()["issues"]


def test_deploy_snapshots_versions_and_rollback(env, jp_support_dict):
    client, fake, _ = env
    r = client.post("/v1/pipelines/jp-support/deploy", json=jp_support_dict)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and len(fake.policies) == 2 and len(fake.models) == 1
    assert r.json()["endpoint"].endswith("/pipelines/jp-support/chat/completions")
    assert r.json()["model"] == "gs-jp-support"

    jp_support_dict["name"] = "renamed"
    assert client.post("/v1/pipelines/jp-support/deploy", json=jp_support_dict).json()["version"] == 2
    assert [v["version"] for v in client.get("/v1/pipelines/jp-support/versions").json()["versions"]] == [2, 1]

    rb = client.post("/v1/pipelines/jp-support/rollback/1").json()
    assert rb["version"] == 3 and rb["graph"]["name"] == "JP customer support"


def test_deploy_invalid_graph_is_rejected_before_konnect(env, jp_support_dict):
    client, fake, _ = env
    jp_support_dict["edges"] = []
    assert client.post("/v1/pipelines/jp-support/deploy", json=jp_support_dict).status_code == 422
    assert fake.calls == []


def test_playground_live_includes_engine_trace(env):
    client, _, calls = env
    r = client.post("/v1/playground", json={"slug": "jp-support", "mode": "live",
                                            "messages": [{"role": "user", "content": "hi"}]}).json()
    assert r["live"]["status"] == 403 and r["live"]["kong_request_id"] == "r1"
    assert r["trace"]["decision"] == "allow"
    assert calls[-2:] == ["http://engine/v1/dry-run", "http://kong/pipelines/jp-support/chat/completions"]


def test_catalog_marks_detectors_the_engine_cannot_run(env):
    types = {t["type"]: t for t in env[0].get("/v1/catalog").json()["node_types"]}
    assert types["laya_classify"]["available"] is False and types["laya_classify"]["missing"] == ["laya"]
    assert types["regex_pii"]["available"] is True
    assert "available" not in types["ai_prompt_guard"]  # native plugins are Kong's concern


def test_validate_rejects_uninstalled_detector(env, jp_support_dict):
    jp_support_dict["nodes"].append({"id": "laya", "type": "laya_classify"})
    jp_support_dict["edges"] = [e for e in jp_support_dict["edges"] if e["source"] != "sentiment"] + [
        {"source": "sentiment", "target": "laya"}, {"source": "laya", "target": "is_kasuhara"}]
    r = env[0].post("/v1/validate", json=jp_support_dict).json()
    assert r["ok"] is False
    assert any(i["node"] == "laya" and "missing Python module(s) laya" in i["message"] for i in r["issues"])


def test_validate_returns_condition_editor_fields(env, jp_support_dict):
    jp_support_dict["nodes"].append({"id": "triage", "type": "laya_classify"})
    jp_support_dict["edges"] = [e for e in jp_support_dict["edges"] if e["source"] != "sentiment"] + [
        {"source": "sentiment", "target": "triage"}, {"source": "triage", "target": "is_kasuhara"}]
    # The mocked engine reports laya as not installed; the fields are still returned.
    r = env[0].post("/v1/validate", json=jp_support_dict).json()
    intent = next(f for f in r["fields"]["triage"] if f["field"] == "flags.intent")
    assert intent["type"] == "enum" and intent["values"][0]["value"] == "question"
    assert r["default_rules"]["triage"]["field"] == "flags.intent"
    assert any(f["field"] == "flags.kasuhara" for f in r["fields"]["sentiment"])
    assert "prompt_guard" not in r["fields"]


def test_llamaguard_prompt_preview(env):
    r = env[0].post("/v1/preview/llamaguard", json={
        "phase": "response", "text": "hello",
        "config": {"policy": [{"id": "leaks", "name": "Internal Leaks", "description": "project codenames"}]}}).json()
    assert "S15: Internal Leaks.\nproject codenames" in r["prompt"] and "Agent: hello" in r["prompt"]
    assert r["codes"] == {"S15": "leaks"}
