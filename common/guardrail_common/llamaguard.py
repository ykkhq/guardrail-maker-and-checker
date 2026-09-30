"""Llama Guard 3 prompt: task instruction + safety policy (unsafe content categories).

The engine sends this prompt to Ollama in raw mode. With the default config it
equals the prompt of Ollama's llama-guard3 template (13 categories, names
only); one deliberate difference is that the final instruction names the
checked role (User or Agent), where Ollama's template always says "User".

Category codes are fixed: the standard categories keep S1-S14 and custom
categories get S15, S16, … in policy order. The trained categories S1-S13 are
always listed in the prompt, even when disabled, and "disabled" is applied to
the answer instead. Measured on llama-guard3:1b: it answers with its trained
codes, and dropping a category from the prompt only makes it pick another one
(a PII prompt with S7 removed came back as S1 Violent Crimes), so filtering is
the only reliable way to switch a category off. Custom categories and a changed
task instruction only take effect on models that follow the prompt (e.g.
llama-guard3:8b).
"""

from __future__ import annotations

from typing import Any

DEFAULT_TASK = ("Check if there is unsafe content in '{role}' messages in conversations "
                "according our safety policy with the below categories.")

# Llama Guard 3 hazard taxonomy (MLCommons). S14 exists only in the 8B model.
DEFAULT_POLICY: list[dict[str, Any]] = [
    {"id": "violent_crimes", "name": "Violent Crimes", "enabled": True},
    {"id": "non_violent_crimes", "name": "Non-Violent Crimes", "enabled": True},
    {"id": "sex_crimes", "name": "Sex Crimes", "enabled": True},
    {"id": "child_exploitation", "name": "Child Exploitation", "enabled": True},
    {"id": "defamation", "name": "Defamation", "enabled": True},
    {"id": "specialized_advice", "name": "Specialized Advice", "enabled": True},
    {"id": "privacy", "name": "Privacy", "enabled": True},
    {"id": "intellectual_property", "name": "Intellectual Property", "enabled": True},
    {"id": "indiscriminate_weapons", "name": "Indiscriminate Weapons", "enabled": True},
    {"id": "hate", "name": "Hate", "enabled": True},
    {"id": "self_harm", "name": "Self-Harm", "enabled": True},
    {"id": "sexual_content", "name": "Sexual Content", "enabled": True},
    {"id": "elections", "name": "Elections", "enabled": True},
    {"id": "code_interpreter_abuse", "name": "Code Interpreter Abuse", "enabled": False,
     "description": "Only the 8B model was trained on this category."},
]
DEFAULT_IDS = {c["id"] for c in DEFAULT_POLICY}
STANDARD_CODES = {c["id"]: f"S{i}" for i, c in enumerate(DEFAULT_POLICY, 1)}
# Always shown to the model, even when disabled: the S1-S13 taxonomy every
# Llama Guard 3 model was trained on (and Ollama's template lists).
TRAINED_IDS = {c["id"] for c in DEFAULT_POLICY[:13]}

ROLE = {"request": "User", "response": "Agent"}


def _policy(policy: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return policy if policy is not None else DEFAULT_POLICY


def enabled_categories(policy: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [c for c in _policy(policy) if c.get("enabled", True)]


def coded(policy: list[dict[str, Any]] | None) -> list[tuple[str, dict[str, Any]]]:
    """(code, category) for every category: fixed S1-S14 for standard ids, S15+ for custom ones."""
    out, n = [], len(DEFAULT_POLICY)
    for c in _policy(policy):
        code = STANDARD_CODES.get(c["id"])
        if code is None:
            n += 1
            code = f"S{n}"
        out.append((code, c))
    return out


def codes(policy: list[dict[str, Any]] | None) -> dict[str, str]:
    """S-code -> category id for the enabled categories."""
    return {code: c["id"] for code, c in coded(policy) if c.get("enabled", True)}


def build_prompt(text: str, config: dict[str, Any], phase: str = "request") -> str:
    role = ROLE.get(phase, "User")
    task = (config.get("task_instruction") or DEFAULT_TASK).replace("{role}", role).strip()
    lines = []
    # The trained taxonomy (S1-S13) always, plus any other enabled category (S14, custom).
    listed = [(code, c) for code, c in coded(config.get("policy"))
              if c["id"] in TRAINED_IDS or c.get("enabled", True)]
    for code, c in sorted(listed, key=lambda x: int(x[0][1:])):
        name = c["name"].strip().rstrip(".")
        lines.append(f"{code}: {name}.")
        if desc := (c.get("description") or "").strip():
            lines.append(desc)
    categories = "\n".join(lines)
    return (
        "<|start_header_id|>user<|end_header_id|>\n\n"
        f"Task: {task}\n\n"
        "<BEGIN UNSAFE CONTENT CATEGORIES>\n"
        f"{categories}\n"
        "<END UNSAFE CONTENT CATEGORIES>\n\n"
        "<BEGIN CONVERSATION>\n\n"
        f"{role}: {text}\n\n"
        "<END CONVERSATION>\n\n"
        f"Provide your safety assessment for ONLY THE LAST {role} message in the above conversation:\n"
        " - First line must read 'safe' or 'unsafe'.\n"
        " - If unsafe, a second line must include a comma-separated list of violated categories."
        "<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
    )


def policy_issues(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a llamaguard_safety config beyond its JSON Schema."""
    errors: list[str] = []
    warnings: list[str] = []
    policy = config.get("policy")
    if policy is not None:
        ids = [c.get("id") for c in policy]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            errors.append(f"policy: duplicate category id(s) {', '.join(dupes)}")
        if not enabled_categories(policy):
            errors.append("policy: enable at least one category")
        for c in enabled_categories(policy):
            if c.get("id") not in DEFAULT_IDS and not (c.get("description") or "").strip():
                warnings.append(f"policy: custom category '{c.get('name')}' has no description; "
                                "Llama Guard needs one to know what it covers")
    task = config.get("task_instruction")
    if task is not None and "{role}" not in task and "User" not in task and "Agent" not in task:
        warnings.append("task_instruction: mention whose messages to check, e.g. '{role}'")
    return errors, warnings
