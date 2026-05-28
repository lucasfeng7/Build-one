"""Data structures and SolidWorks enum mirrors.

Intentionally free of COM imports so decide.py can be unit-tested on macOS
without SolidWorks. sw_client.py overwrites the SW_* defaults below against
win32com.client.constants at runtime, so the hardcoded values only matter
on hosts without SolidWorks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Defaults from swconst.tlb (SolidWorks 2020 SDK). Overwritten at connect time.
SW_DOC_DRAWING: int = 3

SW_TOL_NONE: int = 1
SW_TOL_BILAT: int = 3

SW_LINEAR_DIM: int = 8
SW_HOR_LINEAR_DIM: int = 6
SW_VERT_LINEAR_DIM: int = 11

LINEAR_DIM_TYPES: Final = frozenset({SW_LINEAR_DIM, SW_HOR_LINEAR_DIM, SW_VERT_LINEAR_DIM})


@dataclass(frozen=True)
class Feature:
    value: float
    dim_type: int
    current_tolerance_type: int
    view_name: str
    sheet_name: str
    is_hole_callout: bool = False


@dataclass(frozen=True)
class Tolerance:
    tol_type: int
    plus_value: float
    minus_value: float
