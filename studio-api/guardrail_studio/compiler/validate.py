"""Graph validation. Every rule the Kong runtime imposes is checked here, so the
UI can show errors on the canvas before a deploy is attempted."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

import jsonschema

from guardrail_common import catalog, llamaguard, outputs
from guardrail_common.graph import PipelineGraph, topo_order

REQUEST, RESPONSE = catalog.REQUEST, catalog.RESPONSE


@dataclass
class Issue:
    level: Literal["error", "warning"]
    message: str
    node: str | None = None
    edge: tuple[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "message": self.message, "node": self.node,
                "edge": list(self.edge) if self.edge else None}


@dataclass
class Analysis:
    """Result of validation: issues plus the structure the compiler needs."""

    issues: list[Issue] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    phase: dict[str, str] = field(default_factory=dict)  # node id -> request/response
    start: str | None = None
    llm: str | None = None
    end: str | None = None

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def error(self, msg: str, node: str | None = None, edge: tuple[str, str] | None = None) -> None:
        self.issues.append(Issue("error", msg, node, edge))

    def warn(self, msg: str, node: str | None = None) -> None:
        self.issues.append(Issue("warning", msg, node))


def _reachable(start: str, succ: dict[str, list[str]], removed: str | None = None, stop: set[str] = frozenset()) -> set[str]:
    seen, stack = set(), [start]
    while stack:
        n = stack.pop()
        if n in seen or n == removed:
            continue
        seen.add(n)
        if n in stop and n != start:
            continue
        stack.extend(succ.get(n, []))
    return seen


def _is_vault_ref(value: Any) -> bool:
    return isinstance(value, str) and "{vault://" in value


def _is_whole_vault_ref(value: str) -> bool:
    # Kong only resolves a reference that is the entire field value.
    return bool(re.fullmatch(r"\{vault://[^{}]+\}", value.strip()))


def _laya_issues(cfg: dict[str, Any]) -> list[str]:
    """Cross-field checks for Laya questions that JSON Schema cannot express."""
    questions = cfg.get("questions") or {}
    out = []
    for name in cfg.get("detect_on", []):
        q = questions.get(name)
        if q is None:
            out.append(f"detect_on: '{name}' is not one of the questions ({', '.join(questions) or 'none'})")
        elif q.get("type") == "choice":
            out.append(f"detect_on: '{name}' is a choice question; branch on flags.{name} with a condition instead")
    for name, q in questions.items():
        if q.get("type") == "score" and "threshold" in q and q["threshold"] >= len(q.get("criteria", [])):
            out.append(f"questions.{name}.threshold: {q['threshold']} is above the top level "
                       f"({len(q.get('criteria', [])) - 1})")
    return out


def validate(graph: PipelineGraph) -> Analysis:
    a = Analysis()

    # --- nodes, ids and types
    counts = Counter(n.id for n in graph.nodes)
    for nid, c in counts.items():
        if c > 1:
            a.error(f"duplicate node id '{nid}'", nid)
    types: dict[str, catalog.NodeType] = {}
    for n in graph.nodes:
        try:
            types[n.id] = catalog.get(n.type)
        except KeyError as exc:
            a.error(str(exc), n.id)
    if not a.ok:
        return a

    for t in ("prompt_in", "llm"):
        found = [n.id for n in graph.nodes if n.type == t]
        if len(found) != 1:
            a.error(f"pipeline needs exactly one '{catalog.get(t).label}' node (found {len(found)})")
    outs = [n.id for n in graph.nodes if n.type == "response_out"]
    if len(outs) > 1:
        a.error("pipeline can have at most one 'Response Out' node")
    if not a.ok:
        return a
    a.start = next(n.id for n in graph.nodes if n.type == "prompt_in")
    a.llm = next(n.id for n in graph.nodes if n.type == "llm")
    a.end = outs[0] if outs else None

    # --- edges
    ids = set(counts)
    for e in graph.edges:
        if e.source not in ids or e.target not in ids:
            a.error(f"edge {e.source} -> {e.target} references a missing node", edge=(e.source, e.target))
        elif e.source == e.target:
            a.error("self-loop", e.source, (e.source, e.target))
    if not a.ok:
        return a
    try:
        a.order = topo_order([n.id for n in graph.nodes], graph.edges)
    except ValueError as exc:
        a.error(str(exc))
        return a

    succ: dict[str, list[str]] = {}
    pred: dict[str, list[str]] = {}
    for e in graph.edges:
        succ.setdefault(e.source, []).append(e.target)
        pred.setdefault(e.target, []).append(e.source)
    out_edges = graph.successors()

    def ancestors(node_id: str) -> set[str]:
        return _reachable(node_id, pred) - {node_id}

    # --- phases: request = reachable from start before the LLM; response = after it
    req = _reachable(a.start, succ, stop={a.llm}) - {a.llm}
    resp = _reachable(a.llm, succ) - {a.llm}
    for nid in req:
        a.phase[nid] = REQUEST
    for nid in resp:
        if nid in req:
            a.error("node is reachable both before and after the LLM (edge skips the LLM)", nid)
        a.phase[nid] = RESPONSE
    a.phase[a.llm] = REQUEST
    if a.llm not in _reachable(a.start, succ):
        a.error("the LLM node is not reachable from Prompt In")
    if a.end and a.end not in resp:
        a.error("'Response Out' must come after the LLM", a.end)
    if not a.end and resp:
        a.error("response-phase nodes need a 'Response Out' node after them")

    for n in graph.nodes:
        nt = types[n.id]
        if n.id not in a.phase:
            a.error("node is not connected to the pipeline", n.id)
            continue
        ph = a.phase[n.id]
        if nt.kind != "endpoint" and ph not in nt.phases:
            a.error(f"'{nt.label}' cannot run in the {ph} phase", n.id)

        # config against the node schema
        cfg = catalog.with_defaults(n.type, n.config)
        try:
            jsonschema.validate(cfg, nt.config_schema)
        except jsonschema.ValidationError as exc:
            path = ".".join(str(p) for p in exc.absolute_path) or "config"
            a.error(f"{path}: {exc.message}", n.id)
        schema_ok = not any(i.node == n.id and i.level == "error" for i in a.issues)
        if n.type == "laya_classify" and schema_ok:
            for msg in _laya_issues(cfg):
                a.error(msg, n.id)
        if n.type == "llamaguard_safety" and schema_ok:
            errs, warns = llamaguard.policy_issues(cfg)
            for msg in errs:
                a.error(msg, n.id)
            for msg in warns:
                a.warn(msg, n.id)
        for key, prop in nt.config_schema.get("properties", {}).items():
            if not (prop.get("x-secret") and cfg.get(key)):
                continue
            if not _is_vault_ref(cfg[key]):
                a.warn(f"'{key}' holds a literal secret; use a vault reference such as {{vault://env/NAME}}", n.id)
            elif not _is_whole_vault_ref(cfg[key]):
                a.warn(f"'{key}': Kong resolves a vault reference only when it is the whole value; "
                       "put the full value (e.g. 'Bearer sk-…') in the secret", n.id)

        # outputs
        edges = out_edges.get(n.id, [])
        if n.type == "condition":
            handles = Counter(e.source_handle for e in edges)
            for h in ("true", "false"):
                if handles[h] == 0:
                    a.error(f"condition output '{h}' is not connected", n.id)
            for h in handles:
                if h not in ("true", "false"):
                    a.error(f"condition edges need a 'true' or 'false' handle (got {h!r})", n.id)
        elif n.type == "block":
            if edges:
                a.error("'Block' ends the request; it cannot have outgoing edges", n.id)
        elif n.type not in ("response_out", "llm") and not edges:
            a.error("node has no outgoing edge", n.id)

        # rules that reference other nodes
        if n.type == "condition":
            for r in cfg.get("rules", []):
                if r.get("node") not in ids:
                    a.error(f"condition rule references unknown node '{r.get('node')}'", n.id)
                elif catalog.get(graph.node(r["node"]).type).kind != "custom":
                    a.error(f"condition rule must reference a custom detector node, not '{r['node']}'", n.id)
                elif r["node"] not in ancestors(n.id):
                    a.error(f"condition rule reads '{r['node']}', which does not run before this condition", n.id)
                else:
                    src = graph.node(r["node"])
                    if msg := outputs.check_rule(r, outputs.result_fields(src.type, src.config)):
                        a.error(f"rule on '{r['node']}': {msg}", n.id)

    if not a.ok:
        return a

    # --- Kong runtime rules for native plugins
    native = [n for n in graph.nodes if types[n.id].kind == "native"]
    for plugin, c in Counter(types[n.id].plugin for n in native).items():
        if c > 1:
            a.error(f"Kong allows one '{plugin}' plugin per route; it is used {c} times")
    for n in native:
        # A native plugin runs on every request, so it must sit on every path:
        # removing it must disconnect the phase start from the phase end.
        ph = a.phase[n.id]
        src, dst = (a.start, a.llm) if ph == REQUEST else (a.llm, a.end)
        if dst in _reachable(src, succ, removed=n.id):
            a.error("native Kong plugins run on every request, so they cannot sit inside a branch; "
                    "move it before the condition or after the branches merge", n.id)
    return a
