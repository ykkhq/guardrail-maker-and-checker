from guardrail_engine.detectors.keyword import KeywordBlocklist
from guardrail_engine.detectors.llamaguard import parse_output
from guardrail_engine.detectors.regex_pii import RegexPII

from engine_fakes import PII_PROMPT


def test_regex_pii_masks_email_and_jp_phone():
    r = RegexPII().detect(PII_PROMPT, {})
    assert r.detected
    assert r.text == "こんにちは。私のメールは <EMAIL> で、電話番号は <JP_PHONE> です。サポートをお願いします。"
    assert {e["type"] for e in r.entities} == {"EMAIL", "JP_PHONE"}


def test_regex_pii_mynumber_and_card_are_not_phones():
    r = RegexPII().detect("番号 1234-5678-9012 カード 4111 1111 1111 1111", {})
    assert r.text == "番号 <JP_MYNUMBER> カード <CREDIT_CARD>"


def test_regex_pii_respects_entity_filter():
    r = RegexPII().detect(PII_PROMPT, {"entities": ["EMAIL"]})
    assert "<EMAIL>" in r.text and "090-1234-5678" in r.text


def test_regex_pii_clean_text():
    r = RegexPII().detect("こんにちは", {})
    assert not r.detected and r.text is None


def test_keyword_case_insensitive_by_default():
    d = KeywordBlocklist()
    assert d.detect("Please DROP TABLE users", {"keywords": ["drop table"]}).detected
    assert not d.detect("Please DROP TABLE users", {"keywords": ["drop table"], "case_sensitive": True}).detected


def test_llamaguard_output_parsing():
    assert not parse_output("safe").detected
    r = parse_output("unsafe\nS1,S9")
    assert r.detected and r.flags["categories"] == ["violent_crimes", "indiscriminate_weapons"]
