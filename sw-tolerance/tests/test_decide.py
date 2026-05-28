"""Unit tests for decide.tolerance_for — COM-free, macOS-runnable.

Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import unittest

from sw_tolerance.decide import tolerance_for
from sw_tolerance.models import (
    SW_HOR_LINEAR_DIM,
    SW_LINEAR_DIM,
    SW_TOL_BILAT,
    SW_TOL_NONE,
    SW_VERT_LINEAR_DIM,
    Feature,
)


def _feature(**overrides) -> Feature:
    defaults = dict(
        value=0.01,
        dim_type=SW_LINEAR_DIM,
        current_tolerance_type=SW_TOL_NONE,
        view_name="View1",
        sheet_name="Sheet1",
        is_hole_callout=False,
    )
    defaults.update(overrides)
    return Feature(**defaults)


class DecideTests(unittest.TestCase):
    def test_linear_untoleranced_gets_bilateral_half_mm(self):
        result = tolerance_for(_feature(dim_type=SW_LINEAR_DIM))
        self.assertIsNotNone(result)
        self.assertEqual(result.tol_type, SW_TOL_BILAT)
        self.assertAlmostEqual(result.plus_value, 0.0005)
        self.assertAlmostEqual(result.minus_value, 0.0005)

    def test_horizontal_linear_is_toleranced(self):
        self.assertIsNotNone(tolerance_for(_feature(dim_type=SW_HOR_LINEAR_DIM)))

    def test_vertical_linear_is_toleranced(self):
        self.assertIsNotNone(tolerance_for(_feature(dim_type=SW_VERT_LINEAR_DIM)))

    def test_already_toleranced_is_skipped(self):
        self.assertIsNone(
            tolerance_for(_feature(current_tolerance_type=SW_TOL_BILAT))
        )

    def test_non_linear_dim_is_skipped(self):
        # A made-up dim_type that isn't in LINEAR_DIM_TYPES (e.g. angular).
        self.assertIsNone(tolerance_for(_feature(dim_type=999)))

    def test_hole_callout_is_skipped(self):
        self.assertIsNone(tolerance_for(_feature(is_hole_callout=True)))


if __name__ == "__main__":
    unittest.main()
