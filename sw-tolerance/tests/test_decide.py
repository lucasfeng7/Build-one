"""Unit tests for decide.tolerance_for — COM-free, macOS-runnable.

Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import math
import unittest

from sw_tolerance.decide import tolerance_for
from sw_tolerance.models import (
    SW_ANGULAR_DIM,
    SW_ARC_LENGTH_DIM,
    SW_DIAMETER_DIM,
    SW_HOR_LINEAR_DIM,
    SW_HOR_ORDINATE_DIM,
    SW_LINEAR_DIM,
    SW_RADIAL_DIM,
    SW_TOL_BILAT,
    SW_TOL_NONE,
    SW_VERT_LINEAR_DIM,
    SW_VERT_ORDINATE_DIM,
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

    def test_length_family_dims_get_half_mm(self):
        # Diameter, radius, arc-length, and ordinate dims are all length-valued
        # (metres), so they reuse the ±0.5 mm default like linear dims.
        for dim_type in (
            SW_DIAMETER_DIM,
            SW_RADIAL_DIM,
            SW_ARC_LENGTH_DIM,
            SW_HOR_ORDINATE_DIM,
            SW_VERT_ORDINATE_DIM,
        ):
            with self.subTest(dim_type=dim_type):
                result = tolerance_for(_feature(dim_type=dim_type))
                self.assertIsNotNone(result)
                self.assertEqual(result.tol_type, SW_TOL_BILAT)
                self.assertAlmostEqual(result.plus_value, 0.0005)
                self.assertAlmostEqual(result.minus_value, 0.0005)

    def test_angular_dim_gets_one_degree_in_radians(self):
        result = tolerance_for(_feature(dim_type=SW_ANGULAR_DIM))
        self.assertIsNotNone(result)
        self.assertEqual(result.tol_type, SW_TOL_BILAT)
        self.assertAlmostEqual(result.plus_value, math.radians(1.0))
        self.assertAlmostEqual(result.minus_value, math.radians(1.0))

    def test_already_toleranced_is_skipped(self):
        self.assertIsNone(
            tolerance_for(_feature(current_tolerance_type=SW_TOL_BILAT))
        )

    def test_already_toleranced_angular_is_skipped(self):
        self.assertIsNone(
            tolerance_for(
                _feature(dim_type=SW_ANGULAR_DIM, current_tolerance_type=SW_TOL_BILAT)
            )
        )

    def test_unknown_dim_type_is_skipped(self):
        # A dim_type in no family (e.g. chamfer=10, or a made-up value) gets no
        # tolerance — the policy only covers the length and angular families.
        self.assertIsNone(tolerance_for(_feature(dim_type=10)))
        self.assertIsNone(tolerance_for(_feature(dim_type=999)))

    def test_hole_callout_is_skipped(self):
        self.assertIsNone(tolerance_for(_feature(is_hole_callout=True)))

    def test_hole_callout_of_length_family_is_skipped(self):
        self.assertIsNone(
            tolerance_for(_feature(dim_type=SW_DIAMETER_DIM, is_hole_callout=True))
        )


if __name__ == "__main__":
    unittest.main()
