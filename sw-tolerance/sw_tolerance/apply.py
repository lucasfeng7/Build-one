"""Write tolerances back to a SolidWorks drawing via COM."""
from __future__ import annotations

from typing import List, Optional

from .com import call
from .models import GeometricTolerance, Tolerance


class SaveFailed(RuntimeError):
    """Every save strategy returned an error or raised a COM exception."""


class GtolWriteFailed(RuntimeError):
    """Inserting or populating a geometric-tolerance frame failed."""


# Material-condition text suffixes, the inverse of extract._extract_modifier.
# RFS is implicit (no symbol), so it contributes no suffix.
_MOD_SUFFIX = {"MMC": "(M)", "LMC": "(L)", "RFS": ""}


# swSaveAsOptions_e.swSaveAsOptions_Silent — hardcoded; not consistently exposed
# by name across type library variants.
_SW_SAVE_AS_SILENT: int = 1

# swSetValueInConfiguration_e.swSetValue_InThisConfiguration
_SW_SET_VALUE_THIS_CONFIG: int = 1


def write_tolerance(tol_obj, tolerance: Tolerance) -> None:
    """Mutate a live IDimensionTolerance to the given Tolerance.

    Two SolidWorks API gotchas are encoded here:

    1. tol_obj.Type must be set BEFORE writing values — SetValues2 silently
       no-ops if Type is still swTolNONE.

    2. IDimensionTolerance::SetValues2 takes FOUR args, in the order
       (MinValue, MaxValue, ConfigurationOption, ConfigurationNames) — calling
       it with two raises a COM error. The argument order matters and is
       MIN-FIRST: SolidWorks stores the lower deviation as a negative value
       (MinValue) and the upper deviation as positive (MaxValue), so the minus
       magnitude is negated here and passed first. Passing them max-first is a
       silent corruption that is invisible for a symmetric band (|min| == |max|)
       but applies an asymmetric tolerance backwards. The obsolete 2-arg
       SetValues uses the same (MinValue, MaxValue) order. SetValues2 is also
       documented to no-op/return False for single-configuration docs in some
       releases, so we fall back to SetValues.
    """
    tol_obj.Type = tolerance.tol_type

    max_value = abs(tolerance.plus_value)
    min_value = -abs(tolerance.minus_value)

    try:
        ok = tol_obj.SetValues2(
            min_value, max_value, _SW_SET_VALUE_THIS_CONFIG, "",
        )
    except Exception:
        ok = False

    if not ok:
        # Obsolete but reliable for single-configuration documents. Same
        # (MinValue, MaxValue) order as SetValues2. If this also fails it
        # raises, and the caller records the dimension as failed.
        tol_obj.SetValues(min_value, max_value)


def write_geometric_tolerance(model, disp_dim, gtol: GeometricTolerance) -> None:
    """Insert a GD&T feature control frame for the dimension's feature.

    Best-effort COM and a first-run unknown (confirm on Windows): select the
    geometry the dimension attaches to, insert an ``IGtol`` via the model
    extension, and populate its first frame from ``gtol``. Symmetric with the
    harvest read — the frame text comes from ``frame_values``, the inverse of
    ``extract._build_geometric_tolerance``. Raises ``GtolWriteFailed`` (or lets a
    COM error propagate) on any failure, so the caller records the frame as
    ``failed`` via its per-item try/except, exactly like ``write_tolerance``.
    """
    _select_for_gtol(disp_dim)
    gtol_obj = _insert_gtol(model)
    if gtol_obj is None:
        raise GtolWriteFailed("InsertGtol returned None")
    _set_frame(gtol_obj, frame_values(gtol))


def frame_values(gtol: GeometricTolerance) -> List[str]:
    """The first frame's text values ``[symbol, tolerance, *datums]``. Pure.

    The inverse of ``extract._build_geometric_tolerance``'s parse, so the encoding
    is round-trippable and verifiable on macOS independent of the COM write:
    a ⌀ prefix for a diameter zone, the zone in millimetres, a material-condition
    suffix, and one entry per ordered datum with its own modifier suffix.
    """
    tol_text = "⌀" if gtol.diameter_zone else ""
    tol_text += _format_zone_mm(gtol.zone_value * 1000.0)
    tol_text += _MOD_SUFFIX.get(gtol.material_condition, "")
    datums = [d.letter + _MOD_SUFFIX.get(d.modifier, "") for d in gtol.datum_refs]
    return [gtol.symbol, tol_text] + datums


def _format_zone_mm(value_mm: float) -> str:
    """Zone magnitude in mm, trailing zeros trimmed (e.g. 0.2000 → "0.2")."""
    text = f"{value_mm:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _select_for_gtol(disp_dim) -> None:
    """Select the dimension (so InsertGtol attaches the frame to its geometry).

    ``Select2`` takes arguments (a real callable under both bindings); falls back
    to selecting the underlying annotation. Members/signatures are first-run
    unknowns.
    """
    try:
        disp_dim.Select2(False, 0)
    except Exception:
        ann = call(disp_dim, "GetAnnotation")
        ann.Select3(False, None)


def _insert_gtol(model):
    """Insert an empty IGtol on the active view via the model extension."""
    return call(model.Extension, "InsertGtol")


def _set_frame(gtol_obj, values: List[str]) -> None:
    """Populate the IGtol's first frame — the inverse of GetFrameValues2(0)."""
    gtol_obj.SetFrameValues2(0, values)


def rebuild_and_save(model, out_path: str) -> None:
    """Force a rebuild then save to out_path. Raises SaveFailed on error.

    Out-params (errors/warnings) are passed as byref VARIANTs — the same
    pattern ``OpenDoc6`` uses successfully under the late-binding COM path.

    Method choice is deliberate. ``IModelDocExtension::SaveAs3`` takes an extra
    ``AdvancedSaveAsOptions`` object argument; passing a bare Python ``None``
    for it marshals as an empty VARIANT and raises COM ``Type mismatch`` under
    late binding. So we try the 6-arg ``SaveAs`` first (the canonical
    late-binding pattern, ``None`` for ExportData), then fall back to
    ``SaveAs3`` with explicit null-dispatch VARIANTs for the object args.
    """
    import pythoncom
    import win32com.client

    model.ForceRebuild3(False)

    def _out_pair():
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        return errors, warnings

    # Explicit null object pointer (VBA "Nothing") for SaveAs3's object args.
    null_obj = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

    def _save_as(errors, warnings):
        return model.Extension.SaveAs(
            out_path, 0, _SW_SAVE_AS_SILENT, None, errors, warnings,
        )

    def _save_as3(errors, warnings):
        return model.Extension.SaveAs3(
            out_path, 0, _SW_SAVE_AS_SILENT, null_obj, null_obj, errors, warnings,
        )

    attempts = (("SaveAs", _save_as), ("SaveAs3", _save_as3))

    last: Optional[str] = None
    for label, fn in attempts:
        errors, warnings = _out_pair()
        try:
            ok = fn(errors, warnings)
        except Exception as exc:  # COM type mismatch / missing method — try next
            last = f"{label} raised {exc}"
            continue
        if ok and errors.value == 0:
            return
        last = f"{label}: ok={ok} errors={errors.value} warnings={warnings.value}"

    raise SaveFailed(f"save failed for {out_path!r} ({last})")
