"""Tests for GpuTracker — GPU model state and swap metrics."""

from __future__ import annotations

from donna.llm.gpu_tracker import GpuTracker
from donna.llm.types import GpuConfig


def _make_tracker(home: str = "qwen2.5:32b-instruct-q6_K") -> GpuTracker:
    config = GpuConfig(home_model=home)
    return GpuTracker(config)


class TestGpuTracker:
    def test_initial_state(self):
        t = _make_tracker()
        assert t.loaded_model is None
        assert t.is_home is False

    def test_record_loaded(self):
        t = _make_tracker()
        t.record_loaded("qwen2.5:32b-instruct-q6_K")
        assert t.loaded_model == "qwen2.5:32b-instruct-q6_K"
        assert t.is_home is True

    def test_record_swap(self):
        t = _make_tracker()
        t.record_loaded("qwen2.5:32b-instruct-q6_K")
        t.record_swap_started("qwen2.5-vl:7b")
        t.record_swap_completed("qwen2.5-vl:7b", duration_ms=5000)
        assert t.loaded_model == "qwen2.5-vl:7b"
        assert t.is_home is False
        assert t.swaps_this_hour >= 1

    def test_swap_metrics(self):
        t = _make_tracker()
        t.record_swap_started("model-a")
        t.record_swap_completed("model-a", duration_ms=3000)
        t.record_swap_started("model-b")
        t.record_swap_completed("model-b", duration_ms=5000)
        metrics = t.get_metrics()
        assert metrics["swaps_this_hour"] == 2
        assert metrics["avg_swap_duration_ms_1h"] == 4000

    def test_should_alert_swap_rate(self):
        config = GpuConfig(swaps_per_hour_warning=2)
        t = GpuTracker(config)
        t.record_swap_started("a")
        t.record_swap_completed("a", duration_ms=1000)
        # Record plenty of execution time so swap overhead stays below warning threshold.
        t.record_execution_time(100_000)
        assert t.check_alerts() == []
        t.record_swap_started("b")
        t.record_swap_completed("b", duration_ms=1000)
        t.record_swap_started("c")
        t.record_swap_completed("c", duration_ms=1000)
        alerts = t.check_alerts()
        assert any("swapped" in a.lower() for a in alerts)

    def test_home_model_property(self):
        t = _make_tracker("my-home-model")
        assert t.home_model == "my-home-model"

    def test_execution_time_affects_overhead(self):
        t = _make_tracker()
        t.record_swap_started("a")
        t.record_swap_completed("a", duration_ms=1000)
        t.record_execution_time(9000)
        metrics = t.get_metrics()
        assert metrics["swap_overhead_pct_1h"] == 10.0
