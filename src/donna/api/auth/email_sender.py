"""Magic-link email sender. Uses the project's Gmail integration."""

from __future__ import annotations

from typing import Any


async def send_magic_link(
    gmail: Any,
    *,
    to: str,
    token: str,
    verify_base_url: str,
    from_name: str = "Donna",
) -> None:
    """Compose and send a magic-link email.

    Uses the Gmail integration's create_draft + send_draft pattern so it
    respects the existing `email.yaml` `send_enabled` config gate.
    """
    verify_url = f"{verify_base_url}?token={token}"
    subject = f"{from_name} — access verification"
    body = (
        f"You requested access to Donna from a new device or network.\n\n"
        f"Click this link within 15 minutes to verify:\n\n"
        f"    {verify_url}\n\n"
        f"If you did not request this, ignore this email. The link will "
        f"expire automatically, and you can revoke any trusted IP at "
        f"https://donna.houseoffeuer.com/admin/access.\n"
    )
    draft_id = await gmail.create_draft(to=to, subject=subject, body=body)
    await gmail.send_draft(draft_id)
