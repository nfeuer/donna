"""Preference rule application — Phase 2.

Loads active LearnedPreference rules for a user and applies matching ones
to a TaskParseResult before it is persisted. This is a post-parse,
pre-database step in the InputParser pipeline.

Rules are evaluated locally (no LLM call). Matching uses:
  - keywords: case-insensitive substring match against task title + description
  - domain: exact match against task domain
  - task_type: always matches "parse_task" at input time

See docs/preferences.md.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import Any

import structlog

from donna.orchestrator.input_parser import TaskParseResult
from donna.tasks.database import Database

logger = structlog.get_logger()

# In-process TTL cache: {user_id: (loaded_at, rules)}
_CACHE: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}
_CACHE_TTL_SECONDS = 60


class PreferenceApplier:
    """Applies learned preference rules to TaskParseResult instances.

    Usage:
        applier = PreferenceApplier(db)
        result = await applier.apply_for_user(parse_result, user_id)
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def load_rules(self, user_id: str) -> list[dict[str, Any]]:
        """Load enabled rules for user_id, with TTL caching."""
        now = datetime.now(tz=UTC)
        cached = _CACHE.get(user_id)
        if cached is not None:
            loaded_at, rules = cached
            if (now - loaded_at).total_seconds() < _CACHE_TTL_SECONDS:
                return rules

        conn = self._db.connection
        cursor = await conn.execute(
            """
            SELECT id, rule_type, confidence, condition, action
            FROM learned_preferences
            WHERE user_id = ? AND enabled = 1
            ORDER BY confidence DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        rules = [
            {
                "id": row[0],
                "rule_type": row[1],
                "confidence": row[2],
                "condition": json.loads(row[3]) if row[3] else {},
                "action": json.loads(row[4]) if row[4] else {},
            }
            for row in rows
        ]
        _CACHE[user_id] = (now, rules)
        return rules

    def invalidate_cache(self, user_id: str) -> None:
        """Invalidate cached rules for user_id (call after new rules are saved)."""
        _CACHE.pop(user_id, None)

    def apply(self, result: TaskParseResult, rules: list[dict[str, Any]]) -> TaskParseResult:
        """Apply matching rules to result. Returns a (possibly modified) TaskParseResult.

        Rules are evaluated in confidence-descending order. The first matching
        rule for each output field wins; subsequent rules for the same field
        are skipped.
        """
        if not rules:
            return result

        # Build a search corpus from the task text.
        corpus = " ".join(filter(None, [result.title, result.description or ""])).lower()

        overrides: dict[str, Any] = {}
        applied_fields: set[str] = set()

        for rule in rules:
            action = rule.get("action", {})
            field = action.get("field")
            if not field or field in applied_fields:
                continue

            if self._matches(rule.get("condition", {}), corpus, result):
                overrides[field] = action.get("value")
                applied_fields.add(field)
                logger.info(
                    "preference_rule_applied",
                    rule_id=rule.get("id"),
                    field=field,
                    new_value=action.get("value"),
                    confidence=rule.get("confidence"),
                )

        if not overrides:
            return result

        # Build new TaskParseResult by merging overrides.
        fields = dataclasses.asdict(result)
        for key, value in overrides.items():
            if key in fields:
                fields[key] = value
        return TaskParseResult(**fields)

    def _matches(
        self,
        condition: dict[str, Any],
        corpus: str,
        result: TaskParseResult,
    ) -> bool:
        """Return True if all condition constraints are satisfied."""
        # keyword match
        keywords: list[str] = condition.get("keywords", [])
        if keywords and not any(kw.lower() in corpus for kw in keywords):
            return False

        # domain match
        domain = condition.get("domain")
        if domain and result.domain != domain:
            return False

        # task_type always matches "parse_task" at input time; other values skip.
        task_type = condition.get("task_type")
        return not (task_type and task_type != "parse_task")

    async def apply_for_user(self, result: TaskParseResult, user_id: str) -> TaskParseResult:
        """Convenience async wrapper: load rules then apply."""
        rules = await self.load_rules(user_id)
        return self.apply(result, rules)
