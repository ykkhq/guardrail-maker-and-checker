from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from guardrail_common.llamaguard import build_prompt, codes
from guardrail_engine.detectors.base import Detector
from guardrail_engine.models import DetectorResult


class LlamaGuardSafety(Detector):
    """Llama Guard 3 via Ollama raw generate, or OpenAI-compatible /v1/completions.

    The prompt (task instruction + safety policy) is built from node config
    and sent as a full string so the model template is not applied twice.
    Errors raise, and the executor applies the node's fail_mode.
    """

    type = "llamaguard_safety"

    def __init__(self, timeout_s: float = 30.0) -> None:
        super().__init__()
        self.timeout_s = timeout_s

    def load(self) -> None:
        """Warm the default model so the first request is not a cold start."""
        from guardrail_common.catalog import with_defaults

        self.detect("hello", with_defaults(self.type, {}))

    def detect(self, text: str, config: dict[str, Any]) -> DetectorResult:
        backend = (
            config.get("backend")
            or os.environ.get("LLAMAGUARD_BACKEND", "ollama")
        ).strip()
        prompt = build_prompt(text, config, config.get("_phase", "request"))
        model = config.get("model") or os.environ.get("LLAMAGUARD_MODEL") or "llama-guard3:1b"
        if backend == "openai_completions":
            output = self._openai_completions(prompt, model, config)
        else:
            output = self._ollama_generate(prompt, model, config)
        return parse_output(output, config.get("policy"))

    def _ollama_generate(self, prompt: str, model: str, config: dict[str, Any]) -> str:
        host = (
            config.get("ollama_host")
            or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        ).rstrip("/")
        payload = {
            "model": model,
            "prompt": prompt,
            "raw": True,
            "stream": False,
            "options": {"temperature": 0},
            "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "30m"),
        }
        req = urllib.request.Request(
            f"{host}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.load(resp)["response"].strip()

    def _openai_completions(self, prompt: str, model: str, config: dict[str, Any]) -> str:
        # LM Studio / OpenAI-compatible: same full prompt as Ollama raw.
        base = (
            config.get("base_url")
            or os.environ.get("LLAMAGUARD_BASE_URL", "http://localhost:1234")
        ).rstrip("/")
        payload = {
            "model": model,
            "prompt": prompt,
            "temperature": 0,
            "max_tokens": 128,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if key := (config.get("api_key") or os.environ.get("LLAMAGUARD_API_KEY", "")).strip():
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            f"{base}/v1/completions",
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            data = json.load(resp)
        choice = (data.get("choices") or [{}])[0]
        text = choice.get("text") or choice.get("message", {}).get("content") or ""
        return text.strip()


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
