"""Slice 15 — person-link resolution for meeting-note scaffolds.

Returns ``[[People/{name}]]`` if a corresponding note exists in the
vault, else the bare ``[[{name}]]`` wikilink. Obsidian renders bare
wikilinks as red ("unresolved") which surfaces them in the
"Unresolved links" panel — a useful nudge to write the profile later.

Deliberately **does not** auto-create stub notes. The person-profile
skill in Slice 16 is responsible for creating ``People/{name}.md``; this
module only reports reachability.
"""
from __future__ import annotations

from donna.integrations.vault import VaultClient, VaultReadError


async def resolve_person_link(
    attendee_name: str, vault_client: VaultClient
) -> str:
    """Return the wikilink for ``attendee_name``.

    ``[[People/{name}]]`` when ``People/{name}.md`` exists under the vault
    root, otherwise ``[[{name}]]``. Never mutates the vault.
    """
    candidate = f"People/{attendee_name}.md"
    try:
        await vault_client.stat(candidate)
    except VaultReadError:
        return f"[[{attendee_name}]]"
    return f"[[People/{attendee_name}]]"
