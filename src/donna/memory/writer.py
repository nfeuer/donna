"""Slice 15 — shared orchestrator for memory-informed vault writes.

:class:`MemoryInformedWriter` is the single entry point every
template-write skill (meeting note now; weekly review, person profile,
commitment log, daily reflection in Slice 16) delegates to. It owns:

1. Autonomy-based path redirection (``low`` → ``Inbox/``).
2. Frontmatter-keyed idempotency (short-circuit before LLM spend).
3. Prompt rendering + routed LLM completion.
4. Vault-template rendering and commit.
5. Structured failure handling (log and return, never partial-write).

The caller supplies:

- ``template`` — filename under the configured ``VaultTemplateRenderer``.
- ``task_type`` — routed via ``config/task_types.yaml``.
- ``context_gather`` — async callable returning the render context
  (memory hits, calendar data, resolved attendee wikilinks, etc.).
- ``target_path`` — caller-computed deterministic path.
- ``idempotency_key`` — stable per-event value stored in the rendered
  frontmatter; re-runs with the same key are no-ops.
- ``user_id``, ``autonomy_level``.

See ``slices/slice_15_template_writes_meeting_notes.md §2``,
``spec_v3.md §4 / §7.3``.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import frontmatter
import jinja2
import structlog

from donna.integrations.vault import (
    VaultClient,
    VaultNote,
    VaultReadError,
    VaultWriter,
)
from donna.logging.invocation_logger import InvocationLogger
from donna.memory.person_stub import ensure_person_stubs
from donna.memory.templates import VaultTemplateRenderer
from donna.models.router import ModelRouter

PersonStubHelper = Callable[..., Awaitable[list[str]]]

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class WriteResult:
    """Outcome of a single :meth:`MemoryInformedWriter.run` call.

    ``sha`` is the git commit SHA on a successful write, ``None`` on
    skip or failure. ``skipped=True`` with ``reason="idempotent"`` is
    the happy-path short-circuit; any other ``reason`` is a failure
    string captured from the raising exception.
    """

    path: str
    sha: str | None
    skipped: bool
    reason: str | None


class MemoryInformedWriter:
    """Shared template-write orchestrator."""

    def __init__(
        self,
        *,
        renderer: VaultTemplateRenderer,
        vault_client: VaultClient,
        vault_writer: VaultWriter,
        router: ModelRouter,
        logger: InvocationLogger,
        safety_allowlist: Iterable[str] | None = None,
        person_stub_helper: PersonStubHelper | None = None,
    ) -> None:
        self._renderer = renderer
        self._vault_client = vault_client
        self._vault_writer = vault_writer
        self._router = router
        # Reserved for future pre/post-write telemetry; router calls
        # already emit their own invocation_log rows on every completion.
        self._invocation_logger = logger
        self._safety_allowlist = (
            list(safety_allowlist) if safety_allowlist is not None else []
        )
        self._person_stub_helper = person_stub_helper or ensure_person_stubs
        self._prompt_env = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )

    async def run(
        self,
        *,
        template: str,
        task_type: str,
        context_gather: Callable[[], Awaitable[dict[str, Any]]],
        target_path: str,
        idempotency_key: str,
        user_id: str,
        autonomy_level: str,
    ) -> WriteResult:
        """Execute the memory-informed write pipeline.

        Any exception after the idempotency check is caught, emits
        ``vault_autowrite_failed``, and returns a skipped ``WriteResult``
        — never a partial write.
        """
        effective_path = _apply_autonomy_redirect(target_path, autonomy_level)

        existing = await _read_if_exists(self._vault_client, effective_path)
        if existing is not None and existing.frontmatter.get(
            "idempotency_key"
        ) == idempotency_key:
            logger.info(
                "vault_autowrite_skipped_idempotent",
                path=effective_path,
                template=template,
                idempotency_key=idempotency_key,
                user_id=user_id,
            )
            return WriteResult(
                path=effective_path,
                sha=None,
                skipped=True,
                reason="idempotent",
            )

        try:
            context = await context_gather()

            prompt_src = self._router.get_prompt_template(task_type)
            rendered_prompt = self._prompt_env.from_string(prompt_src).render(
                **context
            )
            llm_output, _meta = await self._router.complete(
                rendered_prompt,
                task_type=task_type,
                user_id=user_id,
            )

            merged = {
                **context,
                "llm": llm_output,
                "now_iso": datetime.now(UTC).isoformat(),
            }
            body, fm = self._renderer.render(template, merged)

            serialized = frontmatter.dumps(frontmatter.Post(body, **fm))
            if not serialized.endswith("\n"):
                serialized = serialized + "\n"

            sha = await self._vault_writer.write(
                effective_path,
                serialized,
                expected_mtime=existing.mtime if existing is not None else None,
                message=f"autowrite: {template} {idempotency_key}",
            )
        except Exception as exc:  # pragma: no cover — exercised via tests
            logger.warning(
                "vault_autowrite_failed",
                path=effective_path,
                template=template,
                idempotency_key=idempotency_key,
                user_id=user_id,
                reason=str(exc),
                exc_type=type(exc).__name__,
            )
            return WriteResult(
                path=effective_path,
                sha=None,
                skipped=True,
                reason=str(exc),
            )

        logger.info(
            "vault_autowrite_written",
            path=effective_path,
            template=template,
            idempotency_key=idempotency_key,
            autonomy_level=autonomy_level,
            redirected_to_inbox=effective_path != target_path,
            user_id=user_id,
            sha=sha,
        )

        # Best-effort stub creation. Never propagates; writer success
        # is not contingent on stub success.
        try:
            created = await self._person_stub_helper(
                body,
                vault_writer=self._vault_writer,
                vault_client=self._vault_client,
                safety_allowlist=self._safety_allowlist,
            )
            if created:
                logger.info(
                    "person_stubs_created",
                    count=len(created),
                    names=created,
                    source_template=template,
                )
        except Exception as exc:
            logger.warning(
                "person_stub_failed",
                path=effective_path,
                template=template,
                reason=str(exc),
                exc_type=type(exc).__name__,
            )

        return WriteResult(
            path=effective_path, sha=sha, skipped=False, reason=None
        )


def _apply_autonomy_redirect(target_path: str, autonomy_level: str) -> str:
    """``low`` autonomy forces writes into ``Inbox/{basename}``."""
    if autonomy_level == "low":
        return f"Inbox/{Path(target_path).name}"
    return target_path


async def _read_if_exists(
    client: VaultClient, path: str
) -> VaultNote | None:
    """Read a note under path, returning None when the file is absent."""
    try:
        return await client.read(path)
    except VaultReadError as exc:
        # VaultReadError message format is "{reason}: {detail}" — the
        # module does not expose a typed reason attribute, so we sniff
        # the prefix. "missing" is the only case we swallow; any other
        # read error (path_escape, not_markdown) should propagate.
        if str(exc).startswith("missing:"):
            return None
        raise

