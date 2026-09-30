from __future__ import annotations

import threading
from typing import Any

from guardrail_engine.detectors.base import Detector
from guardrail_engine.models import DetectorResult


class SentimentKasuhara(Detector):
    """Sentiment + customer-harassment keywords (ported from check_sentiment_and_kasuhara).

    Detected when a harassment keyword matches, or when the text is negative
    with a score at or above `negative_threshold`. Pipelines load per model name.
    """

    type = "sentiment_kasuhara"
    requires = ("transformers",)

    def __init__(self) -> None:
        super().__init__()
        self._pipelines: dict[str, Any] = {}
        self._model_lock = threading.Lock()

    def load(self) -> None:
        """Preload the catalog's default model (other models load on first use)."""
        from guardrail_common.catalog import with_defaults

        self._pipeline(with_defaults(self.type, {})["model"])

    def _pipeline(self, model: str):
        if model not in self._pipelines:
            with self._model_lock:
                if model not in self._pipelines:
                    from transformers import pipeline

                    self._pipelines[model] = pipeline("sentiment-analysis", model=model, device=-1)
        return self._pipelines[model]

    def detect(self, text: str, config: dict[str, Any]) -> DetectorResult:
        keywords = config.get("keywords", [])
        hits = [k for k in keywords if k in text]
        res = self._pipeline(config["model"])(text, truncation=True)[0]
        label = str(res["label"]).lower()
        score = round(float(res["score"]), 3)
        negative = "neg" in label
        very_negative = negative and score >= config.get("negative_threshold", 0.9)
        return DetectorResult(
            detected=bool(hits) or very_negative,
            label=label,
            score=score,
            flags={"kasuhara": bool(hits), "negative": negative},
            details={"matched_keywords": hits},
        )
