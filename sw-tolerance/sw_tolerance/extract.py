"""Walk a SolidWorks drawing: display dimensions and geometric (GD&T) tolerances."""
from __future__ import annotations

import re
import sys
from typing import Iterator, List, Optional, Tuple

from .com import call
from .geometry import resolve_geometry, resolve_geometry_from_annotation
from .models import (
    GEOMETRIC_SYMBOLS,
    SW_DIM_TEXT_PREFIX,
    SW_DIM_TEXT_SUFFIX,
    SW_TOL_NONE,
    DatumRef,
    Feature,
    GeometricTolerance,
    GeometryContext,
    Tolerance,
)

DimTuple = Tuple[object, object, object, Feature]
# (label, geometry, view_name, sheet_name) for a harvested geometric tolerance.
GtolTuple = Tuple[GeometricTolerance, Optional[GeometryContext], str, str]


def iter_dimensions(drawing) -> Iterator[DimTuple]:
    """Yield every display dimension across every sheet of the drawing."""
    for view, view_name, sheet_name in _iter_views(drawing):
        yield from _iter_view(view, view_name, sheet_name)


def iter_geometric_tolerances(drawing) -> Iterator[GtolTuple]:
    """Yield every existing geometric tolerance (IGtol) across the drawing.

    Read-only: harvest.py uses this to collect engineers' applied feature
    control frames as GD&T training labels. Each frame is paired with the 3D
    geometry it attaches to (via resolve_geometry_from_annotation) so the label
    carries its input context. Guarded throughout; the IGtol read API is a
    first-run unknown (see read_geometric_tolerance).
    """
    for view, view_name, sheet_name in _iter_views(drawing):
        for label, geo in _iter_view_gtols(view):
            yield label, geo, view_name, sheet_name


def _iter_views(drawing) -> Iterator[Tuple[object, str, str]]:
    """Yield (view, view_name, sheet_name) for every real view on every sheet.

    Multi-sheet iteration is explicit (invariant #5): loop GetSheetNames /
    ActivateSheet, then GetFirstView (the sheet itself) → GetNextView per sheet.
    Shared by the dimension and GD&T walks so the traversal lives in one place.
    """
    sheet_names = call(drawing, "GetSheetNames")
    if not sheet_names:
        return
    for sheet_name in sheet_names:
        drawing.ActivateSheet(sheet_name)
        # GetFirstView returns the sheet itself; real views start at GetNextView.
        view = call(drawing, "GetFirstView")
        if view is None:
            continue
        view = call(view, "GetNextView")
        while view is not None:
            yield view, _view_name(view), sheet_name
            view = call(view, "GetNextView")


def _iter_view(view, view_name: str, sheet_name: str) -> Iterator[DimTuple]:
    disp_dim = call(view, "GetFirstDisplayDimension5")
    while disp_dim is not None:
        built = _build_feature(disp_dim, view, view_name, sheet_name)
        if built is not None:
            feat, idim, tol = built
            yield disp_dim, idim, tol, feat
        disp_dim = call(disp_dim, "GetNext5")


def _build_feature(
    disp_dim, view, view_name: str, sheet_name: str
) -> Optional[Tuple[Feature, object, object]]:
    try:
        dim_type = int(disp_dim.Type2)
        idim = disp_dim.GetDimension2(0)
        tol = idim.Tolerance
        current_tol_type = int(tol.Type)
        value = float(idim.Value)
    except Exception as e:
        print(f"[extract] WARN {sheet_name}/{view_name}: {e}", file=sys.stderr)
        return None
    # Richer attributes are best-effort: each read is individually guarded
    # (see _display_attrs) so a missing/over-version COM member degrades to the
    # field default rather than dropping a dimension that extracted fine above.
    is_reference, text_prefix, text_suffix = _display_attrs(disp_dim)
    # 3D-model context from the part behind the view; resolve_geometry never
    # raises (returns None on total failure), so it can't cost a dimension that
    # extracted fine above.
    geometry = resolve_geometry(disp_dim, view)
    feat = Feature(
        value=value,
        dim_type=dim_type,
        current_tolerance_type=current_tol_type,
        view_name=view_name,
        sheet_name=sheet_name,
        is_reference=is_reference,
        text_prefix=text_prefix,
        text_suffix=text_suffix,
        geometry=geometry,
    )
    return feat, idim, tol


def _display_attrs(disp_dim) -> Tuple[bool, str, str]:
    """Best-effort richer display attributes: (is_reference, prefix, suffix).

    Every read is guarded independently so one unavailable member never costs
    the others (or the whole dimension). The exact COM members are first-run
    unknowns (see CLAUDE.md) — confirm/adjust on a live Windows run.
    """
    return (
        _safe_is_reference(disp_dim),
        _safe_text(disp_dim, SW_DIM_TEXT_PREFIX),
        _safe_text(disp_dim, SW_DIM_TEXT_SUFFIX),
    )


def _safe_is_reference(disp_dim) -> bool:
    """True if the dim is a reference/driven dim (shown in parentheses).

    ``IsReference`` is a property get (works under both bindings, so no
    ``com.call``). If a SolidWorks build doesn't expose it, default to False —
    the conservative choice (we'd tolerate the dim rather than wrongly skip it).
    """
    try:
        return bool(disp_dim.IsReference)
    except Exception:
        return False


def _safe_text(disp_dim, part: int) -> str:
    """Read one annotation-text part via GetText(part), or "" if unavailable.

    ``GetText`` takes an argument, so it's a real callable under late binding
    (invariant 3a) and is called directly. Returns "" for the common case of no
    prefix/suffix text.
    """
    try:
        text = disp_dim.GetText(part)
        return str(text) if text else ""
    except Exception:
        return ""


def _view_name(view) -> str:
    try:
        return call(view, "GetName2")
    except Exception:
        try:
            return view.Name
        except Exception:
            return "<unknown>"


def read_existing_tolerance(tol_obj) -> Optional[Tolerance]:
    """Read the tolerance already present on a dimension, or None if untoleranced.

    This is the inverse of apply.write_tolerance: it harvests the ground-truth
    label an engineer applied, for use as ML training data. Returns None when
    the dimension carries no tolerance (Type == swTolNONE), so the caller can
    drop unlabeled dimensions.

    GetMaxValue/GetMinValue are *no-arg* COM members, so per invariant 3a they
    must be invoked through com.call (under late binding ``tol_obj.GetMaxValue``
    already returns the value and calling it again would raise). SolidWorks
    stores the lower deviation as a negative magnitude — the same convention
    apply.write_tolerance writes — so both deviations are normalised with abs()
    to mirror the Tolerance shape produced by decide.tolerance_for.
    """
    try:
        tol_type = int(tol_obj.Type)
        if tol_type == SW_TOL_NONE:
            return None
        max_value = float(call(tol_obj, "GetMaxValue"))
        min_value = float(call(tol_obj, "GetMinValue"))
    except Exception as e:
        print(f"[extract] WARN read_existing_tolerance: {e}", file=sys.stderr)
        return None
    return Tolerance(
        tol_type=tol_type,
        plus_value=abs(max_value),
        minus_value=abs(min_value),
    )


def get_active_config_name(model) -> str:
    try:
        cfg = call(model, "GetActiveConfiguration")
        return cfg.Name
    except Exception:
        return "<unknown>"


# --- geometric (GD&T) tolerance extraction ----------------------------------
#
# Geometric tolerances are NOT display dimensions: they are separate IGtol
# annotations on a view. This block walks them read-only for harvest. The COM
# read sits behind one guarded seam (`_gtol_frame`); the parsing/mapping below it
# is pure and unit-tested on macOS.

# Map the symbol text SolidWorks reports (or our own canonical name) onto a
# GEOMETRIC_SYMBOLS value. Keyed on lowercased text so it tolerates the variants
# and abbreviations a frame's symbol field may carry. The canonical names map to
# themselves so a value already in our vocabulary passes straight through.
_SYMBOL_BY_NAME: dict = {name: name for name in GEOMETRIC_SYMBOLS}
_SYMBOL_BY_NAME.update({
    "perpendicular": "perpendicularity",
    "parallel": "parallelism",
    "true position": "position",
    "concentric": "concentricity",
    "runout": "circular_runout",
    "circular run-out": "circular_runout",
    "total run-out": "total_runout",
})

_MODIFIER_TOKENS = (("(M)", "MMC"), ("(L)", "LMC"), ("(S)", "RFS"),
                    ("Ⓜ", "MMC"), ("Ⓛ", "LMC"), ("Ⓢ", "RFS"))


def iter_view_geometric_tolerances(view) -> Iterator[Tuple[GeometricTolerance, Optional[GeometryContext]]]:
    """Public per-view variant used in tests; drawing-level walk is iter_geometric_tolerances."""
    yield from _iter_view_gtols(view)


def _iter_view_gtols(view) -> Iterator[Tuple[GeometricTolerance, Optional[GeometryContext]]]:
    ann = _safe(view, "GetFirstAnnotation2")
    while ann is not None:
        # GetSpecificAnnotation yields the typed object (IGtol for a frame);
        # read_geometric_tolerance returns None for non-gtols, so we needn't know
        # the swAnnotationType code to filter — a wrong type simply yields nothing.
        gtol = _safe(ann, "GetSpecificAnnotation")
        if gtol is not None:
            label = read_geometric_tolerance(gtol)
            if label is not None:
                yield label, resolve_geometry_from_annotation(ann, view)
        ann = _safe(ann, "GetNext2")


def read_geometric_tolerance(gtol) -> Optional[GeometricTolerance]:
    """Read one IGtol into a GeometricTolerance, or None if it isn't a usable frame.

    The single COM read is ``IGtol.GetFrameValues2(0)`` (the first frame's text
    values: symbol, tolerance, then datums) — a first-run unknown to confirm on
    Windows; everything below it is pure text parsing. Returns None when the
    symbol isn't a recognised characteristic or the zone isn't a number, so a
    non-gtol annotation (or an unparseable frame) is silently skipped.
    """
    return _build_geometric_tolerance(_gtol_frame(gtol))


def _gtol_frame(gtol) -> List[str]:
    """The first frame's text values as strings, or [] if unreadable. COM seam."""
    try:
        values = call(gtol, "GetFrameValues2", 0)
    except Exception:
        return []
    if values is None:
        return []
    seq = list(values) if isinstance(values, (tuple, list)) else [values]
    return [str(v) for v in seq]


def _build_geometric_tolerance(values: List[str]) -> Optional[GeometricTolerance]:
    """Pure: assemble a GeometricTolerance from a frame's text values."""
    if not values:
        return None
    symbol = _symbol_from_value(values[0])
    if symbol is None:
        return None
    zone, diameter, material = _parse_zone(values[1]) if len(values) > 1 else (None, False, "RFS")
    if zone is None:
        return None
    datums = tuple(d for d in (_parse_datum(v) for v in values[2:]) if d is not None)
    return GeometricTolerance(
        symbol=symbol,
        zone_value=zone,
        diameter_zone=diameter,
        material_condition=material,
        datum_refs=datums,
    )


def _symbol_from_value(value) -> Optional[str]:
    """Map a frame's symbol text to a GEOMETRIC_SYMBOLS value, or None."""
    if value is None:
        return None
    return _SYMBOL_BY_NAME.get(str(value).strip().lower())


def _parse_zone(text) -> Tuple[Optional[float], bool, str]:
    """Parse a tolerance value text like "⌀0.2(M)" → (metres, is_⌀, material)."""
    s = str(text).strip()
    diameter = s.startswith("⌀") or s.upper().startswith("DIA")
    material = _extract_modifier(s)
    number = _first_number(s)
    if number is None:
        return None, diameter, material
    return number / 1000.0, diameter, material


def _parse_datum(text) -> Optional[DatumRef]:
    """Parse a datum text like "B(M)" → DatumRef("B", "MMC"); "" / non-letter → None."""
    s = str(text).strip()
    if not s or not s[0].isalpha():
        return None
    return DatumRef(letter=s[0].upper(), modifier=_extract_modifier(s))


def _extract_modifier(text) -> str:
    """Material condition embedded in a value/datum text, defaulting to RFS."""
    u = str(text).upper()
    for token, condition in _MODIFIER_TOKENS:
        if token in u:
            return condition
    return "RFS"


def _first_number(text) -> Optional[float]:
    m = re.search(r"[-+]?\d*\.?\d+", str(text))
    if not m:
        return None
    try:
        return abs(float(m.group()))
    except ValueError:
        return None


def _safe(obj, name: str):
    """com.call(obj, name) guarded to None — for the GD&T annotation walk."""
    try:
        return call(obj, name)
    except Exception:
        return None
