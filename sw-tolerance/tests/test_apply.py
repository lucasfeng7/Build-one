"""Unit tests for apply.write_tolerance — COM-free, macOS-runnable.

These use a fake IDimensionTolerance stand-in to pin down the SetValues2 call
contract (argument count, order, and signs) without needing SolidWorks. The
real bug this guards against: SetValues2 needs four arguments — passing only
two raises a "Parameter not optional" COM error at runtime.

Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import unittest

from sw_tolerance import models
from sw_tolerance.apply import ToleranceWriteFailed, write_tolerance
from sw_tolerance.models import SW_TOL_BILAT, Tolerance


class FakeTolerance:
    """Records Type assignment and SetValues2 calls."""

    def __init__(self, set_values_result: bool = True):
        self.type_when_set_values_called = None
        self.type = None
        self.set_values_args = None
        self._result = set_values_result

    @property
    def Type(self):  # noqa: N802 — mirrors the COM property name
        return self.type

    @Type.setter
    def Type(self, value):  # noqa: N802
        self.type = value

    def SetValues2(self, *args):  # noqa: N802 — mirrors the COM method name
        self.type_when_set_values_called = self.type
        self.set_values_args = args
        return self._result


class WriteToleranceTests(unittest.TestCase):
    def _bilateral(self) -> Tolerance:
        return Tolerance(tol_type=SW_TOL_BILAT, plus_value=0.0005, minus_value=0.0005)

    def test_calls_set_values2_with_four_args(self):
        fake = FakeTolerance()
        write_tolerance(fake, self._bilateral())
        self.assertEqual(len(fake.set_values_args), 4)

    def test_min_is_negative_max_is_positive(self):
        fake = FakeTolerance()
        write_tolerance(fake, Tolerance(SW_TOL_BILAT, plus_value=0.0005, minus_value=0.0005))
        min_value, max_value, _which, _names = fake.set_values_args
        self.assertAlmostEqual(min_value, -0.0005)
        self.assertAlmostEqual(max_value, 0.0005)

    def test_passes_config_option_and_empty_config_names(self):
        fake = FakeTolerance()
        write_tolerance(fake, self._bilateral())
        _min, _max, which, names = fake.set_values_args
        self.assertEqual(which, models.SW_SETVALUE_THIS_CONFIG)
        self.assertEqual(names, "")

    def test_type_set_before_set_values2(self):
        fake = FakeTolerance()
        write_tolerance(fake, self._bilateral())
        self.assertEqual(fake.type_when_set_values_called, SW_TOL_BILAT)

    def test_raises_when_set_values2_returns_false(self):
        fake = FakeTolerance(set_values_result=False)
        with self.assertRaises(ToleranceWriteFailed):
            write_tolerance(fake, self._bilateral())


if __name__ == "__main__":
    unittest.main()
