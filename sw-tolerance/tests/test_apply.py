"""Unit tests for apply.write_tolerance — COM-free, macOS-runnable.

A fake IDimensionTolerance records how the tolerance is written so we can
assert the SetValues2 arity/signs and the SetValues fallback without a live
SolidWorks COM object.

Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import unittest

from sw_tolerance.apply import (
    GtolWriteFailed,
    frame_values,
    write_geometric_tolerance,
    write_tolerance,
)
from sw_tolerance.extract import _build_geometric_tolerance
from sw_tolerance.models import (
    SW_TOL_BILAT,
    DatumRef,
    GeometricTolerance,
    Tolerance,
)


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

    def test_setvalues2_called_with_four_args_min_first(self):
        tol = _FakeTol()
        write_tolerance(tol, _TOL)
        self.assertEqual(len(tol.setvalues2_calls), 1)
        # SetValues2 signature is (MinValue, MaxValue, WhichConfigurations,
        # Config_names) — min comes FIRST.
        min_value, max_value, config_opt, config_names = tol.setvalues2_calls[0]
        self.assertAlmostEqual(min_value, -0.0005)
        self.assertAlmostEqual(max_value, 0.0005)
        self.assertEqual(config_opt, 1)
        self.assertEqual(config_names, "")

    def test_asymmetric_tolerance_keeps_min_max_order(self):
        # Regression guard for the arg-order bug: a symmetric band hides a
        # swapped (min, max) because |min| == |max|. An asymmetric tolerance
        # does not — here min must be -0.002 and max +0.001, in that order.
        tol = _FakeTol()
        write_tolerance(tol, Tolerance(SW_TOL_BILAT, plus_value=0.001, minus_value=0.002))
        min_value, max_value, _opt, _names = tol.setvalues2_calls[0]
        self.assertAlmostEqual(min_value, -0.002)
        self.assertAlmostEqual(max_value, 0.001)

    def test_no_fallback_when_setvalues2_succeeds(self):
        tol = _FakeTol(setvalues2_result=True)
        write_tolerance(tol, _TOL)
        self.assertEqual(tol.setvalues_calls, [])

    def test_fallback_to_setvalues_when_setvalues2_returns_false(self):
        tol = _FakeTol(setvalues2_result=False)
        write_tolerance(tol, _TOL)
        self.assertEqual(len(tol.setvalues_calls), 1)
        # The obsolete fallback uses the same (MinValue, MaxValue) order.
        self.assertAlmostEqual(tol.setvalues_calls[0][0], -0.0005)
        self.assertAlmostEqual(tol.setvalues_calls[0][1], 0.0005)

    def test_fallback_to_setvalues_when_setvalues2_raises(self):
        tol = _FakeTol(setvalues2_raises=True)
        write_tolerance(tol, _TOL)
        self.assertEqual(len(tol.setvalues_calls), 1)


# --- geometric (GD&T) frame writing -----------------------------------------

class FrameValuesTests(unittest.TestCase):
    def test_position_with_diameter_and_datums(self):
        g = GeometricTolerance(
            symbol="position", zone_value=0.0002, diameter_zone=True,
            material_condition="MMC",
            datum_refs=(DatumRef("A"), DatumRef("B", "MMC")),
        )
        self.assertEqual(frame_values(g), ["position", "⌀0.2(M)", "A", "B(M)"])

    def test_flatness_plain(self):
        g = GeometricTolerance(symbol="flatness", zone_value=0.00005)
        self.assertEqual(frame_values(g), ["flatness", "0.05"])

    def test_round_trips_through_the_harvest_parser(self):
        # frame_values (write) is the inverse of _build_geometric_tolerance
        # (read): encoding then parsing must reproduce the original frame.
        original = GeometricTolerance(
            symbol="perpendicularity", zone_value=0.0001, diameter_zone=False,
            material_condition="LMC",
            datum_refs=(DatumRef("A"), DatumRef("C", "MMC")),
        )
        parsed = _build_geometric_tolerance(frame_values(original))
        self.assertEqual(parsed, original)


class _FakeGtolObj:
    def __init__(self):
        self.frames = []

    def SetFrameValues2(self, frame, values):
        self.frames.append((frame, list(values)))


class _FakeExt:
    def __init__(self, gtol_obj):
        self._gtol_obj = gtol_obj

    def InsertGtol(self):
        return self._gtol_obj


class _FakeModel:
    def __init__(self, gtol_obj):
        self.Extension = _FakeExt(gtol_obj)


class _FakeDispDim:
    def __init__(self):
        self.selected = False

    def Select2(self, append, mark):
        self.selected = True
        return True


class WriteGeometricToleranceTests(unittest.TestCase):
    def test_inserts_and_populates_first_frame(self):
        gtol_obj = _FakeGtolObj()
        model = _FakeModel(gtol_obj)
        disp = _FakeDispDim()
        g = GeometricTolerance(symbol="position", zone_value=0.0002,
                               diameter_zone=True, datum_refs=(DatumRef("A"),))
        write_geometric_tolerance(model, disp, g)
        self.assertTrue(disp.selected)
        self.assertEqual(gtol_obj.frames, [(0, ["position", "⌀0.2", "A"])])

    def test_raises_when_insert_returns_none(self):
        model = _FakeModel(None)
        with self.assertRaises(GtolWriteFailed):
            write_geometric_tolerance(model, _FakeDispDim(),
                                      GeometricTolerance(symbol="flatness", zone_value=0.0001))


if __name__ == "__main__":
    unittest.main()
