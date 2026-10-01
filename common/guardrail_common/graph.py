"""Pipeline graph model shared by the Web UI, studio-api and the engine.

The shape follows React Flow (nodes + edges, camelCase `sourceHandle`) so the
canvas state can be saved and compiled without translation.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Node(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None
    # Canvas position ({"x": .., "y": ..}); UI-only, not sent to Kong.
    position: dict[str, float] | None = None


class Edge(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    source: str
    target: str
    # Named output of the source node, e.g. "true"/"false" for a condition.
    source_handle: str | None = Field(default=None, alias="sourceHandle")


class PlaygroundSample(BaseModel):
    """A one-click example prompt shown in the Studio Playground."""

    model_config = ConfigDict(extra="ignore")

    label: str
    text: str


class PipelineGraph(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = ""
    nodes: list[Node]
    edges: list[Edge] = Field(default_factory=list)
    # Playground example prompts for this pipeline (UI-only; not sent to Kong).
    playground: list[PlaygroundSample] = Field(default_factory=list)

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def successors(self) -> dict[str, list[Edge]]:
        out: dict[str, list[Edge]] = defaultdict(list)
        for e in self.edges:
            out[e.source].append(e)
        return out

    def predecessors(self) -> dict[str, list[Edge]]:
        inc: dict[str, list[Edge]] = defaultdict(list)
        for e in self.edges:
            inc[e.target].append(e)
        return inc


class Segment(BaseModel):
    """A sub-graph of custom/control nodes executed by the engine in one call.

    `entry` is the first node to run. Edges whose target is not in `nodes`
    leave the segment, meaning "continue to the next stage".
    """

    phase: str = "request"
    entry: str
    nodes: list[Node]
    edges: list[Edge] = Field(default_factory=list)


def topo_order(node_ids: list[str], edges: list[Edge]) -> list[str]:
    """Kahn's algorithm restricted to `node_ids`. Raises ValueError on cycles."""
    ids = set(node_ids)
    indeg = {n: 0 for n in node_ids}
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.source in ids and e.target in ids:
            adj[e.source].append(e.target)
            indeg[e.target] += 1
    # Keep input order for ties so output is deterministic.
    ready = [n for n in node_ids if indeg[n] == 0]
    order: list[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    if len(order) != len(node_ids):
        cyclic = sorted(ids - set(order))
        raise ValueError(f"graph contains a cycle through: {', '.join(cyclic)}")
    return order
