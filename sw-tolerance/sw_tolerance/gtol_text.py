"""The GD&T frame-text codec: GeometricTolerance ↔ SolidWorks frame values.

A feature control frame crosses the COM boundary as a text array
``[symbol, tolerance, datum1, datum2, ...]`` (``IGtol.GetFrameValues2`` /
``SetFrameValues2``). This module owns BOTH directions of that encoding —
``frame_values`` (write side, used by ``apply``) and
``build_geometric_tolerance`` (read side, used by ``extract``) — so the format
knowledge lives in exactly one place and the write↔read round-trip can never
drift apart. Everything here is pure text logic: zero COM imports, unit-tested
on macOS (invariant #1 style).

Encoding rules (shared by both directions):
- symbol: a canonical GEOMETRIC_SYMBOLS name; decode also accepts common
  variants ("perpendicular", "true position", ...).
- tolerance zone: a ⌀ prefix for a diameter (cylindrical) zone, the magnitude
  in MILLIMETRES (GeometricTolerance stores metres), and a material-condition
  suffix — "(M)" MMC / "(L)" LMC / nothing for RFS (RFS is implicit).
- datums: one entry per ordered reference, ``letter`` + modifier suffix.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .models import (
    GEOMETRIC_SYMBOLS,
    MATERIAL_CONDITIONS,
    DatumRef,
    GeometricTolerance,
)

# Decode: map the symbol text SolidWorks reports (or our own canonical name)
# onto a GEOMETRIC_SYMBOLS value. Keyed on lowercased text so it tolerates the
# variants and abbreviations a frame's symbol field may carry. The canonical
# names map to themselves so a value already in our vocabulary passes through.
SYMBOL_BY_NAME: dict = {name: name for name in GEOMETRIC_SYMBOLS}
SYMBOL_BY_NAME.update({
    "perpendicular": "perpendicularity",
    "parallel": "parallelism",
    "true position": "position",
    "concentric": "concentricity",
    "runout": "circular_runout",
    "circular run-out": "circular_runout",
    "total run-out": "total_runout",
})

# Decode: material-condition tokens embedded in a value/datum text.
MODIFIER_TOKENS = (("(M)", "MMC"), ("(L)", "LMC"), ("(S)", "RFS"),
                   ("Ⓜ", "MMC"), ("Ⓛ", "LMC"), ("Ⓢ", "RFS"))

# Encode: the inverse suffixes. RFS is implicit (no symbol), so no suffix.
MOD_SUFFIX = {"MMC": "(M)", "LMC": "(L)", "RFS": ""}

_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+")


# --- encode (GeometricTolerance → frame text) --------------------------------

def frame_values(gtol: GeometricTolerance) -> List[str]:
    """The frame's text values ``[symbol, tolerance, *datums]``. Pure.

    The exact inverse of ``build_geometric_tolerance``, so the encoding is
    round-trippable and verifiable on macOS independent of the COM write.
    """
    tol_text = "⌀" if gtol.diameter_zone else ""
    tol_text += _format_zone_mm(gtol.zone_value * 1000.0)
    tol_text += MOD_SUFFIX.get(gtol.material_condition, "")
    datums = [d.letter + MOD_SUFFIX.get(d.modifier, "") for d in gtol.datum_refs]
    return [gtol.symbol, tol_text] + datums


def _format_zone_mm(value_mm: float) -> str:
    """Zone magnitude in mm, trailing zeros trimmed (e.g. 0.2000 → "0.2")."""
    text = f"{value_mm:.4f}".rstrip("0").rstrip(".")
    return text or "0"


# --- decode (frame text → GeometricTolerance) --------------------------------

def build_geometric_tolerance(values: List[str]) -> Optional[GeometricTolerance]:
    """Pure: assemble a GeometricTolerance from a frame's text values.

    Returns None when the symbol isn't a recognised characteristic or the zone
    isn't a number, so a non-gtol annotation (or an unparseable frame) is
    silently skipped by readers.
    """
    if not values:
        return None
    symbol = symbol_from_value(values[0])
    if symbol is None:
        return None
    zone, diameter, material = parse_zone(values[1]) if len(values) > 1 else (None, False, "RFS")
    if zone is None:
        return None
    datums = tuple(d for d in (parse_datum(v) for v in values[2:]) if d is not None)
    return GeometricTolerance(
        symbol=symbol,
        zone_value=zone,
        diameter_zone=diameter,
        material_condition=material,
        datum_refs=datums,
    )


def symbol_from_value(value) -> Optional[str]:
    """Map a frame's symbol text to a GEOMETRIC_SYMBOLS value, or None."""
    if value is None:
        return None
    return SYMBOL_BY_NAME.get(str(value).strip().lower())


def parse_zone(text) -> Tuple[Optional[float], bool, str]:
    """Parse a tolerance value text like "⌀0.2(M)" → (metres, is_⌀, material)."""
    s = str(text).strip()
    u = s.upper()
    # "DIA" must be a prefix word (DIA0.2), not the start of a longer word.
    diameter = s.startswith("⌀") or (u.startswith("DIA") and not u[3:4].isalpha())
    material = extract_modifier(s)
    number = first_number(s)
    if number is None:
        return None, diameter, material
    return number / 1000.0, diameter, material


def parse_datum(text) -> Optional[DatumRef]:
    """Parse a datum text like "B(M)" → DatumRef("B", "MMC"); "" / non-letter → None."""
    s = str(text).strip()
    if not s or not s[0].isalpha():
        return None
    return DatumRef(letter=s[0].upper(), modifier=extract_modifier(s))


def extract_modifier(text) -> str:
    """Material condition embedded in a value/datum text, defaulting to RFS."""
    u = str(text).upper()
    for token, condition in MODIFIER_TOKENS:
        if token in u:
            return condition
    return "RFS"


def first_number(text) -> Optional[float]:
    m = _NUMBER_RE.search(str(text))
    if not m:
        return None
    try:
        return abs(float(m.group()))
    except ValueError:
        return None


def normalize_condition(value) -> str:
    """Normalise a free-form material condition to MATERIAL_CONDITIONS (default RFS).

    Used by the LLM-reply parser, where the condition arrives as a JSON string
    ("MMC"/"mmc"/None) rather than an embedded "(M)" token.
    """
    candidate = str(value).strip().upper() if value is not None else ""
    return candidate if candidate in MATERIAL_CONDITIONS else "RFS"
