from __future__ import annotations

import logging

from guardrail_engine.detectors.base import Detector
from guardrail_engine.detectors.keyword import KeywordBlocklist
from guardrail_engine.detectors.laya_classify import LayaClassify
from guardrail_engine.detectors.llamaguard import LlamaGuardSafety
from guardrail_engine.detectors.presidio_pii import PresidioPII
from guardrail_engine.detectors.regex_pii import RegexPII
from guardrail_engine.detectors.sentiment_kasuhara import SentimentKasuhara

log = logging.getLogger(__name__)


class Registry:
    def __init__(self, detectors: list[Detector]):
        self._by_type = {d.type: d for d in detectors}

    def get(self, node_type: str) -> Detector:
        try:
            return self._by_type[node_type]
        except KeyError:
            raise KeyError(f"no detector for node type '{node_type}'") from None

    def types(self) -> list[str]:
        return sorted(self._by_type)

    def status(self) -> dict[str, bool]:
        return {t: d.loaded for t, d in self._by_type.items()}

    def availability(self) -> dict[str, dict]:
        out = {}
        for t, d in self._by_type.items():
            missing = d.missing()
            out[t] = {"available": not missing, "loaded": d.loaded, "missing": missing}
        return out

    def preload(self, types: list[str]) -> None:
        for t in types:
            log.info("preloading detector %s", t)
            try:
                self.get(t).ensure_loaded()
            except Exception as exc:  # noqa: BLE001 - retried on first use; fail_mode applies then
                log.warning("preload of %s failed: %s", t, exc)


def default_registry() -> Registry:
    return Registry(
        [KeywordBlocklist(), RegexPII(), LlamaGuardSafety(), PresidioPII(), SentimentKasuhara(), LayaClassify()]
    )
