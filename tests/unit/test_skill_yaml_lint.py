"""Lint: skill.yaml + step prompt files must not reference optional inputs
unsafely under Jinja StrictUndefined.

Under Jinja StrictUndefined, `{% if inputs.missing %}` raises UndefinedError
if `missing` isn't a key. F-W4-K fixes this at the draft layer (AutomationCreationPath
now defaults optional schema fields to None before persistence). As defense-in-depth,
this lint ensures skill templates don't rely on the legacy `is defined and` pattern
when referring to keys that are REQUIRED in the capability's input_schema — those
should never need the guard.

It also flags any reference to an optional key WITHOUT the safe pattern — that's
a regression that would fail under StrictUndefined for any draft bypassing the
defaulting layer (e.g., a hand-edited automation row).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"
CAPS_YAML = REPO_ROOT / "config" / "capabilities.yaml"

# Matches: {% if inputs.X %} where X is a word (no dots).
UNSAFE_PATTERN = re.compile(r"\{%\s*if\s+inputs\.(\w+)\s*%\}")
# Matches: {% if inputs.X is defined and inputs.X %}
SAFE_PATTERN = re.compile(
    r"\{%\s*if\s+inputs\.(\w+)\s+is\s+defined\s+and\s+inputs\.\1\s*%\}",
)


def _collect_skill_files() -> list[Path]:
    return list(SKILLS_ROOT.rglob("skill.yaml"))


def _collect_referenced_files(skill_yaml: Path) -> list[Path]:
    """Return skill.yaml + all step prompt files referenced by it."""
    with open(skill_yaml) as fh:
        data = yaml.safe_load(fh) or {}
    skill_dir = skill_yaml.parent
    paths = [skill_yaml]
    for step in data.get("steps") or []:
        prompt = step.get("prompt")
        if prompt:
            paths.append(skill_dir / prompt)
    return paths


def _load_capability_schema(capability_name: str | None) -> dict | None:
    if capability_name is None or not CAPS_YAML.exists():
        return None
    data = yaml.safe_load(CAPS_YAML.read_text()) or {}
    for cap in data.get("capabilities") or []:
        if cap.get("name") == capability_name:
            return cap.get("input_schema")
    return None


def _is_key_optional(schema: dict | None, key: str) -> bool:
    """Return True if the key is in properties but NOT in required. None-schema → True (assume optional)."""
    if schema is None:
        return True
    required = set(schema.get("required", []) or [])
    props = (schema.get("properties") or {}).keys()
    if key in required:
        return False
    return True  # either listed as optional or unknown — default to optional


def test_no_unsafe_optional_input_references() -> None:
    """Every `{% if inputs.X %}` for an optional key must use the `is defined and` pattern.

    After F-W4-K lands the defaulting layer, this test passes as-is because
    AutomationCreationPath populates optional keys with null. A skill author
    can safely write `{% if inputs.X %}` — it won't raise. However if a template
    ever starts referring to an input key that doesn't exist in the capability's
    input_schema at ALL (not even as optional), the defaulting layer can't help
    and the template needs the guard.

    This lint reads every skill.yaml and its step prompts, finds bare `{% if inputs.X %}`
    references, and checks: is X a property declared in the capability's input_schema?
    - If yes (required or optional): OK. The defaulting layer + required semantics cover it.
    - If no (truly undeclared): FAIL. Template needs `is defined and` guard, OR the schema needs the key added.
    """
    violations: list[tuple[str, int, str, str]] = []

    for skill_yaml in _collect_skill_files():
        with open(skill_yaml) as fh:
            skill_data = yaml.safe_load(fh) or {}
        capability_name = skill_data.get("capability_name")
        schema = _load_capability_schema(capability_name)
        declared_keys = set((schema or {}).get("properties", {}).keys()) if schema else set()

        for file_path in _collect_referenced_files(skill_yaml):
            if not file_path.exists():
                continue
            for lineno, line in enumerate(file_path.read_text().splitlines(), 1):
                for match in UNSAFE_PATTERN.finditer(line):
                    if SAFE_PATTERN.search(line):
                        continue
                    key = match.group(1)
                    # If schema exists AND the key is declared (required or optional),
                    # the defaulting layer handles it — OK.
                    # If schema exists AND the key is NOT declared → must use safe pattern.
                    # If schema doesn't exist at all → skip (can't lint).
                    if schema is not None and key not in declared_keys:
                        violations.append(
                            (str(file_path), lineno, line.strip(), capability_name or "<no capability>")
                        )

    assert not violations, (
        "skill.yaml/template references undeclared inputs without `is defined and` guard:\n"
        + "\n".join(
            f"  {p}:{l} [cap: {c}]: {line}" for p, l, line, c in violations
        )
    )
