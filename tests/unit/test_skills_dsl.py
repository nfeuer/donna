import pytest

from donna.skills.dsl import DSLError, expand_for_each


def test_for_each_over_list():
    spec = {
        "for_each": "{{ state.plan.urls }}",
        "as": "entry",
        "tool": "web_fetch",
        "args": {"url": "{{ entry }}"},
        "store_as": "fetched_{{ loop.index0 }}",
    }
    state = {"plan": {"urls": ["https://a.com", "https://b.com", "https://c.com"]}}
    inputs = {}

    specs = expand_for_each(spec, state=state, inputs=inputs)

    assert len(specs) == 3
    assert specs[0].tool == "web_fetch"
    assert specs[0].args == {"url": "https://a.com"}
    assert specs[0].store_as == "fetched_0"
    assert specs[1].args == {"url": "https://b.com"}
    assert specs[2].store_as == "fetched_2"


def test_for_each_over_dict_items_is_not_supported_in_v1():
    spec = {
        "for_each": "{{ state.plan.mapping }}",
        "as": "entry",
        "tool": "mock",
        "args": {},
        "store_as": "r",
    }
    state = {"plan": {"mapping": {"a": 1, "b": 2}}}
    with pytest.raises(DSLError, match="must be a list"):
        expand_for_each(spec, state=state, inputs={})


def test_for_each_empty_list_produces_no_specs():
    spec = {
        "for_each": "{{ state.plan.urls }}",
        "as": "entry",
        "tool": "mock",
        "args": {},
        "store_as": "r",
    }
    specs = expand_for_each(spec, state={"plan": {"urls": []}}, inputs={})
    assert specs == []


def test_for_each_loop_index_variables_available():
    spec = {
        "for_each": "{{ inputs.items }}",
        "as": "item",
        "tool": "mock",
        "args": {"i": "{{ loop.index0 }}", "one_indexed": "{{ loop.index }}"},
        "store_as": "r_{{ loop.index0 }}",
    }
    specs = expand_for_each(spec, state={}, inputs={"items": ["x", "y"]})
    assert specs[0].args == {"i": "0", "one_indexed": "1"}
    assert specs[1].args == {"i": "1", "one_indexed": "2"}


def test_for_each_missing_required_fields_raises():
    # Missing 'tool' field
    with pytest.raises(DSLError, match="requires"):
        expand_for_each({
            "for_each": "{{ state.items }}",
            "as": "x",
            "args": {},
            "store_as": "r",
        }, state={"items": [1]}, inputs={})


def test_for_each_iterable_over_inputs():
    spec = {
        "for_each": "{{ inputs.urls }}",
        "as": "u",
        "tool": "fetch",
        "args": {"u": "{{ u }}"},
        "store_as": "r_{{ loop.index0 }}",
    }
    specs = expand_for_each(spec, state={}, inputs={"urls": ["x", "y"]})
    assert len(specs) == 2
    assert specs[0].args == {"u": "x"}
    assert specs[1].args == {"u": "y"}


def test_for_each_passes_through_retry_policy():
    spec = {
        "for_each": "{{ inputs.urls }}",
        "as": "u",
        "tool": "fetch",
        "args": {},
        "store_as": "r_{{ loop.index0 }}",
        "retry": {"max_attempts": 3, "backoff_s": [1, 2, 3]},
    }
    specs = expand_for_each(spec, state={}, inputs={"urls": ["x"]})
    assert specs[0].retry == {"max_attempts": 3, "backoff_s": [1, 2, 3]}
