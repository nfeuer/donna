"""Validate a manually-built skill branch and route it through lifecycle.

Slice 21 ships only the **skill** path (``skill_auto_draft`` /
``skill_evolution``). Tool builds reuse the same protocol but add lint
gates — slice 22 will plug those in here. The tool branch raises
``NotImplementedError`` until then.

Flow per row (called from :class:`donna.cost.claude_code_poller.ClaudeCodePoller`):

1. Read skill files from the branch via ``GitRepo.show_file`` —
   read-only access, never checks the branch out into the host repo.
2. Persist a fresh ``skill`` + ``skill_version`` row in
   ``state='claude_native'`` (mirrors :func:`donna.skills.auto_drafter.AutoDrafter._persist_draft`).
3. Persist fixture rows from the branch (one per case file).
4. Run :func:`donna.skills.fixtures.validate_against_fixtures` against
   a :class:`ValidationExecutor` (mocked tool registry, real local
   LLM) — same shape AutoDrafter uses.
5. On pass: hop ``claude_native → skill_candidate → draft → sandbox``.
   The last hop carries ``reason='human_approval'`` because the
   user's "Mark as built" click *is* the human approval signal.
6. On fail: leave skill in ``claude_native`` (untrusted) and return a
   :class:`ValidationOutcome` with the failure detail. The poller
   surfaces it in Discord and on the dashboard.

Realizes docs/superpowers/specs/manual-escalation.md §5.3
(claude_code mode), §10.4 row 1 (validation failure → iterate),
§10.10 (audit events ``escalation_validated`` / ``escalation_failed``).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import Any, Protocol

import aiosqlite
import structlog
import uuid6
import yaml

from donna.cost.escalation_repository import EscalationRequestRow
from donna.integrations.git_repo import GitRepo, GitRepoError
from donna.skills.fixtures import (
    Fixture,
    FixtureValidationReport,
    validate_against_fixtures,
)
from donna.skills.lifecycle import (
    IllegalTransitionError,
    SkillLifecycleManager,
)
from donna.skills.models import SkillRow, SkillVersionRow
from donna.tasks.db_models import SkillState

logger = structlog.get_logger()


# Mirrors AutoDrafter (auto_drafter.py:226). The threshold is shared —
# manual mode reuses the same "is the skill good enough" bar.
DEFAULT_FIXTURE_PASS_RATE = 0.8


@dataclasses.dataclass(frozen=True)
class ValidationOutcome:
    """Slice 21 result of validating a manually-submitted branch.

    The poller writes this into ``escalation_request.validation_result``
    as JSON via :meth:`to_payload`.
    """

    passed: bool
    skill_id: str | None
    pass_rate: float | None
    matched_files: list[str]
    failures: list[dict[str, str]]
    reason: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "skill_id": self.skill_id,
            "pass_rate": self.pass_rate,
            "matched_files": list(self.matched_files),
            "failures": list(self.failures),
            "reason": self.reason,
        }


class ExecutorFactory(Protocol):
    """Returns a fresh :class:`donna.skills.validation_executor.ValidationExecutor`."""

    def __call__(self) -> Any: ...  # pragma: no cover


class ManualValidationRouter:
    """Validates a manual claude_code submission for skill task types."""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        host_repo: GitRepo,
        executor_factory: ExecutorFactory,
        lifecycle: SkillLifecycleManager,
        fixture_pass_rate: float = DEFAULT_FIXTURE_PASS_RATE,
    ) -> None:
        self._conn = conn
        self._host_repo = host_repo
        self._executor_factory = executor_factory
        self._lifecycle = lifecycle
        self._fixture_pass_rate = fixture_pass_rate

    async def validate(
        self,
        row: EscalationRequestRow,
        *,
        branch: str,
        diff_paths: list[str],
        actor_id: str | None = None,
    ) -> ValidationOutcome:
        """Run the skill validation pipeline against the branch.

        Args:
            row: The submitted escalation_request row.
            branch: Branch ref (e.g. ``escalation/01923456-foo``).
            diff_paths: Already scope-validated paths the user touched.
            actor_id: Optional Discord ID of the user; used as the
                ``actor_id`` on the human_approval lifecycle transition.

        Returns:
            :class:`ValidationOutcome`. ``passed=True`` ⇒ skill is in
            ``sandbox``. ``passed=False`` ⇒ skill stays in
            ``claude_native`` (or wasn't created at all if
            ``reason`` is set).
        """
        # The task_type → entity_type mapping is hard-coded for slice
        # 21; slice 22 (tools) extends this dispatch.
        if row.task_type in ("skill_auto_draft", "skill_evolution"):
            return await self._validate_skill(row, branch, diff_paths, actor_id)
        raise NotImplementedError(
            f"manual validation for task_type={row.task_type!r} is not implemented "
            "(slice 22 covers tool builds)"
        )

    # ------------------------------------------------------------------
    # Skill path
    # ------------------------------------------------------------------

    async def _validate_skill(
        self,
        row: EscalationRequestRow,
        branch: str,
        diff_paths: list[str],
        actor_id: str | None,
    ) -> ValidationOutcome:
        capability_name = await self._resolve_capability_name(row)
        if capability_name is None:
            return ValidationOutcome(
                passed=False,
                skill_id=None,
                pass_rate=None,
                matched_files=[],
                failures=[],
                reason=(
                    "could not resolve capability name from "
                    f"originating_entity={row.originating_entity_type!r}/"
                    f"{row.originating_entity_id!r}"
                ),
            )

        # 1. Read the user's committed skill files at branch tip.
        try:
            skill_yaml_text, step_content, output_schemas = (
                await self._read_skill_files(branch, capability_name)
            )
        except _SkillReadError as exc:
            return ValidationOutcome(
                passed=False, skill_id=None, pass_rate=None,
                matched_files=list(diff_paths),
                failures=[{"case_name": "(read)", "reason": str(exc)}],
                reason=f"skill files unreadable: {exc}",
            )

        # 2. Read fixture cases.
        try:
            fixtures = await self._read_fixtures(branch, capability_name)
        except _SkillReadError as exc:
            return ValidationOutcome(
                passed=False, skill_id=None, pass_rate=None,
                matched_files=list(diff_paths),
                failures=[{"case_name": "(fixtures)", "reason": str(exc)}],
                reason=f"fixtures unreadable: {exc}",
            )
        if not fixtures:
            return ValidationOutcome(
                passed=False, skill_id=None, pass_rate=None,
                matched_files=list(diff_paths),
                failures=[],
                reason=(
                    f"branch contained zero fixture cases under "
                    f"fixtures/{capability_name}/ — at least one is required"
                ),
            )

        # 3. Persist skill + skill_version (claude_native) and fixtures.
        skill_id = await self._persist_draft(
            capability_name=capability_name,
            skill_yaml=skill_yaml_text,
            step_content=step_content,
            output_schemas=output_schemas,
        )
        await self._persist_fixtures(skill_id=skill_id, fixtures=fixtures)

        # 4. Validate.
        report = await self._run_validation(
            skill_id=skill_id,
            skill_yaml=skill_yaml_text,
            step_content=step_content,
            output_schemas=output_schemas,
            capability_name=capability_name,
            fixtures=fixtures,
        )
        pass_rate = report.pass_rate
        failures_payload = [
            {"case_name": f.case_name, "reason": f.reason}
            for f in report.failure_details
        ]
        if pass_rate < self._fixture_pass_rate:
            logger.info(
                "manual_skill_validation_failed",
                skill_id=skill_id,
                pass_rate=pass_rate,
                threshold=self._fixture_pass_rate,
                failure_count=len(failures_payload),
            )
            return ValidationOutcome(
                passed=False,
                skill_id=skill_id,
                pass_rate=pass_rate,
                matched_files=list(diff_paths),
                failures=failures_payload,
                reason=(
                    f"fixture pass rate {pass_rate:.2f} below threshold "
                    f"{self._fixture_pass_rate:.2f}"
                ),
            )

        # 5. Lifecycle promotion: claude_native → skill_candidate →
        # draft → sandbox. The final hop is the human_approval reason
        # — the user's "Mark as built" click + green fixtures *is* the
        # human gate. Manual mode goes one hop deeper than AutoDrafter
        # because it has a real human in the loop.
        try:
            await self._lifecycle.transition(
                skill_id=skill_id,
                to_state=SkillState.SKILL_CANDIDATE,
                reason="gate_passed",
                actor="system",
                notes="manual claude_code handoff: detector-equivalent gate",
            )
            await self._lifecycle.transition(
                skill_id=skill_id,
                to_state=SkillState.DRAFT,
                reason="gate_passed",
                actor="system",
                notes=(
                    f"manual claude_code handoff for {row.correlation_id}: "
                    "fixtures passed, awaiting human gate"
                ),
            )
            await self._lifecycle.transition(
                skill_id=skill_id,
                to_state=SkillState.SANDBOX,
                reason="human_approval",
                actor="user",
                actor_id=actor_id,
                notes=(
                    f"manual claude_code handoff approved by user "
                    f"({row.correlation_id})"
                ),
            )
        except IllegalTransitionError as exc:
            logger.exception(
                "manual_skill_lifecycle_transition_failed",
                skill_id=skill_id,
                error=str(exc),
            )
            return ValidationOutcome(
                passed=False,
                skill_id=skill_id,
                pass_rate=pass_rate,
                matched_files=list(diff_paths),
                failures=failures_payload,
                reason=f"lifecycle transition failed: {exc}",
            )

        logger.info(
            "manual_skill_validation_passed",
            skill_id=skill_id,
            pass_rate=pass_rate,
            correlation_id=row.correlation_id,
        )
        return ValidationOutcome(
            passed=True,
            skill_id=skill_id,
            pass_rate=pass_rate,
            matched_files=list(diff_paths),
            failures=failures_payload,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _resolve_capability_name(
        self, row: EscalationRequestRow
    ) -> str | None:
        """Look up capability name from originating_entity_*."""
        ent_type = row.originating_entity_type
        ent_id = row.originating_entity_id
        if ent_type is None or ent_id is None:
            return None
        if ent_type == "skill_candidate_report":
            cursor = await self._conn.execute(
                "SELECT capability_name FROM skill_candidate_report WHERE id = ?",
                (ent_id,),
            )
            r = await cursor.fetchone()
            return str(r[0]) if r and r[0] else None
        if ent_type == "skill":
            cursor = await self._conn.execute(
                "SELECT capability_name FROM skill WHERE id = ?",
                (ent_id,),
            )
            r = await cursor.fetchone()
            return str(r[0]) if r and r[0] else None
        return None

    async def _read_skill_files(
        self, branch: str, capability_name: str
    ) -> tuple[str, dict[str, str], dict[str, dict[str, Any]]]:
        """Read skill.yaml + steps + schemas from the branch via git show."""
        try:
            yaml_text = await self._host_repo.show_file(
                branch, f"skills/{capability_name}/skill.yaml"
            )
        except GitRepoError as exc:
            raise _SkillReadError(
                f"missing skills/{capability_name}/skill.yaml on branch"
            ) from exc

        try:
            skill_data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise _SkillReadError(f"skill.yaml parse error: {exc}") from exc

        step_content: dict[str, str] = {}
        output_schemas: dict[str, dict[str, Any]] = {}
        for step in skill_data.get("steps", []) or []:
            if step.get("kind") != "llm":
                continue
            name = step.get("name")
            prompt_path = step.get("prompt")
            schema_path = step.get("output_schema")
            if not (name and prompt_path and schema_path):
                continue
            try:
                step_content[name] = await self._host_repo.show_file(
                    branch, f"skills/{capability_name}/{prompt_path}"
                )
            except GitRepoError as exc:
                raise _SkillReadError(
                    f"missing prompt {prompt_path} for step {name!r}"
                ) from exc
            try:
                schema_text = await self._host_repo.show_file(
                    branch, f"skills/{capability_name}/{schema_path}"
                )
            except GitRepoError as exc:
                raise _SkillReadError(
                    f"missing schema {schema_path} for step {name!r}"
                ) from exc
            try:
                output_schemas[name] = json.loads(schema_text)
            except json.JSONDecodeError as exc:
                raise _SkillReadError(
                    f"schema {schema_path} for step {name!r} is not valid JSON"
                ) from exc

        return yaml_text, step_content, output_schemas

    async def _read_fixtures(
        self, branch: str, capability_name: str
    ) -> list[Fixture]:
        """Read fixture JSON cases from ``fixtures/<capability>/`` at branch tip."""
        # We list files via ``git ls-tree`` rather than ``git diff`` —
        # the diff is the **change** set; the user might depend on
        # pre-existing fixtures. ls-tree gives the full directory.
        try:
            out = await self._host_repo._run([
                "ls-tree",
                "-r",
                "--name-only",
                branch,
                f"fixtures/{capability_name}/",
            ])
        except GitRepoError as exc:
            raise _SkillReadError(
                f"could not list fixtures for {capability_name}: {exc}"
            ) from exc
        fixture_files = [line for line in out.splitlines() if line.endswith(".json")]
        fixtures: list[Fixture] = []
        for path in fixture_files:
            case_name = path.rsplit("/", 1)[-1].removesuffix(".json")
            try:
                raw = await self._host_repo.show_file(branch, path)
                data = json.loads(raw)
            except (GitRepoError, json.JSONDecodeError) as exc:
                raise _SkillReadError(
                    f"could not read fixture {path}: {exc}"
                ) from exc
            fixtures.append(
                Fixture(
                    case_name=case_name,
                    input=dict(data.get("input", {})),
                    expected_output_shape=data.get("expected_output_shape"),
                    tool_mocks=data.get("tool_mocks"),
                )
            )
        return fixtures

    async def _persist_draft(
        self,
        *,
        capability_name: str,
        skill_yaml: str,
        step_content: dict[str, str],
        output_schemas: dict[str, dict[str, Any]],
    ) -> str:
        """Insert ``skill`` + ``skill_version`` rows in claude_native state.

        If a skill already exists for the capability (e.g. evolution
        path), reuse the existing id — the lifecycle transition below
        will fail loudly if the existing state doesn't allow the move
        to skill_candidate, which is the intended fail-loud behavior.
        """
        cursor = await self._conn.execute(
            "SELECT id FROM skill WHERE capability_name = ?",
            (capability_name,),
        )
        existing = await cursor.fetchone()
        now = datetime.now(UTC).isoformat()
        version_id = str(uuid6.uuid7())

        if existing is not None:
            skill_id = str(existing[0])
            # Insert a fresh version pointing at the new YAML so the
            # validation runs against the user's edits, not the old
            # version. Subsequent lifecycle hops will not change the
            # existing state until skill_candidate transition runs.
            cursor = await self._conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM skill_version "
                "WHERE skill_id = ?",
                (skill_id,),
            )
            max_row = await cursor.fetchone()
            max_v = max_row[0] if max_row else 0
            await self._conn.execute(
                "INSERT INTO skill_version "
                "(id, skill_id, version_number, yaml_backbone, step_content, "
                " output_schemas, created_by, changelog, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    skill_id,
                    int(max_v) + 1,
                    skill_yaml,
                    json.dumps(step_content),
                    json.dumps(output_schemas),
                    "claude_code_manual",
                    "manual claude_code handoff",
                    now,
                ),
            )
            await self._conn.execute(
                "UPDATE skill SET current_version_id = ?, updated_at = ? WHERE id = ?",
                (version_id, now, skill_id),
            )
            await self._conn.commit()
            return skill_id

        skill_id = str(uuid6.uuid7())
        await self._conn.execute(
            "INSERT INTO skill "
            "(id, capability_name, current_version_id, state, "
            " requires_human_gate, baseline_agreement, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                skill_id, capability_name, None,
                SkillState.CLAUDE_NATIVE.value, 0, None, now, now,
            ),
        )
        await self._conn.execute(
            "INSERT INTO skill_version "
            "(id, skill_id, version_number, yaml_backbone, step_content, "
            " output_schemas, created_by, changelog, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id, skill_id, 1, skill_yaml,
                json.dumps(step_content), json.dumps(output_schemas),
                "claude_code_manual",
                "manual claude_code handoff",
                now,
            ),
        )
        await self._conn.execute(
            "UPDATE skill SET current_version_id = ? WHERE id = ?",
            (version_id, skill_id),
        )
        await self._conn.commit()
        return skill_id

    async def _persist_fixtures(
        self,
        *,
        skill_id: str,
        fixtures: list[Fixture],
    ) -> None:
        """Insert ``skill_fixture`` rows from the branch's fixture cases."""
        now = datetime.now(UTC).isoformat()
        for fix in fixtures:
            await self._conn.execute(
                "INSERT INTO skill_fixture "
                "(id, skill_id, case_name, input, expected_output_shape, "
                " source, captured_run_id, created_at, tool_mocks) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid6.uuid7()),
                    skill_id,
                    fix.case_name,
                    json.dumps(fix.input),
                    json.dumps(fix.expected_output_shape)
                    if fix.expected_output_shape
                    else None,
                    "claude_code_manual",
                    None,
                    now,
                    json.dumps(fix.tool_mocks) if fix.tool_mocks else None,
                ),
            )
        await self._conn.commit()

    async def _run_validation(
        self,
        *,
        skill_id: str,
        skill_yaml: str,
        step_content: dict[str, str],
        output_schemas: dict[str, dict[str, Any]],
        capability_name: str,
        fixtures: list[Fixture],
    ) -> FixtureValidationReport:
        """Drive ValidationExecutor against the new fixtures.

        Mirrors :meth:`donna.skills.auto_drafter.AutoDrafter._validate_fixtures`
        — temp in-memory SkillRow + SkillVersionRow, never persisted as
        a "real" skill at this stage; persistence happens above so the
        skill_id is stable for lifecycle audits.
        """
        now = datetime.now(UTC)
        temp_version_id = str(uuid6.uuid7())
        temp_skill = SkillRow(
            id=skill_id,
            capability_name=capability_name,
            current_version_id=temp_version_id,
            state=SkillState.CLAUDE_NATIVE.value,
            requires_human_gate=False,
            baseline_agreement=None,
            created_at=now,
            updated_at=now,
        )
        temp_version = SkillVersionRow(
            id=temp_version_id,
            skill_id=skill_id,
            version_number=1,
            yaml_backbone=skill_yaml,
            step_content=step_content,
            output_schemas=output_schemas,
            created_by="claude_code_manual",
            changelog="manual claude_code handoff (validation snapshot)",
            created_at=now,
        )
        executor = self._executor_factory()
        return await validate_against_fixtures(
            skill=temp_skill,
            executor=executor,
            fixtures=fixtures,
            version=temp_version,
        )


class _SkillReadError(Exception):
    """Internal — surfaced as a ValidationOutcome with reason set."""
