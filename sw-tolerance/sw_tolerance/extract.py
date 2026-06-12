"""Walk a SolidWorks drawing: display dimensions and geometric (GD&T) tolerances."""
from __future__ import annotations

import sys
from typing import Iterator, List, Optional, Tuple

from .com import call
from .geometry import (
    referenced_model_of,
    resolve_geometry,
    resolve_geometry_from_annotation,
)
from .gtol_text import build_geometric_tolerance
from .models import (
    SW_DIM_TEXT_PREFIX,
    SW_DIM_TEXT_SUFFIX,
    SW_TOL_NONE,
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
        for label, geo in iter_view_geometric_tolerances(view):
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
    # The referenced part is a property of the VIEW, so resolve it once here
    # rather than via COM for every dimension on the view.
    ref_model = referenced_model_of(view)
    disp_dim = call(view, "GetFirstDisplayDimension5")
    while disp_dim is not None:
        built = _build_feature(disp_dim, view, ref_model, view_name, sheet_name)
        if built is not None:
            feat, idim, tol = built
            yield disp_dim, idim, tol, feat
        disp_dim = call(disp_dim, "GetNext5")


def _build_feature(
    disp_dim, view, ref_model: str, view_name: str, sheet_name: str
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
    geometry = resolve_geometry(disp_dim, view, referenced_model=ref_model)
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
# read sits behind one guarded seam (`_gtol_frame`); the frame-text decoding is
# `gtol_text.build_geometric_tolerance` — the shared codec whose encode side
# (`gtol_text.frame_values`) apply.py writes with, so read and write can't drift.


def iter_view_geometric_tolerances(view) -> Iterator[Tuple[GeometricTolerance, Optional[GeometryContext]]]:
    """Yield (label, geometry) for every usable IGtol frame on one view."""
    # The referenced part is a property of the VIEW — resolve once per view,
    # not per annotation (mirrors _iter_view).
    ref_model = referenced_model_of(view)
    ann = _safe(view, "GetFirstAnnotation2")
    while ann is not None:
        # GetSpecificAnnotation yields the typed object (IGtol for a frame);
        # read_geometric_tolerance returns None for non-gtols, so we needn't know
        # the swAnnotationType code to filter — a wrong type simply yields nothing.
        gtol = _safe(ann, "GetSpecificAnnotation")
        if gtol is not None:
            label = read_geometric_tolerance(gtol)
            if label is not None:
                geo = resolve_geometry_from_annotation(ann, view, referenced_model=ref_model)
                yield label, geo
        ann = _safe(ann, "GetNext2")


def read_geometric_tolerance(gtol) -> Optional[GeometricTolerance]:
    """Read one IGtol into a GeometricTolerance, or None if it isn't a usable frame.

    The single COM read is ``IGtol.GetFrameValues2(0)`` (the first frame's text
    values: symbol, tolerance, then datums) — a first-run unknown to confirm on
    Windows; the decoding below it is the pure ``gtol_text`` codec. Returns None
    when the symbol isn't a recognised characteristic or the zone isn't a
    number, so a non-gtol annotation (or an unparseable frame) is silently
    skipped.
    """
    return build_geometric_tolerance(_gtol_frame(gtol))


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


def _safe(obj, name: str):
    """com.call(obj, name) guarded to None — for the GD&T annotation walk."""
    try:
        return call(obj, name)
    except Exception:
        return None
