"""Condition rules used by `condition` nodes.

A rule reads a field from an earlier node's DetectorResult, for example:

    {"node": "sentiment", "field": "flags.kasuhara", "op": "eq", "value": true}

Rules are structured data (not expressions), so they are safe to evaluate and
easy to edit with a form in the UI.
"""

from __future__ import annotations

from typing import Any

OPS = ("eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "truthy")

_MISSING = object()


def lookup(results: dict[str, dict[str, Any]], node: str, field: str) -> Any:
    cur: Any = results.get(node, _MISSING)
    for part in field.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def eval_rule(rule: dict[str, Any], results: dict[str, dict[str, Any]]) -> bool:
    actual = lookup(results, rule["node"], rule["field"])
    if actual is _MISSING:
        # A node that did not run (other branch) never satisfies a rule.
        return False
    op = rule.get("op", "eq")
    expected = rule.get("value")
    try:
        match op:
            case "eq":
                return actual == expected
            case "ne":
                return actual != expected
            case "lt":
                return actual < expected
            case "lte":
                return actual <= expected
            case "gt":
                return actual > expected
            case "gte":
                return actual >= expected
            case "in":
                return actual in expected
            case "contains":
                return expected in actual
            case "truthy":
                return bool(actual)
    except TypeError:
        return False
    raise ValueError(f"unknown condition op: {op}")


def eval_condition(config: dict[str, Any], results: dict[str, dict[str, Any]]) -> bool:
    rules = config.get("rules", [])
    if not rules:
        return False
    hits = (eval_rule(r, results) for r in rules)
    return any(hits) if config.get("match", "all") == "any" else all(hits)
