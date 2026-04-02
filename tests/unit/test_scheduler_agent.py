"""Tests for the Scheduler Agent."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from donna.agents.scheduler_agent import SchedulerAgent


class TestSchedulerAgent:
    def test_agent_properties(self) -> None:
        agent = SchedulerAgent()
        assert agent.name == "scheduler"
        assert "calendar_read" in agent.allowed_tools
        assert "task_db_read" in agent.allowed_tools
        assert agent.timeout_seconds == 120
