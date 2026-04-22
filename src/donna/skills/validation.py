from __future__ import annotations

from typing import Any

import jsonschema


class SchemaValidationError(Exception):
    pass


def validate_output(output: Any, schema: dict) -> None:
    try:
        jsonschema.validate(instance=output, schema=schema)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(
            f"Output failed schema validation at "
            f"{'.'.join(str(p) for p in exc.path)}: {exc.message}"
        ) from exc
