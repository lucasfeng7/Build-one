"""Unit tests for apply.write_tolerance — COM-free, macOS-runnable.

A fake IDimensionTolerance records how the tolerance is written so we can
assert the SetValues2 arity/signs and the SetValues fallback without a live
SolidWorks COM object.

Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import unittest

from sw_tolerance.apply import write_tolerance
from sw_tolerance.models import SW_TOL_BILAT, Tolerance


class _FakeTol:
    def __init__(self, setvalues2_result=True, setvalues2_raises=False):
        self.type_set = None
        self.setvalues2_calls = []
        self.setvalues_calls = []
        self._result = setvalues2_result
        self._raises = setvalues2_raises

    @property
    def Type(self):  # pragma: no cover - not read by code under test
        return self.type_set

    @Type.setter
    def Type(self, value):
        self.type_set = value

    def SetValues2(self, *args):
        self.setvalues2_calls.append(args)
        if self._raises:
            raise RuntimeError("COM type mismatch")
        return self._result

    def SetValues(self, *args):
        self.setvalues_calls.append(args)
        return True


_TOL = Tolerance(tol_type=SW_TOL_BILAT, plus_value=0.0005, minus_value=0.0005)


class WriteToleranceTests(unittest.TestCase):
    def test_type_is_set_before_values(self):
        tol = _FakeTol()
        write_tolerance(tol, _TOL)
        self.assertEqual(tol.type_set, SW_TOL_BILAT)

    def test_setvalues2_called_with_four_args_and_negative_min(self):
        tol = _FakeTol()
        write_tolerance(tol, _TOL)
        self.assertEqual(len(tol.setvalues2_calls), 1)
        max_value, min_value, config_opt, config_names = tol.setvalues2_calls[0]
        self.assertAlmostEqual(max_value, 0.0005)
        self.assertAlmostEqual(min_value, -0.0005)
        self.assertEqual(config_opt, 1)
        self.assertEqual(config_names, "")

    def test_no_fallback_when_setvalues2_succeeds(self):
        tol = _FakeTol(setvalues2_result=True)
        write_tolerance(tol, _TOL)
        self.assertEqual(tol.setvalues_calls, [])

    def test_fallback_to_setvalues_when_setvalues2_returns_false(self):
        tol = _FakeTol(setvalues2_result=False)
        write_tolerance(tol, _TOL)
        self.assertEqual(len(tol.setvalues_calls), 1)
        self.assertAlmostEqual(tol.setvalues_calls[0][0], 0.0005)
        self.assertAlmostEqual(tol.setvalues_calls[0][1], -0.0005)

    def test_fallback_to_setvalues_when_setvalues2_raises(self):
        tol = _FakeTol(setvalues2_raises=True)
        write_tolerance(tol, _TOL)
        self.assertEqual(len(tol.setvalues_calls), 1)


if __name__ == "__main__":
    unittest.main()
