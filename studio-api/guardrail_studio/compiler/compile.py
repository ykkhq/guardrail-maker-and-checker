"""Compiles a validated pipeline graph into Konnect AI Gateway entities.

A pipeline becomes one model provider, one model and its policies. The model's
route serves `/pipelines/{slug}/chat/completions` and proxies to the LLM node.
Clients select it by sending the model name (`gs-{slug}`) as `model` in the body.
Stages: every native node becomes its own policy (its Kong plugin name is the
policy type). The custom/control nodes of a phase become one engine segment,
run by a single DataKit policy. AI Gateway policies have no `ordering` field, so
they run in Kong plugin priority order (DataKit before the native AI plugins),
whatever the canvas order.
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
# AI Gateway serves OpenAI-format models at `<route prefix>/chat/completions`.
CHAT_SUFFIX = "/chat/completions"


class CompileError(Exception):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("; ".join(f"{i.node or '-'}: {i.message}" for i in issues if i.level == "error"))


@dataclass
class CompileOptions:
    engine_url: str = "http://guardrail-engine:8080"
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
    slug: str
    provider: dict[str, Any]
    model: dict[str, Any]
    policies: list[dict[str, Any]]  # in canvas order, as listed on the model
    stages: list[Stage]
    issues: list[Issue] = field(default_factory=list)

    @property
    def model_name(self) -> str:
        """The `model` value a client sends to select this pipeline."""
        return self.model["name"]

    @property
    def endpoint_path(self) -> str:
        return self.model["config"]["route"]["paths"][0] + CHAT_SUFFIX

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
                    "a model runs one DataKit policy, so keep all custom/control nodes of a phase together",
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


def _llm_entities(cfg: dict[str, Any], name: str, labels: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The model provider and the model target for the LLM node."""
    provider = cfg["provider"]
    options: dict[str, Any] = {}
    for k in ("max_tokens", "temperature", "upstream_url"):
        if k in cfg:
            options[k] = cfg[k]
    if provider == "anthropic":
        options.setdefault("anthropic_version", "2023-06-01")
        options.setdefault("max_tokens", 1024)
    options.update(cfg.get("options", {}))

    # AI Gateway requires basic auth on every provider; Ollama gets no headers.
    headers = []
    if cfg.get("auth_header_value"):
        headers.append({"name": cfg["auth_header_name"], "value": cfg["auth_header_value"]})
    provider_entity = {"name": name, "display_name": name, "type": provider, "labels": dict(labels),
                       "config": {"auth": {"type": "basic", "headers": headers}}}
    target = {"name": cfg["model"], "provider": name, "config": {"type": provider, **options}}
    return provider_entity, target


def compile_pipeline(graph: PipelineGraph, opts: CompileOptions | None = None) -> CompiledPipeline:
    opts = opts or CompileOptions()
    a = validate(graph)
    if not a.ok:
        raise CompileError(a.issues)
    stages = plan_stages(graph, a)
    if not a.ok:
        raise CompileError(a.issues)

    labels = {"managed-by": TAG, "pipeline": graph.slug}
    name = f"gs-{graph.slug}"
    policies: list[dict[str, Any]] = []

    response_stages = [s for s in stages if s.phase == RESPONSE]
    segments = [s.segment for s in stages if s.segment]

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
        policies.append({"name": instance, "display_name": instance, "type": s.plugin,
                         "labels": dict(labels), "config": config})

    llm = graph.node(a.llm)
    provider, target = _llm_entities(catalog.with_defaults("llm", llm.config), f"{name}-llm", labels)
    model = {
        "name": name,
        "display_name": name,
        "type": "model",
        "labels": dict(labels),
        "formats": [{"type": "openai"}],
        "capabilities": ["generate"],
        "config": {"route": {"paths": [f"{opts.route_prefix.rstrip('/')}/{graph.slug}"]}},
        "targets": [target],
        "policies": [p["name"] for p in policies],
    }

    if response_stages:
        a.warn("response-phase guardrails buffer the LLM response, so streaming (stream: true) is not guarded")
    for phase in (REQUEST, RESPONSE):
        if len({s.plugin for s in stages if s.phase == phase}) > 1:
            a.warn(f"{phase}-phase policies run in Kong plugin priority order (DataKit before native AI plugins), "
                   "not canvas order: AI Gateway policies have no dynamic ordering")
    if any(s.phase == REQUEST for s in stages if s.kind == "segment") and any(s.kind == "segment" for s in response_stages):
        a.warn("one DataKit policy handles both request and response segments; verify on the target data plane")

    return CompiledPipeline(slug=graph.slug, provider=provider, model=model, policies=policies,
                            stages=stages, issues=a.issues)


def to_declarative(compiled: CompiledPipeline) -> dict[str, Any]:
    """The pipeline's AI Gateway entities, keyed like kongctl's declarative format."""
    return {
        "ai_gateway_model_providers": [compiled.provider],
        "ai_gateway_policies": compiled.policies,
        "ai_gateway_models": [compiled.model],
    }
