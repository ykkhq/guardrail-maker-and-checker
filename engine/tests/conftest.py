from __future__ import annotations

import json

import pytest

from guardrail_engine.detectors import Registry
from guardrail_engine.detectors.keyword import KeywordBlocklist
from guardrail_engine.detectors.regex_pii import RegexPII

from engine_fakes import SAMPLES, Broken, FakeLlamaGuard, FakePresidio, FakeSentiment


@pytest.fixture
def registry() -> Registry:
    return Registry([FakeLlamaGuard(), FakePresidio(), FakeSentiment(), KeywordBlocklist(), RegexPII()])


@pytest.fixture
def broken_registry() -> Registry:
    return Registry([Broken(), FakePresidio(), FakeSentiment(), KeywordBlocklist(), RegexPII()])


@pytest.fixture
def jp_support() -> dict:
    return json.loads((SAMPLES / "jp-support.json").read_text())
