"""The interim 'brain': a Gemini-backed predictor for decide.tolerance_for.

Same swap-point contract as ``decide.constant_tolerance`` —
``(feature, family) -> (Tolerance | None, rationale | None)`` — but the
tolerance magnitude is chosen by Gemini per dimension instead of a flat
constant. This is the interim policy that makes the tool feel intelligent
before any training data exists; the recorded rationales also seed the eventual
trained model (it slots in at the same seam, and the LLM labels bootstrap it).

Network/LLM code is isolated here so ``decide.py`` and ``models.py`` stay
COM-free *and* dependency-light. ``google-genai`` is imported lazily so the
package still imports on macOS without the SDK installed (mirroring the lazy-COM
rule).

On any failure — no API key, network error, SDK missing, unparseable reply —
this falls back to ``decide.constant_tolerance`` and prints one stderr warning,
the same graceful-degradation philosophy as ``sw_client.connect()``'s
late-binding fallback. The tool always produces a result.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Optional, Tuple

from .decide import LENGTH, constant_tolerance
from .models import SW_TOL_BILAT, Feature, Tolerance

# Default to Flash: per-dimension calls favour throughput/cost over peak
# reasoning. Override with SW_TOLERANCE_MODEL for a stronger model.
DEFAULT_MODEL = "gemini-2.5-flash"

# google-genai's Client() reads the key from either of these (GEMINI takes
# precedence); the CLI gates the LLM path on the same pair.
API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

_SYSTEM_PROMPT = (
    "You are a mechanical-engineering tolerancing assistant. You are given a "
    "single untoleranced dimension from a manufacturing drawing and must decide "
    "the bilateral tolerance a competent engineer would apply, following common "
    "shop practice (e.g. ISO 2768 general tolerances): tighter on small or "
    "precision features, looser on large or coarse ones. Give the plus and minus "
    "deviations as POSITIVE magnitudes in the SAME unit as the dimension — "
    "millimetres for length dimensions, degrees for angular dimensions. They may "
    "be equal (symmetric) or differ. "
    "Respond with ONLY a JSON object with these keys: "
    '"apply" (boolean — false to leave the dimension untoleranced), '
    '"plus" (number), "minus" (number), '
    '"reason" (string — one concise sentence justifying the choice).'
)

# Late-init client singleton. A list (not a module global rebind) keeps the lazy
# import contained and avoids `global` churn.
_client_box: list = []


def _client():
    if not _client_box:
        from google import genai  # lazy: keep the package importable without the SDK

        _client_box.append(genai.Client())
    return _client_box[0]


# Per-process memoisation: drawings repeat identical dims, and the policy is a
# pure function of the feature, so identical (type, family, rounded value) tuples
# cost a single API call. Rounding collapses float noise from COM extraction.
_cache: dict = {}


def _cache_key(f: Feature, family: str) -> tuple:
    return (f.dim_type, family, round(f.value, 9))


def predict_tolerance(f: Feature, family: str) -> Tuple[Optional[Tolerance], Optional[str]]:
    """Gemini-backed predictor. Falls back to the constant policy on any error."""
    key = _cache_key(f, family)
    if key in _cache:
        return _cache[key]
    try:
        result = _query(f, family)
    except Exception as e:  # noqa: BLE001 — any failure degrades to the constant policy
        print(
            f"WARNING: LLM tolerance prediction failed ({e!r}); "
            f"falling back to constant policy for dim_type={f.dim_type}.",
            file=sys.stderr,
        )
        result = constant_tolerance(f, family)
    _cache[key] = result
    return result


def _query(f: Feature, family: str) -> Tuple[Optional[Tolerance], Optional[str]]:
    is_length = family == LENGTH
    unit = "mm" if is_length else "degrees"
    # SolidWorks stores lengths in metres and angles in radians; Gemini reasons
    # in human units, so convert out for the prompt and back for the Tolerance.
    human_value = f.value * 1000.0 if is_length else math.degrees(f.value)
    user_msg = (
        f"Dimension type code: {f.dim_type} ({family} family).\n"
        f"Nominal value: {human_value:.4g} {unit}.\n"
        f"Decide the bilateral tolerance (in {unit})."
    )
    model = os.environ.get("SW_TOLERANCE_MODEL", DEFAULT_MODEL)
    resp = _client().models.generate_content(
        model=model,
        contents=user_msg,
        config={
            "system_instruction": _SYSTEM_PROMPT,
            "response_mime_type": "application/json",
            "temperature": 0,
        },
    )
    data = json.loads(resp.text)
    reason = data.get("reason")
    if not data.get("apply", False):
        return None, reason
    # abs()-normalise both deviations to positive magnitudes — matches the
    # Tolerance shape and apply.write_tolerance's sign convention (it negates
    # the minus value when writing to SolidWorks).
    plus_h = abs(float(data["plus"]))
    minus_h = abs(float(data["minus"]))
    if is_length:
        plus, minus = plus_h / 1000.0, minus_h / 1000.0
    else:
        plus, minus = math.radians(plus_h), math.radians(minus_h)
    return (
        Tolerance(tol_type=SW_TOL_BILAT, plus_value=plus, minus_value=minus),
        reason,
    )
