import sys
from pathlib import Path

# The watchdog lives under docker/ (its own image), not src/. Put it on the
# path so unit tests can import its pure functions without Docker.
_HEALTHWATCH_DIR = Path(__file__).resolve().parents[3] / "docker" / "healthwatch"
if str(_HEALTHWATCH_DIR) not in sys.path:
    sys.path.insert(0, str(_HEALTHWATCH_DIR))
