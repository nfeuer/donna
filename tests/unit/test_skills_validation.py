import pytest
from donna.skills.validation import SchemaValidationError, validate_output


def test_validate_output_passes_valid_input():
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
    validate_output({"value": 42}, schema)


def test_validate_output_rejects_missing_required():
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
    with pytest.raises(SchemaValidationError):
        validate_output({}, schema)


def test_validate_output_rejects_wrong_type():
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
    with pytest.raises(SchemaValidationError):
        validate_output({"value": "not an int"}, schema)
