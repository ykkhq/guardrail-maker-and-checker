"""Compiles a validated pipeline graph into Kong entities.

Stages: every native node becomes its own plugin. The custom/control nodes of a
phase become one engine segment, run by a single DataKit plugin (Kong allows
one instance of a plugin per route). Request-phase plugins are ordered with
dynamic ordering (`ordering.before.access`) to match the canvas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from guardrail_common import catalog
from guardrail_common.graph import PipelineGraph, Segment
from guardrail_studio.compiler.datakit import datakit_config
from guardrail_studio.compiler.validate import Analysis, Issue, validate

REQUEST, RESPONSE = catalog.REQUEST, catalog.RESPONSE
TAG = "guardrail-studio"


class CompileError(Exception):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("; ".join(f"{i.node or '-'}: {i.message}" for i in issues if i.level == "error"))


@dataclass
class CompileOptions:
    engine_url: str = "http://guardrail-engine:8080"
    # ai-proxy-advanced sets the real upstream; Kong still needs a service URL.
    service_url: str = "http://localhost:32000"
    route_prefix: str = "/pipelines"
    datakit_debug: bool = False
    engine_timeout_ms: int = 10000


@dataclass
class Stage:
    phase: str
    kind: str  # "native" | "segment"
    plugin: str
    node: str | None = None  # native node id
    segment: Segment | None = None

    def describe(self) -> dict[str, Any]:
        d: dict[str, Any] = {"phase": self.phase, "plugin": self.plugin}
        if self.node:
            d["node"] = self.node
        if self.segment:
            d["nodes"] = [n.id for n in self.segment.nodes]
        return d


@dataclass
class CompiledPipeline:
    service: dict[str, Any]
    route: dict[str, Any]
    plugins: list[dict[str, Any]]
    stages: list[Stage]
    issues: list[Issue] = field(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        return [f"{i.node + ': ' if i.node else ''}{i.message}" for i in self.issues if i.level == "warning"]


def _reachable_from(node: str, succ: dict[str, list[str]]) -> set[str]:
    seen, stack = set(), [node]
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(succ.get(n, []))
    return seen


def plan_stages(graph: PipelineGraph, a: Analysis) -> list[Stage]:
    succ: dict[str, list[str]] = {}
    for e in graph.edges:
        succ.setdefault(e.source, []).append(e.target)
    reach = {nid: _reachable_from(nid, succ) for nid in a.order}

    stages: list[Stage] = []
    for phase, phase_start in ((REQUEST, a.start), (RESPONSE, a.llm)):
        ids = [nid for nid in a.order if a.phase.get(nid) == phase and catalog.get(graph.node(nid).type).kind != "endpoint"]
        natives = [nid for nid in ids if catalog.get(graph.node(nid).type).kind == "native"]
        engine = [nid for nid in ids if nid not in natives]

        # Natives sit on every path, so they are totally ordered and every
        # engine node is either before or after each of them.
        groups: dict[int, list[str]] = {}
        for nid in engine:
            groups.setdefault(sum(nid in reach[n] for n in natives), []).append(nid)
        if len(groups) > 1:
            split = natives[min(groups)]
            a.error(f"custom guardrails appear both before and after native plugin '{split}' in the {phase} phase; "
                    "Kong runs one DataKit plugin per route, so keep all custom/control nodes of a phase together",
                    split)
            continue

        seg_stage = None
        if groups:
            (pos, members), = groups.items()
            entry = natives[pos - 1] if pos else phase_start
            member_set = set(members)
            seg = Segment(
                phase=phase,
                entry=entry,
                nodes=[graph.node(nid) for nid in members],
                edges=[e for e in graph.edges if e.target in member_set and (e.source in member_set or e.source == entry)],
            )
            seg_stage = (pos, Stage(phase, "segment", "datakit", segment=seg))

        phase_stages = [Stage(phase, "native", catalog.get(graph.node(n).type).plugin, node=n) for n in natives]
        if seg_stage:
            phase_stages.insert(*seg_stage)
        stages += phase_stages
    return stages


def _llm_plugin(cfg: dict[str, Any]) -> dict[str, Any]:
    provider = cfg["provider"]
    options: dict[str, Any] = {}
    for k in ("max_tokens", "temperature", "upstream_url"):
        if k in cfg:
            options[k] = cfg[k]
    if provider == "ollama":
        # Kong serves Ollama through the llama2 provider in ollama format.
        provider = "llama2"
        options["llama2_format"] = "ollama"
    elif provider == "anthropic":
        options.setdefault("anthropic_version", "2023-06-01")
        options.setdefault("max_tokens", 1024)
    options.update(cfg.get("options", {}))

    target: dict[str, Any] = {
        "route_type": cfg["route_type"],
        "model": {"provider": provider, "name": cfg["model"], "options": options},
    }
    if cfg.get("auth_header_value"):
        target["auth"] = {"header_name": cfg["auth_header_name"], "header_value": cfg["auth_header_value"]}
    return {"targets": [target]}


def compile_pipeline(graph: PipelineGraph, opts: CompileOptions | None = None) -> CompiledPipeline:
    opts = opts or CompileOptions()
    a = validate(graph)
    if not a.ok:
        raise CompileError(a.issues)
    stages = plan_stages(graph, a)
    if not a.ok:
        raise CompileError(a.issues)

    tags = [TAG, f"pipeline:{graph.slug}"]
    name = f"gs-{graph.slug}"
    plugins: list[dict[str, Any]] = []

    request_plugins = [s.plugin for s in stages if s.phase == REQUEST]
    response_stages = [s for s in stages if s.phase == RESPONSE]
    segments = [s.segment for s in stages if s.segment]

    def ordering_for(plugin: str) -> dict[str, Any] | None:
        if plugin not in request_plugins:
            return None
        later = request_plugins[request_plugins.index(plugin) + 1:]
        return {"before": {"access": [*later, "ai-proxy-advanced"]}}

    emitted: set[str] = set()
    for s in stages:
        if s.plugin in emitted:  # DataKit carries both phases' segments
            continue
        emitted.add(s.plugin)
        if s.kind == "native":
            node = graph.node(s.node)
            config = catalog.with_defaults(node.type, node.config)
            instance = f"{name}-{node.id}"
        else:
            config = datakit_config(segments, opts.engine_url, opts.datakit_debug, opts.engine_timeout_ms)
            instance = f"{name}-datakit"
        plugin: dict[str, Any] = {"name": s.plugin, "instance_name": instance, "config": config, "tags": list(tags)}
        if order := ordering_for(s.plugin):
            plugin["ordering"] = order
        plugins.append(plugin)

    llm = graph.node(a.llm)
    plugins.append({
        "name": "ai-proxy-advanced",
        "instance_name": f"{name}-llm",
        "config": _llm_plugin(catalog.with_defaults("llm", llm.config)),
        "tags": list(tags),
    })

    if response_stages:
        a.warn("response-phase guardrails buffer the LLM response, so streaming (stream: true) is not guarded")
    if len(response_stages) > 1:
        a.warn("Kong dynamic ordering only applies to the access phase; response-phase plugins run in "
               "Kong's priority order, not canvas order")
    if any(s.phase == REQUEST for s in stages if s.kind == "segment") and any(s.kind == "segment" for s in response_stages):
        a.warn("one DataKit plugin handles both request and response segments; verify on the target data plane")

    service = {"name": name, "url": opts.service_url, "tags": list(tags)}
    route = {
        "name": name,
        "paths": [f"{opts.route_prefix.rstrip('/')}/{graph.slug}"],
        "methods": ["POST"],
        "strip_path": True,
        "tags": list(tags),
    }
    return CompiledPipeline(service=service, route=route, plugins=plugins, stages=stages, issues=a.issues)


def to_deck(compiled: CompiledPipeline) -> dict[str, Any]:
    """decK state file with the plugins attached to the pipeline's route."""
    route = dict(compiled.route, plugins=compiled.plugins)
    service = dict(compiled.service, routes=[route])
    return {"_format_version": "3.0", "_info": {"select_tags": compiled.service["tags"][1:]}, "services": [service]}
