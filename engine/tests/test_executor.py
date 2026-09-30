import pytest

from guardrail_common.graph import Edge, Node, Segment
from guardrail_engine.executor import run_segment

from engine_fakes import JAILBREAK_PROMPT, KASUHARA_PROMPT, PII_PROMPT, chat


def jp_segment(jp_support) -> Segment:
    """The segment the compiler cuts from jp-support: after prompt_guard, before llm."""
    ids = {"safety", "pii", "sentiment", "is_kasuhara", "block_kasuhara"}
    nodes = [Node(**n) for n in jp_support["nodes"] if n["id"] in ids]
    edges = [Edge(**e) for e in jp_support["edges"] if e["target"] in ids or e["source"] in ids]
    return Segment(entry="prompt_guard", nodes=nodes, edges=edges)


def test_pii_prompt_is_masked_and_allowed(registry, jp_support):
    res = run_segment(jp_segment(jp_support), chat(PII_PROMPT), registry)
    assert res.decision == "allow"
    assert res.body["messages"][0]["content"] == "こんにちは。私のメールは <EMAIL> で、電話番号は <JP_PHONE> です。サポートをお願いします。"
    assert [s.outcome for s in res.trace] == ["mask", "pass", "pass", "branch_false"]


def test_jailbreak_is_blocked_by_safety(registry, jp_support):
    res = run_segment(jp_segment(jp_support), chat(JAILBREAK_PROMPT), registry)
    assert (res.decision, res.status, res.blocked_by) == ("block", 403, "safety")
    assert res.trace[-1].result["flags"]["categories"] == ["indiscriminate_weapons"]


def test_kasuhara_routes_to_block_via_condition(registry, jp_support):
    res = run_segment(jp_segment(jp_support), chat(KASUHARA_PROMPT), registry)
    assert (res.decision, res.blocked_by) == ("block", "block_kasuhara")
    assert [s.outcome for s in res.trace] == ["pass", "pass", "flag", "branch_true", "block"]
    assert "カスタマーハラスメント" in res.message


def test_masking_covers_history_but_checks_use_last_message(registry, jp_support):
    res = run_segment(jp_segment(jp_support), chat("私のメールは a@b.co です", "よろしく"), registry)
    assert res.body["messages"][0]["content"] == "私のメールは <EMAIL> です"
    assert res.decision == "allow"


def test_fail_closed_blocks_with_503(broken_registry, jp_support):
    res = run_segment(jp_segment(jp_support), chat("hello"), broken_registry)
    assert (res.decision, res.status, res.blocked_by) == ("block", 503, "safety")
    assert "ConnectionError" in res.trace[-1].result["error"]


def test_fail_open_continues(broken_registry, jp_support):
    seg = jp_segment(jp_support)
    next(n for n in seg.nodes if n.id == "safety").config["fail_mode"] = "open"
    res = run_segment(seg, chat("hello"), broken_registry)
    assert res.decision == "allow"
    assert next(s for s in res.trace if s.node == "safety").outcome == "error_open"


def test_response_phase_checks_completion_content(registry):
    seg = Segment(phase="response", entry="llm",
                  nodes=[Node(id="kw", type="keyword_blocklist", config={"keywords": ["社外秘"]})],
                  edges=[Edge(source="llm", target="kw"), Edge(source="kw", target="out")])
    body = {"choices": [{"message": {"role": "assistant", "content": "これは社外秘の情報です"}}]}
    res = run_segment(seg, body, registry)
    assert res.decision == "block" and res.blocked_by == "kw"


def test_fan_out_from_virtual_entry(registry):
    seg = Segment(entry="in",
                  nodes=[Node(id="a", type="keyword_blocklist", config={"keywords": ["x"], "on_detect": "flag"}),
                         Node(id="b", type="regex_pii")],
                  edges=[Edge(source="in", target="a"), Edge(source="in", target="b")])
    res = run_segment(seg, chat("x a@b.co"), registry)
    assert {s.node for s in res.trace} == {"a", "b"}
    assert res.body["messages"][0]["content"] == "x <EMAIL>"


def test_bad_entry_raises(registry):
    with pytest.raises(ValueError):
        run_segment(Segment(entry="nope", nodes=[Node(id="a", type="regex_pii")]), chat("hi"), registry)


def test_laya_never_blocks_even_if_config_says_so(jp_support):
    from engine_fakes import FakeLaya
    from guardrail_engine.detectors import Registry

    seg = Segment(entry="in",
                  nodes=[Node(id="triage", type="laya_classify", config={"on_detect": "block"}),
                         Node(id="c", type="condition", config={"rules": [
                             {"node": "triage", "field": "flags.intent", "op": "eq", "value": "complaint"}]}),
                         Node(id="stop", type="block", config={"status": 409})],
                  edges=[Edge(source="in", target="triage"), Edge(source="triage", target="c"),
                         Edge(source="c", target="stop", sourceHandle="true")])
    res = run_segment(seg, chat("まったく、ひどいサービスだ"), Registry([FakeLaya()]))
    assert [s.outcome for s in res.trace] == ["flag", "branch_true", "block"]
    assert (res.blocked_by, res.status) == ("stop", 409)
