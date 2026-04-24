"""Slice 15 — :class:`MemorySkillsConfig` parse + defaults.

Verifies the new ``skills.meeting_note`` block in ``config/memory.yaml``
round-trips through :class:`MemoryConfig`, and that the defaults
match the contract when the block is absent (legacy configs keep
booting).
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from donna.config import MemorySkillsConfig, load_memory_config


def test_loads_skills_meeting_note_block(tmp_path: Path) -> None:
    yaml = dedent(
        """
        vault:
          root: /tmp/donna-vault
          git_author_name: Donna
          git_author_email: donna@example.com
          sync_method: manual
          templates_dir: prompts/vault

        safety:
          max_note_bytes: 200000
          path_allowlist: [Inbox, Meetings, People]

        skills:
          meeting_note:
            enabled: true
            poll_interval_seconds: 30
            lookback_minutes: 10
            autonomy_level: high
            context_limits:
              prior_meetings: 3
              recent_chats: 7
              open_tasks: 2
        """
    ).strip()
    (tmp_path / "memory.yaml").write_text(yaml, encoding="utf-8")
    cfg = load_memory_config(tmp_path)
    assert cfg.skills.meeting_note.enabled is True
    assert cfg.skills.meeting_note.poll_interval_seconds == 30
    assert cfg.skills.meeting_note.lookback_minutes == 10
    assert cfg.skills.meeting_note.autonomy_level == "high"
    assert cfg.skills.meeting_note.context_limits.prior_meetings == 3
    assert cfg.skills.meeting_note.context_limits.recent_chats == 7
    assert cfg.skills.meeting_note.context_limits.open_tasks == 2


def test_defaults_when_skills_block_absent(tmp_path: Path) -> None:
    yaml = dedent(
        """
        vault:
          root: /tmp/donna-vault
          git_author_name: Donna
          git_author_email: donna@example.com
          sync_method: manual
          templates_dir: prompts/vault

        safety:
          max_note_bytes: 200000
          path_allowlist: [Inbox]
        """
    ).strip()
    (tmp_path / "memory.yaml").write_text(yaml, encoding="utf-8")
    cfg = load_memory_config(tmp_path)
    # Full defaults path.
    assert cfg.skills.meeting_note.enabled is True
    assert cfg.skills.meeting_note.autonomy_level == "medium"
    assert cfg.skills.meeting_note.poll_interval_seconds == 60
    assert cfg.skills.meeting_note.lookback_minutes == 5
    assert cfg.skills.meeting_note.context_limits.prior_meetings == 5


def test_rejects_unknown_autonomy_level() -> None:
    """``autonomy_level`` is a ``Literal`` — typos must surface at load time."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MemorySkillsConfig.model_validate(
            {"meeting_note": {"autonomy_level": "reckless"}}
        )
