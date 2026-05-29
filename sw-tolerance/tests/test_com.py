"""Unit tests for com.call — COM-free, macOS-runnable.

These simulate pywin32's two dispatch modes with plain Python fakes:

* early binding  → members are real bound methods (call with ``()``)
* late binding   → no-arg members are already-evaluated attribute values

Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import unittest

from sw_tolerance.com import call


class _FakeDispatch:
    """Stand-in for a late-binding COM object: callable but must not be called.

    pywin32 dynamic dispatch objects are callable (they expose the COM default
    member) yet carry an ``_oleobj_`` attribute. ``call`` must recognise this
    and return the object as-is rather than invoking it.
    """

    _oleobj_ = object()

    def __call__(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("a returned dispatch object must not be called")


class _EarlyBound:
    """Stand-in for an early-binding COM object: members are real methods."""

    def __init__(self, sheets, view):
        self._sheets = sheets
        self._view = view
        self.activated = None

    def GetSheetNames(self):
        return self._sheets

    def GetFirstView(self):
        return self._view

    def ActivateSheet(self, name):
        self.activated = name
        return True


class CallTests(unittest.TestCase):
    def test_early_binding_noarg_method_is_called(self):
        obj = _EarlyBound(("Sheet1", "Sheet2"), None)
        self.assertEqual(call(obj, "GetSheetNames"), ("Sheet1", "Sheet2"))

    def test_early_binding_method_returning_dispatch_is_called(self):
        view = _FakeDispatch()
        obj = _EarlyBound((), view)
        # The bound method must be invoked and its dispatch return value
        # passed straight through (not re-invoked).
        self.assertIs(call(obj, "GetFirstView"), view)

    def test_method_with_args_is_called(self):
        obj = _EarlyBound((), None)
        self.assertTrue(call(obj, "ActivateSheet", "SheetX"))
        self.assertEqual(obj.activated, "SheetX")

    def test_late_binding_tuple_value_returned_as_is(self):
        # Late binding already resolved GetSheetNames to a tuple.
        obj = type("Late", (), {"GetSheetNames": ("Sheet1",)})()
        self.assertEqual(call(obj, "GetSheetNames"), ("Sheet1",))

    def test_late_binding_str_value_returned_as_is(self):
        obj = type("Late", (), {"GetTitle": "drawing.SLDDRW"})()
        self.assertEqual(call(obj, "GetTitle"), "drawing.SLDDRW")

    def test_late_binding_none_returned_as_is(self):
        # End-of-list sentinel from e.g. GetNextView.
        obj = type("Late", (), {"GetNextView": None})()
        self.assertIsNone(call(obj, "GetNextView"))

    def test_late_binding_dispatch_value_not_reinvoked(self):
        view = _FakeDispatch()
        obj = type("Late", (), {"GetFirstView": view})()
        self.assertIs(call(obj, "GetFirstView"), view)


if __name__ == "__main__":
    unittest.main()
