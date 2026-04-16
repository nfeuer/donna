from __future__ import annotations
from typing import Any


class StateObject:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise TypeError(f"StateObject values must be a dict, got {type(value).__name__}")
        self._data[key] = value

    def __getitem__(self, key: str) -> dict[str, Any]:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def step_names(self) -> list[str]:
        return list(self._data.keys())

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return dict(self._data)

    @classmethod
    def from_dict(cls, data: dict[str, dict[str, Any]]) -> StateObject:
        state = cls()
        for k, v in data.items():
            state[k] = v
        return state
