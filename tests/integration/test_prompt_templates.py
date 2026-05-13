"""Smoke tests for prompt template and schema loading.

Iterates all task types and verifies templates load as valid Jinja2
and schemas parse as valid JSON Schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import jinja2
import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


def _load_task_types() -> dict:
    path = CONFIG_DIR / "task_types.yaml"
    if not path.exists():
        pytest.skip("task_types.yaml not found")
    with open(path) as f:
        return yaml.safe_load(f) or {}


class TestPromptTemplateLoading:
    @pytest.fixture
    def task_types(self) -> dict:
        return _load_task_types()

    def test_all_prompt_templates_are_valid_jinja2(self, task_types: dict) -> None:
        env = jinja2.Environment(undefined=jinja2.Undefined)
        errors: list[str] = []

        for task_type, config in task_types.get("task_types", {}).items():
            template_path = config.get("prompt_template")
            if not template_path:
                continue

            full_path = PROJECT_ROOT / template_path
            if not full_path.exists():
                errors.append(f"{task_type}: template not found at {template_path}")
                continue

            try:
                source = full_path.read_text()
                env.parse(source)
            except jinja2.TemplateSyntaxError as exc:
                errors.append(f"{task_type}: Jinja2 syntax error — {exc}")

        assert not errors, "Template loading errors:\n" + "\n".join(errors)


class TestSchemaLoading:
    @pytest.fixture
    def task_types(self) -> dict:
        return _load_task_types()

    def test_all_output_schemas_are_valid_json(self, task_types: dict) -> None:
        errors: list[str] = []

        for task_type, config in task_types.get("task_types", {}).items():
            schema_path = config.get("output_schema")
            if not schema_path:
                continue

            full_path = PROJECT_ROOT / schema_path
            if not full_path.exists():
                errors.append(f"{task_type}: schema not found at {schema_path}")
                continue

            try:
                with open(full_path) as f:
                    schema = json.load(f)
                assert isinstance(schema, dict), "Schema must be a JSON object"
            except (json.JSONDecodeError, AssertionError) as exc:
                errors.append(f"{task_type}: invalid JSON schema — {exc}")

        assert not errors, "Schema loading errors:\n" + "\n".join(errors)
