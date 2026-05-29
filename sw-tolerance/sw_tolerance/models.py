"""Data structures and SolidWorks enum mirrors.

Intentionally free of COM imports so decide.py can be unit-tested on macOS
without SolidWorks.

sw_client.connect() overwrites the SW_* defaults below from
win32com.client.constants when early binding (makepy) succeeds. But under the
late-binding fallback (makepy can't build the type-library cache — the common
SolidWorks first-run case) those constants are NOT available, so these
hardcoded values are what the tool actually runs against. They must therefore
match the real swconst values, not just be best-guesses. The values below are
the documented swconst enum values and are correct for current SolidWorks
releases.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# swDocumentTypes_e.swDocDRAWING
SW_DOC_DRAWING: int = 3

# swTolType_e
SW_TOL_NONE: int = 0
SW_TOL_BILAT: int = 2

# swDimensionType_e (returned by IDisplayDimension::Type2)
SW_LINEAR_DIM: int = 2
SW_HOR_LINEAR_DIM: int = 11
SW_VERT_LINEAR_DIM: int = 12

# swUserPreferenceToggle_e.swDisableMessages — used only in the late-binding
# fallback path in sw_client.connect(); early-binding reads it from constants.
SW_DISABLE_MESSAGES: int = 263

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
