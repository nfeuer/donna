"""Terminal output helpers for the setup wizard."""

from __future__ import annotations

import sys

# ANSI color codes — disabled when not writing to a terminal.
_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

_GREEN = "\033[32m" if _USE_COLOR else ""
_RED = "\033[31m" if _USE_COLOR else ""
_YELLOW = "\033[33m" if _USE_COLOR else ""
_CYAN = "\033[36m" if _USE_COLOR else ""
_BOLD = "\033[1m" if _USE_COLOR else ""
_DIM = "\033[2m" if _USE_COLOR else ""
_RESET = "\033[0m" if _USE_COLOR else ""


def heading(text: str) -> None:
    """Print a bold section heading."""
    print(f"\n{_BOLD}{text}{_RESET}")
    print("=" * len(text))


def subheading(text: str) -> None:
    """Print a subsection heading."""
    print(f"\n{_BOLD}{text}{_RESET}")
    print("-" * len(text))


def step_header(number: int, total: int, name: str) -> None:
    """Print a step header like ``[3/13] Discord Guild``."""
    print(f"\n{_BOLD}{_CYAN}[{number}/{total}] {name}{_RESET}")


def passed(label: str, detail: str = "") -> None:
    """Print a green PASS line."""
    suffix = f" — {detail}" if detail else ""
    print(f"  {_GREEN}[PASS]{_RESET} {label}{_DIM}{suffix}{_RESET}")


def failed(label: str, detail: str = "") -> None:
    """Print a red FAIL line."""
    suffix = f" — {detail}" if detail else ""
    print(f"  {_RED}[FAIL]{_RESET} {label}{suffix}")


def warn(label: str, detail: str = "") -> None:
    """Print a yellow WARN line."""
    suffix = f" — {detail}" if detail else ""
    print(f"  {_YELLOW}[WARN]{_RESET} {label}{suffix}")


def skipped(label: str, detail: str = "") -> None:
    """Print a dimmed SKIP line."""
    suffix = f" — {detail}" if detail else ""
    print(f"  {_DIM}[SKIP]{_RESET} {_DIM}{label}{suffix}{_RESET}")


def done(label: str, detail: str = "") -> None:
    """Print a dimmed DONE line for previously completed steps."""
    suffix = f" — {detail}" if detail else ""
    print(f"  {_GREEN}[DONE]{_RESET} {_DIM}{label}{suffix}{_RESET}")


def info(text: str) -> None:
    """Print an informational line."""
    print(f"  {_DIM}{text}{_RESET}")


def error(text: str) -> None:
    """Print an error line."""
    print(f"  {_RED}{text}{_RESET}")


def mask_secret(value: str, show_prefix: int = 6, show_suffix: int = 4) -> str:
    """Mask a secret, showing only prefix and suffix characters.

    Example: ``sk-ant-api03-abc...WXYZ``
    """
    if len(value) <= show_prefix + show_suffix + 3:
        return "***"
    return f"{value[:show_prefix]}...{value[-show_suffix:]}"


def summary_line(total: int, passed_count: int, failed_count: int, skipped_count: int) -> None:
    """Print a final summary line."""
    parts: list[str] = []
    if passed_count:
        parts.append(f"{_GREEN}{passed_count} passed{_RESET}")
    if failed_count:
        parts.append(f"{_RED}{failed_count} failed{_RESET}")
    if skipped_count:
        parts.append(f"{_DIM}{skipped_count} skipped{_RESET}")
    print(f"\n{_BOLD}Result:{_RESET} {', '.join(parts)} (of {total} steps)")
