from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from guardrail_common.llamaguard import build_prompt, codes
from guardrail_engine.detectors.base import Detector
from guardrail_engine.models import DetectorResult


class LlamaGuardSafety(Detector):
    """Llama Guard 3 on Ollama (ported from CPUHybridGuardrail.check_safety_ollama).

    The prompt (task instruction + safety policy) is built from node config
    and sent in Ollama's raw mode, bypassing the model's fixed template. Errors
    raise, and the executor applies the node's fail_mode (the prototype always
    failed open).
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
            "prompt": build_prompt(text, config, config.get("_phase", "request")),
            "raw": True,
            "stream": False,
            "options": {"temperature": 0},
            # Keep the model loaded between requests (Ollama unloads after 5 min by default).
            "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "30m"),
        }
        req = urllib.request.Request(
            f"{host}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            output = json.load(resp)["response"].strip()
        return parse_output(output, config.get("policy"))


def parse_output(output: str, policy: list[dict[str, Any]] | None = None) -> DetectorResult:
    """Llama Guard answers "safe" or "unsafe\nS<code>[,S<code>...]".

    Codes of enabled categories become category ids. Codes of disabled (or
    unknown) categories are ignored, so an answer naming only those is safe.
    """
    if output.lower().startswith("safe"):
        return DetectorResult(detected=False, label="safe", details={"raw": output})
    lines = output.splitlines()
    found = [c.strip() for c in lines[1].split(",") if c.strip()] if len(lines) > 1 else []
    enabled = codes(policy)
    categories = [enabled[c] for c in found if c in enabled]
    ignored = [c for c in found if c not in enabled]
    # "unsafe" without any code: keep it unsafe rather than guessing.
    unsafe = bool(categories) or not found
    return DetectorResult(
        detected=unsafe,
        label="unsafe" if unsafe else "safe",
        flags={"categories": categories},
        details={"raw": output, "codes": found, "ignored_codes": ignored},
    )
