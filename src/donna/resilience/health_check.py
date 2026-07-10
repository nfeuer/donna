"""Layer 3: Daily self-diagnostic health checks.

Run before morning digest generation. Checks DB integrity, disk space,
sync timestamps, pending migrations, and budget status.

See docs/resilience.md — Layer 3: Daily Self-Diagnostic.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
import structlog

if TYPE_CHECKING:
    from donna.cost.tracker import CostTracker

logger = structlog.get_logger()

_DISK_WARN_THRESHOLD = 0.20  # warn when < 20% free
_SUPABASE_STALE_SECONDS = 3600  # warn if last sync > 1 hour ago


class SelfDiagnostic:
    """Run pre-digest health checks on the Donna system.

    Args:
        tasks_db_path: Path to donna_tasks.db
        logs_db_path: Path to donna_logs.db
        donna_mount: Mount point to check disk space on (default: /donna)
        cost_tracker: Optional CostTracker for budget status check.
        last_supabase_sync_path: Path to a file whose mtime records last sync.
        notify: Optional async callback that receives a plain-text/markdown
            summary of detected issues.  Called only when warnings exist.
        calendar_token_path: Path to the Google Calendar ``token.json``; when
            set, the diagnostic live-probes the refresh token so a dead token
            (``invalid_grant``) is reported with remediation steps.
    """

    def __init__(
        self,
        tasks_db_path: Path,
        logs_db_path: Path,
        donna_mount: Path = Path("/donna"),
        cost_tracker: CostTracker | None = None,
        last_supabase_sync_path: Path | None = None,
        notify: Callable[[str], Awaitable[None]] | None = None,
        calendar_token_path: Path | None = None,
    ) -> None:
        self._tasks_db = tasks_db_path
        self._logs_db = logs_db_path
        self._donna_mount = donna_mount
        self._cost_tracker = cost_tracker
        self._last_supabase_sync_path = last_supabase_sync_path
        self._notify = notify
        self._calendar_token_path = calendar_token_path

    async def run(self) -> list[str]:
        """Run all checks and return a list of warning strings.

        Returns an empty list when all checks pass.
        """
        warnings: list[str] = []

        warnings.extend(await self._check_db_integrity(self._tasks_db, "donna_tasks"))
        warnings.extend(await self._check_db_integrity(self._logs_db, "donna_logs"))
        warnings.extend(self._check_disk_space())
        warnings.extend(self._check_supabase_sync())
        warnings.extend(await self._check_budget())
        warnings.extend(await self._check_ollama())
        warnings.extend(await self._check_calendar_token())

        if warnings:
            logger.warning(
                "self_diagnostic_issues_found",
                event_type="system.health_check",
                issue_count=len(warnings),
            )
        else:
            logger.info("self_diagnostic_all_clear", event_type="system.health_check")

        if warnings and self._notify is not None:
            summary = "\n".join(f"- {w}" for w in warnings)
            try:
                await self._notify(
                    f"**Health Check — {len(warnings)} issue(s) detected at boot:**\n{summary}"
                )
            except Exception:
                logger.warning("health_check_notify_failed", exc_info=True)

        return warnings

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    async def _check_db_integrity(self, db_path: Path, label: str) -> list[str]:
        if not db_path.exists():
            return [f"[{label}] Database file not found: {db_path}"]
        try:
            async with aiosqlite.connect(str(db_path)) as conn:
                cursor = await conn.execute("PRAGMA integrity_check")
                rows = list(await cursor.fetchall())
                if rows and rows[0][0] != "ok":
                    detail = "; ".join(r[0] for r in rows[:3])
                    return [f"[{label}] Integrity check failed: {detail}"]
        except Exception as exc:
            return [f"[{label}] Integrity check error: {exc}"]
        return []

    def _check_disk_space(self) -> list[str]:
        mount = self._donna_mount
        if not mount.exists():
            # Fall back to root if /donna not mounted (dev environment)
            mount = Path("/")
        try:
            usage = shutil.disk_usage(str(mount))
            free_ratio = usage.free / usage.total
            if free_ratio < _DISK_WARN_THRESHOLD:
                free_pct = free_ratio * 100
                total_gb = usage.total / (1024**3)
                free_gb = usage.free / (1024**3)
                return [
                    f"[disk] Low disk space on {mount}: "
                    f"{free_gb:.1f}GB free of {total_gb:.1f}GB ({free_pct:.1f}% free)"
                ]
        except OSError as exc:
            return [f"[disk] Could not check disk usage: {exc}"]
        return []

    def _check_supabase_sync(self) -> list[str]:
        import time

        sync_path = self._last_supabase_sync_path
        if sync_path is None:
            return []
        if not sync_path.exists():
            return ["[supabase] No sync timestamp found — Supabase sync may never have run"]
        try:
            age_s = time.time() - sync_path.stat().st_mtime
            if age_s > _SUPABASE_STALE_SECONDS:
                age_min = int(age_s / 60)
                return [f"[supabase] Last sync was {age_min} minutes ago (threshold: 60 min)"]
        except OSError:
            pass
        return []

    async def _check_ollama(self) -> list[str]:
        """Check Ollama connectivity and required model availability.

        Only runs when DONNA_OLLAMA_URL is set (indicating local LLM is expected).
        """
        ollama_url = os.environ.get("DONNA_OLLAMA_URL")
        if not ollama_url:
            return []

        try:
            from donna.models.providers.ollama import OllamaProvider

            provider = OllamaProvider(base_url=ollama_url, timeout_s=10)
            try:
                healthy = await provider.health()
                if not healthy:
                    return [f"[ollama] Server at {ollama_url} is not responding"]

                required_model = os.environ.get("DONNA_OLLAMA_MODEL")
                if required_model:
                    models = await provider.list_models()
                    if required_model not in models:
                        return [
                            f"[ollama] Required model {required_model!r} not found. "
                            f"Available: {', '.join(models) or 'none'}"
                        ]
            finally:
                await provider.close()
        except ImportError:
            return ["[ollama] OllamaProvider not available (missing aiohttp?)"]
        except Exception as exc:
            return [f"[ollama] Health check error: {exc}"]

        return []

    async def _check_calendar_token(self) -> list[str]:
        """Check that the Google Calendar refresh token is alive.

        Skipped when no ``calendar_token_path`` was configured. Performs one
        live token refresh against Google's token endpoint (no Calendar API
        call, nothing persisted) so a dead refresh token — the classic
        ``invalid_grant`` from a Testing-status OAuth app — is surfaced here
        instead of silently disabling scheduling features.
        """
        token_path = self._calendar_token_path
        if token_path is None:
            return []
        if not token_path.exists():
            return [
                f"[calendar] Token file not found: {token_path} — calendar "
                "features are offline. Re-link with "
                "'python -m donna.integrations.calendar <config_dir>' "
                "(see docs/operations/calendar-oauth.md)."
            ]
        try:
            error = await asyncio.to_thread(_probe_calendar_refresh, token_path)
        except Exception as exc:
            return [f"[calendar] Token probe error: {exc}"]
        if error:
            return [f"[calendar] {error}"]
        return []

    async def _check_budget(self) -> list[str]:
        if self._cost_tracker is None:
            return []
        try:
            summary = await self._cost_tracker.get_monthly_cost()
            budget = float(os.environ.get("DONNA_MONTHLY_BUDGET_USD", "100"))
            pause = float(os.environ.get("DONNA_DAILY_PAUSE_THRESHOLD_USD", "20"))
            warnings: list[str] = []
            if summary.total_usd >= budget * 0.9:
                warnings.append(
                    f"[budget] Monthly spend ${summary.total_usd:.2f} is ≥90% of "
                    f"${budget:.2f} budget"
                )
            daily = await self._cost_tracker.get_daily_cost()
            if daily.total_usd >= pause:
                warnings.append(
                    f"[budget] Daily spend ${daily.total_usd:.2f} has reached "
                    f"the ${pause:.2f} pause threshold"
                )
            return warnings
        except Exception as exc:
            return [f"[budget] Could not check budget: {exc}"]


def _probe_calendar_refresh(token_path: Path) -> str | None:
    """Attempt one OAuth token refresh; return a warning string if it fails.

    Runs synchronously (google-auth is sync) — call via ``asyncio.to_thread``.
    Refreshes in memory only; the token file is never rewritten here, so the
    probe cannot race ``GoogleCalendarClient.authenticate()``.

    Args:
        token_path: Path to the authorized-user ``token.json``.

    Returns:
        ``None`` when the refresh token is alive, otherwise an actionable
        warning string (without the ``[calendar]`` prefix).
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        creds = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
            str(token_path)
        )
    except ValueError as exc:
        return f"Token file unreadable ({exc}) — re-link required"
    if not creds.refresh_token:
        return "Token file has no refresh_token — re-link required"
    try:
        creds.refresh(Request())  # type: ignore[no-untyped-call]
    except RefreshError as exc:
        detail = exc.args[0] if exc.args else "refresh rejected"
        return (
            f"Refresh token rejected ({detail}) — most often the OAuth consent "
            "screen is in 'Testing' status (refresh tokens expire after 7 days). "
            "Publish the OAuth app to Production and re-link; see "
            "docs/operations/calendar-oauth.md."
        )
    return None
