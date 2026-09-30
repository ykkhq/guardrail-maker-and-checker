import os
import pathlib

import yaml

from guardrail_common.graph import PipelineGraph
from guardrail_studio.compiler import compile_pipeline, to_deck

GOLDEN = pathlib.Path(__file__).parent / "golden"


def _dump(obj) -> str:
    return yaml.safe_dump(obj, sort_keys=False, allow_unicode=True, width=120)


def test_jp_support_matches_golden(graph_of):
    """Golden file: review diffs by eye. Regenerate with UPDATE_GOLDEN=1."""
    deck = _dump(to_deck(compile_pipeline(graph_of())))
    path = GOLDEN / "jp-support.deck.yaml"
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(deck)
    assert deck == path.read_text()


def test_stage_order_and_ordering(graph_of):
    c = compile_pipeline(graph_of())
    assert [s.plugin for s in c.stages] == ["ai-prompt-guard", "datakit"]
    by_name = {p["name"]: p for p in c.plugins}
    assert by_name["ai-prompt-guard"]["ordering"] == {"before": {"access": ["datakit", "ai-proxy-advanced"]}}
    assert by_name["datakit"]["ordering"] == {"before": {"access": ["ai-proxy-advanced"]}}
    assert "ordering" not in by_name["ai-proxy-advanced"]


def test_segment_is_entered_from_preceding_native(graph_of):
    seg = compile_pipeline(graph_of()).stages[1].segment
    assert seg.entry == "prompt_guard"
    assert [n.id for n in seg.nodes] == ["pii", "safety", "sentiment", "is_kasuhara", "block_kasuhara"]
    # The false branch leaves the segment to the LLM, so that edge is not inside it.
    assert all(e.target != "llm" for e in seg.edges)


def test_datakit_exits_cover_every_block_status(graph_of):
    dk = next(p for p in compile_pipeline(graph_of()).plugins if p["name"] == "datakit")
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
    dk = next(p for p in c.plugins if p["name"] == "datakit")
    assert dk["ordering"]["before"]["access"] == ["ai-prompt-guard", "ai-proxy-advanced"]


def test_llm_provider_mapping():
    g = PipelineGraph.model_validate({
        "slug": "local", "nodes": [
            {"id": "in", "type": "prompt_in"},
            {"id": "llm", "type": "llm", "config": {"provider": "ollama", "model": "llama3.2",
                                                     "upstream_url": "http://ollama:11434/api/chat"}},
        ], "edges": [{"source": "in", "target": "llm"}]})
    c = compile_pipeline(g)
    target = c.plugins[-1]["config"]["targets"][0]
    assert target["model"] == {"provider": "llama2", "name": "llama3.2",
                               "options": {"upstream_url": "http://ollama:11434/api/chat", "llama2_format": "ollama"}}
    assert "auth" not in target
    assert c.stages == []


def test_response_phase_segment_shares_datakit(graph_of):
    def mutate(d):
        d["nodes"].append({"id": "resp_kw", "type": "keyword_blocklist", "config": {"keywords": ["社外秘"]}})
        d["edges"] = [e for e in d["edges"] if e["source"] != "llm"] + [
            {"source": "llm", "target": "resp_kw"}, {"source": "resp_kw", "target": "out"}]
    c = compile_pipeline(graph_of(mutate))
    dks = [p for p in c.plugins if p["name"] == "datakit"]
    assert len(dks) == 1
    names = [n["name"] for n in dks[0]["config"]["nodes"]]
    assert "REQ_GUARD" in names and "RESP_GUARD" in names
    resp_body = next(n for n in dks[0]["config"]["nodes"] if n["name"] == "RESP_BODY")
    assert resp_body["output"] == "response.body"
    assert any("streaming" in w for w in c.warnings)
