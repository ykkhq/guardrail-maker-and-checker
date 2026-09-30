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


def _with_laya(graph_of, laya_cfg, rule=None):
    """jp-support with a Laya node between sentiment and the condition."""
    def mutate(d):
        d["nodes"].append({"id": "triage", "type": "laya_classify", "config": laya_cfg})
        d["edges"] = [e for e in d["edges"] if e["source"] != "sentiment"] + [
            {"source": "sentiment", "target": "triage"}, {"source": "triage", "target": "is_kasuhara"}]
        if rule:
            d["nodes"][5]["config"]["rules"].append(rule)
    return graph_of(mutate)


def test_laya_presets_and_custom_question_are_valid(graph_of):
    custom = {"questions": {"wants_human": {"type": "noul", "instructions": "Does `message` ask for a human?"}},
              "detect_on": ["wants_human"]}
    assert validate(_with_laya(graph_of, {})).ok
    assert validate(_with_laya(graph_of, custom,
                               {"node": "triage", "field": "flags.wants_human", "op": "eq", "value": True})).ok


@pytest.mark.parametrize("cfg, expected", [
    ({"questions": {"intent": {"type": "choice", "instructions": "x"}}, "detect_on": []}, "'criteria' is a required property"),
    ({"questions": {"u": {"type": "score", "instructions": "x", "criteria": ["a", "b"], "threshold": 0.5}},
      "detect_on": []}, "is not of type 'integer'"),
    ({"questions": {"Bad Name": {"type": "noul", "instructions": "x"}}, "detect_on": []}, "does not match"),
    ({"questions": {"p": {"type": "noul", "instructions": "x", "extra": 1}}, "detect_on": []}, "Additional properties"),
    ({"detect_on": ["department"]}, "'department' is not one of the questions"),
    ({"detect_on": ["intent"]}, "is a choice question"),
    ({"questions": {"u": {"type": "score", "instructions": "x", "criteria": ["a", "b"], "threshold": 5}},
      "detect_on": ["u"]}, "above the top level (1)"),
])
def test_invalid_laya_config(graph_of, cfg, expected):
    errs = [i.message for i in validate(_with_laya(graph_of, cfg)).issues if i.level == "error" and i.node == "triage"]
    assert any(expected in e for e in errs), errs


def test_condition_on_unknown_laya_question_is_rejected(graph_of):
    g = _with_laya(graph_of, {}, {"node": "triage", "field": "flags.department", "op": "eq", "value": "hr"})
    errs = [i.message for i in validate(g).issues if i.level == "error"]
    assert any("'flags.department' is not a result of 'triage'" in e and "flags.intent" in e for e in errs), errs


@pytest.mark.parametrize("rule, expected", [
    ({"field": "detected", "op": "gte", "value": 0.8}, "'detected' is boolean: use eq/ne"),
    ({"field": "flags.pii", "op": "eq", "value": "yes"}, "value must be true or false"),
    ({"field": "flags.intent", "op": "eq", "value": "refund"}, "has no value 'refund'"),
    ({"field": "flags.intent", "op": "in", "value": "billing"}, "'in' needs a list"),
    ({"field": "flags.urgency", "op": "gte", "value": 7}, "ranges 0..3"),
    ({"field": "details.scores.pii", "op": "gt", "value": True}, "must be a number"),
])
def test_laya_rule_must_fit_the_question_type(graph_of, rule, expected):
    g = _with_laya(graph_of, {}, {"node": "triage", **rule})
    errs = [i.message for i in validate(g).issues if i.level == "error"]
    assert any(expected in e for e in errs), errs


@pytest.mark.parametrize("rule", [
    {"field": "flags.intent", "op": "eq", "value": "complaint"},
    {"field": "flags.intent", "op": "in", "value": ["billing", "cancellation"]},
    {"field": "flags.urgency", "op": "gte", "value": 2},
    {"field": "flags.security_risk", "op": "eq", "value": True},
    {"field": "details.scores.pii", "op": "gt", "value": 0.95},
    {"field": "detected", "op": "eq", "value": True},
])
def test_laya_rules_that_fit(graph_of, rule):
    assert validate(_with_laya(graph_of, {}, {"node": "triage", **rule})).ok


def test_laya_cannot_block(graph_of):
    errs = [i.message for i in validate(_with_laya(graph_of, {"on_detect": "block"})).issues if i.node == "triage"]
    assert any("on_detect" in e and "'flag'" in e for e in errs), errs


def test_rule_on_node_after_the_condition_is_rejected(graph_of):
    def mutate(d):
        d["nodes"].append({"id": "late", "type": "keyword_blocklist", "config": {"keywords": ["x"]}})
        d["edges"] = [e for e in d["edges"] if e["source"] != "llm"] + [
            {"source": "llm", "target": "late"}, {"source": "late", "target": "out"}]
        d["nodes"][5]["config"]["rules"].append({"node": "late", "field": "detected", "op": "eq", "value": True})
    errs = [i.message for i in validate(graph_of(mutate)).issues if i.level == "error"]
    assert any("does not run before this condition" in e for e in errs), errs


def test_default_rule_for_laya_prefers_a_choice_question():
    from guardrail_common.outputs import default_rule

    assert default_rule("triage", "laya_classify", {}) == {
        "node": "triage", "field": "flags.intent", "op": "eq", "value": "question"}
    only_noul = {"questions": {"wants_human": {"type": "noul", "instructions": "x"}}, "detect_on": []}
    assert default_rule("t", "laya_classify", only_noul) == {
        "node": "t", "field": "flags.wants_human", "op": "eq", "value": True}
    assert default_rule("s", "sentiment_kasuhara", {})["field"] == "flags.kasuhara"


def test_llamaguard_policy_drives_condition_fields_and_checks(graph_of):
    from guardrail_common.outputs import result_fields

    policy = [{"id": "violent_crimes", "name": "Violent Crimes"},
              {"id": "competitors", "name": "Competitor Mentions", "description": "asks about competitors"}]
    f = next(x for x in result_fields("llamaguard_safety", {"policy": policy}) if x["field"] == "flags.categories")
    assert [v["value"] for v in f["values"]] == ["violent_crimes", "competitors"]

    def mutate(d, rule_value, cfg):
        d["nodes"][3]["config"].update(cfg, on_detect="flag")
        d["nodes"][5]["config"]["rules"].append(
            {"node": "safety", "field": "flags.categories", "op": "contains", "value": rule_value})
    ok = graph_of(lambda d: mutate(d, "competitors", {"policy": policy}))
    assert validate(ok).ok, [i.message for i in validate(ok).issues]
    bad = graph_of(lambda d: mutate(d, "hate", {"policy": policy}))
    assert any("never contains 'hate'" in i.message for i in validate(bad).issues)
    none_on = graph_of(lambda d: d["nodes"][3]["config"].update(policy=[{**policy[0], "enabled": False}]))
    assert any("enable at least one category" in i.message for i in validate(none_on).issues)
