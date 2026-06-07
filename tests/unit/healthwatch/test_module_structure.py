"""Guard against the __main__-ordering bug.

`healthwatch.py` runs as a script (`python healthwatch.py`), so its
``if __name__ == "__main__": main()`` guard executes ``main()`` during module
load. Any function ``main()`` transitively calls (e.g. ``classify``) must be
defined *before* that guard, or the script crashes at runtime with a NameError
that the import-based unit tests cannot see. This test fails if any function or
class is defined after the guard.
"""
import ast
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "docker" / "healthwatch" / "healthwatch.py"


def _is_main_guard(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


def test_no_definitions_after_main_guard():
    body = ast.parse(_SCRIPT.read_text()).body
    guard_indices = [i for i, node in enumerate(body) if _is_main_guard(node)]
    assert guard_indices, "missing `if __name__ == '__main__':` guard"
    after_guard = body[guard_indices[0] + 1:]
    offenders = [
        node.name
        for node in after_guard
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert not offenders, (
        f"definitions after the __main__ guard run too late for main(): {offenders}"
    )
