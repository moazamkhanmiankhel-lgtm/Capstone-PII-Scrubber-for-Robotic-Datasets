"""Make the project root importable when pytest is run directly.

Without this file ``pytest`` only works as ``python -m pytest`` from the
project root, because ``robopii`` would not be on ``sys.path``.
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
