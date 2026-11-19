"""Where the app keeps its files (source checkout or frozen .exe).

A PyInstaller one-file build unpacks the code into a temp dir, so ``__file__``
points at ``_MEIPASS`` while ``data/`` must stay next to the ``.exe``. Everything
that needs the folder uses this module instead of guessing.
"""

from __future__ import annotations

import os
import sys


def app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    return os.path.join(app_root(), "data")
