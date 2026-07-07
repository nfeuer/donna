"""Tests for AnthropicProvider cost computation (single source of price truth).

Regression for the Model-Layer S2 finding: the provider hardcoded Sonnet
pricing and applied it to ANY model id, so the first model change would
silently corrupt cost_usd in the invocation ledger. The provider now prices
from injected per-alias config rates and fails loud on an unpriced model.
"""

from __future__ import annotations

import pytest

from donna.models.providers.anthropic import AnthropicProvider


def test_cost_uses_config_rates() -> None:
    p = AnthropicProvider(api_key="test", cost_rates={"model-a": (1e-6, 2e-6)})
    # 1000 in @ 1e-6 + 500 out @ 2e-6
    assert p._cost("model-a", 1000, 500) == pytest.approx(1000 * 1e-6 + 500 * 2e-6)


def test_cost_unknown_model_with_rates_raises() -> None:
    """If rates are configured but the model isn't among them, fail loud."""
    p = AnthropicProvider(api_key="test", cost_rates={"model-a": (1e-6, 2e-6)})
    with pytest.raises(ValueError, match="No cost rate configured"):
        p._cost("model-b", 10, 10)


def test_cost_sonnet_fallback_when_no_rates() -> None:
    """Direct construction (eval/tests) with no rates: Sonnet ids still price."""
    p = AnthropicProvider(api_key="test")
    # $3/Mtok input → 1M input tokens = $3.00
    assert p._cost("claude-sonnet-4-6", 1_000_000, 0) == pytest.approx(3.0)


def test_cost_non_sonnet_no_rates_raises() -> None:
    """No rates + a non-Sonnet model must NOT be silently priced as Sonnet."""
    p = AnthropicProvider(api_key="test")
    with pytest.raises(ValueError, match="No cost rate configured"):
        p._cost("some-future-model", 10, 10)
