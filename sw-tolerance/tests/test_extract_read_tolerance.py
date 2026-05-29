"""Unit tests for extract.read_existing_tolerance — COM-free, macOS-runnable.

A fake IDimensionTolerance (early-binding style: members are real methods, so
com.call invokes them) lets us assert the harvested label without a live
SolidWorks COM object.

Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import unittest

from sw_tolerance.extract import read_existing_tolerance
from sw_tolerance.models import SW_TOL_BILAT, SW_TOL_NONE


class _FakeTol:
    """Stand-in for IDimensionTolerance under early binding.

    GetMaxValue/GetMinValue are real no-arg methods, matching how com.call
    invokes early-bound members.
    """

    def __init__(self, tol_type, max_value, min_value):
        self.Type = tol_type
        self._max = max_value
        self._min = min_value

    def GetMaxValue(self):
        return self._max

    def GetMinValue(self):
        return self._min


class ReadExistingToleranceTests(unittest.TestCase):
    def test_untoleranced_returns_none(self):
        tol = _FakeTol(SW_TOL_NONE, 0.0, 0.0)
        self.assertIsNone(read_existing_tolerance(tol))

    def test_bilateral_values_are_read(self):
        tol = _FakeTol(SW_TOL_BILAT, 0.0005, -0.0005)
        result = read_existing_tolerance(tol)
        self.assertIsNotNone(result)
        self.assertEqual(result.tol_type, SW_TOL_BILAT)
        self.assertAlmostEqual(result.plus_value, 0.0005)
        self.assertAlmostEqual(result.minus_value, 0.0005)

    def test_negative_min_is_normalised_to_positive(self):
        # SolidWorks stores the lower deviation as a negative magnitude; the
        # harvested label must expose it as a positive minus_value to mirror
        # the Tolerance shape decide.tolerance_for produces.
        tol = _FakeTol(SW_TOL_BILAT, 0.001, -0.002)
        result = read_existing_tolerance(tol)
        self.assertAlmostEqual(result.minus_value, 0.002)

    def test_unexpected_positive_min_is_also_normalised(self):
        # Defensive: whatever the sign SolidWorks returns, abs() makes the
        # magnitude well-defined.
        tol = _FakeTol(SW_TOL_BILAT, 0.001, 0.002)
        result = read_existing_tolerance(tol)
        self.assertAlmostEqual(result.minus_value, 0.002)

    def test_com_failure_returns_none(self):
        class _Boom:
            @property
            def Type(self):
                raise RuntimeError("COM error")

        self.assertIsNone(read_existing_tolerance(_Boom()))


if __name__ == "__main__":
    unittest.main()
