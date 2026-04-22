"""Fixture loader and validation harness for skills."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from donna.skills.validation import SchemaValidationError, validate_output

logger = structlog.get_logger()


@dataclass(slots=True)
class Fixture:
    case_name: str
    input: dict
    expected_output_shape: dict | None = None
    tool_mocks: dict | None = None  # Keyed by fingerprint. See tool_fingerprint.


@dataclass(slots=True)
class FixtureFailureDetail:
    case_name: str
    reason: str


@dataclass(slots=True)
class FixtureValidationReport:
    total: int
    passed: int
    failed: int
    failure_details: list[FixtureFailureDetail] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


class FixtureLoader:
    def load_from_directory(self, fixtures_dir: Path) -> list[Fixture]:
        fixtures: list[Fixture] = []
        if not fixtures_dir.exists():
            return fixtures

        for file in sorted(fixtures_dir.glob("*.json")):
            try:
                with open(file) as f:
                    data = json.load(f)
                fixtures.append(self._make_fixture(
                    case_name=file.stem,
                    input=data["input"],
                    expected_output_shape=data.get("expected_output_shape"),
                    tool_mocks=data.get("tool_mocks"),
                ))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("fixture_load_failed", file=str(file), error=str(exc))

        return fixtures

    @staticmethod
    def _make_fixture(
        case_name: str,
        input: dict,
        expected_output_shape: dict | None = None,
        tool_mocks: dict | None = None,
    ) -> Fixture:
        return Fixture(
            case_name=case_name,
            input=input,
            expected_output_shape=expected_output_shape,
            tool_mocks=tool_mocks,
        )


async def validate_against_fixtures(
    skill: Any,
    executor: Any,
    fixtures: list[Fixture],
    version: Any,
) -> FixtureValidationReport:
    total = len(fixtures)
    passed = 0
    failures: list[FixtureFailureDetail] = []

    for fix in fixtures:
        try:
            result = await executor.execute(
                skill=skill, version=version,
                inputs=fix.input, user_id="fixture_harness",
                tool_mocks=fix.tool_mocks,
            )

            if result.status != "succeeded":
                failures.append(FixtureFailureDetail(
                    case_name=fix.case_name,
                    reason=(
                        f"run status={result.status}: "
                        f"{result.error or result.escalation_reason}"
                    ),
                ))
                continue

            if fix.expected_output_shape:
                try:
                    validate_output(result.final_output, fix.expected_output_shape)
                except SchemaValidationError as exc:
                    failures.append(FixtureFailureDetail(case_name=fix.case_name, reason=str(exc)))
                    continue

            passed += 1

        except Exception as exc:
            failures.append(FixtureFailureDetail(
                case_name=fix.case_name,
                reason=f"exception: {exc}",
            ))

    return FixtureValidationReport(
        total=total, passed=passed, failed=total - passed,
        failure_details=failures,
    )
