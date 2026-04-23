#!/usr/bin/env python3
"""Invoke a registered skill tool from the command line (slice 12).

Useful for smoke-testing the tool surface without booting the full
orchestrator. Loads ``DEFAULT_TOOL_REGISTRY`` with the same non-fatal
client builders the CLI uses, then dispatches a single tool call.

Usage:
    uv run python scripts/dev_tool_call.py [--config-dir PATH] <tool_name> \\
        --key1 value1 --key2 value2 ...

Note: ``--config-dir`` must come **before** the tool name. Everything
after the tool name is collected as tool kwargs by argparse REMAINDER.

Examples:
    uv run python scripts/dev_tool_call.py vault_list --folder Inbox
    uv run python scripts/dev_tool_call.py vault_write \\
        --path "Inbox/$(date +%F)-hello.md" --content '# Hello'
    uv run python scripts/dev_tool_call.py vault_read --path Inbox/foo.md

Numeric and boolean values are parsed heuristically:
    --n 3          -> int 3
    --recursive true   -> bool True
Everything else is passed as a string.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from donna.cli_wiring import _try_build_vault_client, _try_build_vault_writer
from donna.skills import tools as _skill_tools_module


def _coerce(value: str) -> object:
    """Best-effort literal coercion for CLI string values."""
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _parse_kv_args(extra: list[str]) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    it = iter(extra)
    for token in it:
        if not token.startswith("--"):
            raise SystemExit(f"expected --key, got: {token!r}")
        key = token[2:]
        try:
            value = next(it)
        except StopIteration as exc:
            raise SystemExit(f"missing value for --{key}") from exc
        kwargs[key] = _coerce(value)
    return kwargs


async def _main(tool_name: str, kwargs: dict[str, object], config_dir: Path) -> int:
    vault_client = _try_build_vault_client(config_dir)
    vault_writer = await _try_build_vault_writer(config_dir, vault_client)

    registry = _skill_tools_module.DEFAULT_TOOL_REGISTRY
    registry.clear()
    _skill_tools_module.register_default_tools(
        registry,
        vault_client=vault_client,
        vault_writer=vault_writer,
    )

    if tool_name not in registry.list_tool_names():
        available = ", ".join(sorted(registry.list_tool_names())) or "(none)"
        print(
            f"tool {tool_name!r} not registered. available: {available}",
            file=sys.stderr,
        )
        return 2

    try:
        # Dispatch with a single-tool allowlist: in dev the caller chose
        # the tool name, so the allowlist check is redundant but the
        # dispatch path is the supported one.
        result = await registry.dispatch(
            tool_name=tool_name,
            args=kwargs,
            allowed_tools=[tool_name],
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tool", help="Registered tool name, e.g. vault_write")
    parser.add_argument(
        "--config-dir",
        default=str(Path("config")),
        help="Path to the config/ directory (default: ./config)",
    )
    parser.add_argument(
        "extra", nargs=argparse.REMAINDER, help="Tool kwargs as --key value pairs"
    )
    args = parser.parse_args()

    kwargs = _parse_kv_args(args.extra)
    rc = asyncio.run(_main(args.tool, kwargs, Path(args.config_dir)))
    sys.exit(rc)


if __name__ == "__main__":
    main()
