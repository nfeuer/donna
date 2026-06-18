import pytest

from donna.skills.tool_registry import (
    ParameterValidationError,
    ToolNotAllowedError,
    ToolNotFoundError,
    ToolRegistry,
)

# A representative draft-07 schema: requires `url`, rejects unknown keys.
_URL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "url": {"type": "string", "minLength": 1},
        "timeout_s": {"type": "number"},
    },
    "required": ["url"],
}


async def _mock_tool(**kwargs):
    return {"echo": kwargs}


async def test_register_and_dispatch():
    registry = ToolRegistry()
    registry.register("mock_tool", _mock_tool)
    result = await registry.dispatch(
        tool_name="mock_tool",
        args={"x": 1},
        allowed_tools=["mock_tool"],
    )
    assert result == {"echo": {"x": 1}}


async def test_dispatch_respects_allowlist():
    registry = ToolRegistry()
    registry.register("mock_tool", _mock_tool)
    with pytest.raises(ToolNotAllowedError, match="not in step allowlist"):
        await registry.dispatch(
            tool_name="mock_tool",
            args={},
            allowed_tools=["other_tool"],
        )


async def test_dispatch_raises_on_unknown_tool():
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        await registry.dispatch(tool_name="missing", args={}, allowed_tools=["missing"])


async def test_register_overwrites_existing():
    registry = ToolRegistry()
    registry.register("tool", _mock_tool)

    async def other(**kwargs):
        return {"v": 2}

    registry.register("tool", other)
    result = await registry.dispatch("tool", {}, allowed_tools=["tool"])
    assert result == {"v": 2}


async def test_list_tool_names():
    registry = ToolRegistry()
    registry.register("a", _mock_tool)
    registry.register("b", _mock_tool)
    assert sorted(registry.list_tool_names()) == ["a", "b"]


# --- R3: per-tool parameter-schema validation (§7.2 resolution) ---


async def test_valid_args_pass_schema_and_call_handler():
    registry = ToolRegistry()
    registry.register("fetch", _mock_tool, param_schema=_URL_SCHEMA)
    result = await registry.dispatch(
        "fetch", {"url": "https://x", "timeout_s": 5}, allowed_tools=["fetch"]
    )
    assert result == {"echo": {"url": "https://x", "timeout_s": 5}}


async def test_invalid_args_raise_and_handler_not_called():
    calls = {"n": 0}

    async def handler(**kwargs):
        calls["n"] += 1
        return {"ok": True}

    registry = ToolRegistry()
    registry.register("fetch", handler, param_schema=_URL_SCHEMA)

    # Missing required `url`.
    with pytest.raises(ParameterValidationError) as exc:
        await registry.dispatch("fetch", {"timeout_s": 5}, allowed_tools=["fetch"])
    assert exc.value.tool_name == "fetch"
    assert exc.value.errors  # carries the jsonschema error messages
    assert calls["n"] == 0  # fail-closed: handler never ran


async def test_additional_properties_rejected_fail_closed():
    calls = {"n": 0}

    async def handler(**kwargs):
        calls["n"] += 1
        return {"ok": True}

    registry = ToolRegistry()
    registry.register("fetch", handler, param_schema=_URL_SCHEMA)

    with pytest.raises(ParameterValidationError):
        await registry.dispatch(
            "fetch", {"url": "https://x", "bogus": 1}, allowed_tools=["fetch"]
        )
    assert calls["n"] == 0


async def test_allowlist_checked_before_schema():
    """A disallowed tool raises ToolNotAllowedError, never reaching validation."""
    registry = ToolRegistry()
    registry.register("fetch", _mock_tool, param_schema=_URL_SCHEMA)
    with pytest.raises(ToolNotAllowedError):
        # Invalid args too, but the allowlist gate fires first.
        await registry.dispatch("fetch", {}, allowed_tools=["other"])


async def test_caller_identity_threaded_to_log(monkeypatch):
    """task_type + agent_name reach the registry and the audit log.

    Spies on the module logger directly (rather than structlog capture) so the
    assertion is robust to the suite's ``cache_logger_on_first_use=True`` global
    config — once a module logger is cached, a test-local ``structlog.configure``
    cannot re-bind it, which makes log-capture helpers flaky under full-suite
    ordering.
    """
    import donna.skills.tool_registry as registry_mod

    calls: list[tuple[str, dict]] = []

    def _spy_info(event, **kw):
        calls.append((event, kw))

    monkeypatch.setattr(registry_mod.logger, "info", _spy_info)

    registry = ToolRegistry()
    registry.register("fetch", _mock_tool, param_schema=_URL_SCHEMA)
    await registry.dispatch(
        "fetch", {"url": "https://x"}, allowed_tools=["fetch"],
        task_type="skill_step::news::fetch", agent_name="news",
    )

    executed = [kw for event, kw in calls if event == "tool_executed"]
    assert executed, "expected a tool_executed audit log record"
    rec = executed[-1]
    assert rec["task_type"] == "skill_step::news::fetch"
    assert rec["agent_name"] == "news"
    assert rec["tool"] == "fetch"


async def test_no_schema_path_alerts_and_proceeds():
    """A schema-less tool dispatch fires a fallback alert but still runs."""
    alerts: list[dict] = []

    async def fake_alert(**kwargs):
        alerts.append(kwargs)
        return True

    registry = ToolRegistry(fallback_alert=fake_alert)
    registry.register("bare", _mock_tool)  # no param_schema
    result = await registry.dispatch("bare", {"anything": 1}, allowed_tools=["bare"])
    assert result == {"echo": {"anything": 1}}
    assert len(alerts) == 1
    assert alerts[0]["component"] == "skills_tool_registry"


async def test_overwrite_can_drop_a_stale_schema():
    registry = ToolRegistry()
    registry.register("t", _mock_tool, param_schema=_URL_SCHEMA)
    assert registry.has_schema("t") is True
    registry.register("t", _mock_tool)  # re-register without a schema
    assert registry.has_schema("t") is False


async def test_clear_drops_schemas():
    registry = ToolRegistry()
    registry.register("t", _mock_tool, param_schema=_URL_SCHEMA)
    registry.clear()
    assert registry.has_schema("t") is False
    assert registry.list_tool_names() == []
