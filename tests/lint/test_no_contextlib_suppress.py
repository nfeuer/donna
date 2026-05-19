"""Lint: ban contextlib.suppress(Exception) in application code.

Blanket exception suppression hides failures that should be observable.
Use explicit try/except with logging and dispatch_fallback_alert() instead.
See CLAUDE.md Conventions and 2026-05-19-fallback-observability-design.md §4.
"""

from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "donna"
BANNED_PATTERN = "contextlib.suppress(Exception)"


def test_no_contextlib_suppress_in_src() -> None:
    violations: list[str] = []
    for py_file in SRC_DIR.rglob("*.py"):
        text = py_file.read_text()
        for i, line in enumerate(text.splitlines(), start=1):
            if BANNED_PATTERN in line:
                rel = py_file.relative_to(SRC_DIR.parent.parent)
                violations.append(f"{rel}:{i}: {line.strip()}")

    assert not violations, (
        f"Found {len(violations)} contextlib.suppress(Exception) usage(s).\n"
        "Replace with explicit try/except + logger.warning(..., event_type='fallback_activated').\n"
        "See CLAUDE.md Conventions.\n\n"
        + "\n".join(violations)
    )
