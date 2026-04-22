"""Google Calendar client for Donna.

Wraps google-api-python-client in async via asyncio.to_thread.
All Donna-created events carry extended properties:
  extendedProperties.private.donnaManaged = "true"
  extendedProperties.private.donnaTaskId  = "<task-uuid>"

OAuth2 tokens are stored on disk at the path from CalendarConfig.
On first run, a local browser flow is opened to obtain consent.

See docs/scheduling.md and slices/slice_04_calendar.md.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime
from typing import Any, cast

import structlog

from donna.config import CalendarConfig

logger = structlog.get_logger()

# Extended property key names — these are the contract between Donna and Calendar.
_PROP_MANAGED = "donnaManaged"
_PROP_TASK_ID = "donnaTaskId"


@dataclasses.dataclass(frozen=True)
class CalendarEvent:
    """Lightweight representation of a Google Calendar event."""

    event_id: str
    calendar_id: str
    summary: str
    start: datetime
    end: datetime
    donna_managed: bool
    donna_task_id: str | None
    etag: str


def _parse_event(raw: dict[str, Any], calendar_id: str) -> CalendarEvent:
    """Convert a raw Google Calendar API event dict to CalendarEvent."""
    private = raw.get("extendedProperties", {}).get("private", {})
    donna_managed = private.get(_PROP_MANAGED, "false").lower() == "true"
    donna_task_id = private.get(_PROP_TASK_ID)

    # Events can have dateTime (timed) or date (all-day) starts.
    start_raw = raw.get("start", {})
    end_raw = raw.get("end", {})

    start_str = start_raw.get("dateTime") or start_raw.get("date", "")
    end_str = end_raw.get("dateTime") or end_raw.get("date", "")

    start = _parse_dt(start_str)
    end = _parse_dt(end_str)

    return CalendarEvent(
        event_id=raw["id"],
        calendar_id=calendar_id,
        summary=raw.get("summary", ""),
        start=start,
        end=end,
        donna_managed=donna_managed,
        donna_task_id=donna_task_id,
        etag=raw.get("etag", ""),
    )


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 datetime or date string to a UTC-aware datetime."""
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    # All-day events use "YYYY-MM-DD" format.
    if "T" not in value and len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class GoogleCalendarClient:
    """Async wrapper around the Google Calendar v3 API.

    Usage:
        client = GoogleCalendarClient(config)
        await client.authenticate()
        events = await client.list_events("primary", start, end)

    The underlying google-api-python-client is synchronous; all blocking calls
    are run in a thread pool via asyncio.to_thread().

    In tests, pass a pre-built ``service`` object to bypass OAuth2.
    """

    def __init__(
        self,
        config: CalendarConfig,
        service: Any | None = None,
    ) -> None:
        self._config = config
        self._service = service  # injected in tests; None until authenticate()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(self) -> None:
        """Load or refresh OAuth2 credentials and build the API service."""
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
                creds = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                    str(token_path), scopes
                )

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(client_secrets), scopes
                    )
                    creds = flow.run_local_server(port=0)
                token_path.write_text(creds.to_json())

            return build("calendar", "v3", credentials=creds)

        self._service = await asyncio.to_thread(_build_service)
        logger.info("calendar_authenticated")

    @property
    def _svc(self) -> Any:
        if self._service is None:
            raise RuntimeError("Not authenticated. Call authenticate() first.")
        return self._service

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_events(
        self,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[CalendarEvent]:
        """Return all events from *calendar_id* in [time_min, time_max].

        Uses pagination to fetch all pages. Cancelled/deleted events are
        excluded (showDeleted=False).
        """
        time_min_str = _to_rfc3339(time_min)
        time_max_str = _to_rfc3339(time_max)

        def _fetch() -> list[dict[str, Any]]:
            svc = self._svc
            results: list[dict[str, Any]] = []
            page_token: str | None = None
            while True:
                kwargs: dict[str, Any] = dict(
                    calendarId=calendar_id,
                    timeMin=time_min_str,
                    timeMax=time_max_str,
                    singleEvents=True,
                    orderBy="startTime",
                    showDeleted=False,
                )
                if page_token:
                    kwargs["pageToken"] = page_token
                response = svc.events().list(**kwargs).execute()
                results.extend(response.get("items", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
            return results

        raw_events = await asyncio.to_thread(_fetch)
        events = [_parse_event(e, calendar_id) for e in raw_events]

        logger.info(
            "calendar_list_events",
            calendar_id=calendar_id,
            count=len(events),
            time_min=time_min_str,
            time_max=time_max_str,
        )
        return events

    # ------------------------------------------------------------------
    # Write operations (personal calendar only)
    # ------------------------------------------------------------------

    async def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: datetime,
        end: datetime,
        task_id: str,
    ) -> CalendarEvent:
        """Create a Donna-managed event and return its CalendarEvent representation."""
        body = {
            "summary": summary,
            "start": {"dateTime": _to_rfc3339(start), "timeZone": "UTC"},
            "end": {"dateTime": _to_rfc3339(end), "timeZone": "UTC"},
            "extendedProperties": {
                "private": {
                    _PROP_MANAGED: "true",
                    _PROP_TASK_ID: task_id,
                }
            },
        }

        def _create() -> dict[str, Any]:
            return cast(
                dict[str, Any],
                self._svc.events().insert(calendarId=calendar_id, body=body).execute(),
            )

        raw = await asyncio.to_thread(_create)
        event = _parse_event(raw, calendar_id)

        logger.info(
            "calendar_event_created",
            event_id=event.event_id,
            task_id=task_id,
            calendar_id=calendar_id,
            start=_to_rfc3339(start),
        )
        return event

    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        start: datetime,
        end: datetime,
    ) -> CalendarEvent:
        """Update the start/end time of an existing event."""
        body = {
            "start": {"dateTime": _to_rfc3339(start), "timeZone": "UTC"},
            "end": {"dateTime": _to_rfc3339(end), "timeZone": "UTC"},
        }

        def _update() -> dict[str, Any]:
            return cast(
                dict[str, Any],
                self._svc.events()
                .patch(calendarId=calendar_id, eventId=event_id, body=body)
                .execute(),
            )

        raw = await asyncio.to_thread(_update)
        event = _parse_event(raw, calendar_id)

        logger.info(
            "calendar_event_updated",
            event_id=event_id,
            calendar_id=calendar_id,
            new_start=_to_rfc3339(start),
        )
        return event

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete an event from the calendar."""

        def _delete() -> None:
            self._svc.events().delete(calendarId=calendar_id, eventId=event_id).execute()

        await asyncio.to_thread(_delete)

        logger.info(
            "calendar_event_deleted",
            event_id=event_id,
            calendar_id=calendar_id,
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _to_rfc3339(dt: datetime) -> str:
    """Format a datetime as RFC 3339 (Google Calendar API requirement)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()
