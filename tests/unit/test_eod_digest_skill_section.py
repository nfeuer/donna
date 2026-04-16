"""Unit tests for EodDigest skill-system section.

Tests _assemble_skill_system_data and _render_skill_section in isolation using
an in-memory SQLite DB with the required skill-system tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from donna.notifications.eod_digest import EodDigest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
    CREATE TABLE skill (
        id TEXT PRIMARY KEY, capability_name TEXT NOT NULL,
        current_version_id TEXT, state TEXT NOT NULL,
        requires_human_gate INTEGER, baseline_agreement REAL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE skill_state_transition (
        id TEXT PRIMARY KEY, skill_id TEXT, from_state TEXT,
        to_state TEXT, reason TEXT, actor TEXT, actor_id TEXT,
        at TEXT, notes TEXT
    );
    CREATE TABLE skill_candidate_report (
        id TEXT PRIMARY KEY, capability_name TEXT, task_pattern_hash TEXT,
        expected_savings_usd REAL, volume_30d INTEGER, variance_score REAL,
        status TEXT, reported_at TEXT, resolved_at TEXT
    );
    CREATE TABLE invocation_log (
        id TEXT PRIMARY KEY, timestamp TEXT, task_type TEXT,
        task_id TEXT, model_alias TEXT, model_actual TEXT,
        input_hash TEXT, latency_ms INTEGER, tokens_in INTEGER,
        tokens_out INTEGER, cost_usd REAL, output TEXT,
        quality_score REAL, is_shadow INTEGER,
        eval_session_id TEXT, spot_check_queued INTEGER, user_id TEXT
    );
"""


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript(_CREATE_TABLES)
    await conn.commit()
    yield conn
    await conn.close()


def _make_digest(conn) -> EodDigest:
    """Construct EodDigest with a minimal Database-shaped wrapper around conn."""
    db_wrapper = SimpleNamespace(
        connection=conn,
        list_tasks=lambda **kwargs: [],
    )
    return EodDigest(
        db=db_wrapper,
        service=None,
        gmail=None,
        user_id="test",
        user_email="",
        email_config=None,
    )


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _insert_skill(conn, skill_id: str, capability_name: str, state: str = "sandbox") -> None:
    now = _now_utc().isoformat()
    await conn.execute(
        "INSERT INTO skill (id, capability_name, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (skill_id, capability_name, state, now, now),
    )
    await conn.commit()


async def _insert_transition(
    conn,
    skill_id: str,
    from_state: str,
    to_state: str,
    at: str,
    reason: str = "test-reason",
) -> None:
    await conn.execute(
        """INSERT INTO skill_state_transition
           (id, skill_id, from_state, to_state, reason, at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), skill_id, from_state, to_state, reason, at),
    )
    await conn.commit()


async def _insert_candidate(
    conn,
    candidate_id: str,
    capability_name: str,
    status: str,
    resolved_at: str,
    expected_savings_usd: float = 5.0,
) -> None:
    now = _now_utc().isoformat()
    await conn.execute(
        """INSERT INTO skill_candidate_report
           (id, capability_name, expected_savings_usd, status, reported_at, resolved_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (candidate_id, capability_name, expected_savings_usd, status, now, resolved_at),
    )
    await conn.commit()


async def _insert_invocation(
    conn,
    task_type: str,
    timestamp: str,
    cost_usd: float,
) -> None:
    await conn.execute(
        """INSERT INTO invocation_log
           (id, timestamp, task_type, cost_usd)
           VALUES (?, ?, ?, ?)""",
        (str(uuid.uuid4()), timestamp, task_type, cost_usd),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNoActivity:
    async def test_no_activity_returns_stub(self, db) -> None:
        """Empty DB → stub 'No changes in the last 24 hours.' message."""
        digest = _make_digest(db)
        now = _now_utc()
        data = await digest._assemble_skill_system_data(now)
        text = digest._render_skill_section(data)
        assert "No changes in the last 24 hours." in text
        assert "Skill System Changes" in text


class TestDraftedCandidates:
    async def test_drafted_candidates_appear(self, db) -> None:
        """Drafted candidate within 24h shows capability name and expected savings."""
        digest = _make_digest(db)
        now = _now_utc()
        resolved = (now - timedelta(hours=2)).isoformat()

        await _insert_candidate(
            db,
            candidate_id="cand-1",
            capability_name="summarise_email",
            status="drafted",
            resolved_at=resolved,
            expected_savings_usd=12.50,
        )

        data = await digest._assemble_skill_system_data(now)
        assert len(data["drafted"]) == 1
        assert data["drafted"][0]["capability_name"] == "summarise_email"
        assert data["drafted"][0]["expected_monthly_savings_usd"] == 12.50

        text = digest._render_skill_section(data)
        assert "summarise_email" in text
        assert "$12.50" in text
        assert "Auto-drafted" in text

    async def test_non_drafted_status_excluded(self, db) -> None:
        """Candidate with status='pending' is not included in drafted list."""
        digest = _make_digest(db)
        now = _now_utc()
        resolved = (now - timedelta(hours=1)).isoformat()

        await _insert_candidate(
            db,
            candidate_id="cand-2",
            capability_name="parse_invoice",
            status="pending",
            resolved_at=resolved,
        )

        data = await digest._assemble_skill_system_data(now)
        assert len(data["drafted"]) == 0


class TestPromotedTransitions:
    async def test_promoted_sandbox_to_shadow_primary(self, db) -> None:
        """sandbox → shadow_primary transition within 24h shows in promoted list."""
        digest = _make_digest(db)
        now = _now_utc()
        at = (now - timedelta(hours=3)).isoformat()

        await _insert_skill(db, "skill-A", "classify_ticket", state="shadow_primary")
        await _insert_transition(db, "skill-A", "sandbox", "shadow_primary", at)

        data = await digest._assemble_skill_system_data(now)
        assert len(data["promoted"]) == 1
        p = data["promoted"][0]
        assert p["capability_name"] == "classify_ticket"
        assert p["from_state"] == "sandbox"
        assert p["to_state"] == "shadow_primary"

        text = digest._render_skill_section(data)
        assert "classify_ticket" in text
        assert "sandbox" in text
        assert "shadow_primary" in text
        assert "Promoted" in text

    async def test_promoted_shadow_primary_to_trusted(self, db) -> None:
        """shadow_primary → trusted transition appears in promoted list."""
        digest = _make_digest(db)
        now = _now_utc()
        at = (now - timedelta(hours=1)).isoformat()

        await _insert_skill(db, "skill-B", "route_task", state="trusted")
        await _insert_transition(db, "skill-B", "shadow_primary", "trusted", at)

        data = await digest._assemble_skill_system_data(now)
        assert len(data["promoted"]) == 1
        assert data["promoted"][0]["to_state"] == "trusted"


class TestDemotedTransitions:
    async def test_demoted_transitions_appear(self, db) -> None:
        """trusted → flagged_for_review transition within 24h shows in demoted list."""
        digest = _make_digest(db)
        now = _now_utc()
        at = (now - timedelta(hours=4)).isoformat()

        await _insert_skill(db, "skill-C", "send_summary", state="flagged_for_review")
        await _insert_transition(db, "skill-C", "trusted", "flagged_for_review", at, reason="high_divergence")

        data = await digest._assemble_skill_system_data(now)
        assert len(data["demoted"]) == 1
        assert data["demoted"][0]["capability_name"] == "send_summary"

        text = digest._render_skill_section(data)
        assert "send_summary" in text
        assert "Demoted" in text


class TestFlaggedTransitions:
    async def test_flagged_transitions_appear(self, db) -> None:
        """Any transition INTO flagged_for_review (from any state) shows in flagged list."""
        digest = _make_digest(db)
        now = _now_utc()
        at = (now - timedelta(hours=2)).isoformat()

        await _insert_skill(db, "skill-D", "extract_dates", state="flagged_for_review")
        await _insert_transition(
            db, "skill-D", "sandbox", "flagged_for_review", at, reason="manual_review"
        )

        data = await digest._assemble_skill_system_data(now)
        assert len(data["flagged"]) == 1
        f = data["flagged"][0]
        assert f["capability_name"] == "extract_dates"
        assert f["reason"] == "manual_review"

        text = digest._render_skill_section(data)
        assert "extract_dates" in text
        assert "manual_review" in text
        assert "Flagged for review" in text


class TestSkillSystemCost:
    async def test_skill_system_cost_sums_three_task_types(self, db) -> None:
        """skill_auto_draft + skill_equivalence_judge + triage_failure costs are summed."""
        digest = _make_digest(db)
        now = _now_utc()
        ts = (now - timedelta(hours=1)).isoformat()

        await _insert_invocation(db, "skill_auto_draft", ts, 0.10)
        await _insert_invocation(db, "skill_equivalence_judge", ts, 0.05)
        await _insert_invocation(db, "triage_failure", ts, 0.03)
        # Other task types must NOT be counted.
        await _insert_invocation(db, "morning_digest", ts, 1.00)
        await _insert_invocation(db, "task_decompose", ts, 0.50)

        data = await digest._assemble_skill_system_data(now)
        assert abs(data["skill_system_cost_usd"] - 0.18) < 1e-9

        text = digest._render_skill_section(data)
        assert "0.1800" in text

    async def test_excluded_task_types_not_counted(self, db) -> None:
        """Only the three skill-system task_types are summed; others are ignored."""
        digest = _make_digest(db)
        now = _now_utc()
        ts = (now - timedelta(hours=1)).isoformat()

        await _insert_invocation(db, "morning_digest", ts, 5.00)
        await _insert_invocation(db, "pm_agent", ts, 2.00)

        data = await digest._assemble_skill_system_data(now)
        assert data["skill_system_cost_usd"] == 0.0


class TestTimeWindow:
    async def test_24h_window_excludes_older(self, db) -> None:
        """Data older than 24h is not included in any list."""
        digest = _make_digest(db)
        now = _now_utc()
        old_at = (now - timedelta(hours=25)).isoformat()

        # Insert skill + transition older than 24h.
        await _insert_skill(db, "skill-E", "old_skill", state="flagged_for_review")
        await _insert_transition(db, "skill-E", "sandbox", "flagged_for_review", old_at)

        # Insert old candidate.
        await _insert_candidate(
            db,
            candidate_id="cand-old",
            capability_name="old_candidate",
            status="drafted",
            resolved_at=old_at,
        )

        # Insert old invocation cost.
        await _insert_invocation(db, "skill_auto_draft", old_at, 9.99)

        data = await digest._assemble_skill_system_data(now)
        assert data["flagged"] == []
        assert data["drafted"] == []
        assert data["promoted"] == []
        assert data["demoted"] == []
        assert data["skill_system_cost_usd"] == 0.0

        text = digest._render_skill_section(data)
        assert "No changes in the last 24 hours." in text

    async def test_recent_data_within_window_included(self, db) -> None:
        """Data exactly within 24h (e.g., 23h ago) is included."""
        digest = _make_digest(db)
        now = _now_utc()
        recent_at = (now - timedelta(hours=23)).isoformat()

        await _insert_skill(db, "skill-F", "recent_skill", state="flagged_for_review")
        await _insert_transition(db, "skill-F", "sandbox", "flagged_for_review", recent_at)

        data = await digest._assemble_skill_system_data(now)
        assert len(data["flagged"]) == 1
