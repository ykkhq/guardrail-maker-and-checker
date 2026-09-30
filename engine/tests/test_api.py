from fastapi.testclient import TestClient

from guardrail_engine.main import create_app

from engine_fakes import JAILBREAK_PROMPT, PII_PROMPT, chat


def client(registry) -> TestClient:
    return TestClient(create_app(registry))


def test_detect_endpoint_applies_catalog_defaults(registry):
    r = client(registry).post("/v1/detect/regex_pii", json={"text": PII_PROMPT})
    assert r.status_code == 200
    assert "<EMAIL>" in r.json()["text"]


def test_detect_unknown_type_404(registry):
    assert client(registry).post("/v1/detect/nope", json={"text": "x"}).status_code == 404


def test_segment_execute(registry):
    seg = {"entry": "kw", "nodes": [{"id": "kw", "type": "keyword_blocklist", "config": {"keywords": ["explosive"]}}]}
    r = client(registry).post("/v1/segments/execute", json={"segment": seg, "body": chat(JAILBREAK_PROMPT)})
    assert r.status_code == 200
    assert r.json()["decision"] == "block"


def test_segment_execute_rejects_native_node(registry):
    seg = {"entry": "g", "nodes": [{"id": "g", "type": "ai_prompt_guard"}]}
    r = client(registry).post("/v1/segments/execute", json={"segment": seg, "body": chat("hi")})
    assert r.status_code == 422


def test_dry_run_bridges_native_nodes(registry, jp_support):
    r = client(registry).post("/v1/dry-run", json={"graph": jp_support, "body": chat(PII_PROMPT)})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["decision"] == "allow"
    assert [s["node"] for s in data["trace"]] == ["pii", "safety", "sentiment", "is_kasuhara"]
    assert "<EMAIL>" in data["body"]["messages"][0]["content"]


def test_detectors_report_missing_modules():
    from guardrail_engine.detectors import default_registry

    det = client(default_registry()).get("/v1/detectors").json()["detectors"]
    assert det["regex_pii"] == {"available": True, "loaded": False, "missing": []}
    try:
        import laya  # noqa: F401
    except ImportError:
        assert det["laya_classify"]["available"] is False
        assert det["laya_classify"]["missing"] == ["laya"]
