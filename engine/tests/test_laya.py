import pytest

from guardrail_common.catalog import LAYA_PRESET_QUESTIONS, with_defaults
from guardrail_engine.detectors.laya_classify import LayaClassify, interpret, to_laya

ANSWERS = {
    "pii": {"type": "noul", "noul": 0.95},
    "intent": {"type": "choice", "choice": "complaint", "probabilities": {"complaint": 0.7, "other": 0.3}},
    "urgency": {"type": "score", "score": 2.6},
    "security_risk": {"type": "noul", "noul": 0.52},
}


class FakeRouter:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def predict(self, state, questions):
        self.calls.append((state, questions))
        return {"answers": {k: v for k, v in self.answers.items() if k in questions}}


def laya_with(answers) -> LayaClassify:
    d = LayaClassify()
    d.router = FakeRouter(answers)
    d._loaded = True  # skip loading the real model
    return d


def test_presets_map_each_question_type():
    r = interpret(LAYA_PRESET_QUESTIONS, ANSWERS, ["pii", "security_risk", "urgency"])
    assert r.flags == {"pii": True, "intent": "complaint", "urgency": 3, "security_risk": False}
    # urgency level 3 >= preset threshold 3; pii 0.95 >= 0.9; security_risk 0.52 < 0.9
    assert r.details["matched"] == ["pii", "urgency"]
    assert r.detected and r.score == 0.95 and r.label == "complaint"
    assert r.details["scores"]["intent"] == 0.7


def test_default_thresholds():
    qs = {"a": {"type": "noul", "instructions": "x"},
          "b": {"type": "score", "instructions": "y", "criteria": ["l0", "l1", "l2"]}}
    r = interpret(qs, {"a": {"noul": 0.8}, "b": {"score": 1.2}}, ["a", "b"])
    assert r.flags == {"a": True, "b": 1}  # noul default 0.75; score default = top level (2)
    assert r.details["matched"] == ["a"]


def test_threshold_is_stripped_before_calling_laya():
    assert "threshold" not in to_laya(LAYA_PRESET_QUESTIONS)["pii"]
    assert to_laya(LAYA_PRESET_QUESTIONS)["intent"]["criteria"] == LAYA_PRESET_QUESTIONS["intent"]["criteria"]


def test_custom_questions_are_sent_to_laya():
    custom = {"language_is_japanese": {"type": "noul", "instructions": "Is `message` written in Japanese?",
                                       "threshold": 0.6}}
    d = laya_with({"language_is_japanese": {"noul": 0.99}})
    r = d.detect("こんにちは", {"questions": custom, "detect_on": ["language_is_japanese"]})
    state, sent = d.router.calls[0]
    assert state == {"message": "こんにちは"}
    assert sent == {"language_is_japanese": {"type": "noul", "instructions": "Is `message` written in Japanese?"}}
    assert r.detected and r.flags == {"language_is_japanese": True}


def test_catalog_defaults_use_presets_and_are_not_shared():
    cfg = with_defaults("laya_classify", {})
    assert list(cfg["questions"]) == ["pii", "intent", "urgency", "security_risk"]
    cfg["questions"]["pii"]["threshold"] = 0.1
    assert LAYA_PRESET_QUESTIONS["pii"]["threshold"] == 0.9


def test_unknown_detect_on_is_an_error():
    with pytest.raises(ValueError, match="unknown questions: nope"):
        laya_with(ANSWERS).detect("x", {"questions": LAYA_PRESET_QUESTIONS, "detect_on": ["nope"]})
