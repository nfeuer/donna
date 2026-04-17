"""Test the `donna test-notification` CLI subcommand argument parsing."""

from __future__ import annotations


def test_test_notification_subcommand_parses() -> None:
    from donna.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args([
        "test-notification",
        "--type", "digest",
        "--channel", "tasks",
        "--content", "hello",
    ])
    assert ns.command == "test-notification"
    assert ns.type == "digest"
    assert ns.channel == "tasks"
    assert ns.content == "hello"


def test_build_parser_registers_existing_subcommands() -> None:
    """Regression — the refactor to _build_parser must not drop any existing subcommands."""
    from donna.cli import _build_parser
    parser = _build_parser()
    for cmd in ("run", "health", "backup"):
        ns = parser.parse_args([cmd])
        assert ns.command == cmd
