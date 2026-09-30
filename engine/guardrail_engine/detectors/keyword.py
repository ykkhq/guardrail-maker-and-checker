from __future__ import annotations

from typing import Any

from guardrail_engine.detectors.base import Detector
from guardrail_engine.models import DetectorResult


class KeywordBlocklist(Detector):
    type = "keyword_blocklist"

    def detect(self, text: str, config: dict[str, Any]) -> DetectorResult:
        keywords = config.get("keywords", [])
        if config.get("case_sensitive"):
            hits = [k for k in keywords if k in text]
        else:
            lowered = text.lower()
            hits = [k for k in keywords if k.lower() in lowered]
        return DetectorResult(detected=bool(hits), label="keyword" if hits else None, details={"matched": hits})
