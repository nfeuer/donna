"""Tests for donna.skills.tool_test_kit (slice 22)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from donna.skills.tool_test_kit import is_inert_at_import


def _write_module(tmp_path: Path, pkg_name: str, name: str, src: str) -> None:
    pkg = tmp_path / pkg_name
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / f"{name}.py").write_text(textwrap.dedent(src))
    sys.path.insert(0, str(tmp_path))


def test_inert_module_passes(tmp_path):
    _write_module(
        tmp_path,
        "pkgroot_inert",
        "_slc22_inert",
        """
        from typing import Any

        async def foo(x: Any) -> Any:
            return x
        """,
    )
    try:
        is_inert_at_import("pkgroot_inert._slc22_inert")
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("pkgroot_inert._slc22_inert", None)
        sys.modules.pop("pkgroot_inert", None)


def test_module_with_import_time_open_raises(tmp_path):
    _write_module(
        tmp_path,
        "pkgroot_io",
        "_slc22_io",
        """
        DATA = open(__file__).read()
        """,
    )
    try:
        with pytest.raises(AssertionError, match="I/O at import time"):
            is_inert_at_import("pkgroot_io._slc22_io")
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("pkgroot_io._slc22_io", None)
        sys.modules.pop("pkgroot_io", None)
