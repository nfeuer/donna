"""Unit tests for the SelfDiagnostic calendar-token check.

The probe itself (network refresh) is monkeypatched; these tests cover the
wiring: skip-when-unconfigured, missing-file warning, probe-error surfacing,
and pass-through of a healthy probe.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import donna.resilience.health_check as health_check
from donna.resilience.health_check import SelfDiagnostic


def _diagnostic(tmp_path: Path, token_path: Path | None) -> SelfDiagnostic:
    return SelfDiagnostic(
        tasks_db_path=tmp_path / "tasks.db",
        logs_db_path=tmp_path / "logs.db",
        calendar_token_path=token_path,
    )


@pytest.mark.asyncio
async def test_skipped_when_not_configured(tmp_path: Path) -> None:
    diag = _diagnostic(tmp_path, token_path=None)
    assert await diag._check_calendar_token() == []


@pytest.mark.asyncio
async def test_warns_when_token_file_missing(tmp_path: Path) -> None:
    diag = _diagnostic(tmp_path, token_path=tmp_path / "token.json")
    warnings = await diag._check_calendar_token()
    assert len(warnings) == 1
    assert "[calendar]" in warnings[0]
    assert "re-link" in warnings[0].lower()


@pytest.mark.asyncio
async def test_warns_when_probe_reports_dead_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"refresh_token": "rt"}))
    monkeypatch.setattr(
        health_check,
        "_probe_calendar_refresh",
        lambda _path: "Refresh token rejected (invalid_grant) — re-link required",
    )
    diag = _diagnostic(tmp_path, token_path=token)
    warnings = await diag._check_calendar_token()
    assert len(warnings) == 1
    assert "invalid_grant" in warnings[0]


@pytest.mark.asyncio
async def test_silent_when_probe_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"refresh_token": "rt"}))
    monkeypatch.setattr(health_check, "_probe_calendar_refresh", lambda _path: None)
    diag = _diagnostic(tmp_path, token_path=token)
    assert await diag._check_calendar_token() == []
