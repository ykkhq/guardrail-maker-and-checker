from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from guardrail_engine.detectors.base import Detector
from guardrail_engine.models import DetectorResult

# Llama Guard 3 hazard categories, for readable labels.
CATEGORIES = {
    "S1": "violent_crimes", "S2": "non_violent_crimes", "S3": "sex_crimes",
    "S4": "child_exploitation", "S5": "defamation", "S6": "specialized_advice",
    "S7": "privacy", "S8": "intellectual_property", "S9": "indiscriminate_weapons",
    "S10": "hate", "S11": "self_harm", "S12": "sexual_content", "S13": "elections",
    "S14": "code_interpreter_abuse",
}


class LlamaGuardSafety(Detector):
    """Llama Guard 3 on Ollama (ported from CPUHybridGuardrail.check_safety_ollama).

    Calls the Ollama REST API directly so the engine needs no Ollama client
    package. Errors raise, and the executor applies the node's fail_mode (the
    prototype always failed open).
    """

    type = "llamaguard_safety"

    def __init__(self, timeout_s: float = 30.0) -> None:
        super().__init__()
        self.timeout_s = timeout_s

    def load(self) -> None:
        """Warm the default model in Ollama so the first request is not a cold start."""
        from guardrail_common.catalog import with_defaults

        self.detect("hello", with_defaults(self.type, {}))

    def detect(self, text: str, config: dict[str, Any]) -> DetectorResult:
        host = (config.get("ollama_host") or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        payload = {
            "model": config.get("model", "llama-guard3:1b"),
            "messages": [{"role": "user", "content": text}],
            "stream": False,
            # Keep the model loaded between requests (Ollama unloads after 5 min by default).
            "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "30m"),
        }
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            output = json.load(resp)["message"]["content"].strip()
        return parse_output(output)


def parse_output(output: str) -> DetectorResult:
    # Llama Guard answers "safe" or "unsafe\nS<code>[,S<code>...]".
    if output.lower().startswith("safe"):
        return DetectorResult(detected=False, label="safe", details={"raw": output})
    lines = output.splitlines()
    codes = [c.strip() for c in lines[1].split(",")] if len(lines) > 1 else []
    return DetectorResult(
        detected=True,
        label="unsafe",
        flags={"categories": [CATEGORIES.get(c, c) for c in codes]},
        details={"raw": output, "codes": codes},
    )
