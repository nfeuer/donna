"""Unit tests for the user-facing OutputRenderer (output standard slice 1).

Covers: format resolution (exact → category default → generic), missing-field
tolerance, truncation, voice-pass behaviour and fallback, embed construction,
and the invariant that no surface ever receives raw JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from donna.config import (
    EmbedSpec,
    OutputFormatEntry,
    OutputFormatsConfig,
    load_output_formats_config,
)
from donna.notifications.output_renderer import OutputRenderer, RenderedMessage

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_PRODUCT_PAYLOAD: dict[str, Any] = {
    "ok": True,
    "price_usd": 29.99,
    "currency": "USD",
    "in_stock": True,
    "size_available": True,
    "triggers_alert": True,
    "title": "Seraphina Gown",
}


def _config(tmp_path: Path, **overrides: Any) -> OutputFormatsConfig:
    """A minimal config with one specific format and one category default."""
    (tmp_path / "specific.md.j2").write_text(
        "{{ title }} is ${{ price_usd }}{% if in_stock %}, in stock{% endif %}."
    )
    (tmp_path / "default.md.j2").write_text(
        "{% for k, v in payload.items() if not k.startswith('_') %}"
        "{{ k }}: {{ v }}\n{% endfor %}"
    )
    kwargs: dict[str, Any] = dict(
        formats={
            "automation_alert.product_watch": OutputFormatEntry(
                template="specific.md.j2",
                embed=EmbedSpec(
                    title="🛍️ {title} — ${price_usd}",
                    colour="good_news",
                    fields=["price_usd", "in_stock", "size_available"],
                    url_field="url",
                ),
                voice_pass=True,
            ),
            "automation_alert.default": OutputFormatEntry(template="default.md.j2"),
        },
    )
    kwargs.update(overrides)
    return OutputFormatsConfig(**kwargs)


@pytest.mark.asyncio
async def test_exact_format_renders_facts(tmp_path: Path) -> None:
    r = OutputRenderer(_config(tmp_path), project_root=tmp_path)
    msg = await r.render(
        "automation_alert.product_watch",
        SAMPLE_PRODUCT_PAYLOAD,
        context={"automation_name": "shirt on sale", "url": "https://x.example/p"},
    )
    assert isinstance(msg, RenderedMessage)
    assert "Seraphina Gown" in msg.text
    assert "29.99" in msg.text
    assert not msg.text.lstrip().startswith("{")


@pytest.mark.asyncio
async def test_unknown_capability_falls_back_to_category_default(tmp_path: Path) -> None:
    r = OutputRenderer(_config(tmp_path), project_root=tmp_path)
    msg = await r.render(
        "automation_alert.web_check",
        {"answer": "pool is open", "triggers_alert": True},
        context={"automation_name": "pool schedule"},
    )
    assert "answer: pool is open" in msg.text


@pytest.mark.asyncio
async def test_totally_unknown_surface_never_emits_json(tmp_path: Path) -> None:
    cfg = OutputFormatsConfig(formats={})
    r = OutputRenderer(cfg, project_root=tmp_path)
    payload = {"price_usd": 12.5, "nested": {"a": 1}}
    msg = await r.render("reminder.exotic", payload, context={"automation_name": "x"})
    assert "price usd: 12.5" in msg.text
    # The rendered text must never be the JSON dump of the payload.
    assert json.dumps(payload, indent=2) not in msg.text
    assert msg.embed is None


@pytest.mark.asyncio
async def test_missing_payload_fields_are_tolerated(tmp_path: Path) -> None:
    r = OutputRenderer(_config(tmp_path), project_root=tmp_path)
    # No title, no price — template and embed title pattern reference both.
    msg = await r.render("automation_alert.product_watch", {"in_stock": True})
    assert msg.text  # renders something, no exception


@pytest.mark.asyncio
async def test_text_truncated_to_discord_limit(tmp_path: Path) -> None:
    (tmp_path / "long.md.j2").write_text("{{ blob }}")
    cfg = OutputFormatsConfig(
        formats={"automation_alert.default": OutputFormatEntry(template="long.md.j2")}
    )
    r = OutputRenderer(cfg, project_root=tmp_path)
    msg = await r.render("automation_alert.big", {"blob": "x" * 5000})
    assert len(msg.text) <= 1900


@pytest.mark.asyncio
async def test_voice_pass_rewrites_description(tmp_path: Path) -> None:
    async def voice(description: str, payload: dict[str, Any]) -> str | None:
        return "That's $4 under your threshold — keep watching?"

    r = OutputRenderer(_config(tmp_path), project_root=tmp_path, voice_fn=voice)
    msg = await r.render("automation_alert.product_watch", SAMPLE_PRODUCT_PAYLOAD)
    assert "keep watching?" in msg.text


@pytest.mark.asyncio
async def test_voice_pass_failure_falls_back_to_template(tmp_path: Path) -> None:
    async def voice(description: str, payload: dict[str, Any]) -> str | None:
        raise RuntimeError("ollama down")

    r = OutputRenderer(_config(tmp_path), project_root=tmp_path, voice_fn=voice)
    msg = await r.render("automation_alert.product_watch", SAMPLE_PRODUCT_PAYLOAD)
    assert "Seraphina Gown is $29.99" in msg.text  # template facts shipped anyway


@pytest.mark.asyncio
async def test_voice_pass_skipped_when_disabled_globally(tmp_path: Path) -> None:
    calls: list[str] = []

    async def voice(description: str, payload: dict[str, Any]) -> str | None:
        calls.append(description)
        return "voiced"

    cfg = _config(tmp_path, voice_pass={"enabled": False})
    r = OutputRenderer(cfg, project_root=tmp_path, voice_fn=voice)
    await r.render("automation_alert.product_watch", SAMPLE_PRODUCT_PAYLOAD)
    assert calls == []


@pytest.mark.asyncio
async def test_embed_built_with_title_colour_fields(tmp_path: Path) -> None:
    r = OutputRenderer(_config(tmp_path), project_root=tmp_path)
    msg = await r.render(
        "automation_alert.product_watch",
        SAMPLE_PRODUCT_PAYLOAD,
        context={"url": "https://x.example/p"},
    )
    assert msg.embed is not None
    assert "Seraphina Gown" in msg.embed.title
    assert "$29.99" in msg.embed.title
    assert msg.embed.url == "https://x.example/p"
    field_names = [f.name for f in msg.embed.fields]
    assert "price_usd" in field_names


@pytest.mark.asyncio
async def test_embed_title_tolerates_missing_keys(tmp_path: Path) -> None:
    r = OutputRenderer(_config(tmp_path), project_root=tmp_path)
    msg = await r.render("automation_alert.product_watch", {"price_usd": 5})
    assert msg.embed is not None  # no KeyError from the {title} placeholder


# ---------------------------------------------------------------------------
# Real-config drift tests (load the live YAML like the other config tests do)
# ---------------------------------------------------------------------------


def test_real_config_loads_and_templates_exist() -> None:
    cfg = load_output_formats_config(REPO_ROOT / "config")
    assert "automation_alert.default" in cfg.formats
    assert "automation_alert.product_watch" in cfg.formats
    for key, entry in cfg.formats.items():
        assert (REPO_ROOT / entry.template).exists(), f"{key}: missing {entry.template}"
        if entry.embed is not None:
            assert entry.embed.colour in cfg.colours, f"{key}: unknown colour"


@pytest.mark.asyncio
async def test_real_product_watch_format_golden() -> None:
    cfg = load_output_formats_config(REPO_ROOT / "config")
    r = OutputRenderer(cfg, project_root=REPO_ROOT)
    msg = await r.render(
        "automation_alert.product_watch",
        SAMPLE_PRODUCT_PAYLOAD,
        context={
            "automation_name": "shirt on sale",
            "url": "https://www.example.com/shirt",
            "max_price_usd": 34,
        },
    )
    assert "Seraphina Gown" in msg.text
    assert "29.99" in msg.text
    assert "{" not in msg.text.replace("{}", "")  # no JSON artifacts
    assert msg.embed is not None
