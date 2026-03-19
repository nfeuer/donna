"""Response validation for LLM structured output.

Validates JSON output from LLM calls against JSON Schema definitions.
On mismatch, raises ValidationError for retry handling by the caller.
See docs/model-layer.md.
"""

from __future__ import annotations

from typing import Any

import jsonschema
import structlog

logger = structlog.get_logger()


class ValidationError(Exception):
    """Raised when LLM output fails schema validation."""

    def __init__(self, message: str, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(message)


def validate_output(data: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Validate LLM JSON output against a JSON schema.

    Args:
        data: Parsed JSON dict from the LLM response.
        schema: JSON Schema (draft-07) to validate against.

    Returns:
        The validated data dict (unchanged).

    Raises:
        ValidationError: If the data does not conform to the schema.
    """
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))

    if errors:
        error_messages = [
            f"{'.'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
            for e in errors
        ]
        logger.warning(
            "llm_output_validation_failed",
            error_count=len(error_messages),
            errors=error_messages,
        )
        raise ValidationError(
            f"LLM output failed schema validation with {len(error_messages)} error(s)",
            errors=error_messages,
        )

    return data
