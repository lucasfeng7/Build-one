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

# swDimensionType_e (returned by IDisplayDimension::Type2). The enum is
# contiguous from 0 in declaration order; the three linear values below are
# documented and the rest follow the same sequence (Unknown=0, Ordinate=1,
# Linear=2, Angular=3, ArcLength=4, Radial=5, Diameter=6, HorOrdinate=7,
# VertOrdinate=8, ZAxis=9, Chamfer=10, HorLinear=11, VertLinear=12).
SW_LINEAR_DIM: int = 2
SW_HOR_LINEAR_DIM: int = 11
SW_VERT_LINEAR_DIM: int = 12
SW_ANGULAR_DIM: int = 3
SW_ARC_LENGTH_DIM: int = 4
SW_RADIAL_DIM: int = 5
SW_DIAMETER_DIM: int = 6
SW_ORDINATE_DIM: int = 1
SW_HOR_ORDINATE_DIM: int = 7
SW_VERT_ORDINATE_DIM: int = 8

# swUserPreferenceToggle_e.swDisableMessages — used only in the late-binding
# fallback path in sw_client.connect(); early-binding reads it from constants.
SW_DISABLE_MESSAGES: int = 263

# swDimensionTextParts_e — selectors for IDisplayDimension::GetText(part). These
# pick the non-value annotation text around a dimension (a leading ⌀ / "M6" etc.
# in the prefix, a trailing "TYP" / "MAX" etc. in the suffix). The enum is small
# and stable across SolidWorks releases; synced from the live type library on
# the early-binding path (see sw_client._sync_constants).
SW_DIM_TEXT_PREFIX: int = 1
SW_DIM_TEXT_SUFFIX: int = 2

LINEAR_DIM_TYPES: Final = frozenset({SW_LINEAR_DIM, SW_HOR_LINEAR_DIM, SW_VERT_LINEAR_DIM})

# Dimension-type families grouped by the *unit* SolidWorks stores their value
# and tolerance in. The constant policy (and the future ML model) must emit a
# tolerance in the right unit per family, so decide.tolerance_for branches on
# these sets rather than on individual types.
#
# Length family — value/tolerance in METERS, so the ±0.5 mm default applies.
LENGTH_DIM_TYPES: Final = LINEAR_DIM_TYPES | frozenset({
    SW_DIAMETER_DIM,
    SW_RADIAL_DIM,
    SW_ARC_LENGTH_DIM,
    SW_ORDINATE_DIM,
    SW_HOR_ORDINATE_DIM,
    SW_VERT_ORDINATE_DIM,
})
# Angular family — value/tolerance in RADIANS, so it needs its own default.
ANGULAR_DIM_TYPES: Final = frozenset({SW_ANGULAR_DIM})


@dataclass(frozen=True)
class Feature:
    value: float
    dim_type: int
    current_tolerance_type: int
    view_name: str
    sheet_name: str
    is_hole_callout: bool = False
    # Richer context the predictor reasons over (and the future ML model trains
    # on). All have safe defaults so the extractor can degrade gracefully when a
    # COM read is unavailable, and so existing call sites/tests stay valid.
    #
    # is_reference: the dim is a reference/driven dim (shown in parentheses).
    #   These are never toleranced, so decide skips them — both a correctness
    #   fix and a real signal.
    # text_prefix / text_suffix: the non-value annotation text around the dim
    #   (e.g. a leading "⌀" / "M6", a trailing "TYP" / "MAX"), which carries
    #   intent the bare dim_type misses.
    is_reference: bool = False
    text_prefix: str = ""
    text_suffix: str = ""


@dataclass(frozen=True)
class Tolerance:
    tol_type: int
    plus_value: float
    minus_value: float
