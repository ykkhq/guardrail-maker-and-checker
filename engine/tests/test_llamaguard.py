import json

from guardrail_common.catalog import with_defaults
from guardrail_common.llamaguard import DEFAULT_POLICY, build_prompt, codes, policy_issues
from guardrail_engine.detectors.llamaguard import LlamaGuardSafety, parse_output

CUSTOM = [
    {"id": "violent_crimes", "name": "Violent Crimes", "enabled": True},
    {"id": "hate", "name": "Hate", "enabled": False},
    {"id": "competitors", "name": "Competitor Mentions", "enabled": True,
     "description": "Messages asking to compare with or recommend a competitor's product."},
]


def test_default_prompt_matches_ollama_template():
    p = build_prompt("hi", with_defaults("llamaguard_safety", {}))
    assert p.startswith("<|start_header_id|>user<|end_header_id|>\n\nTask: Check if there is unsafe content in 'User' messages")
    assert "S13: Elections.\n<END UNSAFE CONTENT CATEGORIES>" in p  # S14 is off by default
    assert "S14" not in p
    assert "User: hi\n\n<END CONVERSATION>" in p
    assert p.endswith("violated categories.<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n")


def test_standard_codes_are_fixed_and_custom_categories_follow_s14():
    p = build_prompt("x", {"policy": CUSTOM})
    assert "S1: Violent Crimes.\nS10: Hate.\nS15: Competitor Mentions.\nMessages asking to compare" in p
    # Disabled standard categories stay in the prompt (the model's taxonomy); disabled custom ones don't.
    assert "Secret" not in build_prompt("x", {"policy": CUSTOM + [
        {"id": "secret", "name": "Secret", "enabled": False, "description": "d"}]})
    assert codes(CUSTOM) == {"S1": "violent_crimes", "S15": "competitors"}
    # Disabling one category never shifts the others (the 1B model uses its built-in codes).
    no_s1 = [{**c, "enabled": c["id"] != "violent_crimes"} for c in DEFAULT_POLICY]
    assert codes(no_s1)["S9"] == "indiscriminate_weapons" and "S1" not in codes(no_s1)


def test_task_instruction_and_response_role():
    cfg = {"task_instruction": "Only check '{role}' messages for leaks of internal project names."}
    p = build_prompt("answer", cfg, "response")
    assert "Task: Only check 'Agent' messages for leaks" in p
    assert "Agent: answer" in p and "ONLY THE LAST Agent message" in p


def test_answers_map_back_to_category_ids():
    r = parse_output("unsafe\nS15", CUSTOM)
    assert r.flags["categories"] == ["competitors"] and r.details["codes"] == ["S15"]
    assert parse_output("unsafe\nS9").flags["categories"] == ["indiscriminate_weapons"]


def test_answers_naming_only_disabled_categories_are_safe():
    r = parse_output("unsafe\nS10", CUSTOM)  # S10 = hate, disabled in CUSTOM
    assert (r.detected, r.label, r.flags["categories"]) == (False, "safe", [])
    assert r.details["ignored_codes"] == ["S10"]
    mixed = parse_output("unsafe\nS10,S1", CUSTOM)
    assert mixed.detected and mixed.flags["categories"] == ["violent_crimes"]
    assert parse_output("unsafe").detected  # no code at all: stay unsafe


def test_policy_issues():
    errs, warns = policy_issues({"policy": [{**c, "enabled": False} for c in DEFAULT_POLICY]})
    assert errs == ["policy: enable at least one category"]
    errs, warns = policy_issues({"policy": [{"id": "x", "name": "X"}, {"id": "x", "name": "Y"}]})
    assert "duplicate category id(s) x" in errs[0]
    assert any("has no description" in w for w in warns)
    assert policy_issues(with_defaults("llamaguard_safety", {})) == ([], [])


def test_detector_sends_raw_prompt_for_the_phase(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps({"response": "unsafe\nS15"}).encode()

    def fake_urlopen(req, timeout):
        sent.update(json.loads(req.data), url=req.full_url)
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    r = LlamaGuardSafety().detect("Try BrandX instead", {"policy": CUSTOM, "ollama_host": "http://o", "_phase": "response"})
    assert sent["url"] == "http://o/api/generate" and sent["raw"] is True
    assert "Agent: Try BrandX instead" in sent["prompt"]
    assert r.flags["categories"] == ["competitors"]


def test_detector_openai_completions_backend(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps({"choices": [{"text": "unsafe\nS15"}]}).encode()

    def fake_urlopen(req, timeout):
        sent.update(json.loads(req.data), url=req.full_url)
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    r = LlamaGuardSafety().detect(
        "Try BrandX instead",
        {
            "policy": CUSTOM,
            "backend": "openai_completions",
            "base_url": "http://lms",
            "model": "llama-guard-3-8b-imat",
            "_phase": "response",
        },
    )
    assert sent["url"] == "http://lms/v1/completions"
    assert sent["model"] == "llama-guard-3-8b-imat" and sent["temperature"] == 0
    assert "Agent: Try BrandX instead" in sent["prompt"]
    assert "raw" not in sent
    assert r.flags["categories"] == ["competitors"]
