"""Contract test: the segment spec the compiler inlines into DataKit is exactly
what the engine's /v1/segments/execute accepts, and it behaves as designed."""

import json
import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine" / "tests"))

from engine_fakes import (  # noqa: E402
    JAILBREAK_PROMPT, KASUHARA_PROMPT, PII_PROMPT, FakeLlamaGuard, FakePresidio, FakeSentiment, chat,
)
from guardrail_common.graph import PipelineGraph  # noqa: E402
from guardrail_engine.detectors import Registry  # noqa: E402
from guardrail_engine.main import create_app  # noqa: E402
from guardrail_studio.compiler import compile_pipeline  # noqa: E402


def compiled_segment() -> dict:
    graph = PipelineGraph.model_validate(json.loads((ROOT / "samples" / "jp-support.json").read_text()))
    dk = next(p for p in compile_pipeline(graph).plugins if p["name"] == "datakit")
    static = next(n for n in dk["config"]["nodes"] if n["name"] == "REQ_SEGMENT")
    # Round-trip through JSON, as Kong would store and send it.
    return json.loads(json.dumps(static["values"]["segment"]))


def run(prompt: str) -> dict:
    client = TestClient(create_app(Registry([FakeLlamaGuard(), FakePresidio(), FakeSentiment()])))
    r = client.post("/v1/segments/execute", json={"segment": compiled_segment(), "body": chat(prompt)})
    assert r.status_code == 200, r.text
    return r.json()


def test_pii_masked():
    res = run(PII_PROMPT)
    assert res["decision"] == "allow" and "<EMAIL>" in res["body"]["messages"][0]["content"]


def test_jailbreak_blocked():
    assert (run(JAILBREAK_PROMPT)["blocked_by"], run(JAILBREAK_PROMPT)["status"]) == ("safety", 403)


def test_kasuhara_branch_blocks():
    assert run(KASUHARA_PROMPT)["blocked_by"] == "block_kasuhara"
