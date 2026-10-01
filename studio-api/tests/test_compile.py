import os
import pathlib

import yaml

from guardrail_common.graph import PipelineGraph
from guardrail_studio.compiler import compile_pipeline, to_declarative

GOLDEN = pathlib.Path(__file__).parent / "golden"


def _dump(obj) -> str:
    return yaml.safe_dump(obj, sort_keys=False, allow_unicode=True, width=120)


def test_jp_support_matches_golden(graph_of):
    """Golden file: review diffs by eye. Regenerate with UPDATE_GOLDEN=1."""
    out = _dump(to_declarative(compile_pipeline(graph_of())))
    path = GOLDEN / "jp-support.ai-gateway.yaml"
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(out)
    assert out == path.read_text()


def test_stage_order_and_model_policies(graph_of):
    c = compile_pipeline(graph_of())
    assert [s.plugin for s in c.stages] == ["ai-prompt-guard", "datakit"]
    assert [p["type"] for p in c.policies] == ["ai-prompt-guard", "datakit"]
    assert c.model["policies"] == ["gs-jp-support-prompt_guard", "gs-jp-support-datakit"]
    assert all("ordering" not in p for p in c.policies)
    assert c.endpoint_path == "/pipelines/jp-support/chat/completions"


def test_segment_is_entered_from_preceding_native(graph_of):
    seg = compile_pipeline(graph_of()).stages[1].segment
    assert seg.entry == "prompt_guard"
    assert [n.id for n in seg.nodes] == ["pii", "safety", "sentiment", "is_kasuhara", "block_kasuhara"]
    # The false branch leaves the segment to the LLM, so that edge is not inside it.
    assert all(e.target != "llm" for e in seg.edges)


def test_datakit_exits_cover_every_block_status(graph_of):
    dk = next(p for p in compile_pipeline(graph_of()).policies if p["type"] == "datakit")
    exits = {n["status"] for n in dk["config"]["nodes"] if n["type"] == "exit"}
    # 403: safety block_status and block_kasuhara; 503: safety fail-closed.
    assert exits == {403, 503}
    body = next(n for n in dk["config"]["nodes"] if n["name"] == "REQ_BODY")
    assert body["output"] == "service_request.body"


def test_reordering_nodes_changes_plugin_order(graph_of):
    """Move prompt guard after the custom nodes: the DataKit plugin must now run first."""
    def mutate(d):
        d["edges"] = [
            {"source": "in", "target": "safety"},
            {"source": "safety", "target": "pii"},
            {"source": "pii", "target": "sentiment"},
            {"source": "sentiment", "target": "is_kasuhara"},
            {"source": "is_kasuhara", "sourceHandle": "true", "target": "block_kasuhara"},
            {"source": "is_kasuhara", "sourceHandle": "false", "target": "prompt_guard"},
            {"source": "prompt_guard", "target": "llm"},
            {"source": "llm", "target": "out"},
        ]
    c = compile_pipeline(graph_of(mutate))
    assert [s.plugin for s in c.stages] == ["datakit", "ai-prompt-guard"]
    assert c.stages[0].segment.entry == "in"
    assert c.model["policies"] == ["gs-jp-support-datakit", "gs-jp-support-prompt_guard"]


def test_llm_provider_mapping():
    g = PipelineGraph.model_validate({
        "slug": "local", "nodes": [
            {"id": "in", "type": "prompt_in"},
            {"id": "llm", "type": "llm", "config": {"provider": "ollama", "model": "llama3.2",
                                                     "upstream_url": "http://ollama:11434/api/chat"}},
        ], "edges": [{"source": "in", "target": "llm"}]})
    c = compile_pipeline(g)
    assert c.model["targets"] == [{"name": "llama3.2", "provider": "gs-local-llm",
                                   "config": {"type": "ollama", "upstream_url": "http://ollama:11434/api/chat"}}]
    assert c.provider["type"] == "ollama"
    assert c.provider["config"] == {"auth": {"type": "basic", "headers": []}}
    assert c.policies == [] and c.model["policies"] == []
    assert c.stages == []


def test_to_gateway_entities_maps_proxy_and_policies(graph_of):
    from guardrail_studio.compiler.gateway import to_gateway_entities

    c = compile_pipeline(graph_of())
    deck = to_gateway_entities(c)
    svc = deck["services"][0]
    assert svc["name"] == "gs-jp-support"
    assert svc["routes"][0]["paths"] == ["/pipelines/jp-support/chat/completions"]
    assert svc["routes"][0]["strip_path"] is False
    names = [p["name"] for p in svc["plugins"]]
    assert names[0] == "ai-proxy-advanced"
    assert "ai-prompt-guard" in names and "datakit" in names
    proxy = svc["plugins"][0]
    assert proxy["config"]["targets"][0]["model"]["provider"] == "openai"
    assert proxy["config"]["targets"][0]["model"]["name"] == "gpt-4o-mini"
    assert proxy["config"]["targets"][0]["auth"]["header_value"] == "{vault://env/OPENAI_AUTH_HEADER}"
    dk = next(p for p in svc["plugins"] if p["name"] == "datakit")
    assert dk["instance_name"] == "gs-jp-support-datakit"
    assert any(n["type"] == "call" for n in dk["config"]["nodes"])


def test_to_gateway_entities_lm_studio_upstream():
    from guardrail_studio.compiler.gateway import to_gateway_entities

    g = PipelineGraph.model_validate({
        "slug": "lms", "nodes": [
            {"id": "in", "type": "prompt_in"},
            {"id": "llm", "type": "llm", "config": {
                "provider": "openai", "model": "local-chat",
                "upstream_url": "http://host.docker.internal:1234/v1/chat/completions",
                "auth_header_value": "Bearer lm-studio",
            }},
        ], "edges": [{"source": "in", "target": "llm"}]})
    proxy = to_gateway_entities(compile_pipeline(g))["services"][0]["plugins"][0]
    t = proxy["config"]["targets"][0]
    assert t["model"]["options"]["upstream_url"].endswith("/v1/chat/completions")
    assert t["auth"]["header_value"] == "Bearer lm-studio"


def test_response_phase_segment_shares_datakit(graph_of):
    def mutate(d):
        d["nodes"].append({"id": "resp_kw", "type": "keyword_blocklist", "config": {"keywords": ["社外秘"]}})
        d["edges"] = [e for e in d["edges"] if e["source"] != "llm"] + [
            {"source": "llm", "target": "resp_kw"}, {"source": "resp_kw", "target": "out"}]
    c = compile_pipeline(graph_of(mutate))
    dks = [p for p in c.policies if p["type"] == "datakit"]
    assert len(dks) == 1
    names = [n["name"] for n in dks[0]["config"]["nodes"]]
    assert "REQ_GUARD" in names and "RESP_GUARD" in names
    resp_body = next(n for n in dks[0]["config"]["nodes"] if n["name"] == "RESP_BODY")
    assert resp_body["output"] == "response.body"
    assert any("streaming" in w for w in c.warnings)


def test_segment_omits_empty_node_config(graph_of):
    """Konnect rejects an empty object inside DataKit static values."""
    def mutate(d):
        next(n for n in d["nodes"] if n["id"] == "pii")["config"] = {}
    dk = next(p for p in compile_pipeline(graph_of(mutate)).policies if p["type"] == "datakit")
    seg = dk["config"]["nodes"][0]["values"]["segment"]
    assert "config" not in next(n for n in seg["nodes"] if n["id"] == "pii")
