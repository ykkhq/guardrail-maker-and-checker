import httpx

from guardrail_studio.compiler import compile_pipeline
from guardrail_studio.konnect import Konnect

from studio_fakes import GW, FakeKonnect

LABELS = {"managed-by": "guardrail-studio", "pipeline": "jp-support"}


def client(fake: FakeKonnect) -> Konnect:
    return Konnect("tok", transport=httpx.MockTransport(fake.handler))


def test_first_deploy_creates_provider_policies_and_model(graph_of):
    fake = FakeKonnect()
    res = client(fake).deploy(GW, compile_pipeline(graph_of()))
    assert list(res.policies) == ["gs-jp-support-prompt_guard", "gs-jp-support-datakit"]
    model = fake.models[res.model_id]
    assert model["policies"] == ["gs-jp-support-prompt_guard", "gs-jp-support-datakit"]
    assert model["targets"][0]["provider"] == "gs-jp-support-llm"
    assert fake.store["model-providers"][res.provider_id]["name"] == "gs-jp-support-llm"
    assert res.deleted == []


def test_redeploy_updates_in_place_and_removes_stale(graph_of):
    fake = FakeKonnect(
        policies=[
            {"id": "p-guard", "name": "gs-jp-support-prompt_guard", "type": "ai-prompt-guard", "labels": LABELS},
            {"id": "p-old", "name": "gs-jp-support-old_pii", "type": "ai-sanitizer", "labels": LABELS},
            {"id": "p-other", "name": "someone-else", "type": "ai-sanitizer", "labels": {"pipeline": "other"}},
        ],
        models=[{"id": "m-1", "name": "gs-jp-support", "labels": LABELS,
                 "policies": ["gs-jp-support-prompt_guard", "gs-jp-support-old_pii"]}],
    )
    res = client(fake).deploy(GW, compile_pipeline(graph_of()))
    assert res.policies["gs-jp-support-prompt_guard"] == "p-guard"
    assert res.model_id == "m-1"
    assert res.deleted == ["gs-jp-support-old_pii"]
    assert "p-other" in fake.policies  # other pipelines are untouched
    # The model drops the stale policy before it is deleted.
    calls = [(m, p) for m, p in fake.calls if m != "GET"]
    assert calls.index(("PUT", f"/v1/ai-gateways/{GW}/models/m-1")) < calls.index(
        ("DELETE", f"/v1/ai-gateways/{GW}/policies/p-old"))


def test_undeploy_removes_model_before_policies(graph_of):
    fake = FakeKonnect()
    k = client(fake)
    k.deploy(GW, compile_pipeline(graph_of()))
    removed = k.undeploy(GW, "jp-support")
    assert removed[0] == "models/gs-jp-support"
    assert all(not items for items in fake.store.values())


def test_dp_certificate_replaces_other_certificates():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "c-old", "cert": "OLD"}]})
        return httpx.Response(201 if req.method == "POST" else 204, json={})

    k = Konnect("tok", transport=httpx.MockTransport(handler))
    assert k.ensure_dp_certificate(GW, "NEW") is True
    base = f"/v1/ai-gateways/{GW}/data-plane-certificates"
    assert calls[1:] == [("DELETE", f"{base}/c-old"), ("POST", base)]
