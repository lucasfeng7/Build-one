"""Write tolerances back to a SolidWorks drawing via COM."""
from __future__ import annotations

from .models import Tolerance


class SaveFailed(RuntimeError):
    """SaveAs3 returned non-zero errors."""


# swSaveAsOptions_e.swSaveAsOptions_Silent — hardcoded; not consistently exposed
# by name across type library variants.
_SW_SAVE_AS_SILENT: int = 1


def write_tolerance(tol_obj, tolerance: Tolerance) -> None:
    """Mutate a live IDimensionTolerance to the given Tolerance.

    Setting tol_obj.Type must happen BEFORE SetValues2 — SetValues2 silently
    no-ops if Type is still swTolNONE. This is the #1 SolidWorks API gotcha.
    """
    tol_obj.Type = tolerance.tol_type
    tol_obj.SetValues2(tolerance.plus_value, tolerance.minus_value)


def rebuild_and_save(model, out_path: str) -> None:
    """Force a rebuild then SaveAs3 to out_path. Raises SaveFailed on error."""
    import pythoncom
    import win32com.client

    model.ForceRebuild3(False)

    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)

    ok = model.Extension.SaveAs3(
        out_path, 0, _SW_SAVE_AS_SILENT, None, None, errors, warnings,
    )

    if not ok or errors.value != 0:
        raise SaveFailed(
            f"SaveAs3 failed for {out_path!r}: errors={errors.value}, warnings={warnings.value}"
        )
