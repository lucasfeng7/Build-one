"""COM connection management for SolidWorks."""
from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator, Optional

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
    ensure_error: Optional[Exception] = None

    try:
        sw = gencache.EnsureDispatch("SldWorks.Application")
        constants = win32com.client.constants
    except Exception as e:
        ensure_error = e

    if sw is None:
        try:
            sw = win32com.client.Dispatch("SldWorks.Application")
        except Exception as e:
            raise SolidWorksUnavailable(
                f"Could not dispatch SldWorks.Application: {e} "
                f"(EnsureDispatch also failed: {ensure_error})"
            ) from e
        # Late-binding succeeded but live constants aren't available, so the
        # SW 2020 SDK defaults in models.py are in play. Tell the user so
        # version drift (e.g. SW 2024 enum changes) isn't silent.
        print(
            f"warning: EnsureDispatch failed ({ensure_error}); "
            "falling back to late-binding with SW 2020 SDK enum defaults. "
            "Run `python -m win32com.client.makepy SldWorks.Application` "
            "to enable live constant sync.",
            file=sys.stderr,
        )

    if constants is not None:
        _sync_constants(constants)

    sw.Visible = True
    _disable_messages(sw, constants)
    return sw


def _disable_messages(sw, constants) -> None:
    """Suppress SolidWorks UI prompts; tolerates missing constants."""
    try:
        toggle = (
            int(constants.swDisableMessages)
            if constants is not None
            else models.SW_DISABLE_MESSAGES
        )
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
        title = call(model, "GetTitle")
        sw.CloseDoc(title)
