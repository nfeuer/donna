"""Gmail client for Donna.

Wraps google-api-python-client (Gmail v1) in async via asyncio.to_thread.
All operations are authenticated via OAuth2 with restricted scopes:
  - gmail.readonly: search and read emails
  - gmail.compose: create drafts (no send)

Sending is behind a feature flag (send_enabled in config/email.yaml).
If send_enabled=False, calling send_draft() raises RuntimeError.

Credentials are stored on disk at paths from EmailConfig.credentials.
On first run, a local browser flow is opened to obtain consent.

In tests, pass a pre-built ``service`` object to bypass OAuth2 (same
pattern as GoogleCalendarClient).

See docs/integrations.md and slices/slice_08_email_corrections.md.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import email as email_lib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any

import structlog

from donna.config import EmailConfig

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class EmailMessage:
    """Lightweight representation of a Gmail message."""

    id: str
    subject: str
    sender: str
    recipients: list[str]
    body_text: str
    snippet: str
    date: datetime


class GmailClient:
    """Async wrapper around the Gmail v1 API.

    Usage:
        client = GmailClient(config)
        await client.authenticate()
        msgs = await client.search_emails("from:boss@example.com is:unread")
        draft_id = await client.create_draft(to="nick@example.com", subject="Hi", body="...")

    The underlying google-api-python-client is synchronous; all blocking calls
    are run in a thread pool via asyncio.to_thread().

    In tests, pass a pre-built ``service`` object to bypass OAuth2.
    """

    def __init__(
        self,
        config: EmailConfig,
        service: Any | None = None,
    ) -> None:
        self._config = config
        self._service = service  # injected in tests; None until authenticate()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(self) -> None:
        """Load or refresh OAuth2 credentials and build the Gmail API service."""
        if self._service is not None:
            return  # already provided (test stub or re-authentication)

        def _build_service() -> Any:
            from pathlib import Path

            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build

            creds_cfg = self._config.credentials
            token_path = Path(creds_cfg.token_path)
            client_secrets = Path(creds_cfg.client_secrets_path)
            scopes = creds_cfg.scopes

            creds = None
            if token_path.exists():
                creds = Credentials.from_authorized_user_file(str(token_path), scopes)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(client_secrets), scopes
                    )
                    creds = flow.run_local_server(port=0)
                token_path.write_text(creds.to_json())

            return build("gmail", "v1", credentials=creds)

        self._service = await asyncio.to_thread(_build_service)
        logger.info("gmail_authenticated")

    @property
    def _svc(self) -> Any:
        if self._service is None:
            raise RuntimeError("Not authenticated. Call authenticate() first.")
        return self._service

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def search_emails(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[EmailMessage]:
        """Search the inbox and return matching messages.

        Args:
            query: Gmail search query (e.g. "from:boss@example.com is:unread").
            max_results: Maximum number of results to return.

        Returns:
            List of EmailMessage objects, newest first.
        """
        def _do_search() -> list[dict[str, Any]]:
            result = (
                self._svc.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
            return result.get("messages", [])

        stubs = await asyncio.to_thread(_do_search)
        logger.info("gmail_search", query=query, count=len(stubs))

        messages: list[EmailMessage] = []
        for stub in stubs:
            try:
                msg = await self.read_email(stub["id"])
                messages.append(msg)
            except Exception:
                logger.exception("gmail_read_failed", message_id=stub["id"])

        return messages

    async def read_email(self, message_id: str) -> EmailMessage:
        """Fetch and parse a single Gmail message by ID.

        Args:
            message_id: The Gmail message ID.

        Returns:
            Parsed EmailMessage.
        """
        def _do_fetch() -> dict[str, Any]:
            return (
                self._svc.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )

        raw = await asyncio.to_thread(_do_fetch)
        msg = _parse_message(raw)
        logger.info("gmail_read", message_id=message_id, subject=msg.subject)
        return msg

    async def get_message(self, *, message_id: str) -> EmailMessage:
        """Fetch and parse a single Gmail message by ID (keyword-arg form).

        Thin alias over ``read_email`` with a keyword-only signature, used by
        the gmail_get_message skill tool.

        Args:
            message_id: The Gmail message ID.

        Returns:
            Parsed EmailMessage.
        """
        return await self.read_email(message_id)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create_draft(self, to: str, subject: str, body: str) -> str:
        """Create a Gmail draft. Never sends without explicit approval.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            body: Plain-text email body.

        Returns:
            The created draft's ID.
        """
        mime = MIMEText(body, "plain")
        mime["to"] = to
        mime["subject"] = subject
        raw_bytes = base64.urlsafe_b64encode(mime.as_bytes()).decode()

        def _do_create() -> dict[str, Any]:
            return (
                self._svc.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": raw_bytes}})
                .execute()
            )

        result = await asyncio.to_thread(_do_create)
        draft_id: str = result["id"]
        logger.info("gmail_draft_created", draft_id=draft_id, to=to, subject=subject)
        return draft_id

    async def send_draft(self, draft_id: str) -> bool:
        """Send a previously created draft.

        Raises RuntimeError if send_enabled is False in config (the default).

        Args:
            draft_id: The draft ID returned by create_draft().

        Returns:
            True if sent successfully.
        """
        if not self._config.send_enabled:
            raise RuntimeError(
                "Email send is disabled. Set send_enabled=true in config/email.yaml "
                "to allow Donna to send emails directly."
            )

        def _do_send() -> dict[str, Any]:
            return (
                self._svc.users()
                .drafts()
                .send(userId="me", body={"id": draft_id})
                .execute()
            )

        result = await asyncio.to_thread(_do_send)
        message_id: str = result.get("id", "")
        logger.info("gmail_sent", draft_id=draft_id, message_id=message_id)
        return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _parse_message(raw: dict[str, Any]) -> EmailMessage:
    """Convert a raw Gmail API message dict to EmailMessage."""
    headers: dict[str, str] = {}
    for h in raw.get("payload", {}).get("headers", []):
        headers[h["name"].lower()] = h["value"]

    subject = headers.get("subject", "")
    sender = headers.get("from", "")
    to_raw = headers.get("to", "")
    recipients = [r.strip() for r in to_raw.split(",") if r.strip()]

    date_str = headers.get("date", "")
    date = _parse_date(date_str)

    body_text = _extract_body_text(raw.get("payload", {}))
    snippet = raw.get("snippet", "")

    return EmailMessage(
        id=raw["id"],
        subject=subject,
        sender=sender,
        recipients=recipients,
        body_text=body_text,
        snippet=snippet,
        date=date,
    )


def _extract_body_text(payload: dict[str, Any]) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""

    # Multipart: recurse into parts, prefer text/plain.
    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    # Fallback: any nested text.
    for part in parts:
        text = _extract_body_text(part)
        if text:
            return text

    return ""


def _parse_date(value: str) -> datetime:
    """Parse an RFC 2822 date string to UTC-aware datetime, or return epoch."""
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = email_lib.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)
