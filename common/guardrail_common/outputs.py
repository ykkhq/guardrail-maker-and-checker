"""Result fields each detector produces, for building and checking condition rules.

A field descriptor is what the condition editor offers for one node:

    {"field": "flags.intent", "label": "intent (choice)", "type": "enum",
     "ops": ["eq", "ne", "in"], "values": [{"value": "billing", "label": "billing: invoice, …"}]}

Types: boolean, number, integer, string, enum (fixed values), list (contains).
Laya fields are derived from the node's own questions.
"""

from __future__ import annotations

from typing import Any

from guardrail_common import catalog, llamaguard

OPS: dict[str, list[str]] = {
    "boolean": ["eq", "ne"],
    "number": ["gte", "gt", "lte", "lt", "eq", "ne"],
    "integer": ["gte", "gt", "lte", "lt", "eq", "ne"],
    "string": ["eq", "ne", "contains"],
    "enum": ["eq", "ne", "in"],
    "list": ["contains"],
}

def _f(field: str, label: str, type_: str, **extra: Any) -> dict[str, Any]:
    return {"field": field, "label": label, "type": type_, "ops": OPS[type_], **extra}


def _values(items) -> list[dict[str, Any]]:
    return [{"value": v, "label": l} for v, l in items]


DETECTED = _f("detected", "detected (any hit)", "boolean")

_STATIC: dict[str, list[dict[str, Any]]] = {
    "presidio_pii": [DETECTED, _f("score", "highest entity score", "number", min=0, max=1)],
    "regex_pii": [DETECTED],
    "sentiment_kasuhara": [
        DETECTED,
        _f("flags.kasuhara", "harassment keyword", "boolean"),
        _f("flags.negative", "negative sentiment", "boolean"),
        _f("label", "sentiment label", "string"),
        _f("score", "sentiment confidence", "number", min=0, max=1),
    ],
    "keyword_blocklist": [DETECTED, _f("details.matched", "matched keywords", "list")],
}


def laya_fields(config: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = catalog.with_defaults("laya_classify", config)
    fields = [_f("detected", f"detected ({', '.join(cfg.get('detect_on', [])) or 'nothing'})", "boolean")]
    for name, q in (cfg.get("questions") or {}).items():
        t = q.get("type")
        hint = q.get("instructions", "")
        if t == "noul":
            fields.append(_f(f"flags.{name}", f"{name} (yes/no)", "boolean", help=hint))
            fields.append(_f(f"details.scores.{name}", f"{name} probability", "number", min=0, max=1, help=hint))
        elif t == "choice" and isinstance(q.get("criteria"), dict):
            fields.append(_f(f"flags.{name}", f"{name} (choice)", "enum", help=hint,
                             values=_values((k, f"{k}: {v}" if v else k) for k, v in q["criteria"].items())))
        elif t == "score" and isinstance(q.get("criteria"), list):
            fields.append(_f(f"flags.{name}", f"{name} (level)", "integer", help=hint, min=0,
                             max=len(q["criteria"]) - 1,
                             values=_values((i, f"{i}: {c}") for i, c in enumerate(q["criteria"]))))
    return fields


def llamaguard_fields(config: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = catalog.with_defaults("llamaguard_safety", config)
    cats = [(code, c) for code, c in llamaguard.coded(cfg.get("policy")) if c.get("enabled", True)]
    return [
        _f("detected", "unsafe (any enabled category)", "boolean"),
        _f("label", "verdict", "enum", values=_values([("safe", "safe"), ("unsafe", "unsafe")])),
        _f("flags.categories", "violated categories", "list",
           values=_values((c["id"], f"{code}: {c['name']}") for code, c in cats)),
    ]


def result_fields(node_type: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Fields a condition rule may read from a node of this type ([] for non-detectors)."""
    if node_type == "laya_classify":
        return laya_fields(config)
    if node_type == "llamaguard_safety":
        return llamaguard_fields(config)
    return _STATIC.get(node_type, [])


def check_rule(rule: dict[str, Any], fields: list[dict[str, Any]]) -> str | None:
    """Explain why a rule does not fit the source node's fields, or None if it does."""
    by_field = {f["field"]: f for f in fields}
    f = by_field.get(rule.get("field", ""))
    if f is None:
        return (f"'{rule.get('field')}' is not a result of '{rule.get('node')}'; "
                f"use one of: {', '.join(by_field) or 'none'}")
    op = rule.get("op", "eq")
    if op not in f["ops"]:
        return f"'{f['field']}' is {f['type']}: use {'/'.join(f['ops'])}, not '{op}'"
    v = rule.get("value")
    allowed = [x["value"] for x in f.get("values", [])]
    match f["type"]:
        case "boolean":
            if not isinstance(v, bool):
                return f"'{f['field']}' is true/false; value must be true or false, not {v!r}"
        case "number" | "integer":
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return f"'{f['field']}' is a number; value must be a number, not {v!r}"
            if "min" in f and v < f["min"] or "max" in f and v > f["max"]:
                return f"'{f['field']}' ranges {f.get('min')}..{f.get('max')}; {v} is outside it"
        case "enum":
            vals = v if op == "in" else [v]
            if op == "in" and not isinstance(v, list):
                return f"'in' needs a list of values for '{f['field']}'"
            bad = [x for x in vals if x not in allowed]
            if bad:
                return f"'{f['field']}' has no value {', '.join(map(repr, bad))}; choose from {', '.join(map(str, allowed))}"
        case "list":
            if allowed and v not in allowed:
                return f"'{f['field']}' never contains {v!r}; choose from {', '.join(map(str, allowed))}"
    return None


def default_rule(node_id: str, node_type: str, config: dict[str, Any]) -> dict[str, Any] | None:
    """A sensible first rule for a condition placed after this node."""
    fields = [f for f in result_fields(node_type, config) if f["field"] != "detected"]
    if node_type == "laya_classify":
        # Prefer a choice question (routing), then yes/no, then a level.
        order = {"enum": 0, "boolean": 1, "integer": 2}
        fields = sorted((f for f in fields if f["type"] in order), key=lambda f: order[f["type"]])
    if not fields:
        return {"node": node_id, "field": "detected", "op": "eq", "value": True}
    f = fields[0]
    if f["type"] == "boolean":
        return {"node": node_id, "field": f["field"], "op": "eq", "value": True}
    if f["type"] == "enum":
        return {"node": node_id, "field": f["field"], "op": "eq", "value": f["values"][0]["value"]}
    if f["type"] == "integer":
        return {"node": node_id, "field": f["field"], "op": "gte", "value": f.get("max", 1)}
    return {"node": node_id, "field": f["field"], "op": f["ops"][0], "value": f.get("max", 0)}
