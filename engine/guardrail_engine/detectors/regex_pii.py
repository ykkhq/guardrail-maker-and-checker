from __future__ import annotations

import re
from typing import Any

from guardrail_engine.detectors.base import Detector
from guardrail_engine.models import DetectorResult

# Patterns from guardrail_agent (JP_MYNUMBER, JP_PHONE) plus email and card.
# Order matters: My Number (12 digits) and cards are matched before phones.
PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "CREDIT_CARD": re.compile(r"(?<!\d)(?:\d{4}[-\s]?){3}\d{4}(?!\d)"),
    "JP_MYNUMBER": re.compile(r"(?<!\d)\d{4}[-\s]?\d{4}[-\s]?\d{4}(?!\d)"),
    "JP_PHONE": re.compile(r"(?<!\d)0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{4}(?!\d)"),
}


class RegexPII(Detector):
    type = "regex_pii"

    def detect(self, text: str, config: dict[str, Any]) -> DetectorResult:
        wanted = config.get("entities") or list(PATTERNS)
        entities: list[dict[str, Any]] = []
        masked = text
        for name, pattern in PATTERNS.items():
            if name not in wanted:
                continue

            def _sub(m: re.Match[str], name: str = name) -> str:
                entities.append({"type": name, "start": m.start(), "end": m.end()})
                return f"<{name}>"

            masked = pattern.sub(_sub, masked)
        return DetectorResult(
            detected=bool(entities),
            label="pii" if entities else None,
            text=masked if entities else None,
            entities=entities,
        )
