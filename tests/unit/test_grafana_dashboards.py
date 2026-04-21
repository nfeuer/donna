"""Smoke tests for Grafana dashboard JSON shipped in docker/grafana/dashboards/.

Validates that each dashboard parses, references only the configured
datasources, and has non-empty queries on every panel target. Does not
execute the queries — that's a separate local-stack smoke test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS_DIR = REPO_ROOT / "docker" / "grafana" / "dashboards"
DATASOURCES_DIR = REPO_ROOT / "docker" / "grafana" / "datasources"

# Datasource names declared by docker/grafana/datasources/*.yaml. Any panel
# target referencing a name outside this set would fail to resolve at load
# time in the provisioned Grafana container.
_ALLOWED_DATASOURCES = {"Loki", "DonnaLogs"}


def _load_dashboards() -> list[tuple[str, dict]]:
    files = sorted(DASHBOARDS_DIR.glob("*.json"))
    assert files, f"no dashboard JSON files in {DASHBOARDS_DIR}"
    return [(p.name, json.loads(p.read_text())) for p in files]


@pytest.mark.parametrize("name,dashboard", _load_dashboards())
def test_dashboard_json_parses(name: str, dashboard: dict) -> None:
    assert dashboard.get("title"), f"{name}: missing title"
    assert dashboard.get("uid"), f"{name}: missing uid"
    assert isinstance(dashboard.get("panels"), list), f"{name}: panels not a list"


@pytest.mark.parametrize("name,dashboard", _load_dashboards())
def test_dashboard_panels_reference_allowed_datasources(
    name: str, dashboard: dict,
) -> None:
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            ds = target.get("datasource")
            if ds is None:
                continue
            ds_name = ds if isinstance(ds, str) else ds.get("type") or ds.get("name")
            assert ds_name in _ALLOWED_DATASOURCES, (
                f"{name}: panel {panel.get('title')!r} references "
                f"unknown datasource {ds_name!r}; allowed: "
                f"{sorted(_ALLOWED_DATASOURCES)}"
            )


@pytest.mark.parametrize("name,dashboard", _load_dashboards())
def test_dashboard_panels_have_non_empty_queries(name: str, dashboard: dict) -> None:
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            has_query = bool(target.get("expr")) or bool(target.get("rawSql"))
            assert has_query, (
                f"{name}: panel {panel.get('title')!r} target has neither "
                f"`expr` (LogQL) nor `rawSql` (SQL)"
            )


def test_skill_system_dashboard_has_four_panels() -> None:
    """Wave 3 F-12 delivers exactly the 4 panels specified in the backlog:
    state distribution, evolution success rate, nightly cron outcomes,
    cost by skill."""
    path = DASHBOARDS_DIR / "skill_system.json"
    assert path.exists(), "docker/grafana/dashboards/skill_system.json missing"
    dash = json.loads(path.read_text())
    assert len(dash["panels"]) == 4, (
        f"expected 4 panels, got {len(dash['panels'])}"
    )

    by_title = {p["title"] for p in dash["panels"]}
    assert by_title == {
        "Skill State Distribution Over Time",
        "Evolution Success Rate per Skill",
        "Nightly Cron Outcomes",
        "Cost Breakdown by Skill",
    }


def test_skill_system_cost_panel_uses_sql_datasource() -> None:
    """Panel 4 (cost by skill) must query the SQL datasource because
    invocation_log lives in SQLite, not Loki (see Wave 3 plan A1)."""
    dash = json.loads((DASHBOARDS_DIR / "skill_system.json").read_text())
    cost_panel = next(
        p for p in dash["panels"] if p["title"] == "Cost Breakdown by Skill"
    )
    assert cost_panel["targets"][0]["datasource"] == "DonnaLogs"
    assert "rawSql" in cost_panel["targets"][0]
    assert "invocation_log" in cost_panel["targets"][0]["rawSql"]
    assert "skill_id" in cost_panel["targets"][0]["rawSql"]


def test_sqlite_datasource_yaml_declared() -> None:
    """The SQL datasource the skill_system dashboard references must be
    provisioned by a YAML in docker/grafana/datasources/."""
    yaml_path = DATASOURCES_DIR / "sqlite.yaml"
    assert yaml_path.exists(), (
        "docker/grafana/datasources/sqlite.yaml missing — the DonnaLogs "
        "datasource referenced by skill_system.json will not resolve"
    )
    text = yaml_path.read_text()
    assert "frser-sqlite-datasource" in text
    assert "DonnaLogs" in text
