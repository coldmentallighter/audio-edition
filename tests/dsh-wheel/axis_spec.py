"""The frozen chart axis, re-exported from `backend/chart_axis.py`.

**The spec itself lives in `backend/`** -- the renderer consumes it, and `backend` must
not import from `tests/`. This module stays so that every documented command keeps
working:

    python tests/dsh-wheel/axis_spec.py     # axis table + invariants -> ALL PASS

Everything below is the backend module, re-exported: there is exactly one definition of
the F / knee map, and it is not here.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.chart_axis import *                                       # noqa: F401,F403
from backend.chart_axis import (KNEE, KNEE_SHARE, LABELLED, LABEL_HEIGHT,  # noqa: F401
                                LOG_KNEE, LOG_KNEE_BASE, LOG_SHIFT,
                                LUFS_BOTTOM, LUFS_TOP, PLOT_H_REF, RED_ABOVE,
                                TICKS, UNLABELLED, check, frac, invert,
                                is_labelled, is_red, layout_report,
                                min_plot_height, ticks, top_clearance, y)

if __name__ == "__main__":
    print(layout_report())
    print()
    print("invariants:")
    print("ALL PASS" if check() else "PROBLEMS")
