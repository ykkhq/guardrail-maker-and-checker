"""Read and rewrite the text parts of OpenAI-style chat bodies."""

from __future__ import annotations

import copy
from typing import Any, Iterator


class ChatDoc:
    """Mutable view over a chat body.

    Request phase: `messages[]` with role "user" are the guarded texts. Checks
    look at the last user message; masking applies to every user message so
    PII in the history is not sent upstream either.

    Response phase: `choices[].message.content` of the completion.
    """

    def __init__(self, body: dict[str, Any], phase: str = "request"):
        self.body = copy.deepcopy(body)
        self.phase = phase

    def _slots(self) -> Iterator[tuple[dict[str, Any], str | int]]:
        """Yield (container, key) pairs whose value is a guarded string."""
        if self.phase == "response":
            for choice in self.body.get("choices", []):
                msg = choice.get("message") or {}
                if isinstance(msg.get("content"), str):
                    yield msg, "content"
            return
        for msg in self.body.get("messages", []):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                yield msg, "content"
            elif isinstance(content, list):
                # Multi-part content: only text parts are guarded.
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                        yield part, "text"

    def texts(self) -> list[str]:
        return [c[k] for c, k in self._slots()]

    def primary_text(self) -> str:
        """Text that checks run on: last user message, or the response content."""
        texts = self.texts()
        if not texts:
            return ""
        return texts[-1] if self.phase == "request" else "\n".join(texts)

    def rewrite(self, fn) -> bool:
        """Apply `fn(text) -> str | None` to every guarded text. Returns True if anything changed."""
        changed = False
        for container, key in list(self._slots()):
            new = fn(container[key])
            if new is not None and new != container[key]:
                container[key] = new
                changed = True
        return changed
