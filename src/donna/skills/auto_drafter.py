"""AutoDrafter — generates skill YAML + fixtures via Claude, validates, persists.

Phase 3 Task 9. The most sensitive component in the skill system: Claude is
generating Claude-executable skills. Flow:

1. Pull top `new` candidates from :class:`SkillCandidateRepository`.
2. For each, look up the referenced capability and a handful of recent
   invocation samples to seed the prompt.
3. Ask Claude (via ``reasoner`` alias) to emit a strict-JSON payload with
   ``skill_yaml``, ``step_prompts``, ``output_schemas``, and ``fixtures``.
4. Persist the skill + skill_version in-memory as ``claude_native`` then
   transition to ``draft`` through :class:`SkillLifecycleManager` so the
   audit row is written.
5. Mark candidate ``drafted`` on success; ``dismissed`` on any failure
   (missing capability, malformed output, LLM error, fixture pass_rate
   below ``config.auto_draft_fixture_pass_rate``).

Validation strategy
-------------------

``validate_against_fixtures`` needs a live ``SkillExecutor`` to run each
fixture end-to-end. The drafter requires an ``executor_factory`` callable
returning a real executor (``ValidationExecutor`` in production). Wave 1
removed the deferred / vacuous-pass path — every draft runs fixture
validation through the sandbox.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog
import uuid6

from donna.cost.budget import BudgetPausedError
from donna.skills.candidate_report import (
    SkillCandidateReportRow,
    SkillCandidateRepository,
)
from donna.skills.fixtures import Fixture, validate_against_fixtures
from donna.skills.lifecycle import SkillLifecycleManager
from donna.skills.models import SkillRow, SkillVersionRow
from donna.tasks.db_models import SkillState

logger = structlog.get_logger()

TASK_TYPE = "skill_auto_draft"

ExecutorFactory = Callable[[], Any]


@dataclass(slots=True)
class AutoDraftReport:
    """Outcome of drafting one candidate.

    ``outcome`` is one of:
      * ``drafted`` — skill persisted in ``draft`` state, candidate closed.
      * ``dismissed`` — capability missing, llm error, fixture validation
        failed, or unrecoverable error; candidate marked ``dismissed``.
      * ``budget_exhausted`` — daily spend cap hit mid-draft; candidate
        left untouched for the next nightly run.
      * ``malformed_output`` — the LLM returned JSON but without the
        required keys; candidate dismissed.
    """

    candidate_id: str
    outcome: str
    skill_id: str | None = None
    pass_rate: float | None = None
    rationale: str | None = None


class AutoDrafter:
    """Nightly Claude-driven skill-generation entry point."""

    def __init__(
        self,
        connection: aiosqlite.Connection,
        model_router: Any,
        budget_guard: Any,
        candidate_repo: SkillCandidateRepository,
        lifecycle_manager: SkillLifecycleManager,
        config: Any,
        executor_factory: ExecutorFactory,
        estimated_draft_cost_usd: float = 0.50,
    ) -> None:
        self._conn = connection
        self._router = model_router
        self._budget_guard = budget_guard
        self._repo = candidate_repo
        self._lifecycle = lifecycle_manager
        self._config = config
        self._executor_factory = executor_factory
        self._estimated_cost = estimated_draft_cost_usd

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        remaining_budget_usd: float,
        max_drafts: int,
    ) -> list[AutoDraftReport]:
        """Pull top `new` candidates, draft each, return outcomes.

        Stops early when remaining_budget_usd < estimated_draft_cost_usd.
        """
        candidates = await self._repo.list_new(limit=max_drafts)
        reports: list[AutoDraftReport] = []

        for cand in candidates:
            if remaining_budget_usd < self._estimated_cost:
                logger.info(
                    "skill_auto_draft_budget_exhausted",
                    remaining=remaining_budget_usd,
                    per_draft_estimate=self._estimated_cost,
                    candidate_id=cand.id,
                )
                break
            try:
                report = await self.draft_one(cand)
            except Exception as exc:  # pragma: no cover - safety net
                logger.exception(
                    "skill_auto_draft_unexpected_error", candidate_id=cand.id
                )
                report = AutoDraftReport(
                    candidate_id=cand.id, outcome="dismissed", rationale=str(exc)
                )
            reports.append(report)
            remaining_budget_usd -= self._estimated_cost

        return reports

    async def draft_one(
        self, candidate: SkillCandidateReportRow
    ) -> AutoDraftReport:
        """Draft a single candidate. Public for dashboard manual-trigger path."""
        # 1. Look up the capability row.
        capability = await self._lookup_capability(candidate.capability_name)
        if capability is None:
            logger.warning(
                "skill_auto_draft_capability_not_found",
                candidate_id=candidate.id,
                capability_name=candidate.capability_name,
            )
            await self._repo.mark_dismissed(candidate.id)
            return AutoDraftReport(
                candidate_id=candidate.id,
                outcome="dismissed",
                rationale="capability not found",
            )

        # 2. Gather recent invocation samples as in-context examples.
        samples = await self._recent_invocation_samples(
            candidate.capability_name, limit=5
        )

        # 3. Call Claude.
        try:
            if self._budget_guard is not None:
                await self._budget_guard.check_pre_call(user_id="system")
            parsed, _metadata = await self._router.complete(
                prompt=self._build_prompt(capability, samples),
                task_type=TASK_TYPE,
                task_id=None,
                user_id="system",
            )
        except BudgetPausedError:
            logger.info(
                "skill_auto_draft_budget_paused", candidate_id=candidate.id
            )
            return AutoDraftReport(
                candidate_id=candidate.id, outcome="budget_exhausted"
            )
        except Exception as exc:
            logger.warning(
                "skill_auto_draft_llm_call_failed",
                candidate_id=candidate.id,
                error=str(exc),
            )
            await self._repo.mark_dismissed(candidate.id)
            return AutoDraftReport(
                candidate_id=candidate.id,
                outcome="dismissed",
                rationale=f"llm call failed: {exc}",
            )

        # 4. Parse the structured output.
        skill_yaml, step_prompts, output_schemas, fixtures_data = (
            self._extract_draft_payload(parsed)
        )
        if skill_yaml is None:
            logger.warning(
                "skill_auto_draft_malformed_output",
                candidate_id=candidate.id,
                parsed_keys=list(parsed.keys()) if isinstance(parsed, dict) else None,
            )
            await self._repo.mark_dismissed(candidate.id)
            return AutoDraftReport(
                candidate_id=candidate.id,
                outcome="malformed_output",
                rationale="llm output missing required keys",
            )

        # 5. Fixture validation in sandbox (or deferred).
        pass_rate = await self._validate_fixtures(
            skill_yaml=skill_yaml,
            step_prompts=step_prompts,
            output_schemas=output_schemas,
            fixtures_data=fixtures_data,
            capability_name=candidate.capability_name,
        )
        threshold = float(self._config.auto_draft_fixture_pass_rate)
        if pass_rate < threshold:
            logger.info(
                "skill_auto_draft_validation_failed",
                candidate_id=candidate.id,
                pass_rate=pass_rate,
                threshold=threshold,
            )
            await self._repo.mark_dismissed(candidate.id)
            return AutoDraftReport(
                candidate_id=candidate.id,
                outcome="dismissed",
                pass_rate=pass_rate,
                rationale=(
                    f"fixture pass rate {pass_rate:.2f} below threshold "
                    f"{threshold:.2f}"
                ),
            )

        # 6. Persist skill + skill_version, then transition to DRAFT.
        #    Skill starts as ``claude_native``; the transition table requires
        #    us to hop through ``skill_candidate`` before reaching ``draft``
        #    (see donna.skills.lifecycle._build_transitions).
        skill_id = await self._persist_draft(
            capability_name=candidate.capability_name,
            skill_yaml=skill_yaml,
            step_prompts=step_prompts,
            output_schemas=output_schemas,
        )

        await self._lifecycle.transition(
            skill_id=skill_id,
            to_state=SkillState.SKILL_CANDIDATE,
            reason="gate_passed",
            actor="system",
            notes=f"auto-draft candidate {candidate.id}: detector gate passed",
        )
        await self._lifecycle.transition(
            skill_id=skill_id,
            to_state=SkillState.DRAFT,
            reason="gate_passed",
            actor="system",
            notes=f"auto-drafted from candidate {candidate.id}",
        )

        # Persist the generated fixtures (with tool_mocks) so validation
        # and later evolution gates can replay them. See Wave 1 §5.1.
        await self._persist_generated_fixtures(
            skill_id=skill_id, fixtures_data=fixtures_data
        )

        await self._repo.mark_drafted(candidate.id)

        logger.info(
            "skill_auto_draft_succeeded",
            candidate_id=candidate.id,
            skill_id=skill_id,
            pass_rate=pass_rate,
        )
        return AutoDraftReport(
            candidate_id=candidate.id,
            outcome="drafted",
            skill_id=skill_id,
            pass_rate=pass_rate,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _lookup_capability(
        self, capability_name: str | None
    ) -> dict[str, Any] | None:
        if not capability_name:
            return None
        cursor = await self._conn.execute(
            """
            SELECT id, name, description, input_schema, trigger_type, status
              FROM capability
             WHERE name = ?
            """,
            (capability_name,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "input_schema": row[3],
            "trigger_type": row[4],
            "status": row[5],
        }

    async def _recent_invocation_samples(
        self, capability_name: str | None, limit: int = 5
    ) -> list[dict[str, Any]]:
        if not capability_name:
            return []
        cursor = await self._conn.execute(
            """
            SELECT input_hash, output
              FROM invocation_log
             WHERE task_type = ?
             ORDER BY timestamp DESC
             LIMIT ?
            """,
            (capability_name, limit),
        )
        rows = await cursor.fetchall()
        samples: list[dict[str, Any]] = []
        for input_hash, output in rows:
            samples.append(
                {"input_hash": input_hash, "output": output}
            )
        return samples

    def _build_prompt(
        self,
        capability: dict[str, Any],
        samples: list[dict[str, Any]],
    ) -> str:
        """Construct the strict-JSON prompt for Claude."""
        input_schema_raw = capability.get("input_schema")
        try:
            input_schema = (
                json.loads(input_schema_raw) if input_schema_raw else {}
            )
        except (TypeError, ValueError):
            input_schema = {}

        return (
            f"Generate a skill for the capability '{capability['name']}'.\n\n"
            f"Capability description: {capability.get('description', '')}\n\n"
            f"Input schema:\n{json.dumps(input_schema, indent=2)}\n\n"
            f"Recent invocation examples (hashed inputs + raw outputs):\n"
            f"{json.dumps(samples, indent=2)}\n\n"
            "Generate:\n"
            "1. A skill YAML backbone with 1-3 `llm` steps.\n"
            "2. Per-step prompts (markdown) keyed by step name.\n"
            "3. Per-step output schemas (JSON Schema) keyed by step name.\n"
            "4. 3-5 fixture test cases. Each fixture must include:\n"
            "   - \"case_name\": short identifier (snake_case).\n"
            "   - \"input\": an object matching the capability's input schema.\n"
            "   - \"expected_output_shape\": a STRUCTURAL JSON Schema for the\n"
            "     final output — field names, types, required fields, and\n"
            "     nesting only; do NOT pin values except for closed enums\n"
            "     (e.g., {\"status\": {\"enum\": [\"in_stock\", \"sold_out\"]}}).\n"
            "   - \"tool_mocks\": a JSON object mapping tool-invocation\n"
            "     fingerprints to result blobs. Fingerprint format is\n"
            "     \"<tool_name>:<canonical-sorted-JSON>\". For tools with\n"
            "     specific rules (web_fetch keys only on {\"url\": ...};\n"
            "     gmail_read keys only on {\"message_id\": ...}), compose the\n"
            "     fingerprint from those args only. Fixtures for pure-LLM\n"
            "     skills (no tool steps) may set tool_mocks to {}.\n\n"
            "Your response MUST be strict JSON matching this shape:\n"
            "{\n"
            '  "skill_yaml": "<YAML string>",\n'
            '  "step_prompts": {"<step_name>": "<prompt markdown>"},\n'
            '  "output_schemas": {"<step_name>": {<JSON schema>}},\n'
            '  "fixtures": [{"case_name": "...", "input": {...}, '
            '"expected_output_shape": {...}, "tool_mocks": {...}}]\n'
            "}\n"
        )

    @staticmethod
    def _extract_draft_payload(
        parsed: Any,
    ) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, list[Any] | None]:
        """Pull the four required keys out of the LLM response.

        Returns ``(None, None, None, None)`` when the payload is malformed.
        """
        if not isinstance(parsed, dict):
            return None, None, None, None

        skill_yaml = parsed.get("skill_yaml")
        step_prompts = parsed.get("step_prompts")
        output_schemas = parsed.get("output_schemas")
        fixtures_data = parsed.get("fixtures")

        if not isinstance(skill_yaml, str) or not skill_yaml.strip():
            return None, None, None, None
        if not isinstance(step_prompts, dict) or not step_prompts:
            return None, None, None, None
        if not isinstance(output_schemas, dict) or not output_schemas:
            return None, None, None, None
        if not isinstance(fixtures_data, list) or not fixtures_data:
            return None, None, None, None

        return skill_yaml, step_prompts, output_schemas, fixtures_data

    async def _validate_fixtures(
        self,
        *,
        skill_yaml: str,
        step_prompts: dict[str, Any],
        output_schemas: dict[str, Any],
        fixtures_data: list[Any],
        capability_name: str,
    ) -> float:
        """Run generated fixtures through a sandbox executor.

        The ``executor_factory`` is required (Wave 1 F-5); validation always
        runs against a real ``ValidationExecutor``.
        """
        fixtures = [
            Fixture(
                case_name=str(item.get("case_name", f"case_{i}")),
                input=dict(item.get("input", {})),
                expected_output_shape=item.get("expected_output_shape"),
                tool_mocks=item.get("tool_mocks"),
            )
            for i, item in enumerate(fixtures_data)
            if isinstance(item, dict)
        ]

        # Build in-memory SkillRow + SkillVersionRow — NOT persisted.
        now = datetime.now(UTC)
        temp_skill_id = str(uuid6.uuid7())
        temp_version_id = str(uuid6.uuid7())
        temp_skill = SkillRow(
            id=temp_skill_id,
            capability_name=capability_name,
            current_version_id=temp_version_id,
            state=SkillState.DRAFT.value,
            requires_human_gate=False,
            baseline_agreement=None,
            created_at=now,
            updated_at=now,
        )
        temp_version = SkillVersionRow(
            id=temp_version_id,
            skill_id=temp_skill_id,
            version_number=1,
            yaml_backbone=skill_yaml,
            step_content=step_prompts,
            output_schemas=output_schemas,
            created_by="claude_auto_draft",
            changelog="Auto-drafted (sandbox validation)",
            created_at=now,
        )

        executor = self._executor_factory()
        report = await validate_against_fixtures(
            skill=temp_skill,
            executor=executor,
            fixtures=fixtures,
            version=temp_version,
        )
        return report.pass_rate

    async def _persist_draft(
        self,
        *,
        capability_name: str,
        skill_yaml: str,
        step_prompts: dict[str, Any],
        output_schemas: dict[str, Any],
    ) -> str:
        """Insert skill + skill_version in claude_native state; return skill_id.

        If a skill already exists for the capability, return the existing id
        without modification — the subsequent lifecycle transition will fail
        loudly if the existing state doesn't permit the move to DRAFT, which
        is the desired behavior (don't silently stomp on approved skills).
        """
        cursor = await self._conn.execute(
            "SELECT id FROM skill WHERE capability_name = ?",
            (capability_name,),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            return existing[0]

        now = datetime.now(UTC).isoformat()
        skill_id = str(uuid6.uuid7())
        version_id = str(uuid6.uuid7())

        await self._conn.execute(
            """
            INSERT INTO skill
                (id, capability_name, current_version_id, state,
                 requires_human_gate, baseline_agreement, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (skill_id, capability_name, None, SkillState.CLAUDE_NATIVE.value,
             0, None, now, now),
        )
        await self._conn.execute(
            """
            INSERT INTO skill_version
                (id, skill_id, version_number, yaml_backbone, step_content,
                 output_schemas, created_by, changelog, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id, skill_id, 1, skill_yaml,
                json.dumps(step_prompts), json.dumps(output_schemas),
                "claude_auto_draft",
                "Auto-drafted from candidate report",
                now,
            ),
        )
        await self._conn.execute(
            "UPDATE skill SET current_version_id = ? WHERE id = ?",
            (version_id, skill_id),
        )
        await self._conn.commit()
        return skill_id

    async def _persist_generated_fixtures(
        self,
        *,
        skill_id: str,
        fixtures_data: list[Any],
    ) -> None:
        """Write each Claude-generated fixture row to ``skill_fixture``.

        ``tool_mocks`` is threaded through per fixture item. Fixtures that
        omit the key (pure-LLM skills) store NULL. See Wave 1 spec §5.1.
        """
        for i, item in enumerate(fixtures_data):
            if not isinstance(item, dict):
                continue
            await _persist_fixture(
                conn=self._conn,
                skill_id=skill_id,
                case_name=str(item.get("case_name", f"case_{i}")),
                input_=dict(item.get("input", {})),
                expected_output_shape=item.get("expected_output_shape"),
                tool_mocks=item.get("tool_mocks"),
                source="claude_generated",
            )
        await self._conn.commit()


async def _persist_fixture(
    *,
    conn: aiosqlite.Connection,
    skill_id: str,
    case_name: str,
    input_: dict[str, Any],
    expected_output_shape: dict[str, Any] | None,
    tool_mocks: dict[str, Any] | None,
    source: str,
    captured_run_id: str | None = None,
) -> str:
    """Insert a ``skill_fixture`` row; return the new id.

    ``tool_mocks`` is a fingerprint-keyed dict of mocked tool results,
    JSON-serialized. See §5.1 of
    ``docs/superpowers/specs/archive/2026-04-16-skill-system-wave-1-production-enablement-design.md``.
    """
    fixture_id = str(uuid6.uuid7())
    await conn.execute(
        "INSERT INTO skill_fixture "
        "(id, skill_id, case_name, input, expected_output_shape, "
        " source, captured_run_id, created_at, tool_mocks) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fixture_id,
            skill_id,
            case_name,
            json.dumps(input_),
            json.dumps(expected_output_shape) if expected_output_shape else None,
            source,
            captured_run_id,
            datetime.now(tz=UTC).isoformat(),
            json.dumps(tool_mocks) if tool_mocks else None,
        ),
    )
    return fixture_id
