import pytest

from donna.skills.state import StateObject


def test_state_object_set_and_get():
    state = StateObject()
    state["step1"] = {"value": 42}
    assert state["step1"] == {"value": 42}


def test_state_object_contains():
    state = StateObject()
    state["foo"] = {"bar": 1}
    assert "foo" in state
    assert "baz" not in state


def test_state_object_serialize_and_restore():
    state = StateObject()
    state["step1"] = {"a": 1}
    state["step2"] = {"b": "hello"}
    data = state.to_dict()
    assert data == {"step1": {"a": 1}, "step2": {"b": "hello"}}
    restored = StateObject.from_dict(data)
    assert restored["step1"] == {"a": 1}
    assert restored["step2"] == {"b": "hello"}


def test_state_object_iter_step_names():
    state = StateObject()
    state["a"] = {"x": 1}
    state["b"] = {"x": 2}
    assert sorted(state.step_names()) == ["a", "b"]


def test_state_object_rejects_non_dict_step_output():
    state = StateObject()
    with pytest.raises(TypeError, match="must be a dict"):
        state["step"] = "not a dict"
