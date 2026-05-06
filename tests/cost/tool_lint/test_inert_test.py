"""Lint tests — inert_at_import test fixture (slice 22 §10.5 row 5)."""

from __future__ import annotations

from donna.cost.tool_lint.inert_test import check_inert_at_import_test

_GOOD_TEST = (
    "from donna.skills.tool_test_kit import is_inert_at_import\n"
    "\n"
    "def test_no_io_at_import():\n"
    "    is_inert_at_import('donna.skills.tools.foo')\n"
)


def test_passes_when_test_present_with_correct_call():
    src = {"tests/skills/tools/test_foo.py": _GOOD_TEST}
    failures = check_inert_at_import_test(
        ["tests/skills/tools/test_foo.py"], src, "foo"
    )
    assert failures == []


def test_fails_when_test_file_missing():
    failures = check_inert_at_import_test([], {}, "foo")
    assert any(f.rule == "inert_test" for f in failures)


def test_fails_when_test_file_lacks_call():
    src = {
        "tests/skills/tools/test_foo.py": (
            "def test_smoke():\n    assert True\n"
        ),
    }
    failures = check_inert_at_import_test(
        ["tests/skills/tools/test_foo.py"], src, "foo"
    )
    assert any(f.rule == "inert_test" for f in failures)


def test_fails_when_call_uses_wrong_module():
    src = {
        "tests/skills/tools/test_foo.py": (
            "from donna.skills.tool_test_kit import is_inert_at_import\n"
            "def test_smoke():\n"
            "    is_inert_at_import('some.other.module')\n"
        ),
    }
    failures = check_inert_at_import_test(
        ["tests/skills/tools/test_foo.py"], src, "foo"
    )
    assert any(f.rule == "inert_test" for f in failures)


def test_passes_with_dotted_call_form():
    src = {
        "tests/skills/tools/test_foo.py": (
            "import donna.skills.tool_test_kit as kit\n"
            "def test_smoke():\n"
            "    kit.is_inert_at_import('donna.skills.tools.foo')\n"
        ),
    }
    failures = check_inert_at_import_test(
        ["tests/skills/tools/test_foo.py"], src, "foo"
    )
    assert failures == []
