"""Reusable test helpers for tool branches (slice 22).

Tool-build branches must include a regression test that asserts the
tool module is **inert** at import time — i.e. importing it does not
trigger any network or disk I/O. The lint pipeline checks the test
*exists* (see :mod:`donna.cost.tool_lint.inert_test`); this module
provides the helper that the test calls so the assertion is reusable.

Usage in ``tests/skills/tools/test_<tool_name>.py``:

.. code-block:: python

    from donna.skills.tool_test_kit import is_inert_at_import

    def test_no_io_at_import():
        is_inert_at_import("donna.skills.tools.<tool_name>")

The helper stubs out network / disk modules **before** the import,
runs the import, and asserts no stub recorded a call.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
from typing import Any
from unittest import mock


class _RecordingMock(mock.MagicMock):
    """Records every call but never raises by default."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.__call_paths: list[str] = []  # type: ignore[attr-defined]


def is_inert_at_import(module_name: str) -> None:
    """Import ``module_name`` under stubbed I/O; assert no calls happened.

    Stubs out ``socket.socket``, ``urllib.request.urlopen``, ``open``
    (builtins), ``subprocess.run``, ``subprocess.Popen``,
    ``requests.get`` / ``post`` / ``request`` if importable, and
    ``httpx.get`` / ``post`` / ``request`` if importable.

    Raises:
        AssertionError: if any stub was called during import.
    """
    # Drop cached module so the import fully re-runs under our stubs.
    if module_name in sys.modules:
        del sys.modules[module_name]
    parent = module_name.rsplit(".", 1)[0]
    if parent in sys.modules and parent != module_name:
        # Don't drop parent packages — only the leaf.
        pass

    patches: list[Any] = []
    recorders: list[mock.MagicMock] = []

    def _record(target: str) -> mock.MagicMock | None:
        try:
            patcher = mock.patch(target, new_callable=_RecordingMock)
        except (AttributeError, ModuleNotFoundError):
            return None
        try:
            recorder = patcher.start()
        except (AttributeError, ModuleNotFoundError):
            return None
        patches.append(patcher)
        recorders.append(recorder)
        return recorder

    targets = [
        "socket.socket",
        "urllib.request.urlopen",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.check_output",
        "subprocess.check_call",
        "builtins.open",
    ]
    for opt in ("requests.get", "requests.post", "requests.request"):
        targets.append(opt)
    for opt in ("httpx.get", "httpx.post", "httpx.request", "httpx.Client"):
        targets.append(opt)
    for opt in ("aiohttp.ClientSession",):
        targets.append(opt)

    for target in targets:
        _record(target)

    try:
        importlib.import_module(module_name)
        offending = [
            (rec, rec.call_count)
            for rec in recorders
            if rec.call_count > 0
        ]
        if offending:
            details = ", ".join(
                f"{rec._extract_mock_name() or 'mock'}={count}"  # type: ignore[attr-defined]
                for rec, count in offending
            )
            raise AssertionError(
                f"`{module_name}` performed I/O at import time: {details}"
            )
    finally:
        for patcher in patches:
            with contextlib.suppress(RuntimeError):
                patcher.stop()
