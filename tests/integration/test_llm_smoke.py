"""Smoke tests for LLM task parsing via real Anthropic API.

Marked with @pytest.mark.llm — skipped by default, run with:
    pytest -m llm

Expected cost: ~$0.05 for 3 calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from donna.config import load_models_config, load_task_types_config
from donna.models.router import ModelRouter
from donna.orchestrator.input_parser import InputParser

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
FIXTURES_DIR = PROJECT_ROOT / "fixtures"


@pytest.fixture
def router() -> ModelRouter:
    models_config = load_models_config(CONFIG_DIR)
    task_types_config = load_task_types_config(CONFIG_DIR)
    return ModelRouter(models_config, task_types_config, PROJECT_ROOT)


@pytest.fixture
def parser(router: ModelRouter) -> InputParser:
    mock_logger = AsyncMock()
    mock_logger.log = AsyncMock(return_value="inv-smoke")
    return InputParser(router, mock_logger, PROJECT_ROOT)


@pytest.fixture
def tier1_cases() -> list[dict]:
    with open(FIXTURES_DIR / "parse_task" / "tier1_baseline.json") as f:
        data = json.load(f)
    return data["cases"]


def _get_case(cases: list[dict], case_id: str) -> dict:
    for case in cases:
        if case["id"] == case_id:
            return case
    raise ValueError(f"Fixture case {case_id} not found")


@pytest.mark.llm
class TestLLMSmoke:
    """Smoke tests that call the real Anthropic API."""

    async def test_buy_milk(self, parser: InputParser, tier1_cases: list[dict]) -> None:
        case = _get_case(tier1_cases, "t1-001")
        result = await parser.parse(case["input"], user_id="nick")

        assert result.title.lower() == "buy milk"
        assert result.domain == "personal"
        assert result.priority <= 2  # Should be low priority
        assert result.agent_eligible is False

    async def test_pay_electric_bill(self, parser: InputParser, tier1_cases: list[dict]) -> None:
        case = _get_case(tier1_cases, "t1-002")
        result = await parser.parse(case["input"], user_id="nick")

        assert "electric" in result.title.lower() or "bill" in result.title.lower()
        assert result.domain == "personal"
        assert result.deadline_type == "hard"
        assert result.deadline is not None

    async def test_send_invoice(self, parser: InputParser, tier1_cases: list[dict]) -> None:
        case = _get_case(tier1_cases, "t1-004")
        result = await parser.parse(case["input"], user_id="nick")

        assert "invoice" in result.title.lower()
        assert result.domain == "work"
        assert result.agent_eligible is False
