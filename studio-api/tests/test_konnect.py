import httpx

from guardrail_studio.compiler import compile_pipeline
from guardrail_studio.konnect import Konnect, entity_id

from studio_fakes import CP, FakeKonnect


def client(fake: FakeKonnect) -> Konnect:
    return Konnect("tok", transport=httpx.MockTransport(fake.handler))


def test_first_deploy_creates_plugins_on_route(graph_of):
    fake = FakeKonnect()
    res = client(fake).deploy(CP, compile_pipeline(graph_of()))
    assert set(res.plugins) == {"gs-jp-support-prompt_guard", "gs-jp-support-datakit", "gs-jp-support-llm"}
    route_id = entity_id("route", "gs-jp-support")
    assert res.route_id == route_id
    assert all(p["route"] == {"id": route_id} for p in fake.plugins.values())
    assert res.deleted == []


def test_redeploy_updates_in_place_and_removes_stale(graph_of):
    fake = FakeKonnect(plugins=[
        {"id": entity_id("plugin", "gs-jp-support-prompt_guard"), "name": "ai-prompt-guard", "instance_name": "gs-jp-support-prompt_guard",
         "tags": ["guardrail-studio", "pipeline:jp-support"]},
        {"id": "p-old", "name": "ai-sanitizer", "instance_name": "gs-jp-support-old_pii",
         "tags": ["guardrail-studio", "pipeline:jp-support"]},
        {"id": "p-other", "name": "ai-sanitizer", "instance_name": "someone-else",
         "tags": ["pipeline:other"]},
    ])
    res = client(fake).deploy(CP, compile_pipeline(graph_of()))
    assert res.plugins["gs-jp-support-prompt_guard"] == entity_id("plugin", "gs-jp-support-prompt_guard")
    assert res.deleted == ["gs-jp-support-old_pii"]
    assert "p-other" in fake.plugins  # other pipelines are untouched
    # Stale plugins are deleted before new ones are created.
    methods = [m for m, p in fake.calls if "/plugins" in p]
    assert methods.index("DELETE") < methods.index("PUT")
    assert "POST" not in methods
