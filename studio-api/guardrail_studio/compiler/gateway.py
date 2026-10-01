"""Map a CompiledPipeline to Kong Gateway 3.x Service / Route / plugins.

Used when GUARDRAIL_DEPLOY_TARGET=gateway (kong-enterprise/docker). Policies keep
the same config as Konnect AI Gateway (datakit, ai-prompt-guard, …); the LLM
becomes ai-proxy-advanced on the service.
"""

from __future__ import annotations

from typing import Any

from guardrail_studio.compiler.compile import CompiledPipeline

TAG = "guardrail-studio"
DEMO_TAG = "plugin-demo"
# Placeholder upstream: ai-proxy-advanced calls the real LLM via upstream_url.
DEFAULT_SERVICE_URL = "http://mockbin:8080"


def pipeline_tags(slug: str) -> list[str]:
    return [DEMO_TAG, TAG, f"pipeline:{slug}"]


def _proxy_target(compiled: CompiledPipeline) -> dict[str, Any]:
    """ai-proxy-advanced target from the AI Gateway provider + model target."""
    import os

    target = compiled.model["targets"][0]
    options = {k: v for k, v in target["config"].items() if k != "type"}
    model: dict[str, Any] = {
        "provider": compiled.provider["type"],
        "name": target["name"],
    }
    if options:
        model["options"] = options
    entry: dict[str, Any] = {"route_type": "llm/v1/chat", "model": model}
    headers = (compiled.provider.get("config") or {}).get("auth", {}).get("headers") or []
    if headers:
        h = dict(headers[0])
        # Local LM Studio: resolve placeholder / vault into a concrete Bearer header.
        lms = (os.environ.get("LM_STUDIO_AUTH_HEADER") or "").strip()
        val = h.get("value") or ""
        if lms and (val in ("Bearer lm-studio", "{vault://env/LM_STUDIO_AUTH_HEADER}") or "LM_STUDIO" in val):
            h["value"] = lms
        entry["auth"] = {"header_name": h["name"], "header_value": h["value"]}
    return entry


def to_gateway_entities(
    compiled: CompiledPipeline,
    *,
    service_url: str = DEFAULT_SERVICE_URL,
) -> dict[str, Any]:
    """decK-shaped entities for one pipeline (Admin API upsert uses the same shape)."""
    name = compiled.model_name
    tags = pipeline_tags(compiled.slug)
    plugins: list[dict[str, Any]] = [
        {
            "name": "ai-proxy-advanced",
            "instance_name": f"{name}-llm",
            "tags": tags,
            "config": {"targets": [_proxy_target(compiled)]},
        }
    ]
    for p in compiled.policies:
        plugins.append({
            "name": p["type"],
            "instance_name": p["name"],
            "tags": tags,
            "config": p["config"],
        })
    return {
        "services": [
            {
                "name": name,
                "url": service_url,
                "tags": tags,
                "routes": [
                    {
                        "name": f"{name}-chat",
                        "paths": [compiled.endpoint_path],
                        "strip_path": False,
                        "methods": ["POST"],
                        "protocols": ["http", "https"],
                        "tags": tags,
                    }
                ],
                "plugins": plugins,
            }
        ]
    }
