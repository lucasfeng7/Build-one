"""Walk a SolidWorks drawing and yield (displayDim, idim, tol_obj, Feature)."""
from __future__ import annotations

import sys
from typing import Iterator, Optional, Tuple

from .models import Feature

DimTuple = Tuple[object, object, object, Feature]


def iter_dimensions(drawing) -> Iterator[DimTuple]:
    """Yield every display dimension across every sheet of the drawing."""
    sheet_names = drawing.GetSheetNames()
    if not sheet_names:
        return
    for sheet_name in sheet_names:
        drawing.ActivateSheet(sheet_name)
        yield from _iter_sheet(drawing, sheet_name)


def _iter_sheet(drawing, sheet_name: str) -> Iterator[DimTuple]:
    # GetFirstView returns the sheet itself; real views start at GetNextView.
    view = drawing.GetFirstView()
    if view is None:
        return
    view = view.GetNextView()
    while view is not None:
        yield from _iter_view(view, _view_name(view), sheet_name)
        view = view.GetNextView()


def _iter_view(view, view_name: str, sheet_name: str) -> Iterator[DimTuple]:
    disp_dim = view.GetFirstDisplayDimension5()
    while disp_dim is not None:
        built = _build_feature(disp_dim, view_name, sheet_name)
        if built is not None:
            feat, idim, tol = built
            yield disp_dim, idim, tol, feat
        disp_dim = disp_dim.GetNext5()


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
    feat = Feature(
        value=value,
        dim_type=dim_type,
        current_tolerance_type=current_tol_type,
        view_name=view_name,
        sheet_name=sheet_name,
    )
    return feat, idim, tol


def _view_name(view) -> str:
    try:
        return view.GetName2()
    except Exception:
        try:
            return view.Name
        except Exception:
            return "<unknown>"


def get_active_config_name(model) -> str:
    try:
        cfg = model.GetActiveConfiguration()
        return cfg.Name
    except Exception:
        return "<unknown>"
