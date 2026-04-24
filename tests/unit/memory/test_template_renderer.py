"""Slice 15 — :class:`VaultTemplateRenderer` unit tests.

Covers the three contract guarantees documented in the module docstring:
StrictUndefined raises on missing variables; a first-line YAML block is
parsed and returned separately from the body; templates with no
frontmatter return an empty dict and the full rendered text.
"""
from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

from donna.memory.templates import VaultTemplateRenderer


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_strict_undefined_raises_on_missing_var(tmp_path: Path) -> None:
    _write(tmp_path / "t.md.j2", "Hello {{ name }}\n")
    r = VaultTemplateRenderer(tmp_path)
    with pytest.raises(jinja2.UndefinedError):
        r.render("t.md.j2", {})


def test_first_line_frontmatter_extracted(tmp_path: Path) -> None:
    _write(
        tmp_path / "meeting.md.j2",
        "---\n"
        "type: meeting\n"
        "idempotency_key: {{ key }}\n"
        "---\n"
        "# {{ title }}\n\n"
        "Body.\n",
    )
    r = VaultTemplateRenderer(tmp_path)
    body, fm = r.render("meeting.md.j2", {"key": "E1", "title": "Sync"})

    assert fm == {"type": "meeting", "idempotency_key": "E1"}
    # python-frontmatter strips the block; body retains the rendered content.
    assert "---" not in body
    assert "# Sync" in body
    assert "Body." in body


def test_template_without_frontmatter_returns_empty_dict(tmp_path: Path) -> None:
    _write(tmp_path / "plain.md.j2", "# Hello {{ name }}\n\nBody.\n")
    r = VaultTemplateRenderer(tmp_path)
    body, fm = r.render("plain.md.j2", {"name": "Nick"})

    assert fm == {}
    assert "Hello Nick" in body


def test_dotted_access_via_attrdict_wrap(tmp_path: Path) -> None:
    """Slice 15 reuses ``wrap_context`` from ``skills._render`` so nested
    dicts support ``{{ event.title }}`` in templates (not just bracket
    access), matching the convention used by DSL-side Jinja rendering."""
    _write(
        tmp_path / "nested.md.j2",
        "---\nevent_id: {{ event.event_id }}\n---\n# {{ event.summary }}\n",
    )
    r = VaultTemplateRenderer(tmp_path)
    body, fm = r.render(
        "nested.md.j2",
        {"event": {"event_id": "E42", "summary": "1:1 with Alice"}},
    )
    assert fm == {"event_id": "E42"}
    assert "# 1:1 with Alice" in body


def test_missing_template_raises(tmp_path: Path) -> None:
    r = VaultTemplateRenderer(tmp_path)
    with pytest.raises(jinja2.TemplateNotFound):
        r.render("nope.md.j2", {})


def test_missing_dir_raises() -> None:
    with pytest.raises(FileNotFoundError):
        VaultTemplateRenderer(Path("/nonexistent/slice15"))
