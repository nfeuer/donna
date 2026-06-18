"""Declarative per-tool parameter schemas for the skill ToolRegistry.

R3 (§7.2 resolution —
``docs/superpowers/specs/2026-06-17-subagent-72-resolution-design.md``) makes the
tool-validation seam load-bearing (CLAUDE.md principle #6). Each built-in skill
tool gets a JSON schema describing its **caller-supplied** arguments; the schemas
live as version-controlled data files under ``schemas/tools/<tool>.json`` (config
over code — CLAUDE.md principle #1) and are loaded here at registration time.

Injected dependencies bound via ``functools.partial`` at registration (e.g.
``client``/``store``/``task_db``) are *not* caller-supplied and are deliberately
absent from the schemas.

The :data:`TOOL_PARAM_SCHEMA_FILES` map is the single source of truth for which
tools must carry a schema; :func:`load_tool_param_schemas` reads them and is the
function ``register_default_tools`` uses to make every production tool schema'd.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# Repo root: this file is src/donna/skills/tool_param_schemas.py, so the
# version-controlled ``schemas/`` directory sits four parents up.
_DEFAULT_SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "schemas" / "tools"

# Every built-in tool registered by ``donna.skills.tools.register_default_tools``
# maps to its schema file under ``schemas/tools/``. Keep this in lock-step with
# the registrations there — the unit test
# ``tests/unit/test_tool_param_schemas.py`` asserts the two stay aligned so no
# production tool can be registered unschema'd.
TOOL_PARAM_SCHEMA_FILES: dict[str, str] = {
    "web_fetch": "web_fetch.json",
    "rss_fetch": "rss_fetch.json",
    "html_extract": "html_extract.json",
    "browser_extract_text": "browser_extract_text.json",
    "browser_screenshot": "browser_screenshot.json",
    "gmail_search": "gmail_search.json",
    "gmail_get_message": "gmail_get_message.json",
    "email_read": "email_read.json",
    "calendar_read": "calendar_read.json",
    "task_db_read": "task_db_read.json",
    "cost_summary": "cost_summary.json",
    "vault_read": "vault_read.json",
    "vault_list": "vault_list.json",
    "vault_link": "vault_link.json",
    "vault_write": "vault_write.json",
    "vault_undo_last": "vault_undo_last.json",
    "memory_search": "memory_search.json",
}


class ToolParamSchemaError(Exception):
    """Raised when a declared per-tool parameter schema cannot be loaded."""


def load_tool_param_schemas(
    schemas_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load every declared per-tool parameter schema from disk.

    Args:
        schemas_dir: Directory holding the ``<tool>.json`` schema files.
            Defaults to the repo-root ``schemas/tools/`` directory.

    Returns:
        A map of tool name -> parsed JSON schema, covering every entry in
        :data:`TOOL_PARAM_SCHEMA_FILES`.

    Raises:
        ToolParamSchemaError: If a declared schema file is missing or not valid
            JSON. Fail-loud by design — a tool registered without a loadable
            schema would otherwise silently fall through to the no-schema path.
    """
    base = schemas_dir or _DEFAULT_SCHEMAS_DIR
    schemas: dict[str, dict[str, Any]] = {}
    for tool_name, filename in TOOL_PARAM_SCHEMA_FILES.items():
        path = base / filename
        try:
            with path.open() as fh:
                schemas[tool_name] = json.load(fh)
        except FileNotFoundError as exc:
            raise ToolParamSchemaError(
                f"missing parameter schema for tool {tool_name!r}: {path}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ToolParamSchemaError(
                f"invalid JSON in parameter schema for tool {tool_name!r}: {path}: {exc}"
            ) from exc
    logger.info("tool_param_schemas_loaded", count=len(schemas))
    return schemas
