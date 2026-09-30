"""Node catalog: every node type the Web UI palette can place on the canvas.

Kinds:
  endpoint  fixed start/LLM/end nodes
  native    compiled to a Kong AI plugin; `config` is passed as the plugin config
  custom    executed by guardrail-engine (called from a Kong DataKit plugin)
  control   executed by guardrail-engine: branching and blocking

`config_schema` is JSON Schema. The UI renders it as a form, and studio-api
validates node config against it. Properties marked `"x-secret": true` should
hold a Kong vault reference such as `{vault://env/OPENAI_API_KEY}`.

Native plugin schemas list the commonly used plugin fields and allow extra
properties, so any other plugin field can be set too. Check them against the
data plane version you deploy to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REQUEST = "request"
RESPONSE = "response"


@dataclass(frozen=True)
class NodeType:
    type: str
    label: str
    category: str
    kind: str
    description: str
    config_schema: dict[str, Any]
    phases: tuple[str, ...] = (REQUEST,)
    plugin: str | None = None  # Kong plugin name for native nodes
    outputs: tuple[str, ...] = ("out",)  # named source handles
    transforms_text: bool = False  # may rewrite the prompt (masking)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "category": self.category,
            "kind": self.kind,
            "description": self.description,
            "phases": list(self.phases),
            "plugin": self.plugin,
            "outputs": list(self.outputs),
            "transforms_text": self.transforms_text,
            "config_schema": self.config_schema,
        }


def _obj(props: dict[str, Any], required: list[str] | None = None, extra: bool = False) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": extra}
    if required:
        schema["required"] = required
    return schema


def _secret(title: str) -> dict[str, Any]:
    return {"type": "string", "title": title, "x-secret": True}


def _guarding_mode(default: str = "INPUT") -> dict[str, Any]:
    return {"type": "string", "enum": ["INPUT", "OUTPUT", "BOTH"], "default": default}


# Config shared by all custom detectors.
_DETECTOR_COMMON: dict[str, Any] = {
    "on_detect": {
        "type": "string",
        "enum": ["block", "flag"],
        "default": "block",
        "title": "When detected",
        "description": "block: stop the request. flag: record the result and continue (use a condition node to decide).",
    },
    "fail_mode": {
        "type": "string",
        "enum": ["closed", "open"],
        "default": "closed",
        "title": "When the detector errors",
        "description": "closed: block the request. open: let it through with a warning.",
    },
    "block_status": {"type": "integer", "minimum": 400, "maximum": 599, "default": 403},
    "block_message": {"type": "string", "default": "Request blocked by guardrail policy."},
}

_MASK_ACTION = {
    "type": "string",
    "enum": ["mask", "block", "flag"],
    "default": "mask",
    "title": "When PII is found",
}

_PII_COMMON = {k: v for k, v in _DETECTOR_COMMON.items() if k != "on_detect"} | {"on_detect": _MASK_ACTION}


_TYPES: list[NodeType] = [
    # --- endpoints -------------------------------------------------------
    NodeType(
        type="prompt_in",
        label="Prompt In",
        category="Endpoints",
        kind="endpoint",
        description="The client request (OpenAI-style chat body) enters the pipeline here.",
        config_schema=_obj({}),
    ),
    NodeType(
        type="llm",
        label="LLM",
        category="LLM",
        kind="endpoint",
        description="Proxies the request to an LLM provider with ai-proxy-advanced.",
        phases=(REQUEST, RESPONSE),
        plugin="ai-proxy-advanced",
        config_schema=_obj(
            {
                "provider": {"type": "string", "enum": ["openai", "anthropic", "azure", "bedrock", "gemini", "mistral", "ollama"], "default": "openai"},
                "model": {"type": "string", "default": "gpt-4o-mini"},
                "route_type": {"type": "string", "enum": ["llm/v1/chat", "llm/v1/completions"], "default": "llm/v1/chat"},
                "auth_header_name": {"type": "string", "default": "Authorization"},
                "auth_header_value": _secret("Auth header value, e.g. {vault://env/OPENAI_AUTH_HEADER} holding 'Bearer sk-…'"),
                "upstream_url": {"type": "string", "title": "Upstream URL (Ollama or self-hosted)"},
                "max_tokens": {"type": "integer", "minimum": 1},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                "options": {"type": "object", "title": "Extra model options (passed to ai-proxy-advanced model.options)"},
            },
            required=["provider", "model"],
        ),
    ),
    NodeType(
        type="response_out",
        label="Response Out",
        category="Endpoints",
        kind="endpoint",
        description="The LLM response is returned to the client here.",
        phases=(RESPONSE,),
        config_schema=_obj({}),
    ),
    # --- custom detectors (guardrail-engine) -------------------------------
    NodeType(
        type="llamaguard_safety",
        label="Llama Guard Safety",
        category="Safety",
        kind="custom",
        description="Jailbreak and unsafe-content check with Llama Guard 3 on Ollama.",
        phases=(REQUEST, RESPONSE),
        config_schema=_obj(
            {
                "model": {"type": "string", "default": "llama-guard3:1b"},
                "ollama_host": {"type": "string", "description": "Leave empty to use the engine's OLLAMA_HOST."},
            }
            | _DETECTOR_COMMON
        ),
    ),
    NodeType(
        type="presidio_pii",
        label="PII Masking (Presidio)",
        category="PII",
        kind="custom",
        description="Detects and masks PII with Presidio + spaCy, including Japanese My Number and phone numbers.",
        phases=(REQUEST, RESPONSE),
        transforms_text=True,
        config_schema=_obj(
            {
                "language": {"type": "string", "enum": ["ja", "en"], "default": "ja"},
                "entities": {"type": "array", "items": {"type": "string"}, "default": [], "description": "Empty means all entities."},
                "score_threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
            }
            | _PII_COMMON
        ),
    ),
    NodeType(
        type="regex_pii",
        label="PII Masking (Regex)",
        category="PII",
        kind="custom",
        description="Lightweight regex masking: email, JP phone, My Number, credit card. No ML models needed.",
        phases=(REQUEST, RESPONSE),
        transforms_text=True,
        config_schema=_obj(
            {
                "entities": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["EMAIL", "JP_PHONE", "JP_MYNUMBER", "CREDIT_CARD"]},
                    "default": ["EMAIL", "JP_PHONE", "JP_MYNUMBER", "CREDIT_CARD"],
                },
            }
            | _PII_COMMON
        ),
    ),
    NodeType(
        type="sentiment_kasuhara",
        label="Sentiment / Kasuhara",
        category="Sentiment",
        kind="custom",
        description="Japanese BERT sentiment plus customer-harassment (カスハラ) keyword detection.",
        config_schema=_obj(
            {
                "model": {"type": "string", "default": "koheiduck/bert-japanese-finetuned-sentiment", "description": "Must be a sentiment-classification model (a base BERT gives random labels)."},
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["責任者を出せ", "金返せ", "死ね", "バカ", "訴えてやる", "ボケ"],
                },
                "negative_threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.9,
                                       "description": "Negative score at or above this also counts as detected."},
            }
            | _DETECTOR_COMMON
            | {"on_detect": _DETECTOR_COMMON["on_detect"] | {"default": "flag"}}
        ),
    ),
    NodeType(
        type="laya_classify",
        label="Laya Classifier",
        category="Classification",
        kind="custom",
        description="Typed triage (pii, intent, urgency, security risk…) with the Laya router. Branch on its flags.",
        config_schema=_obj(
            {
                "pii_threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.75},
                "security_risk_threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.75},
            }
            | _DETECTOR_COMMON
            | {"on_detect": _DETECTOR_COMMON["on_detect"] | {"default": "flag"}}
        ),
    ),
    NodeType(
        type="keyword_blocklist",
        label="Keyword Blocklist",
        category="Safety",
        kind="custom",
        description="Blocks or flags prompts containing any listed keyword.",
        phases=(REQUEST, RESPONSE),
        config_schema=_obj(
            {
                "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "case_sensitive": {"type": "boolean", "default": False},
            }
            | _DETECTOR_COMMON,
            required=["keywords"],
        ),
    ),
    # --- control ---------------------------------------------------------------
    NodeType(
        type="condition",
        label="Condition",
        category="Control",
        kind="control",
        description="Routes to the 'true' or 'false' output based on earlier detector results.",
        phases=(REQUEST, RESPONSE),
        outputs=("true", "false"),
        config_schema=_obj(
            {
                "match": {"type": "string", "enum": ["all", "any"], "default": "all"},
                "rules": {
                    "type": "array",
                    "minItems": 1,
                    "items": _obj(
                        {
                            "node": {"type": "string", "title": "Node id"},
                            "field": {"type": "string", "title": "Result field", "examples": ["detected", "score", "label", "flags.kasuhara"]},
                            "op": {"type": "string", "enum": ["eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "truthy"], "default": "eq"},
                            "value": {},
                        },
                        required=["node", "field"],
                    ),
                },
            },
            required=["rules"],
        ),
    ),
    NodeType(
        type="block",
        label="Block",
        category="Control",
        kind="control",
        description="Stops the request and returns an error to the client.",
        phases=(REQUEST, RESPONSE),
        outputs=(),
        config_schema=_obj(
            {
                "status": {"type": "integer", "minimum": 400, "maximum": 599, "default": 403},
                "message": {"type": "string", "default": "Request blocked by guardrail policy."},
            }
        ),
    ),
    # --- native Kong AI plugins ---------------------------------------------------
    NodeType(
        type="ai_prompt_guard",
        label="Prompt Guard (regex)",
        category="Safety",
        kind="native",
        plugin="ai-prompt-guard",
        description="Kong ai-prompt-guard: allow/deny prompts by regex.",
        config_schema=_obj(
            {
                "allow_patterns": {"type": "array", "items": {"type": "string"}},
                "deny_patterns": {"type": "array", "items": {"type": "string"}},
                "allow_all_conversation_history": {"type": "boolean", "default": False},
                "match_all_roles": {"type": "boolean", "default": False},
            },
            extra=True,
        ),
    ),
    NodeType(
        type="ai_semantic_prompt_guard",
        label="Semantic Prompt Guard",
        category="Safety",
        kind="native",
        plugin="ai-semantic-prompt-guard",
        description="Kong ai-semantic-prompt-guard: allow/deny by semantic similarity (needs embeddings + vector DB).",
        config_schema=_obj(
            {
                "rules": _obj(
                    {
                        "allow_prompts": {"type": "array", "items": {"type": "string"}},
                        "deny_prompts": {"type": "array", "items": {"type": "string"}},
                    },
                    extra=True,
                ),
                "embeddings": {"type": "object"},
                "vectordb": {"type": "object"},
                "search": {"type": "object"},
            },
            extra=True,
        ),
    ),
    NodeType(
        type="ai_sanitizer",
        label="AI Sanitizer (PII)",
        category="PII",
        kind="native",
        plugin="ai-sanitizer",
        phases=(REQUEST, RESPONSE),
        transforms_text=True,
        description="Kong ai-sanitizer: PII anonymization with the ai-pii-service container.",
        config_schema=_obj(
            {
                "host": {"type": "string", "default": "ai-pii-service"},
                "port": {"type": "integer", "default": 8080},
                "anonymize": {"type": "array", "items": {"type": "string"}, "default": ["general", "phone", "email", "creditcard"]},
                "sanitization_mode": _guarding_mode(),
                "redact_type": {"type": "string", "enum": ["placeholder", "synthetic"], "default": "placeholder"},
                "recover_redacted": {"type": "boolean", "default": False},
                "block_if_detected": {"type": "boolean", "default": False},
                "stop_on_error": {"type": "boolean", "default": True},
            },
            extra=True,
        ),
    ),
    NodeType(
        type="ai_prompt_decorator",
        label="Prompt Decorator",
        category="Prompt shaping",
        kind="native",
        plugin="ai-prompt-decorator",
        description="Kong ai-prompt-decorator: prepend/append system or user messages.",
        config_schema=_obj(
            {
                "prompts": _obj(
                    {
                        "prepend": {"type": "array", "items": _obj({"role": {"type": "string", "enum": ["system", "user", "assistant"]}, "content": {"type": "string"}})},
                        "append": {"type": "array", "items": _obj({"role": {"type": "string", "enum": ["system", "user", "assistant"]}, "content": {"type": "string"}})},
                    }
                )
            },
            extra=True,
        ),
    ),
    NodeType(
        type="ai_azure_content_safety",
        label="Azure Content Safety",
        category="Content Safety",
        kind="native",
        plugin="ai-azure-content-safety",
        phases=(REQUEST, RESPONSE),
        description="Kong ai-azure-content-safety: Azure AI Content Safety moderation.",
        config_schema=_obj(
            {
                "content_safety_url": {"type": "string"},
                "content_safety_key": _secret("Content Safety key"),
                "categories": {
                    "type": "array",
                    "items": _obj({"name": {"type": "string", "enum": ["Hate", "SelfHarm", "Sexual", "Violence"]}, "rejection_level": {"type": "integer", "minimum": 0, "maximum": 7}}),
                },
                "reveal_failure_reason": {"type": "boolean", "default": True},
            },
            required=["content_safety_url"],
            extra=True,
        ),
    ),
    NodeType(
        type="ai_aws_guardrails",
        label="AWS Bedrock Guardrails",
        category="Content Safety",
        kind="native",
        plugin="ai-aws-guardrails",
        phases=(REQUEST, RESPONSE),
        description="Kong ai-aws-guardrails: Amazon Bedrock Guardrails.",
        config_schema=_obj(
            {
                "guardrails_id": {"type": "string"},
                "guardrails_version": {"type": "string", "default": "DRAFT"},
                "aws_region": {"type": "string"},
                "aws_access_key_id": _secret("AWS access key id"),
                "aws_secret_access_key": _secret("AWS secret access key"),
                "guarding_mode": _guarding_mode(),
            },
            required=["guardrails_id", "aws_region"],
            extra=True,
        ),
    ),
    NodeType(
        type="ai_lakera_guard",
        label="Lakera Guard",
        category="Content Safety",
        kind="native",
        plugin="ai-lakera-guard",
        phases=(REQUEST, RESPONSE),
        description="Kong ai-lakera-guard: Lakera prompt-injection and content screening.",
        config_schema=_obj({"api_key": _secret("Lakera API key"), "guarding_mode": _guarding_mode()}, extra=True),
    ),
    NodeType(
        type="ai_gcp_model_armor",
        label="GCP Model Armor",
        category="Content Safety",
        kind="native",
        plugin="ai-gcp-model-armor",
        phases=(REQUEST, RESPONSE),
        description="Kong ai-gcp-model-armor: Google Cloud Model Armor screening.",
        config_schema=_obj(
            {
                "project_id": {"type": "string"},
                "location_id": {"type": "string"},
                "template_id": {"type": "string"},
                "guarding_mode": _guarding_mode(),
            },
            required=["project_id", "location_id", "template_id"],
            extra=True,
        ),
    ),
]

CATALOG: dict[str, NodeType] = {t.type: t for t in _TYPES}


def get(node_type: str) -> NodeType:
    try:
        return CATALOG[node_type]
    except KeyError:
        raise KeyError(f"unknown node type: {node_type}") from None


def with_defaults(node_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """Fill top-level defaults from the node's schema into `config`."""
    props = get(node_type).config_schema.get("properties", {})
    merged = {k: p["default"] for k, p in props.items() if "default" in p}
    merged.update(config)
    return merged
