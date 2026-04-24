"""Slice 15 — file-based Jinja renderer for vault templates.

Design decision (intentionally narrow):

Templates live on disk under ``prompts/vault/`` and are **self-contained**:
every template emits its own frontmatter as the first output block, using
the standard Obsidian ``---\\n...\\n---`` YAML delimiter. Callers never
pass frontmatter in — the template owns it. The renderer round-trips the
rendered text through ``python-frontmatter`` (already on the dependency
tree via :mod:`donna.integrations.vault`) to return ``(body, dict)``.

Why not ``{% set frontmatter = {...} %}``? That would require parsing
the template AST post-render to pull the variable back out. The YAML
block is simpler, matches the file format the vault already stores, and
keeps the renderer dumb.

Missing context keys raise :class:`jinja2.UndefinedError` thanks to
:class:`~jinja2.StrictUndefined` — templates must declare every variable
they use, which catches silent mis-renders during development.

See ``slices/slice_15_template_writes_meeting_notes.md §1`` and
``spec_v3.md §1.3``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter
import jinja2

from donna.skills._render import wrap_context


class VaultTemplateRenderer:
    """File-based Jinja renderer with StrictUndefined and frontmatter split."""

    def __init__(self, templates_dir: Path) -> None:
        if not templates_dir.is_dir():
            raise FileNotFoundError(
                f"templates_dir does not exist or is not a directory: {templates_dir}"
            )
        self._templates_dir = templates_dir
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(templates_dir)),
            undefined=jinja2.StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )

    @property
    def templates_dir(self) -> Path:
        return self._templates_dir

    def render(
        self, template_name: str, context: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Render ``template_name`` and split out its frontmatter block.

        Returns ``(body, frontmatter_dict)``. If the rendered text has no
        frontmatter block the dict is empty and ``body`` is the full
        rendered text.

        Raises:
            jinja2.UndefinedError: if the template references a missing
                context key (via StrictUndefined).
            jinja2.TemplateNotFound: if ``template_name`` is not under the
                configured ``templates_dir``.
        """
        wrapped = wrap_context(context)
        template = self._env.get_template(template_name)
        rendered = template.render(**wrapped)
        post = frontmatter.loads(rendered)
        return post.content, dict(post.metadata)
