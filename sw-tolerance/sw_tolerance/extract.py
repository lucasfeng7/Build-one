"""Walk a SolidWorks drawing and yield (displayDim, idim, tol_obj, Feature)."""
from __future__ import annotations

import sys
from typing import Iterator, Optional, Tuple

from .com import call
from .models import (
    SW_DIM_TEXT_PREFIX,
    SW_DIM_TEXT_SUFFIX,
    SW_TOL_NONE,
    Feature,
    Tolerance,
)

DimTuple = Tuple[object, object, object, Feature]


def iter_dimensions(drawing) -> Iterator[DimTuple]:
    """Yield every display dimension across every sheet of the drawing."""
    sheet_names = call(drawing, "GetSheetNames")
    if not sheet_names:
        return
    for sheet_name in sheet_names:
        drawing.ActivateSheet(sheet_name)
        yield from _iter_sheet(drawing, sheet_name)


def _iter_sheet(drawing, sheet_name: str) -> Iterator[DimTuple]:
    # GetFirstView returns the sheet itself; real views start at GetNextView.
    view = call(drawing, "GetFirstView")
    if view is None:
        return
    view = call(view, "GetNextView")
    while view is not None:
        yield from _iter_view(view, _view_name(view), sheet_name)
        view = call(view, "GetNextView")


def _iter_view(view, view_name: str, sheet_name: str) -> Iterator[DimTuple]:
    disp_dim = call(view, "GetFirstDisplayDimension5")
    while disp_dim is not None:
        built = _build_feature(disp_dim, view_name, sheet_name)
        if built is not None:
            feat, idim, tol = built
            yield disp_dim, idim, tol, feat
        disp_dim = call(disp_dim, "GetNext5")


def _build_feature(
    disp_dim, view_name: str, sheet_name: str
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
    feat = Feature(
        value=value,
        dim_type=dim_type,
        current_tolerance_type=current_tol_type,
        view_name=view_name,
        sheet_name=sheet_name,
        is_reference=is_reference,
        text_prefix=text_prefix,
        text_suffix=text_suffix,
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
