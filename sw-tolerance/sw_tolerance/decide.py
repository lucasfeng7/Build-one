"""The swap point: pure function from Feature to Tolerance.

MVP body is a constant policy (bilateral 0.5 mm on untoleranced linear dims).
A future ML model replaces this function without touching extract or apply.
Keep this file free of COM imports so it stays unit-testable on macOS.
"""
from __future__ import annotations

from typing import Final

from .models import (
    LINEAR_DIM_TYPES,
    SW_TOL_BILAT,
    SW_TOL_NONE,
    Feature,
    Tolerance,
)

_HALF_WIDTH_METERS: Final[float] = 0.0005  # 0.5 mm


def tolerance_for(f: Feature) -> Tolerance | None:
    if f.current_tolerance_type != SW_TOL_NONE:
        return None
    if f.dim_type not in LINEAR_DIM_TYPES:
        return None
    if f.is_hole_callout:
        return None
    return Tolerance(
        tol_type=SW_TOL_BILAT,
        plus_value=_HALF_WIDTH_METERS,
        minus_value=_HALF_WIDTH_METERS,
    )
