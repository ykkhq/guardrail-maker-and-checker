"""Tests against the real models. Run with: pytest -m models (needs the `models` extra,
spaCy ja_core_news_trf, HF model downloads, and Ollama with llama-guard3:1b)."""

import pytest

from engine_fakes import KASUHARA_PROMPT, PII_PROMPT

pytestmark = pytest.mark.models


def test_presidio_masks_japanese_pii():
    pytest.importorskip("presidio_analyzer")
    from guardrail_engine.detectors.presidio_pii import PresidioPII

    r = PresidioPII().detect(PII_PROMPT, {"language": "ja", "score_threshold": 0.5})
    assert r.detected and "user@example.com" not in r.text and "090-1234-5678" not in r.text


def test_sentiment_flags_kasuhara():
    pytest.importorskip("transformers")
    from guardrail_common.catalog import with_defaults
    from guardrail_engine.detectors.sentiment_kasuhara import SentimentKasuhara

    r = SentimentKasuhara().detect(KASUHARA_PROMPT, with_defaults("sentiment_kasuhara", {}))
    assert r.flags["kasuhara"] is True
