import pytest

from guardrail_studio.compiler import CompileError, compile_pipeline, validate


def errors(graph) -> list[str]:
    return [f"{i.node}: {i.message}" for i in validate(graph).issues if i.level == "error"]


def test_sample_is_valid(graph_of):
    a = validate(graph_of())
    assert a.ok, errors(graph_of())
    assert a.phase["safety"] == "request" and a.phase["out"] == "response"


def test_native_plugin_inside_branch_is_rejected(graph_of):
    def mutate(d):
        # Prompt guard only on the condition's false branch; the true branch blocks.
        d["edges"] = [e for e in d["edges"] if not (
            e["source"] in ("in", "prompt_guard") or e["target"] == "prompt_guard"
            or (e["source"] == "is_kasuhara" and e.get("sourceHandle") == "false"))]
        d["edges"] += [
            {"source": "in", "target": "pii"},
            {"source": "is_kasuhara", "sourceHandle": "false", "target": "prompt_guard"},
            {"source": "prompt_guard", "target": "llm"},
        ]
    g = graph_of(mutate)
    assert validate(g).ok  # guard is on the only path to the LLM: allowed
    def bypass(d):
        # An extra edge that reaches the LLM without passing the guard.
        mutate(d)
        d["edges"].append({"source": "pii", "target": "llm"})
    errs = errors(graph_of(bypass))
    assert any("prompt_guard" in e and "inside a branch" in e for e in errs)


def test_custom_nodes_split_by_native_are_rejected(graph_of):
    def mutate(d):
        d["edges"] = [
            {"source": "in", "target": "safety"},
            {"source": "safety", "target": "prompt_guard"},
            {"source": "prompt_guard", "target": "pii"},
            {"source": "pii", "target": "sentiment"},
            {"source": "sentiment", "target": "is_kasuhara"},
            {"source": "is_kasuhara", "sourceHandle": "true", "target": "block_kasuhara"},
            {"source": "is_kasuhara", "sourceHandle": "false", "target": "llm"},
            {"source": "llm", "target": "out"},
        ]
    with pytest.raises(CompileError, match="one DataKit plugin per route"):
        compile_pipeline(graph_of(mutate))


@pytest.mark.parametrize("mutate, expected", [
    (lambda d: d["nodes"].append({"id": "x", "type": "nope"}), "unknown node type"),
    (lambda d: d["nodes"].append({"id": "orphan", "type": "regex_pii"}), "not connected"),
    (lambda d: d["edges"].append({"source": "llm", "target": "safety"}), "cycle"),
    (lambda d: d["edges"].remove({"source": "is_kasuhara", "sourceHandle": "true", "target": "block_kasuhara"}),
     "output 'true' is not connected"),
    (lambda d: d["edges"].append({"source": "block_kasuhara", "target": "llm"}), "cannot have outgoing edges"),
    (lambda d: d["nodes"].append({"id": "pg2", "type": "ai_prompt_guard"}) or d["edges"].extend(
        [{"source": "prompt_guard", "target": "pg2"}, {"source": "pg2", "target": "safety"}]), "one 'ai-prompt-guard'"),
    (lambda d: d["nodes"][2]["config"].update(fail_mode="sometimes"), "fail_mode"),
    (lambda d: d["nodes"][5]["config"]["rules"][0].update(node="prompt_guard"), "custom detector node"),
    (lambda d: d["nodes"].append({"id": "sent2", "type": "sentiment_kasuhara"}) or d["edges"].extend(
        [{"source": "llm", "target": "sent2"}, {"source": "sent2", "target": "out"}]), "cannot run in the response phase"),
])
def test_invalid_graphs(graph_of, mutate, expected):
    errs = errors(graph_of(mutate))
    assert any(expected in e for e in errs), errs


def test_literal_secret_warns(graph_of):
    g = graph_of(lambda d: d["nodes"][7]["config"].update(auth_header_value="Bearer sk-live-123"))
    warns = [i.message for i in validate(g).issues if i.level == "warning"]
    assert any("vault reference" in w for w in warns)


def test_partial_vault_reference_warns(graph_of):
    g = graph_of(lambda d: d["nodes"][7]["config"].update(auth_header_value="Bearer {vault://env/OPENAI_API_KEY}"))
    warns = [i.message for i in validate(g).issues if i.level == "warning"]
    assert any("whole value" in w for w in warns)
    assert not [i for i in validate(graph_of()).issues if i.level == "warning"]
