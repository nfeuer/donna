"""Deterministic tool-invocation fingerprinting for validation mocks.

Each tool has a rule that selects the subset of args relevant for
identifying a unique invocation. ``web_fetch`` keys only on ``url`` —
timeouts and headers don't change the response. ``gmail_read`` keys
only on ``message_id``. Tools without an explicit rule fall back to
canonical sorted-JSON of all args.

Future tools should register an explicit rule when they have dynamic
args (tokens, nonces, timestamps) that should be ignored.
"""

from __future__ import annotations

import json
from collections.abc import Callable

_RULES: dict[str, Callable[[dict], dict]] = {
    "web_fetch": lambda args: {"url": args["url"]},
    "gmail_read": lambda args: {"message_id": args["message_id"]},
    "gmail_send": lambda args: {
        "to": args["to"], "subject": args["subject"], "body": args["body"],
    },
}


def fingerprint(tool_name: str, args: dict) -> str:
    """Return a stable fingerprint for a tool invocation.

    Raises ``KeyError`` if an explicit rule requires a field absent from
    ``args`` — this surfaces bad fixture data early.
    """
    rule = _RULES.get(tool_name)
    canonical_args = rule(args) if rule is not None else args
    encoded = json.dumps(canonical_args, sort_keys=True, separators=(",", ":"))
    return f"{tool_name}:{encoded}"
