"""The swap point: pure function from Feature to Tolerance.

MVP body is a constant policy: bilateral 0.5 mm on untoleranced length-valued
dims, and bilateral 1° on untoleranced angular dims. A future ML model replaces
this function without touching extract or apply. Keep this file free of COM
imports so it stays unit-testable on macOS.
"""
from __future__ import annotations

import math
from typing import Final

from .models import (
    ANGULAR_DIM_TYPES,
    LENGTH_DIM_TYPES,
    SW_TOL_BILAT,
    SW_TOL_NONE,
    Feature,
    Tolerance,
)

# Length dims store value/tolerance in metres; angular dims in radians. The two
# defaults are the per-unit analogues of the same flat MVP policy.
_HALF_WIDTH_METERS: Final[float] = 0.0005  # 0.5 mm
_ANGULAR_HALF_WIDTH_RAD: Final[float] = math.radians(1.0)  # 1°


def tolerance_for(f: Feature) -> Tolerance | None:
    if f.current_tolerance_type != SW_TOL_NONE:
        return None
    if f.is_hole_callout:
        return None
    if f.dim_type in LENGTH_DIM_TYPES:
        half_width = _HALF_WIDTH_METERS
    elif f.dim_type in ANGULAR_DIM_TYPES:
        half_width = _ANGULAR_HALF_WIDTH_RAD
    else:
        return None
    return Tolerance(
        tol_type=SW_TOL_BILAT,
        plus_value=half_width,
        minus_value=half_width,
    )
