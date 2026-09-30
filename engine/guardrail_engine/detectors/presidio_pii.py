from __future__ import annotations

import os
from typing import Any

from guardrail_engine.detectors.base import Detector
from guardrail_engine.models import DetectorResult

# spaCy model per language. Override with PRESIDIO_SPACY_<LANG>, e.g. PRESIDIO_SPACY_EN.
SPACY_MODELS = {"ja": "ja_core_news_trf", "en": "en_core_web_lg"}


class PresidioPII(Detector):
    """Presidio + spaCy PII masking (ported from CPUHybridGuardrail.mask_pii_presidio).

    Adds the prototype's Japanese My Number and phone recognizers. The
    language comes from node config instead of being hardcoded to "ja".
    """

    type = "presidio_pii"
    requires = ("presidio_analyzer", "presidio_anonymizer", "spacy")

    def load(self) -> None:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        langs = [l.strip() for l in os.environ.get("PRESIDIO_LANGUAGES", "ja").split(",") if l.strip()]
        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": [
                {"lang_code": l, "model_name": os.environ.get(f"PRESIDIO_SPACY_{l.upper()}", SPACY_MODELS[l])}
                for l in langs
            ],
            # spaCy ja tags phone numbers and whole "email / phone" strings as ORG,
            # which then hides the real entity type. Organisations are not PII here.
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": {
                    "PER": "PERSON", "PERSON": "PERSON", "LOC": "LOCATION", "GPE": "LOCATION",
                    "DATE": "DATE_TIME", "TIME": "DATE_TIME", "NORP": "NRP",
                },
                "labels_to_ignore": ["ORG", "ORGANIZATION", "FAC", "PRODUCT", "CARDINAL", "ORDINAL",
                                     "QUANTITY", "MONEY", "PERCENT", "EVENT", "WORK_OF_ART", "LAW", "LANGUAGE"],
            },
        }
        nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()
        self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=langs)
        self.anonymizer = AnonymizerEngine()
        self.languages = langs

        if "ja" in langs:
            self.analyzer.registry.add_recognizer(
                PatternRecognizer(
                    supported_entity="JP_MYNUMBER",
                    patterns=[Pattern(name="jp_mynumber", regex=r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}\b", score=0.95)],
                    supported_language="ja",
                )
            )
            self.analyzer.registry.add_recognizer(
                PatternRecognizer(
                    supported_entity="JP_PHONE",
                    patterns=[Pattern(name="jp_phone", regex=r"\b0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{4}\b", score=0.95)],
                    supported_language="ja",
                )
            )

    def detect(self, text: str, config: dict[str, Any]) -> DetectorResult:
        self.ensure_loaded()
        language = config.get("language", "ja")
        if language not in self.languages:
            raise ValueError(f"language '{language}' not loaded; set PRESIDIO_LANGUAGES (loaded: {self.languages})")
        results = self.analyzer.analyze(
            text=text,
            language=language,
            entities=config.get("entities") or None,
            score_threshold=config.get("score_threshold", 0.5),
        )
        if not results:
            return DetectorResult(detected=False)
        masked = self.anonymizer.anonymize(text=text, analyzer_results=results).text
        return DetectorResult(
            detected=True,
            label="pii",
            score=max(r.score for r in results),
            text=masked,
            entities=[{"type": r.entity_type, "start": r.start, "end": r.end, "score": round(r.score, 3)} for r in results],
        )
