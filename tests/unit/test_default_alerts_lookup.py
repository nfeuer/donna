"""CapabilityDefaultAlertsLookup — loads default_alert_conditions from YAML."""
from pathlib import Path

import yaml

from donna.capabilities.default_alerts_lookup import CapabilityDefaultAlertsLookup


def _write_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "capabilities.yaml"
    p.write_text(yaml.dump({
        "capabilities": [
            {
                "name": "product_watch",
                "description": "watch",
                "trigger_type": "on_schedule",
                "default_alert_conditions": {
                    "field": "triggers_alert",
                    "op": "==",
                    "value": True,
                },
            },
            {
                "name": "no_alerts_cap",
                "description": "none",
                "trigger_type": "ad_hoc",
            },
        ]
    }))
    return p


def test_lookup_returns_conditions(tmp_path: Path) -> None:
    lookup = CapabilityDefaultAlertsLookup(_write_yaml(tmp_path))
    result = lookup.get("product_watch")
    assert result == {"field": "triggers_alert", "op": "==", "value": True}


def test_lookup_returns_none_for_no_defaults(tmp_path: Path) -> None:
    lookup = CapabilityDefaultAlertsLookup(_write_yaml(tmp_path))
    assert lookup.get("no_alerts_cap") is None


def test_lookup_returns_none_for_unknown(tmp_path: Path) -> None:
    lookup = CapabilityDefaultAlertsLookup(_write_yaml(tmp_path))
    assert lookup.get("nonexistent") is None
