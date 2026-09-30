from __future__ import annotations

import copy
import json
import pathlib

import pytest

from guardrail_common.graph import PipelineGraph

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"


@pytest.fixture
def jp_support_dict() -> dict:
    return json.loads((SAMPLES / "jp-support.json").read_text())


@pytest.fixture
def graph_of(jp_support_dict):
    def make(mutate=None) -> PipelineGraph:
        d = copy.deepcopy(jp_support_dict)
        if mutate:
            mutate(d)
        return PipelineGraph.model_validate(d)

    return make
