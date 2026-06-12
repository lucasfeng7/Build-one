"""The swap point: pure function from Feature to a tolerance decision.

`decide_for(feature)` is the single seam through which all tolerancing judgment
flows. It runs the deterministic skip rules, then delegates the *decision* to a
pluggable predictor:

    constant_tolerance      — the flat MVP policy and the universal fallback
    predict.predict_tolerance — the interim DeepSeek-backed "brain" (CLI swaps it in)
    <trained model>         — the eventual final predictor, dropped in here later

A predictor returns a `ToleranceDecision`: an optional bilateral `Tolerance`
(the ± size band), a tuple of `GeometricTolerance`s (GD&T feature control
frames), and an optional rationale. One feature can receive both — a hole gets a
size ± and a position FCF — which is why the seam returns the richer decision
rather than a bare `Tolerance`. `tolerance_for` is the thin `.dimensional` view
for callers that only need the ± band.

The default predictor is the constant policy, so this module stays pure and
unit-testable on macOS with no network and no SolidWorks. The CLI swaps in the
DeepSeek predictor at runtime via `set_predictor`. Keep this file free of COM
imports (invariant #1); `predict` is imported lazily by callers, never here.
"""
from __future__ import annotations

import math
from typing import Callable, Final, Optional

from .models import (
    ANGULAR_DIM_TYPES,
    LENGTH_DIM_TYPES,
    SW_TOL_BILAT,
    SW_TOL_NONE,
    Feature,
    Tolerance,
    ToleranceDecision,
)

# Dimension families, keyed by the unit SolidWorks stores their value in.
LENGTH: Final[str] = "length"   # value/tolerance in metres
ANGULAR: Final[str] = "angular"  # value/tolerance in radians

# Length dims store value/tolerance in metres; angular dims in radians. The two
# defaults are the per-unit analogues of the same flat MVP policy.
_HALF_WIDTH_METERS: Final[float] = 0.0005  # 0.5 mm
_ANGULAR_HALF_WIDTH_RAD: Final[float] = math.radians(1.0)  # 1°

# A predictor maps a (tolerable) feature and its resolved family to a
# ToleranceDecision (optional ± band + optional GD&T frames + optional
# rationale). Every predictor — the constant policy, the DeepSeek brain, the
# eventual trained model — shares this signature so they swap at the one seam.
Predictor = Callable[[Feature, str], ToleranceDecision]

# A feature that passed the skip rules but gets no tolerance from a predictor,
# and the value the skip rules themselves return. Immutable, so sharing is safe.
_NO_TOLERANCE: Final = ToleranceDecision(dimensional=None, geometric=(), rationale=None)


def _family_of(dim_type: int) -> Optional[str]:
    if dim_type in LENGTH_DIM_TYPES:
        return LENGTH
    if dim_type in ANGULAR_DIM_TYPES:
        return ANGULAR
    return None


def constant_tolerance(f: Feature, family: str) -> ToleranceDecision:
    """The flat MVP policy and the universal fallback.

    Bilateral ±0.5 mm on length-valued dims, ±1° on angular dims, and no GD&T.
    Pure, COM-free, offline — this is what runs when no LLM is configured and
    what every richer predictor degrades to on failure.
    """
    half_width = _HALF_WIDTH_METERS if family == LENGTH else _ANGULAR_HALF_WIDTH_RAD
    return ToleranceDecision(
        dimensional=Tolerance(
            tol_type=SW_TOL_BILAT,
            plus_value=half_width,
            minus_value=half_width,
        ),
        geometric=(),
        rationale=None,
    )


_active_predictor: Predictor = constant_tolerance


def set_predictor(predictor: Predictor) -> None:
    """Swap the active predictor (e.g. the CLI installs the DeepSeek brain)."""
    global _active_predictor
    _active_predictor = predictor


def use_constant() -> None:
    """Restore the constant policy as the active predictor."""
    set_predictor(constant_tolerance)


def decide_for(f: Feature) -> ToleranceDecision:
    """The swap point: skip rules + predictor dispatch, returning the full decision.

    The skip rules live here — not in the predictors — so they are the single
    source of truth and the LLM path can *never* re-tolerance an already-
    toleranced dim, a reference dim, a hole callout, or an unsupported dim
    family. This preserves the harvest/apply invariants regardless of which
    predictor runs. A skipped feature returns the empty decision (no ±, no GD&T).
    """
    if f.current_tolerance_type != SW_TOL_NONE:
        return _NO_TOLERANCE
    if f.is_reference:
        # Reference/driven dims (shown in parentheses) are not toleranced —
        # they only report a value derived elsewhere. Never add a ± to them.
        return _NO_TOLERANCE
    if f.is_hole_callout:
        return _NO_TOLERANCE
    family = _family_of(f.dim_type)
    if family is None:
        return _NO_TOLERANCE
    return _active_predictor(f, family)


def tolerance_for(f: Feature) -> Optional[Tolerance]:
    """The dimensional tolerance to apply to ``f``, or None to skip."""
    return decide_for(f).dimensional
