"""COM connection management for SolidWorks."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from . import models


class SolidWorksUnavailable(RuntimeError):
    """Dispatch failed — not on Windows, or SolidWorks not installed."""


class OpenFailed(RuntimeError):
    """OpenDoc6 returned non-zero errors or None."""


# swOpenDocOptions_e.swOpenDocOptions_Silent — well-known, not exposed via
# named constants in all type library variants, so hardcoded here.
_SW_OPEN_DOC_SILENT: int = 1


def connect() -> object:
    """Dispatch SldWorks.Application and configure the session.

    Side effect: overwrites models.SW_* constants with values from the live
    type library so the rest of the package is value-correct regardless of
    SolidWorks version drift.
    """
    try:
        import pythoncom  # noqa: F401
        from win32com.client import constants, gencache
    except ImportError as e:
        raise SolidWorksUnavailable(
            "pywin32 not available — this tool only runs on Windows with SolidWorks installed."
        ) from e

    try:
        sw = gencache.EnsureDispatch("SldWorks.Application")
    except Exception as e:
        raise SolidWorksUnavailable(f"Could not dispatch SldWorks.Application: {e}") from e

    _sync_constants(constants)

    sw.Visible = True
    sw.SetUserPreferenceToggle(int(constants.swDisableMessages), True)
    return sw


def _sync_constants(constants) -> None:
    models.SW_DOC_DRAWING = int(constants.swDocDRAWING)
    models.SW_TOL_NONE = int(constants.swTolNONE)
    models.SW_TOL_BILAT = int(constants.swTolBILAT)
    models.SW_LINEAR_DIM = int(constants.swLinearDimension)
    models.SW_HOR_LINEAR_DIM = int(constants.swHorLinearDimension)
    models.SW_VERT_LINEAR_DIM = int(constants.swVertLinearDimension)
    models.LINEAR_DIM_TYPES = frozenset({
        models.SW_LINEAR_DIM,
        models.SW_HOR_LINEAR_DIM,
        models.SW_VERT_LINEAR_DIM,
    })


@contextmanager
def open_drawing(sw, path: str) -> Iterator[object]:
    """Open a drawing and ensure CloseDoc on exit."""
    import pythoncom
    import win32com.client

    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)

    model = sw.OpenDoc6(
        path, models.SW_DOC_DRAWING, _SW_OPEN_DOC_SILENT, "", errors, warnings,
    )

    if model is None or errors.value != 0:
        raise OpenFailed(
            f"OpenDoc6 failed for {path!r}: errors={errors.value}, warnings={warnings.value}"
        )

    try:
        yield model
    finally:
        title = model.GetTitle()
        sw.CloseDoc(title)
