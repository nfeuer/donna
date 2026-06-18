"""R3 (§7.2 resolution): every production skill tool ships a param schema.

Guards the invariant that no tool registered by ``register_default_tools`` can be
dispatched unschema'd in production. See
``docs/superpowers/specs/2026-06-17-subagent-72-resolution-design.md`` §5 R3.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import jsonschema
import pytest

from donna.skills.tool_param_schemas import (
    TOOL_PARAM_SCHEMA_FILES,
    ToolParamSchemaError,
    load_tool_param_schemas,
)
from donna.skills.tool_registry import ToolRegistry
from donna.skills.tools import register_default_tools


def test_all_declared_schemas_load_and_are_valid_draft7():
    schemas = load_tool_param_schemas()
    assert set(schemas) == set(TOOL_PARAM_SCHEMA_FILES)
    for name, schema in schemas.items():
        # Each schema must itself be a valid draft-07 schema.
        jsonschema.Draft7Validator.check_schema(schema)
        assert schema.get("type") == "object", name


def test_every_default_tool_is_registered_with_a_schema():
    """Register with every client wired; assert each tool has a schema."""
    reg = ToolRegistry()
    register_default_tools(
        reg,
        gmail_client=MagicMock(),
        calendar_client=MagicMock(),
        task_db=MagicMock(),
        cost_tracker=MagicMock(),
        vault_client=MagicMock(),
        vault_writer=MagicMock(),
        memory_store=MagicMock(),
    )
    registered = set(reg.list_tool_names())
    # Sanity: all the client-gated tools registered with the mocks present.
    assert registered == set(TOOL_PARAM_SCHEMA_FILES)
    for name in registered:
        assert reg.has_schema(name), f"{name} registered without a param schema"


def test_default_tools_without_clients_still_all_schema_d():
    """Only the always-on tools register, but each still carries a schema."""
    reg = ToolRegistry()
    register_default_tools(reg)
    for name in reg.list_tool_names():
        assert reg.has_schema(name), f"{name} registered without a param schema"


def test_missing_schema_file_fails_loud(tmp_path):
    with pytest.raises(ToolParamSchemaError, match="missing parameter schema"):
        load_tool_param_schemas(schemas_dir=tmp_path)
