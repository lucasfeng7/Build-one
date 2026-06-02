"""Unit tests for decide.tolerance_for — COM-free, macOS-runnable.

Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import math
import unittest

from sw_tolerance.decide import (
    decide_with_rationale,
    set_predictor,
    tolerance_for,
    use_constant,
)
from sw_tolerance.models import (
    Tolerance,
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


class PredictorSeamTests(unittest.TestCase):
    """The pluggable-predictor seam: dispatch, skip-rule ordering, rationale."""

    def tearDown(self):
        # Always restore the module default so one test can't bleed into another.
        use_constant()

    def test_default_predictor_is_constant(self):
        # With no predictor installed, the seam behaves exactly like the old
        # constant policy — this is why the DecideTests above pass unchanged.
        result = tolerance_for(_feature(dim_type=SW_LINEAR_DIM))
        self.assertAlmostEqual(result.plus_value, 0.0005)

    def test_set_predictor_swaps_behavior(self):
        sentinel = Tolerance(tol_type=SW_TOL_BILAT, plus_value=0.123, minus_value=0.456)
        set_predictor(lambda f, family: (sentinel, "because"))
        self.assertIs(tolerance_for(_feature()), sentinel)

    def test_rationale_is_passed_through(self):
        set_predictor(lambda f, family: (None, "left as-is"))
        tol, rationale = decide_with_rationale(_feature())
        self.assertIsNone(tol)
        self.assertEqual(rationale, "left as-is")

    def test_family_is_resolved_for_predictor(self):
        seen = []

        def recording_predictor(f, family):
            seen.append(family)
            return None, None

        set_predictor(recording_predictor)
        decide_with_rationale(_feature(dim_type=SW_ANGULAR_DIM))
        decide_with_rationale(_feature(dim_type=SW_LINEAR_DIM))
        self.assertEqual(seen, ["angular", "length"])

    def test_predictor_not_called_for_skipped_features(self):
        # The skip rules must run BEFORE the predictor, so the LLM (or any
        # predictor) never sees an already-toleranced dim, a hole callout, or an
        # unsupported family — preserving the harvest/apply invariants.
        calls = []
        set_predictor(lambda f, family: calls.append(family) or (None, None))
        self.assertIsNone(tolerance_for(_feature(current_tolerance_type=SW_TOL_BILAT)))
        self.assertIsNone(tolerance_for(_feature(is_hole_callout=True)))
        self.assertIsNone(tolerance_for(_feature(dim_type=10)))  # unknown family
        self.assertEqual(calls, [], "predictor must not run for skipped features")


if __name__ == "__main__":
    unittest.main()
