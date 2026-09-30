from __future__ import annotations

import threading
from typing import Any

from guardrail_engine.models import DetectorResult


class Detector:
    """Base class. Subclasses implement `detect`; heavy models go in `load`.

    `load` runs once, on first use or at startup preload, under a lock so
    concurrent requests do not load a model twice.
    """

    type: str = ""
    # Python modules the detector imports lazily; missing ones make it unavailable.
    requires: tuple[str, ...] = ()

    def __init__(self) -> None:
        self._loaded = False
        self._lock = threading.Lock()

    def load(self) -> None:  # pragma: no cover - overridden by heavy detectors
        pass

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if not self._loaded:
                self.load()
                self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    def missing(self) -> list[str]:
        import importlib.util

        return [m for m in self.requires if importlib.util.find_spec(m) is None]

    def detect(self, text: str, config: dict[str, Any]) -> DetectorResult:
        raise NotImplementedError
