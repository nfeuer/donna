"""Load default_alert_conditions from capabilities.yaml by capability name."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class CapabilityDefaultAlertsLookup:
    def __init__(self, yaml_path: Path) -> None:
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
        self._defaults: dict[str, dict[str, Any]] = {}
        for entry in data.get("capabilities", []):
            name = entry.get("name")
            conditions = entry.get("default_alert_conditions")
            if name and conditions:
                self._defaults[name] = conditions

    def get(self, capability_name: str) -> dict[str, Any] | None:
        return self._defaults.get(capability_name)
