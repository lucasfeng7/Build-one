"""The interim 'brain': a DeepSeek-backed predictor for decide.tolerance_for.

Same swap-point contract as ``decide.constant_tolerance`` —
``(feature, family) -> (Tolerance | None, rationale | None)`` — but the
tolerance magnitude is chosen by DeepSeek per dimension instead of a flat
constant. This is the interim policy that makes the tool feel intelligent
before any training data exists; the recorded rationales also seed the eventual
trained model (it slots in at the same seam, and the LLM labels bootstrap it).

DeepSeek exposes an OpenAI-compatible API, so this talks to it through the
``openai`` SDK pointed at DeepSeek's base URL. Network/LLM code is isolated here
so ``decide.py`` and ``models.py`` stay COM-free *and* dependency-light;
``openai`` is imported lazily so the package still imports on macOS without the
SDK installed (mirroring the lazy-COM rule).

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
from typing import Tuple

from .decide import LENGTH, constant_tolerance
from .models import (
    GEOMETRIC_SYMBOLS,
    MATERIAL_CONDITIONS,
    SW_TOL_BILAT,
    DatumRef,
    Feature,
    GeometricTolerance,
    Tolerance,
    ToleranceDecision,
)

# DeepSeek-V3 chat model. Override with SW_TOLERANCE_MODEL (e.g. deepseek-reasoner).
DEFAULT_MODEL = "deepseek-chat"

# DeepSeek's OpenAI-compatible endpoint, and the key the CLI gates the LLM path on.
BASE_URL = "https://api.deepseek.com"
API_KEY_ENV_VARS = ("DEEPSEEK_API_KEY",)

_SYSTEM_PROMPT = (
    "You are a mechanical-engineering tolerancing assistant. You are given a "
    "single untoleranced dimension from a manufacturing drawing — with the 3D "
    "feature it measures, when known — and must decide the tolerances a competent "
    "engineer would apply, following common shop practice (e.g. ISO 2768 general "
    "tolerances and ASME Y14.5 / ISO 1101 geometric tolerancing): tighter on "
    "small, precision, or mating features, looser on large or coarse ones.\n"
    "First, the bilateral SIZE tolerance: give the plus and minus deviations as "
    "POSITIVE magnitudes in the SAME unit as the dimension — millimetres for "
    "length dimensions, degrees for angular dimensions. They may be equal "
    "(symmetric) or differ.\n"
    "Then, OPTIONALLY, any geometric tolerances (GD&T feature control frames) the "
    "feature warrants — e.g. position on a hole locating to datums, "
    "perpendicularity/parallelism/flatness on a datum face, concentricity or "
    "runout on a turned diameter. Only propose a frame when the geometry clearly "
    "calls for one; most dimensions need none. Geometric zone values are LINEAR, "
    "in MILLIMETRES. Use only these characteristics: "
    + ", ".join(sorted(GEOMETRIC_SYMBOLS)) + ". "
    "Datums are single letters (A/B/C) in order; material conditions are RFS, "
    "MMC, or LMC.\n"
    "Respond with ONLY a JSON object with these keys: "
    '"apply" (boolean — false to leave the SIZE untoleranced), '
    '"plus" (number), "minus" (number), '
    '"geometric" (array — possibly empty — of objects with keys "symbol", '
    '"zone" (number, mm), "diameter_zone" (boolean), "material_condition" '
    '("RFS"/"MMC"/"LMC"), and "datums" (array of {"letter","modifier"})), '
    '"reason" (string — one concise sentence justifying the choice).'
)

# Late-init client singleton. A list (not a module global rebind) keeps the lazy
# import contained and avoids `global` churn.
_client_box: list = []


def _client():
    if not _client_box:
        from openai import OpenAI  # lazy: keep the package importable without the SDK

        api_key = next((os.environ[v] for v in API_KEY_ENV_VARS if os.environ.get(v)), None)
        _client_box.append(OpenAI(api_key=api_key, base_url=BASE_URL))
    return _client_box[0]


# Per-process memoisation: drawings repeat identical dims, and the policy is a
# pure function of the feature, so identical (type, family, rounded value) tuples
# cost a single API call. Rounding collapses float noise from COM extraction.
_cache: dict = {}


def _cache_key(f: Feature, family: str) -> tuple:
    # Include the inputs the prompt actually varies on — annotation text and 3D
    # geometry context — so two dims that share (type, value) but differ in
    # feature kind/size don't collapse to one cached answer.
    g = f.geometry
    geo_key = (
        (g.feature_kind, g.surface_type,
         round(g.nominal_diameter, 9) if g.nominal_diameter is not None else None,
         g.is_internal, g.hole_standard)
        if g is not None else None
    )
    return (f.dim_type, family, round(f.value, 9), f.text_prefix, f.text_suffix, geo_key)


def predict_tolerance(f: Feature, family: str) -> ToleranceDecision:
    """DeepSeek-backed predictor. Falls back to the constant policy on any error."""
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


def _query(f: Feature, family: str) -> ToleranceDecision:
    is_length = family == LENGTH
    unit = "mm" if is_length else "degrees"
    # SolidWorks stores lengths in metres and angles in radians; the model reasons
    # in human units, so convert out for the prompt and back for the Tolerance.
    human_value = f.value * 1000.0 if is_length else math.degrees(f.value)
    lines = [
        f"Dimension type code: {f.dim_type} ({family} family).",
        f"Nominal value: {human_value:.4g} {unit}.",
    ]
    # Surface the dim's annotation text when present — a leading "⌀"/"M6" or a
    # trailing "TYP"/"MAX" carries intent the bare type/value miss. (Reference
    # dims are skipped upstream, so they never reach here.)
    annotation = " ".join(p for p in (f.text_prefix, f.text_suffix) if p).strip()
    if annotation:
        lines.append(f"Annotation text: {annotation}")
    geo_line = _geometry_line(f)
    if geo_line:
        lines.append(geo_line)
    lines.append(f"Decide the size tolerance (in {unit}) and any geometric tolerances.")
    user_msg = "\n".join(lines)
    model = os.environ.get("SW_TOLERANCE_MODEL", DEFAULT_MODEL)
    resp = _client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=512,
    )
    data = json.loads(resp.choices[0].message.content)
    reason = data.get("reason")
    geometric = _parse_geometric(data.get("geometric"))
    dimensional = None
    if data.get("apply", False):
        # abs()-normalise both deviations to positive magnitudes — matches the
        # Tolerance shape and apply.write_tolerance's sign convention (it negates
        # the minus value when writing to SolidWorks).
        plus_h = abs(float(data["plus"]))
        minus_h = abs(float(data["minus"]))
        if is_length:
            plus, minus = plus_h / 1000.0, minus_h / 1000.0
        else:
            plus, minus = math.radians(plus_h), math.radians(minus_h)
        dimensional = Tolerance(tol_type=SW_TOL_BILAT, plus_value=plus, minus_value=minus)
    return ToleranceDecision(dimensional=dimensional, geometric=geometric, rationale=reason)


def _geometry_line(f: Feature) -> str:
    """One-line 3D-feature context for the prompt, or "" when nothing is known."""
    g = f.geometry
    if g is None:
        return ""
    bits = []
    if g.feature_kind and g.feature_kind != "unknown":
        bits.append(f"feature {g.feature_kind}")
    if g.surface_type and g.surface_type != "unknown":
        bits.append(f"{g.surface_type} surface")
    if g.nominal_diameter:
        bits.append(f"nominal Ø{g.nominal_diameter * 1000.0:.4g} mm")
    if g.is_internal is not None:
        bits.append("internal (hole/bore)" if g.is_internal else "external (boss/shaft)")
    if g.hole_standard:
        bits.append(f"hole standard {g.hole_standard}")
    return "Geometry context: " + "; ".join(bits) + "." if bits else ""


def _parse_geometric(items) -> Tuple[GeometricTolerance, ...]:
    """Parse the LLM's optional ``geometric`` array into GeometricTolerances.

    Defensive: silently drops any frame with an unknown characteristic, a
    non-numeric zone, or a malformed datum, so one bad entry never sinks the
    whole prediction (the caller already falls back to the constant policy on a
    hard parse error). Zone values arrive in mm and convert to metres.
    """
    if not items:
        return ()
    out = []
    for it in items:
        try:
            symbol = str(it["symbol"]).strip().lower()
            if symbol not in GEOMETRIC_SYMBOLS:
                continue
            zone = abs(float(it["zone"])) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        out.append(GeometricTolerance(
            symbol=symbol,
            zone_value=zone,
            diameter_zone=bool(it.get("diameter_zone", False)),
            material_condition=_norm_condition(it.get("material_condition")),
            datum_refs=_parse_datums(it.get("datums")),
        ))
    return tuple(out)


def _parse_datums(items) -> Tuple[DatumRef, ...]:
    if not items:
        return ()
    refs = []
    for d in items:
        try:
            letter = str(d["letter"]).strip().upper()[:1]
        except (KeyError, TypeError, ValueError):
            continue
        if letter:
            refs.append(DatumRef(letter=letter, modifier=_norm_condition(d.get("modifier"))))
    return tuple(refs)


def _norm_condition(value) -> str:
    """Normalise a material condition to one of MATERIAL_CONDITIONS (default RFS)."""
    candidate = str(value).strip().upper() if value is not None else ""
    return candidate if candidate in MATERIAL_CONDITIONS else "RFS"
