"""Re-key a skill_run.tool_result_cache into fingerprint-keyed mocks.

Shared between runtime (EvolutionGates, capture-fixture endpoint) and the
Alembic backfill migration `add_fixture_tool_mocks.py`. The migration has
its own inline implementation because migrations must be runnable
standalone (no imports from the application package). The duplication is
intentional and documented in both places.

Runtime callers should prefer this helper so rule-based tools
(web_fetch, gmail_*) produce fingerprints that match the live
MockToolRegistry's dispatch-time fingerprinting.
"""

from __future__ import annotations

import copy
from typing import Any

from donna.skills.tool_fingerprint import fingerprint


def cache_to_mocks(tool_result_cache: dict[str, Any]) -> dict[str, Any]:
    """Transform cache_id-keyed entries into fingerprint-keyed mocks.

    Input: ``{cache_id: {"tool": str, "args": dict, "result": Any}}``
    Output: ``{f"{tool}:{canonical_args}": result}``

    Entries missing ``tool`` or ``result``, or whose value is not a dict,
    are silently skipped. Results are deep-copied so callers can mutate
    the output without affecting the source cache.
    """
    mocks: dict[str, Any] = {}
    for entry in tool_result_cache.values():
        if not isinstance(entry, dict):
            continue
        tool = entry.get("tool")
        args = entry.get("args") or {}
        result = entry.get("result")
        if tool is None or result is None:
            continue
        fp = fingerprint(tool, args)
        mocks[fp] = copy.deepcopy(result)
    return mocks
