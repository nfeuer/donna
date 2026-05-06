"""Lint tests — import_io (slice 22 §10.5 row 5)."""

from __future__ import annotations

import ast

from donna.cost.tool_lint.import_io import check_import_time_io


def _tree(src: str) -> ast.AST:
    return ast.parse(src)


def test_rejects_top_level_requests_get():
    failures = check_import_time_io(
        _tree("import requests\nrequests.get('https://x')\n"), "x.py"
    )
    assert any(f.rule == "import_io" for f in failures)


def test_rejects_top_level_open():
    failures = check_import_time_io(_tree("open('/etc/passwd')\n"), "x.py")
    assert any("open" in f.message for f in failures)


def test_rejects_top_level_subprocess_run():
    failures = check_import_time_io(
        _tree("import subprocess\nsubprocess.run(['ls'])\n"), "x.py"
    )
    assert len(failures) == 1


def test_allows_call_inside_function():
    src = (
        "import requests\n"
        "def fetch():\n"
        "    return requests.get('https://x').text\n"
    )
    failures = check_import_time_io(_tree(src), "x.py")
    assert failures == []


def test_allows_call_inside_class_method():
    src = (
        "import requests\n"
        "class C:\n"
        "    def m(self):\n"
        "        return requests.get('https://x')\n"
    )
    failures = check_import_time_io(_tree(src), "x.py")
    assert failures == []


def test_passes_clean_module():
    src = (
        "from typing import Any\n"
        "DEFAULT = 5\n"
        "def f(x: Any) -> Any:\n"
        "    return x\n"
    )
    failures = check_import_time_io(_tree(src), "x.py")
    assert failures == []


def test_descends_into_top_level_if_block():
    src = (
        "import os\n"
        "if os.environ.get('X'):\n"
        "    import requests\n"
        "    requests.get('https://x')\n"
    )
    failures = check_import_time_io(_tree(src), "x.py")
    assert any(f.rule == "import_io" for f in failures)


def test_rejects_pathlib_read_text_at_top_level():
    src = (
        "from pathlib import Path\n"
        "DATA = Path('/tmp/x').read_text()\n"
    )
    failures = check_import_time_io(_tree(src), "x.py")
    assert any("pathlib" in f.message for f in failures)
