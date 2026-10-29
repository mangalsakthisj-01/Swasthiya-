from pathlib import Path
import sys

# Support both `pytest` and `python -m pytest` invocations in CI and local shells.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
