"""Preference rule extraction — Phase 2.

Reads batches of unprocessed `correction_log` rows, groups them by field,
and calls the LLM to synthesise recurring patterns into `LearnedPreference`
rules. Marks processed corrections via the `rule_extracted` column.

Runs as a weekly background task. See docs/preferences.md.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from donna.config import PreferencesConfig
from donna.models.router import ModelRouter
from donna.models.validation import validate_output
from donna.tasks.database import Database

logger = structlog.get_logger()

TASK_TYPE = "extract_preferences"


class PreferenceRuleExtractor:
    """Extracts learned preference rules from the correction_log table.

    Usage:
        extractor = PreferenceRuleExtractor(db, router, user_id, project_root)
        new_rule_ids = await extractor.extract()
        asyncio.create_task(extractor.run_weekly())
    """

    def __init__(
        self,
        db: Database,
        router: ModelRouter,
        user_id: str,
        project_root: Path,
        config: PreferencesConfig | None = None,
    ) -> None:
        self._db = db
        self._router = router
        self._user_id = user_id
        self._project_root = project_root
        self._config = config

    async def run_weekly(self) -> None:
        """Sleep until the configured interval has passed, then run extract()."""
        interval_days = 7
        if self._config is not None:
            interval_days = self._config.schedule.extract_interval_days

        logger.info("rule_extractor_started", interval_days=interval_days, user_id=self._user_id)

        while True:
            wait_seconds = interval_days * 86400
            logger.info("rule_extractor_waiting", wait_seconds=wait_seconds)
            await asyncio.sleep(wait_seconds)

            try:
                new_ids = await self.extract()
                logger.info("rule_extractor_ran", new_rules=len(new_ids), user_id=self._user_id)
            except Exception:
                logger.exception("rule_extractor_failed", user_id=self._user_id)

    async def extract(self) -> list[str]:
        """Run one extraction pass. Returns list of new LearnedPreference IDs."""
        min_corrections = 3
        max_batch = 50
        if self._config is not None:
            min_corrections = self._config.schedule.min_corrections_to_extract
            max_batch = self._config.schedule.max_corrections_per_batch

        corrections = await self._load_unprocessed_corrections(max_batch)
        if not corrections:
            logger.info("rule_extractor_no_corrections", user_id=self._user_id)
            return []

        # Group by field_corrected; only process fields with enough signal.
        by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in corrections:
            by_field[row["field_corrected"]].append(row)

        eligible = {
            field: rows
            for field, rows in by_field.items()
            if len(rows) >= min_corrections
        }
        if not eligible:
            logger.info("rule_extractor_insufficient_corrections", user_id=self._user_id)
            return []

        # Flatten back to a batch for the LLM.
        batch = [row for rows in eligible.values() for row in rows]

        existing_rules = await self._load_existing_rules()
        raw_rules = await self._call_llm(batch, existing_rules)

        if not raw_rules:
            return []

        min_confidence = 0.7
        if self._config is not None:
            min_confidence = self._config.schedule.min_confidence

        filtered = [r for r in raw_rules if r.get("confidence", 0) >= min_confidence]
        if not filtered:
            logger.info("rule_extractor_no_rules_above_threshold", user_id=self._user_id)
            return []

        return await self._save_rules(filtered)

    async def _load_unprocessed_corrections(self, limit: int) -> list[dict[str, Any]]:
        """Load correction_log rows that have not yet been processed."""
        conn = self._db.connection
        cursor = await conn.execute(
            """
            SELECT id, field_corrected, original_value, corrected_value,
                   task_type, input_text, timestamp
            FROM correction_log
            WHERE user_id = ? AND rule_extracted IS NULL
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (self._user_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "field_corrected": row[1],
                "original_value": row[2],
                "corrected_value": row[3],
                "task_type": row[4],
                "input_text": row[5],
                "timestamp": row[6],
            }
            for row in rows
        ]

    async def _load_existing_rules(self) -> list[dict[str, Any]]:
        """Load active LearnedPreference rows for duplicate-avoidance."""
        conn = self._db.connection
        cursor = await conn.execute(
            """
            SELECT rule_type, rule_text, confidence, condition, action
            FROM learned_preferences
            WHERE user_id = ? AND enabled = 1
            """,
            (self._user_id,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                "rule_type": row[0],
                "rule_text": row[1],
                "confidence": row[2],
                "condition": json.loads(row[3]) if row[3] else {},
                "action": json.loads(row[4]) if row[4] else {},
            })
        return result

    async def _call_llm(
        self,
        corrections: list[dict[str, Any]],
        existing_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Render the prompt, call the LLM, validate, and return extracted rules."""
        template_path = self._project_root / "prompts" / "extract_preferences.md"
        template = template_path.read_text()

        now = datetime.now(tz=UTC)
        prompt = (
            template
            .replace("{{ current_date }}", now.strftime("%Y-%m-%d"))
            .replace("{{ corrections_json }}", json.dumps(corrections, indent=2))
            .replace("{{ existing_rules_json }}", json.dumps(existing_rules, indent=2))
        )

        schema = self._router.get_output_schema(TASK_TYPE)
        result, _ = await self._router.complete(prompt, task_type=TASK_TYPE, user_id=self._user_id)
        validated = validate_output(result, schema)
        return validated.get("rules", [])

    async def _save_rules(self, rules: list[dict[str, Any]]) -> list[str]:
        """Persist rules and mark corrections as processed. Returns new rule IDs."""
        conn = self._db.connection
        new_ids: list[str] = []

        for rule in rules:
            rule_id = str(uuid.uuid4())
            now_iso = datetime.now(tz=UTC).isoformat()

            correction_ids: list[str] = rule.get("supporting_correction_ids", [])

            await conn.execute(
                """
                INSERT INTO learned_preferences
                    (id, user_id, rule_type, rule_text, confidence,
                     condition, action, supporting_corrections, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    rule_id,
                    self._user_id,
                    rule["rule_type"],
                    rule["rule_text"],
                    rule["confidence"],
                    json.dumps(rule.get("condition", {})),
                    json.dumps(rule.get("action", {})),
                    json.dumps(correction_ids),
                    now_iso,
                ),
            )

            # Mark supporting corrections as processed.
            for cid in correction_ids:
                await conn.execute(
                    "UPDATE correction_log SET rule_extracted = ? WHERE id = ?",
                    (rule_id, cid),
                )

            new_ids.append(rule_id)
            logger.info(
                "preference_rule_saved",
                rule_id=rule_id,
                rule_type=rule["rule_type"],
                confidence=rule["confidence"],
                user_id=self._user_id,
            )

        await conn.commit()
        return new_ids
