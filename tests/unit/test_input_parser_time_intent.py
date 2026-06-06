"""TaskParseResult carries time_intent; parse() falls back when the LLM omits it."""

from donna.orchestrator.input_parser import TaskParseResult, _to_parse_result


def _base(**over):
    data = {
        "title": "Send invoices", "description": None, "domain": "personal",
        "priority": 2, "deadline": None, "deadline_type": "none",
        "estimated_duration": 30, "recurrence": None, "tags": [],
        "prep_work_flag": False, "agent_eligible": False, "confidence": 0.9,
    }
    data.update(over)
    return data


def test_result_has_time_intent_field_defaulting_none():
    result = _to_parse_result(_base())
    assert isinstance(result, TaskParseResult)
    assert result.time_intent is None


def test_result_preserves_time_intent_dict():
    ti = {"kind": "exact", "due_at": "2026-06-07T12:00:00+00:00", "strictness": "soft"}
    result = _to_parse_result(_base(time_intent=ti))
    assert result.time_intent == ti
