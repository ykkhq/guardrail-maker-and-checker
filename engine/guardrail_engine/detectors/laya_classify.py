from __future__ import annotations

from typing import Any

from guardrail_common.catalog import LAYA_PRESET_QUESTIONS
from guardrail_engine.detectors.base import Detector
from guardrail_engine.models import DetectorResult

NOUL_THRESHOLD = 0.75  # default when a noul question sets no threshold


def to_laya(questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Strip our extension fields; Laya rejects keys it does not know."""
    return {name: {k: v for k, v in q.items() if k != "threshold"} for name, q in questions.items()}


def interpret(questions: dict[str, dict[str, Any]], answers: dict[str, dict[str, Any]],
              detect_on: list[str]) -> DetectorResult:
    """Map Laya answers to a DetectorResult.

    flags[name]  noul: bool (probability >= threshold), score: int level, choice: label
    hits[name]   noul: same bool, score: level >= threshold (default: top level), choice: never
    details.scores[name]  noul: probability, score: raw level, choice: probability of the label
    """
    flags: dict[str, Any] = {}
    hits: dict[str, bool] = {}
    scores: dict[str, float] = {}
    for name, q in questions.items():
        a = answers.get(name, {})
        match q["type"]:
            case "noul":
                p = float(a.get("noul", 0.0))
                flags[name] = hits[name] = p >= q.get("threshold", NOUL_THRESHOLD)
                scores[name] = round(p, 4)
            case "score":
                raw = float(a.get("score", 0.0))
                level = int(round(raw))
                flags[name] = level
                hits[name] = level >= q.get("threshold", len(q["criteria"]) - 1)
                scores[name] = round(raw, 4)
            case "choice":
                label = a.get("choice")
                flags[name] = label
                hits[name] = False
                scores[name] = round(float(a.get("probabilities", {}).get(label, 0.0)), 4)

    matched = [n for n in detect_on if hits.get(n)]
    first_choice = next((flags[n] for n, q in questions.items() if q["type"] == "choice"), None)
    return DetectorResult(
        detected=bool(matched),
        # Highest probability among the detect_on noul questions that fired.
        score=max((scores[n] for n in matched if questions[n]["type"] == "noul"), default=None),
        label=first_choice,
        flags=flags,
        details={"scores": scores, "matched": matched, "answers": answers},
    )


class LayaClassify(Detector):
    """Laya typed classification with user-defined questions.

    Questions come from node config (default: the pii / intent / urgency /
    security_risk presets in the catalog). The prompt is passed to Laya as the
    state field `message`, which instructions can reference.
    """

    type = "laya_classify"
    requires = ("laya",)

    def load(self) -> None:
        from laya import Router

        self.router = Router(preload=True)
        self.router.preload(["english", "multilingual"])

    def detect(self, text: str, config: dict[str, Any]) -> DetectorResult:
        self.ensure_loaded()
        questions = config.get("questions") or LAYA_PRESET_QUESTIONS
        detect_on = config.get("detect_on", ["pii", "security_risk"])
        unknown = [n for n in detect_on if n not in questions]
        if unknown:
            raise ValueError(f"detect_on names unknown questions: {', '.join(unknown)}")
        answers = self.router.predict({"message": text}, to_laya(questions))["answers"]
        return interpret(questions, answers, detect_on)
