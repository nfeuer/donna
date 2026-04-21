"""Unit test: news_check skill iterates over inputs.feed_urls."""
from __future__ import annotations

from pathlib import Path

import yaml


def test_news_check_skill_yaml_uses_for_each_over_feed_urls() -> None:
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "news_check" / "skill.yaml"
    data = yaml.safe_load(skill_path.read_text())
    fetch_step = next(s for s in data["steps"] if s["name"] == "fetch_items")
    assert "tool_invocations" in fetch_step, "fetch_items must have tool_invocations"
    invocations = fetch_step["tool_invocations"]
    assert len(invocations) == 1, "fetch_items must have exactly one tool_invocation block"
    inv = invocations[0]
    assert "for_each" in inv, "fetch_items tool_invocation must use for_each"
    assert inv["for_each"] == "{{ inputs.feed_urls }}", (
        f"expected for_each: '{{{{ inputs.feed_urls }}}}', got {inv['for_each']!r}"
    )
    inv_str = yaml.safe_dump(inv)
    assert "feed_urls[0]" not in inv_str, "hardcoded feed_urls[0] must be removed"
