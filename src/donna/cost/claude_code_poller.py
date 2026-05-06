"""Background poller that ingests submitted claude_code branches.

One coroutine per process. Mirrors the polling pattern of
:class:`donna.notifications.escalation_delivery_loop.EscalationDeliveryLoop`
(60 s tick, awaitable ``run`` entry, ``tick_once`` exposed for tests).

Each tick:

1. Pulls ``escalation_request`` rows where ``mode='claude_code' AND
   status='submitted'`` (slice 19's submit endpoint is the only writer
   that produces this state).
2. Verifies the branch exists in the host repo. If not: posts a
   "branch not found" hint via the Discord feedback callback and
   leaves the row as ``submitted`` so a later push triggers
   re-ingestion (no status change, no iteration burn).
3. If a SHA was supplied at submit time, compares it to the current
   branch tip and rejects with ``status='failed'`` on mismatch
   (spec §10.3 row 4 — force-push between submit and validation).
4. Computes the diff against the row's pinned ``base_sha`` (or the
   configured base_ref as a fallback).
5. Runs :class:`DiffValidator` against the row's snapshotted
   ``target_paths``.
6. Hands off to :class:`ManualValidationRouter` for the skill path.
7. Writes the ValidationOutcome JSON onto the row, transitions
   status to ``validated`` or ``failed``, fires the appropriate
   audit event.

A separate sweeper run inside the same tick promotes
``failed AND iteration >= manual_iteration_limit`` rows to
``cancelled`` with ``human_review=1`` and posts a Discord notice
(spec §10.4 row 2).

Realizes docs/superpowers/specs/manual-escalation.md §5.3, §10.3,
§10.4 rows 1–2, §10.10.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from donna.cost.diff_validator import DiffValidator
from donna.cost.escalation_audit import write_escalation_event
from donna.cost.escalation_repository import (
    EscalationRepository,
    EscalationRequestRow,
)
from donna.cost.manual_validation_router import (
    ManualValidationRouter,
    ValidationOutcome,
)
from donna.integrations.git_repo import GitRepo

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()

DEFAULT_TICK_SECONDS = 60
DEFAULT_BASE_REF = "main"
DEFAULT_MANUAL_ITERATION_LIMIT = 3

# Audit event names — keep aligned with spec §10.10 wording.
EVENT_VALIDATED = "escalation_validated"
EVENT_FAILED = "escalation_failed"
EVENT_ITERATION_LIMIT_REACHED = "iteration_limit_reached"
EVENT_BRANCH_NOT_FOUND = "escalation_branch_not_found"


# Discord feedback callback contract: post a short message bound to
# this escalation. Signature accepts the row and the message body.
# Optional — tests may pass None; production cli_wiring binds it to
# the bot.
FeedbackCallback = Callable[[EscalationRequestRow, str], Awaitable[None]]


@dataclasses.dataclass(frozen=True)
class PollerStats:
    """Summary returned by :meth:`ClaudeCodePoller.tick_once`."""

    processed: int
    validated: int
    failed: int
    branch_missing: int
    iteration_capped: int


class ClaudeCodePoller:
    """Validates submitted claude_code branches and routes the outcome."""

    def __init__(
        self,
        *,
        repository: EscalationRepository,
        host_repo: GitRepo | None,
        validation_router: ManualValidationRouter | None,
        base_ref: str = DEFAULT_BASE_REF,
        feedback: FeedbackCallback | None = None,
        manual_iteration_limit: int = DEFAULT_MANUAL_ITERATION_LIMIT,
        feedback_max_failing_cases: int = 3,
        dashboard_base_url: str = "",
        tick_seconds: int = DEFAULT_TICK_SECONDS,
    ) -> None:
        self._repo = repository
        self._host_repo = host_repo
        self._router = validation_router
        self._base_ref = base_ref
        self._feedback = feedback
        self._manual_iteration_limit = manual_iteration_limit
        self._feedback_max = feedback_max_failing_cases
        self._dashboard_base_url = dashboard_base_url.rstrip("/")
        self._tick_seconds = tick_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Background entrypoint — schedule from server.run_server()."""
        if self._host_repo is None or self._router is None:
            # Fail-soft: if claude_code mode is misconfigured (no host
            # repo mount, no router) we don't crash the orchestrator —
            # the gate would already have refused to offer the button.
            logger.info(
                "claude_code_poller_inactive_no_dependencies",
                has_host_repo=self._host_repo is not None,
                has_router=self._router is not None,
            )
            return
        logger.info(
            "claude_code_poller_started",
            tick_seconds=self._tick_seconds,
            base_ref=self._base_ref,
        )
        while True:
            try:
                await self.tick_once()
            except Exception:
                logger.exception("claude_code_poller_tick_failed")
            await asyncio.sleep(self._tick_seconds)

    async def tick_once(
        self, *, now: datetime | None = None
    ) -> PollerStats:
        """One ingestion + iteration-cap pass. Exposed for tests."""
        ts = now or datetime.now(tz=UTC)
        rows = await self._repo.list_submitted_claude_code()
        validated = failed = branch_missing = 0
        for row in rows:
            outcome = await self._process_row(row, now=ts)
            if outcome == "validated":
                validated += 1
            elif outcome == "failed":
                failed += 1
            elif outcome == "branch_missing":
                branch_missing += 1

        # Iteration-cap sweep — promote failed rows at cap to cancelled.
        capped_rows = await self._repo.list_failed_at_iteration_cap(
            manual_iteration_limit=self._manual_iteration_limit
        )
        iteration_capped = 0
        for row in capped_rows:
            await self._cancel_at_cap(row, now=ts)
            iteration_capped += 1

        return PollerStats(
            processed=len(rows),
            validated=validated,
            failed=failed,
            branch_missing=branch_missing,
            iteration_capped=iteration_capped,
        )

    # ------------------------------------------------------------------
    # Per-row processing
    # ------------------------------------------------------------------

    async def _process_row(
        self,
        row: EscalationRequestRow,
        *,
        now: datetime,
    ) -> str:
        assert self._host_repo is not None
        assert self._router is not None

        # Parse the submitted result payload to extract branch + sha.
        # Slice 19's submit endpoint stores the full payload as JSON in
        # `result`. We re-read it here rather than trust ``branch_name``
        # alone because the payload also carries the optional sha.
        branch = row.branch_name
        if branch is None:
            logger.warning(
                "claude_code_poller_missing_branch",
                correlation_id=row.correlation_id,
            )
            await self._mark_failed_with_reason(
                row, reason="branch_name not set on submitted row", now=now
            )
            return "failed"
        submitted_sha = _extract_sha(row)

        # Step 1: branch existence.
        try:
            exists = await self._host_repo.branch_exists(branch)
        except Exception:
            logger.exception(
                "claude_code_poller_branch_check_failed",
                correlation_id=row.correlation_id,
                branch=branch,
            )
            return "branch_missing"
        if not exists:
            logger.info(
                "claude_code_branch_not_found",
                correlation_id=row.correlation_id,
                branch=branch,
            )
            await write_escalation_event(
                self._repo._conn,
                event=EVENT_BRANCH_NOT_FOUND,
                escalation_request_id=row.id,
                correlation_id=row.correlation_id,
                user_id=row.user_id,
                task_id=row.task_id,
                payload={"branch": branch},
                now=now,
            )
            await self._post_feedback(
                row,
                f"Branch `{branch}` not found in host repo. Did you push? "
                f"You can also run `/donna submit {row.correlation_id} "
                f"--branch {branch}` once the branch is pushed.",
            )
            return "branch_missing"

        # Step 2: SHA pin (force-push protection — spec §10.3 row 4).
        if submitted_sha is not None:
            current_sha = await self._host_repo.rev_parse(f"refs/heads/{branch}")
            if current_sha is None or not current_sha.startswith(submitted_sha):
                outcome = ValidationOutcome(
                    passed=False, skill_id=None, pass_rate=None,
                    matched_files=[],
                    failures=[],
                    reason=(
                        f"branch SHA changed since submission: submitted "
                        f"{submitted_sha!r}, current {current_sha!r} — "
                        "resubmit to validate the new tip"
                    ),
                )
                await self._record_failure(row, outcome, now=now)
                return "failed"

        # Step 3: scope check.
        base_ref = row.base_sha or self._base_ref
        diff_paths = await self._host_repo.diff_names(base_ref, branch)
        if row.target_paths is None:
            # Defensive — shouldn't happen for claude_code rows.
            outcome = ValidationOutcome(
                passed=False, skill_id=None, pass_rate=None,
                matched_files=[], failures=[],
                reason="row missing target_paths snapshot",
            )
            await self._record_failure(row, outcome, now=now)
            return "failed"
        scope = DiffValidator.validate(diff_paths, row.target_paths)
        if not scope.ok:
            outcome = ValidationOutcome(
                passed=False, skill_id=None, pass_rate=None,
                matched_files=scope.matched,
                failures=[
                    {"case_name": "(scope)", "reason": p}
                    for p in scope.out_of_scope
                ],
                reason=(
                    f"branch touched {len(scope.out_of_scope)} file(s) "
                    "outside declared scope"
                ),
            )
            await self._record_failure(row, outcome, now=now)
            return "failed"

        # Step 4: validation.
        try:
            outcome = await self._router.validate(
                row,
                branch=branch,
                diff_paths=scope.matched,
                actor_id=row.resolved_by,  # discord id from button click
            )
        except Exception as exc:
            logger.exception(
                "claude_code_poller_validation_raised",
                correlation_id=row.correlation_id,
            )
            outcome = ValidationOutcome(
                passed=False, skill_id=None, pass_rate=None,
                matched_files=scope.matched, failures=[],
                reason=f"validation raised: {exc}",
            )
            await self._record_failure(row, outcome, now=now)
            return "failed"

        if outcome.passed:
            await self._record_success(row, outcome, now=now)
            return "validated"
        await self._record_failure(row, outcome, now=now)
        return "failed"

    async def _record_success(
        self,
        row: EscalationRequestRow,
        outcome: ValidationOutcome,
        *,
        now: datetime,
    ) -> None:
        await self._repo.mark_validated(
            row.id, validation_result=outcome.to_payload(), now=now
        )
        await write_escalation_event(
            self._repo._conn,
            event=EVENT_VALIDATED,
            escalation_request_id=row.id,
            correlation_id=row.correlation_id,
            user_id=row.user_id,
            task_id=row.task_id,
            payload={
                "skill_id": outcome.skill_id,
                "pass_rate": outcome.pass_rate,
                "branch": row.branch_name,
                "iteration": row.iteration,
            },
            now=now,
        )
        await self._post_feedback(
            row,
            (
                f"Validated `{row.branch_name}`. Skill `{outcome.skill_id}` is "
                f"in sandbox (pass rate {outcome.pass_rate:.0%}). "
                f"Merge into main when ready: "
                f"`git checkout main && git merge --no-ff {row.branch_name}`. "
                f"{self._dashboard_link(row)}"
            ),
        )

    async def _record_failure(
        self,
        row: EscalationRequestRow,
        outcome: ValidationOutcome,
        *,
        now: datetime,
    ) -> None:
        # The iteration cap check is at submit-time (slice 19); the
        # poller marks the row failed regardless of iteration count and
        # the separate cap-sweep cancels rows that have hit the limit.
        await self._repo.mark_failed(
            row.id, validation_result=outcome.to_payload(), now=now
        )
        await write_escalation_event(
            self._repo._conn,
            event=EVENT_FAILED,
            escalation_request_id=row.id,
            correlation_id=row.correlation_id,
            user_id=row.user_id,
            task_id=row.task_id,
            payload={
                "skill_id": outcome.skill_id,
                "pass_rate": outcome.pass_rate,
                "reason": outcome.reason,
                "branch": row.branch_name,
                "iteration": row.iteration,
                "out_of_scope_count": sum(
                    1 for f in outcome.failures if f.get("case_name") == "(scope)"
                ),
            },
            now=now,
        )
        await self._post_feedback(row, self._format_failure_message(row, outcome))

    async def _mark_failed_with_reason(
        self, row: EscalationRequestRow, *, reason: str, now: datetime
    ) -> None:
        outcome = ValidationOutcome(
            passed=False, skill_id=None, pass_rate=None,
            matched_files=[], failures=[], reason=reason,
        )
        await self._record_failure(row, outcome, now=now)

    async def _cancel_at_cap(
        self, row: EscalationRequestRow, *, now: datetime
    ) -> None:
        ok = await self._repo.mark_iteration_cap_reached(row.id, now=now)
        if not ok:
            return
        await write_escalation_event(
            self._repo._conn,
            event=EVENT_ITERATION_LIMIT_REACHED,
            escalation_request_id=row.id,
            correlation_id=row.correlation_id,
            user_id=row.user_id,
            task_id=row.task_id,
            payload={
                "iteration": row.iteration,
                "limit": self._manual_iteration_limit,
            },
            now=now,
        )
        await self._post_feedback(
            row,
            (
                f"Iteration cap ({self._manual_iteration_limit}) reached on "
                f"escalation `{row.correlation_id}`. Routing to human review. "
                f"{self._dashboard_link(row)}"
            ),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _post_feedback(
        self, row: EscalationRequestRow, message: str
    ) -> None:
        if self._feedback is None:
            return
        try:
            await self._feedback(row, message)
        except Exception:
            logger.exception(
                "claude_code_feedback_failed",
                correlation_id=row.correlation_id,
            )

    def _format_failure_message(
        self,
        row: EscalationRequestRow,
        outcome: ValidationOutcome,
    ) -> str:
        head = f"Manual build failed on `{row.branch_name}`."
        if outcome.reason:
            head += f"\nReason: {outcome.reason}"
        if outcome.pass_rate is not None:
            head += f"\nPass rate: {outcome.pass_rate:.0%}"
        if outcome.failures:
            shown = outcome.failures[: self._feedback_max]
            cases = "\n".join(
                f"  • `{f.get('case_name', '?')}`: {f.get('reason', '?')}"
                for f in shown
            )
            head += f"\nFailures ({len(outcome.failures)} total):\n{cases}"
            if len(outcome.failures) > self._feedback_max:
                head += (
                    f"\n  …and {len(outcome.failures) - self._feedback_max} "
                    "more — see dashboard for full list."
                )
        head += f"\nIteration {row.iteration} / {self._manual_iteration_limit}. "
        head += self._dashboard_link(row)
        return head

    def _dashboard_link(self, row: EscalationRequestRow) -> str:
        if not self._dashboard_base_url:
            return ""
        return f"{self._dashboard_base_url}/escalations/{row.correlation_id}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _extract_sha(row: EscalationRequestRow) -> str | None:
    """Pull the optional sha out of the submission ``result`` JSON.

    The submit endpoint persists the full payload (mode/branch/sha) as
    a JSON string in ``escalation_request.result``; the repo decodes
    it onto ``submitted_payload`` for the row dataclass.
    """
    payload = row.submitted_payload
    if not isinstance(payload, dict):
        return None
    sha = payload.get("sha")
    return str(sha) if sha else None
