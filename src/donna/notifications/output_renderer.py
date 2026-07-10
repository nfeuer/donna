"""User-facing output renderer — the output standard (slice 1).

Every user-facing message that starts as structured data (automation alert
outputs today; reminders and digests in later slices) is rendered here so no
surface ever shows raw JSON. Design:
``docs/superpowers/specs/2026-07-10-output-standard-design.md``;
spec_v3.md §25 (automations subsystem).

Rendering is deterministic-first: a Jinja2 template produces the description
from ``config/output_formats.yaml``; the optional voice pass (a local-LLM
rewrite of the description sentence only) is garnish and every failure of it
falls back to the template text with ``event_type="fallback_activated"``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

from donna.config import OutputFormatEntry, OutputFormatsConfig

logger = structlog.get_logger()

# Discord hard limit is 2000; keep headroom for channel prefixes (matches
# NotificationService's existing truncation contract).
_TEXT_LIMIT = 1900
_EMBED_TITLE_LIMIT = 256
_EMBED_DESC_LIMIT = 4096

VoiceFn = Callable[[str, dict[str, Any]], Awaitable[str | None]]


@dataclasses.dataclass(frozen=True)
class RenderedMessage:
    """A rendered user-facing message.

    ``text`` is always populated and self-sufficient (SMS/email/log surfaces);
    ``embed`` is the rich Discord shape when the format defines one and
    discord.py is importable, else None.
    """

    text: str
    embed: Any | None = None


class _Tolerant(dict):
    """format_map helper: missing keys render as ``?`` instead of raising."""

    def __missing__(self, key: str) -> str:
        return "?"


class OutputRenderer:
    """Render structured payloads into user-facing messages.

    Args:
        config: Parsed ``config/output_formats.yaml``.
        project_root: Base directory template paths are resolved against.
        voice_fn: Optional async callable ``(description, payload) -> str|None``
            that rewrites the description in Donna's voice via the local LLM.
            Exceptions and falsy returns fall back to the template description.
    """

    def __init__(
        self,
        config: OutputFormatsConfig,
        project_root: Path,
        voice_fn: VoiceFn | None = None,
    ) -> None:
        self._config = config
        self._root = project_root
        self._voice_fn = voice_fn

    async def render(
        self,
        surface: str,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> RenderedMessage:
        """Render *payload* for *surface* (e.g. ``automation_alert.product_watch``).

        Never raises on content problems and never emits raw JSON: unknown
        surfaces get a key/value rendering, missing fields render as blanks.

        Args:
            surface: Format key; falls back to ``<category>.default``.
            payload: The structured output (e.g. a capability's output dict).
            context: Extra template variables (``automation_name``, inputs
                like ``url``/``max_price_usd``). Payload keys win on clash.

        Returns:
            RenderedMessage with plain text and an optional Discord embed.
        """
        merged: dict[str, Any] = {**(context or {}), **payload}
        entry = self._resolve(surface)

        if entry is None:
            description = _generic_lines(merged)
            title_line = str(merged.get("automation_name") or surface)
            text = _truncate(f"{title_line}\n{description}".strip(), _TEXT_LIMIT)
            return RenderedMessage(text=text)

        description = self._render_template(entry, merged, surface)
        description = await self._apply_voice_pass(entry, description, merged, surface)

        field_lines = _field_lines(entry, merged)
        url = _url_of(entry, merged)
        title = _embed_title(entry, merged) or str(merged.get("automation_name") or "")

        parts = [p for p in (title, description.strip(), field_lines, url) if p]
        text = _truncate("\n".join(parts), _TEXT_LIMIT)
        embed = self._build_embed(entry, title, description, merged, url)
        return RenderedMessage(text=text, embed=embed)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve(self, surface: str) -> OutputFormatEntry | None:
        entry = self._config.formats.get(surface)
        if entry is not None:
            return entry
        category = surface.rsplit(".", 1)[0]
        return self._config.formats.get(f"{category}.default")

    def _render_template(
        self, entry: OutputFormatEntry, merged: dict[str, Any], surface: str
    ) -> str:
        try:
            from jinja2 import Environment

            template_text = (self._root / entry.template).read_text()
            env = Environment(autoescape=False)
            return env.from_string(template_text).render(
                **merged, payload=dict(merged)
            )
        except Exception as exc:
            logger.warning(
                "output_template_render_failed",
                event_type="fallback_activated",
                surface=surface,
                template=entry.template,
                error=str(exc),
            )
            return _generic_lines(merged)

    async def _apply_voice_pass(
        self,
        entry: OutputFormatEntry,
        description: str,
        merged: dict[str, Any],
        surface: str,
    ) -> str:
        if (
            not entry.voice_pass
            or not self._config.voice_pass.enabled
            or self._voice_fn is None
        ):
            return description
        try:
            voiced = await self._voice_fn(description, merged)
        except Exception as exc:
            logger.warning(
                "output_voice_pass_failed",
                event_type="fallback_activated",
                surface=surface,
                error=str(exc),
            )
            return description
        return voiced.strip() if voiced else description

    def _build_embed(
        self,
        entry: OutputFormatEntry,
        title: str,
        description: str,
        merged: dict[str, Any],
        url: str | None,
    ) -> Any | None:
        if entry.embed is None:
            return None
        try:
            import discord
        except ImportError:
            return None
        colour = self._config.colours.get(entry.embed.colour, 0x3498DB)
        embed = discord.Embed(
            title=_truncate(title, _EMBED_TITLE_LIMIT),
            description=_truncate(description.strip(), _EMBED_DESC_LIMIT),
            colour=colour,
        )
        if url:
            embed.url = url
        for key in entry.embed.fields:
            if key in merged and merged[key] is not None:
                embed.add_field(name=key, value=_pretty(merged[key]), inline=True)
        return embed


def _embed_title(entry: OutputFormatEntry, merged: dict[str, Any]) -> str | None:
    if entry.embed is None:
        return None
    return entry.embed.title.format_map(_Tolerant(merged))


def _url_of(entry: OutputFormatEntry, merged: dict[str, Any]) -> str | None:
    if entry.embed is None or entry.embed.url_field is None:
        return None
    value = merged.get(entry.embed.url_field)
    return str(value) if value else None


def _field_lines(entry: OutputFormatEntry, merged: dict[str, Any]) -> str:
    if entry.embed is None or not entry.embed.fields:
        return ""
    parts = [
        f"{key.replace('_', ' ')}: {_pretty(merged[key])}"
        for key in entry.embed.fields
        if key in merged and merged[key] is not None
    ]
    return " · ".join(parts)


def _generic_lines(merged: dict[str, Any]) -> str:
    """Key/value fallback rendering — the never-JSON floor."""
    lines = []
    for key, value in merged.items():
        if key.startswith("_") or key in ("ok", "triggers_alert", "payload"):
            continue
        if isinstance(value, dict):
            continue
        if value is None:
            continue
        lines.append(f"{key.replace('_', ' ')}: {_pretty(value)}")
    return "\n".join(lines)


def _pretty(value: Any) -> str:
    if value is True:
        return "✓"
    if value is False:
        return "✗"
    return str(value)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
