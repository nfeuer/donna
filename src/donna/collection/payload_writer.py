"""Fire-and-forget writer for LLM request/response payloads.

Writes JSON files to date-partitioned directories for forensic inspection.
Never raises on I/O failure — logs a warning and returns None instead.

Part of the Claude Inspector feature (§9 forensics tooling).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


class PayloadWriter:
    """Writes LLM request/response payloads as JSON to disk.

    Files are stored in date-partitioned directories:
        ``{base_dir}/{YYYY-MM-DD}/{invocation_id}.json``

    Each file contains::

        {"request": {...}, "response": {...}}

    The writer tracks cumulative bytes written via an in-memory counter.
    Use ``sync_size_from_disk()`` to reconcile with actual disk usage.

    Args:
        base_dir: Root directory for payload storage.
        max_bytes: Maximum total bytes allowed on disk (default 1 GiB).
    """

    def __init__(self, base_dir: Path, max_bytes: int = 1_073_741_824) -> None:
        self._base_dir = base_dir
        self._max_bytes = max_bytes
        self.current_bytes: int = 0

    @property
    def base_dir(self) -> Path:
        """Root directory for payload storage."""
        return self._base_dir

    @property
    def max_bytes(self) -> int:
        """Maximum total bytes allowed on disk."""
        return self._max_bytes

    async def write(
        self,
        invocation_id: str,
        request: dict[str, Any],
        response: dict[str, Any],
        *,
        for_date: date | None = None,
    ) -> str | None:
        """Write a payload to disk.

        Args:
            invocation_id: Unique identifier for this LLM invocation.
            request: The request payload sent to the LLM.
            response: The response payload received from the LLM.
            for_date: Date partition to use. Defaults to today.

        Returns:
            Relative path on success (``YYYY-MM-DD/invocation_id.json``),
            or None on failure.
        """
        target_date = for_date or date.today()
        date_dir = self._base_dir / target_date.isoformat()
        rel_path = f"{target_date.isoformat()}/{invocation_id}.json"

        if self.current_bytes >= self._max_bytes:
            logger.warning(
                "payload_writer.budget_exceeded",
                current_bytes=self.current_bytes,
                max_bytes=self._max_bytes,
            )
            return None

        payload = {"request": request, "response": response}

        try:
            date_dir.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload, separators=(",", ":"), default=str)
            file_path = date_dir / f"{invocation_id}.json"
            file_path.write_text(data, encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "payload_writer.write_failed",
                invocation_id=invocation_id,
                error=str(exc),
            )
            return None

        written_bytes = len(data.encode("utf-8"))
        self.current_bytes += written_bytes
        logger.debug(
            "payload_writer.wrote",
            invocation_id=invocation_id,
            bytes=written_bytes,
            rel_path=rel_path,
        )
        return rel_path

    async def sync_size_from_disk(self) -> int:
        """Walk the base directory and recalculate actual disk usage.

        Returns:
            Total bytes found on disk.
        """
        total = 0
        try:
            if self._base_dir.exists():
                for file in self._base_dir.rglob("*.json"):
                    try:
                        total += file.stat().st_size
                    except OSError:
                        continue
        except OSError as exc:
            logger.warning(
                "payload_writer.sync_failed",
                error=str(exc),
            )
        self.current_bytes = total
        return total
