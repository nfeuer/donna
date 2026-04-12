"""Tests for LLM gateway types and config loading."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from donna.llm.types import GatewayConfig, Priority, load_gateway_config


class TestPriority:
    def test_ordering(self) -> None:
        assert Priority.CRITICAL < Priority.NORMAL < Priority.BACKGROUND

    def test_from_str(self) -> None:
        assert Priority.from_str("critical") == Priority.CRITICAL
        assert Priority.from_str("normal") == Priority.NORMAL
        assert Priority.from_str("background") == Priority.BACKGROUND

    def test_from_str_unknown_defaults_normal(self) -> None:
        assert Priority.from_str("unknown") == Priority.NORMAL


class TestGatewayConfig:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "llm_gateway.yaml"
        config_file.write_text(textwrap.dedent("""\
            scheduling:
              active_hours: "07:00-21:00"
              schedule_drain_minutes: 3
            queue:
              max_external_depth: 15
              max_interrupt_count: 5
            rate_limits:
              default:
                requests_per_minute: 5
                requests_per_hour: 50
            budget:
              daily_external_usd: 10.0
              alert_pct: 90
            cloud:
              max_per_request_usd: 1.0
              daily_cloud_external_usd: 5.0
            alerts:
              queue_depth_warning: 8
              rate_limit_alert_threshold: 2
              debounce_minutes: 5
            priority_map:
              parse_task: critical
              generate_nudge: background
            ollama_health_check: false
        """))
        cfg = load_gateway_config(tmp_path)

        assert cfg.active_hours_start == 7
        assert cfg.active_hours_end == 21
        assert cfg.schedule_drain_minutes == 3
        assert cfg.max_external_depth == 15
        assert cfg.max_interrupt_count == 5
        assert cfg.default_rpm == 5
        assert cfg.default_rph == 50
        assert cfg.daily_external_usd == 10.0
        assert cfg.max_per_request_usd == 1.0
        assert cfg.priority_for_task_type("parse_task") == Priority.CRITICAL
        assert cfg.priority_for_task_type("generate_nudge") == Priority.BACKGROUND
        assert cfg.priority_for_task_type("unknown_task") == Priority.NORMAL
        assert cfg.ollama_health_check is False

    def test_load_defaults_when_file_missing(self, tmp_path: Path) -> None:
        cfg = load_gateway_config(tmp_path)
        assert cfg.active_hours_start == 6
        assert cfg.active_hours_end == 22
        assert cfg.max_external_depth == 20
        assert cfg.default_rpm == 10
        assert cfg.daily_external_usd == 5.0
        assert cfg.ollama_health_check is True
