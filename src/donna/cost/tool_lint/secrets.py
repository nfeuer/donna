"""§10.5 row 2 — block hardcoded credential values in tool source.

Two-tier check:

1. **Curated regex set** (always on). Covers the common
   provider-prefixed token shapes — ``sk-…``, ``xoxb-…``, ``ghp_…``,
   ``AKIA…`` (AWS), PEM private-key headers, plus a vault-key naming
   convention guard (any module-level string assigned to ``*_secret``,
   ``*_token``, ``*_api_key`` that doesn't go through ``vault.read``).
2. **detect-secrets shim** (opt-in via ``ToolLintConfig.detect_secrets_enabled``).
   When ``True`` and the package is importable, runs
   :func:`detect_secrets.scan` on the same text and merges findings.
   Slice 22 ships the regex set as the default; the shim is wired so
   slice 24 (escalation hardening) can flip the flag without code
   changes.

Curated regex selection prioritises low false-positive rate over
exhaustive coverage. The vault-key heuristic intentionally ignores
``"vault.read('foo')"`` — that's the *correct* pattern.
"""

from __future__ import annotations

import importlib.util
import re

from donna.cost.tool_lint.types import LintFailure

_PROVIDER_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_api_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_api_key", re.compile(r"sk-[A-Za-z0-9]{40,}")),
    ("slack_bot_token", re.compile(r"xoxb-[A-Za-z0-9\-]{8,}")),
    ("slack_app_token", re.compile(r"xapp-[A-Za-z0-9\-]{8,}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_header", re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
)

# Match assignment of a long string to a name suggesting a credential.
# Catches ``MY_API_KEY = "abc123…"`` patterns that don't trigger the
# provider regexes. Whitespace around `=` is allowed.
_VAULT_NAME_ASSIGNMENT = re.compile(
    r"""(?xim)
    ^                              # line start
    [ \t]*                         # leading indent
    ([A-Z_]*(?:SECRET|TOKEN|API_KEY|PASSWORD|CREDENTIAL)[A-Z_]*)
    \s*=\s*
    (['"])([^'"]{16,})\2           # quoted long string
    """
)


def _scan_provider_tokens(text: str, path: str) -> list[LintFailure]:
    failures: list[LintFailure] = []
    for rule_name, pattern in _PROVIDER_TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            failures.append(
                LintFailure(
                    rule=f"secrets:{rule_name}",
                    path=path,
                    line=line,
                    message=(
                        f"hardcoded credential matching {rule_name} pattern "
                        "— use vault.read('<name>') instead"
                    ),
                )
            )
    return failures


def _scan_vault_naming(text: str, path: str) -> list[LintFailure]:
    failures: list[LintFailure] = []
    for match in _VAULT_NAME_ASSIGNMENT.finditer(text):
        # Skip if vault.read appears in the value — that's the correct pattern.
        value = match.group(3)
        if "vault.read" in value or "os.environ" in value or "getenv" in value:
            continue
        line = text.count("\n", 0, match.start()) + 1
        failures.append(
            LintFailure(
                rule="secrets:vault_naming",
                path=path,
                line=line,
                message=(
                    f"`{match.group(1)} = '...'` looks like an inline "
                    "credential — use vault.read('<name>') instead"
                ),
            )
        )
    return failures


def _scan_with_detect_secrets(text: str, path: str) -> list[LintFailure]:
    if importlib.util.find_spec("detect_secrets") is None:
        return []
    try:
        # Lazy import; library is optional.
        from detect_secrets.core.scan import scan_line  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - defensive
        return []
    failures: list[LintFailure] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for finding in scan_line(line):
            failures.append(
                LintFailure(
                    rule=f"secrets:detect-secrets:{finding.type}",
                    path=path,
                    line=line_no,
                    message=(
                        f"detect-secrets flagged {finding.type} — review and "
                        "move to vault.read() if real"
                    ),
                )
            )
    return failures


def scan_for_secrets(
    text: str,
    path: str,
    *,
    detect_secrets_enabled: bool = False,
) -> list[LintFailure]:
    """Scan ``text`` for hardcoded credentials.

    Args:
        text: File source.
        path: Logical path (for the failure record).
        detect_secrets_enabled: If True and the ``detect-secrets``
            package is importable, also run the library scanner.
    """
    failures: list[LintFailure] = []
    failures.extend(_scan_provider_tokens(text, path))
    failures.extend(_scan_vault_naming(text, path))
    if detect_secrets_enabled:
        failures.extend(_scan_with_detect_secrets(text, path))
    return failures
