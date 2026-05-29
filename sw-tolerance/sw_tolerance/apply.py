"""Write tolerances back to a SolidWorks drawing via COM."""
from __future__ import annotations

from typing import Optional

from .models import Tolerance


class SaveFailed(RuntimeError):
    """Every save strategy returned an error or raised a COM exception."""


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
