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
from typing import Final, Optional, Tuple

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


# Feature-kind taxonomy stored in GeometryContext.feature_kind. String-valued
# (not an int enum) because it's a *derived* classification, not a SolidWorks
# enum: the extractor maps a 3D feature/face onto one of these, the predictor
# reasons over them, and the ML model will one-hot them. Keep the vocabulary
# small and stable — adding a value is a (harmless) dataset-schema change.
FEATURE_KIND_VALUES: Final = frozenset({
    "hole_clearance", "hole_tapped", "counterbore", "countersink",
    "boss", "fillet", "chamfer", "planar_face", "cylindrical_face", "unknown",
})
# Surface-type taxonomy stored in GeometryContext.surface_type, from the
# ISurface.IsPlane/IsCylinder/IsCone family.
SURFACE_TYPE_VALUES: Final = frozenset({
    "plane", "cylinder", "cone", "other", "unknown",
})


@dataclass(frozen=True)
class GeometryContext:
    """3D-model context resolved from the part behind a drawing dimension.

    Populated by the (COM-bearing, heavily guarded) ``geometry.resolve_geometry``
    from the faces/feature a display dimension attaches to in the referenced part.
    This is what lets the predictor tell a precision bore from a rough slot, and
    is the input that makes intelligent GD&T selection possible.

    Every field defaults so extraction degrades gracefully: a missing/over-version
    COM member, or a dimension whose view references an assembly (parts-only for
    now), yields a partly- or wholly-``unknown`` context rather than dropping the
    dimension. Kept COM-free and pure (invariant #1) so it serialises into the
    report and harvest dataset via ``asdict`` and is unit-testable on macOS.

    Fields:
    - feature_kind: one of FEATURE_KIND_VALUES — what the dim measures.
    - surface_type: one of SURFACE_TYPE_VALUES — the attached face's surface.
    - nominal_diameter: cylinder diameter in METRES (Feature units), else None.
    - is_internal: True for a hole/bore, False for a boss/shaft, None if unknown.
    - hole_standard: Hole Wizard designation (e.g. "M6", "⌀6.6"), else "".
    - referenced_model: the part the dim's view references (filename), else "".
    """
    feature_kind: str = "unknown"
    surface_type: str = "unknown"
    nominal_diameter: Optional[float] = None
    is_internal: Optional[bool] = None
    hole_standard: str = ""
    referenced_model: str = ""


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
    # 3D-model context resolved from the part behind the drawing (see
    # GeometryContext). None when geometry resolution wholly fails, so a
    # dimension that extracted fine in 2D is never lost. asdict() serialises the
    # nested dataclass recursively, so it flows into the report and the harvest
    # dataset automatically — which is why adding it bumps both schema versions.
    geometry: Optional[GeometryContext] = None


@dataclass(frozen=True)
class Tolerance:
    """A bilateral dimensional tolerance — a ± band on a dimension's value."""
    tol_type: int
    plus_value: float
    minus_value: float


# Geometric (GD&T) characteristics the predictor may propose. String-valued, like
# the geometry taxonomies above: the model is fully general, but the predictor and
# constant policy target this starter set first (form: flatness; orientation:
# perpendicularity, parallelism; location: position, concentricity; runout). The
# extract/apply COM layers map these onto swGtolSymbol enum values. Keep the
# vocabulary stable — it's part of the report and dataset schema.
GEOMETRIC_SYMBOLS: Final = frozenset({
    "flatness", "perpendicularity", "parallelism",
    "position", "concentricity", "circular_runout", "total_runout",
})
# Material-condition modifiers (feature-of-size / datum-reference): regardless of
# feature size (RFS), at maximum material condition (MMC), at least material (LMC).
MATERIAL_CONDITIONS: Final = frozenset({"RFS", "MMC", "LMC"})


@dataclass(frozen=True)
class DatumRef:
    """One datum reference in a feature control frame (e.g. B at MMC).

    ``letter`` is the datum label ("A"/"B"/"C"); ``modifier`` is its material
    condition. Order matters in a frame — primary, then secondary, then tertiary —
    so these are held in an ordered tuple on GeometricTolerance.
    """
    letter: str
    modifier: str = "RFS"


@dataclass(frozen=True)
class GeometricTolerance:
    """One geometric tolerance (a feature control frame): characteristic, zone,
    optional ⌀ zone, material condition, and ordered datum references.

    Pure and COM-free like Tolerance, so it serialises into the report and the
    harvest dataset via ``asdict`` and is unit-testable on macOS. The zone is a
    LINEAR distance in METRES (a geometric tolerance zone is always a length,
    even for orientation/location of an angular feature), mirroring the metres
    convention Tolerance uses for length dims.
    """
    symbol: str                                  # one of GEOMETRIC_SYMBOLS
    zone_value: float                            # tolerance zone width, in metres
    diameter_zone: bool = False                  # ⌀ (cylindrical) zone, e.g. position
    material_condition: str = "RFS"              # one of MATERIAL_CONDITIONS
    datum_refs: Tuple[DatumRef, ...] = ()        # ordered primary/secondary/tertiary


@dataclass(frozen=True)
class ToleranceDecision:
    """The full output of the swap point for one feature.

    Generalises the seam beyond a bare ± so a single feature (e.g. a hole) can
    receive BOTH a dimensional size tolerance AND one or more geometric
    tolerances (a position FCF). ``dimensional`` is None to leave the value
    untoleranced; ``geometric`` is empty when no FCF applies. ``rationale`` is the
    predictor's free-text justification (the LLM fills it; the constant policy
    leaves it None) — recorded in the report and useful as future training signal.
    """
    dimensional: Optional[Tolerance] = None
    geometric: Tuple[GeometricTolerance, ...] = ()
    rationale: Optional[str] = None
