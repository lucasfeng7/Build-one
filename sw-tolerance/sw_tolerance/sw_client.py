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
    SolidWorks version drift.  Falls back to late-binding Dispatch when the
    type library cache cannot be built (e.g. first run without makepy).
    """
    try:
        import pythoncom  # noqa: F401
        import win32com.client
        from win32com.client import gencache
    except ImportError as e:
        raise SolidWorksUnavailable(
            "pywin32 not available — this tool only runs on Windows with SolidWorks installed."
        ) from e

    sw = None
    constants = None

    # Attempt early-binding first so _sync_constants can read the type library.
    try:
        sw = gencache.EnsureDispatch("SldWorks.Application")
        constants = win32com.client.constants
    except Exception:
        pass

    # Fall back to late-binding if the type library cache couldn't be built.
    if sw is None:
        try:
            sw = win32com.client.Dispatch("SldWorks.Application")
        except Exception as e:
            raise SolidWorksUnavailable(f"Could not dispatch SldWorks.Application: {e}") from e

    if constants is not None:
        _sync_constants(constants)
    # If constants is None we keep the SDK defaults hardcoded in models.py.

    sw.Visible = True
    _disable_messages(sw, constants)
    return sw


def _disable_messages(sw, constants) -> None:
    """Suppress SolidWorks UI prompts; tolerates missing constants."""
    try:
        toggle = int(constants.swDisableMessages) if constants is not None else 263
        sw.SetUserPreferenceToggle(toggle, True)
    except Exception:
        pass


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
