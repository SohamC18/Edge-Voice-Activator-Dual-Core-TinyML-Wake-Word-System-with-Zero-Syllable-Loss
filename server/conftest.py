"""Makes ``edge_voice`` importable without an editable install.

Keeps ``pytest`` runnable straight from a fresh clone (``python -m pytest``)
while ``server/pyproject.toml`` still supports ``pip install -e ./server`` for
a proper environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
