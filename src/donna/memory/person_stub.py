"""Auto-create ``People/{name}.md`` stubs for bare wikilinks in vault writes.

Shared hook invoked by :class:`~donna.memory.writer.MemoryInformedWriter`
after every successful template write. Scans the rendered body for bare
``[[Name]]`` wikilinks that do *not* already resolve under ``People/``
and writes empty stub notes so future retrievals and the
``person_profile`` skill (slice 16) have anchors to fill in.

Design notes:

- Only bare ``[[Name]]`` are considered — ``[[People/Name]]``,
  ``[[Projects/X]]``, ``[[Name|alias]]`` and ``[[Name#heading]]`` are
  excluded so we never shadow an explicit namespaced link.
- ``People`` must appear in ``safety.path_allowlist``; otherwise the
  helper is a no-op (no bypass of the vault safety envelope).
- Never overwrites: a successful ``VaultClient.stat`` short-circuits
  per-name.
- Failures never propagate — the caller is expected to swallow
  exceptions so stub creation is strictly best-effort.

See ``spec_v3.md §30.7`` and ``slices/slice_16_*.md``.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from collections.abc import Iterable

import structlog

from donna.integrations.vault import VaultClient, VaultReadError, VaultWriter

logger = structlog.get_logger()


# Matches ``[[Name]]`` only. Disallows ``/`` (namespaced), ``|`` (alias)
# and ``#`` (heading reference) inside the capture to avoid
# ``[[People/X]]`` / ``[[X|alt]]`` / ``[[X#H]]`` false positives.
_BARE_WIKILINK_RE = re.compile(r"\[\[([^\[\]/|#]+)\]\]")

_PEOPLE_FOLDER = "People"


def _stub_body(name: str, iso_now: str) -> str:
    return (
        "---\n"
        "type: person\n"
        f"name: {name}\n"
        "autowritten_by: donna\n"
        "stub: true\n"
        f"autowritten_at: {iso_now}\n"
        "---\n"
        "\n"
        f"# {name}\n"
        "\n"
        "_(Stub created by Donna on first mention. The weekly "
        "`person_profile` sweep will fill this in as context accrues.)_\n"
    )


def _extract_bare_names(body: str) -> list[str]:
    """Return de-duplicated bare wikilink targets, preserving first-seen order."""
    seen: dict[str, None] = {}
    for match in _BARE_WIKILINK_RE.finditer(body):
        name = match.group(1).strip()
        if not name:
            continue
        seen.setdefault(name, None)
    return list(seen.keys())


async def ensure_person_stubs(
    body: str,
    *,
    vault_writer: VaultWriter,
    vault_client: VaultClient,
    safety_allowlist: Iterable[str],
) -> list[str]:
    """Create missing ``People/{name}.md`` stubs for bare wikilinks in ``body``.

    Args:
        body: Rendered markdown body to scan. Frontmatter is irrelevant;
            callers may pass the body with or without it.
        vault_writer: Used to commit new stub files.
        vault_client: Used to probe for existing notes (``stat``).
        safety_allowlist: The effective ``safety.path_allowlist`` from
            ``config/memory.yaml``. If ``People`` is absent, the helper
            returns ``[]`` without attempting any writes.

    Returns:
        The list of names for which a stub was created. Empty if no
        bare wikilinks were present, ``People`` is not allowlisted, or
        every referenced person already had a note.
    """
    if _PEOPLE_FOLDER not in set(safety_allowlist):
        return []

    names = _extract_bare_names(body)
    if not names:
        return []

    created: list[str] = []
    iso_now = datetime.now(UTC).isoformat()
    for name in names:
        stub_path = f"{_PEOPLE_FOLDER}/{name}.md"
        try:
            await vault_client.stat(stub_path)
            # Exists — never overwrite.
            continue
        except VaultReadError as exc:
            if not str(exc).startswith("missing:"):
                # Any other read error (path_escape etc.) is a real
                # problem — let the caller's try/except surface it via
                # ``person_stub_failed``.
                raise

        await vault_writer.write(
            stub_path,
            _stub_body(name, iso_now),
            expected_mtime=None,
            message=f"autowrite: person_stub {name}",
        )
        created.append(name)
        logger.info("person_stub_created", name=name, path=stub_path)

    return created
