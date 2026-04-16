"""LocalLLMInputExtractor — extracts structured inputs from free text using local LLM."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


class LocalLLMInputExtractor:
    """Extracts structured inputs using the local LLM with JSON-mode output."""

    def __init__(self, model_router: Any) -> None:
        self._router = model_router

    async def extract(
        self,
        user_message: str,
        schema: dict,
        user_id: str,
    ) -> dict[str, Any]:
        prompt = self._build_prompt(user_message, schema)

        try:
            output, _meta = await self._router.complete(
                prompt=prompt,
                schema=schema,
                model_alias="local_parser",
                task_type="capability_input_extraction",
                user_id=user_id,
            )
            if not isinstance(output, dict):
                logger.warning("input_extractor_unexpected_output_type", type=type(output).__name__)
                return {}
            return output
        except Exception as exc:
            logger.warning("input_extractor_failed", error=str(exc), user_id=user_id)
            return {}

    @staticmethod
    def _build_prompt(user_message: str, schema: dict) -> str:
        props = schema.get("properties", {})
        field_lines = []
        for field_name, field_def in props.items():
            desc = field_def.get("description", "")
            ftype = field_def.get("type", "any")
            field_lines.append(f"- {field_name} ({ftype}): {desc}".rstrip(": "))

        required = schema.get("required", [])
        required_str = ", ".join(required) if required else "(none)"
        field_block = "\n".join(field_lines) if field_lines else "(no fields declared)"

        return (
            "You are Donna's input extractor. Extract structured fields from "
            "the user message below against the schema. If a field cannot be "
            "determined from the message, leave it null or omit it — do not "
            "invent information.\n\n"
            f"User message:\n{user_message}\n\n"
            f"Fields to extract:\n{field_block}\n\n"
            f"Required fields: {required_str}\n\n"
            "Return a JSON object containing the extracted fields."
        )
