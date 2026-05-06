"""Lint tests — allowlist (slice 22 §10.5 row 4)."""

from __future__ import annotations

from donna.cost.tool_lint.allowlist import check_allowlist_update


def test_passes_when_unallowlisted_marker_present():
    src = {
        "src/donna/skills/tools/foo.py": (
            "unallowlisted = True\n"
            "requires_rebuild = False\n"
        ),
    }
    failures = check_allowlist_update(["src/donna/skills/tools/foo.py"], src, "foo")
    assert failures == []


def test_passes_when_agents_yaml_mentions_tool():
    src = {
        "src/donna/skills/tools/foo.py": "",
        "config/agents.yaml": (
            "agents:\n"
            "  - name: writer\n"
            "    tools:\n"
            "      - foo\n"
        ),
    }
    failures = check_allowlist_update(
        ["src/donna/skills/tools/foo.py", "config/agents.yaml"], src, "foo"
    )
    assert failures == []


def test_fails_when_no_allowlist_touched():
    src = {"src/donna/skills/tools/foo.py": ""}
    failures = check_allowlist_update(
        ["src/donna/skills/tools/foo.py"], src, "foo"
    )
    assert any(f.rule == "allowlist" for f in failures)


def test_fails_when_allowlist_touched_but_tool_missing():
    src = {
        "src/donna/skills/tools/foo.py": "",
        "config/agents.yaml": "agents: []\n",
    }
    failures = check_allowlist_update(
        ["src/donna/skills/tools/foo.py", "config/agents.yaml"], src, "foo"
    )
    assert any(f.rule == "allowlist" for f in failures)


def test_passes_when_skills_yaml_mentions_tool():
    src = {
        "src/donna/skills/tools/foo.py": "",
        "config/skills.yaml": (
            "skills:\n"
            "  - name: my_skill\n"
            "    tools:\n"
            "      - foo\n"
        ),
    }
    failures = check_allowlist_update(
        ["src/donna/skills/tools/foo.py", "config/skills.yaml"], src, "foo"
    )
    assert failures == []
