"""Unit tests for geometric-tolerance (GD&T) extraction — COM-free.

Covers the pure frame-text parsing/mapping, the single guarded COM read
(GetFrameValues2) via a fake IGtol, the view annotation walk, and the harvest
record shape. The real IGtol/annotation COM members are confirmed on Windows.
Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import unittest

from sw_tolerance.extract import (
    _build_geometric_tolerance,
    _extract_modifier,
    _first_number,
    _parse_datum,
    _parse_zone,
    _symbol_from_value,
    iter_view_geometric_tolerances,
    read_geometric_tolerance,
)
from sw_tolerance.models import DatumRef, GeometricTolerance


# --- pure symbol / modifier / number helpers --------------------------------

class SymbolMappingTests(unittest.TestCase):
    def test_canonical_names_pass_through(self):
        for name in ("position", "flatness", "total_runout"):
            self.assertEqual(_symbol_from_value(name), name)

    def test_variants_normalise(self):
        self.assertEqual(_symbol_from_value("Perpendicular"), "perpendicularity")
        self.assertEqual(_symbol_from_value("true position"), "position")
        self.assertEqual(_symbol_from_value("Concentric"), "concentricity")

    def test_unknown_and_none(self):
        self.assertIsNone(_symbol_from_value("bogus"))
        self.assertIsNone(_symbol_from_value(None))


class ModifierAndNumberTests(unittest.TestCase):
    def test_modifier_tokens(self):
        self.assertEqual(_extract_modifier("⌀0.2(M)"), "MMC")
        self.assertEqual(_extract_modifier("B(L)"), "LMC")
        self.assertEqual(_extract_modifier("A"), "RFS")

    def test_first_number(self):
        self.assertAlmostEqual(_first_number("⌀0.2(M)"), 0.2)
        self.assertAlmostEqual(_first_number("0.05"), 0.05)
        self.assertIsNone(_first_number("ABC"))


class ParseZoneTests(unittest.TestCase):
    def test_diameter_zone_with_modifier(self):
        zone, dia, mat = _parse_zone("⌀0.2(M)")
        self.assertAlmostEqual(zone, 0.0002)  # 0.2 mm → m
        self.assertTrue(dia)
        self.assertEqual(mat, "MMC")

    def test_plain_zone(self):
        zone, dia, mat = _parse_zone("0.05")
        self.assertAlmostEqual(zone, 0.00005)
        self.assertFalse(dia)
        self.assertEqual(mat, "RFS")

    def test_dia_prefix_word(self):
        _, dia, _ = _parse_zone("DIA0.1")
        self.assertTrue(dia)

    def test_unparseable_zone(self):
        self.assertEqual(_parse_zone("n/a"), (None, False, "RFS"))


class ParseDatumTests(unittest.TestCase):
    def test_plain_and_modified(self):
        self.assertEqual(_parse_datum("A"), DatumRef("A", "RFS"))
        self.assertEqual(_parse_datum("B(M)"), DatumRef("B", "MMC"))

    def test_empty_or_nonletter(self):
        self.assertIsNone(_parse_datum(""))
        self.assertIsNone(_parse_datum("0.5"))


# --- frame assembly ---------------------------------------------------------

class BuildGeometricToleranceTests(unittest.TestCase):
    def test_position_with_datums(self):
        g = _build_geometric_tolerance(["position", "⌀0.2(M)", "A", "B(M)", "C"])
        self.assertEqual(g.symbol, "position")
        self.assertAlmostEqual(g.zone_value, 0.0002)
        self.assertTrue(g.diameter_zone)
        self.assertEqual(g.material_condition, "MMC")
        self.assertEqual([d.letter for d in g.datum_refs], ["A", "B", "C"])
        self.assertEqual(g.datum_refs[1].modifier, "MMC")

    def test_flatness_no_datums(self):
        g = _build_geometric_tolerance(["flatness", "0.05"])
        self.assertEqual(g.symbol, "flatness")
        self.assertAlmostEqual(g.zone_value, 0.00005)
        self.assertFalse(g.diameter_zone)
        self.assertEqual(g.datum_refs, ())

    def test_unknown_symbol_rejected(self):
        self.assertIsNone(_build_geometric_tolerance(["bogus", "0.1"]))

    def test_missing_zone_rejected(self):
        self.assertIsNone(_build_geometric_tolerance(["position"]))

    def test_empty_rejected(self):
        self.assertIsNone(_build_geometric_tolerance([]))


# --- COM read seam + annotation walk (fake COM) -----------------------------

class FakeGtol:
    def __init__(self, values, raise_on_read=False):
        self._values = values
        self._raise = raise_on_read

    def GetFrameValues2(self, frame):
        if self._raise:
            raise RuntimeError("not a gtol")
        return self._values


class FakeAnnotation:
    def __init__(self, specific=None, nxt=None, entities=None):
        self._specific = specific
        self._next = nxt
        self._entities = entities or []

    def GetSpecificAnnotation(self):
        return self._specific

    def GetNext2(self):
        return self._next

    def GetAttachedEntities3(self):
        return self._entities


class FakeView:
    def __init__(self, first_annotation=None):
        self._first = first_annotation

    def GetFirstAnnotation2(self):
        return self._first


class ReadGeometricToleranceTests(unittest.TestCase):
    def test_reads_through_getframevalues2(self):
        g = read_geometric_tolerance(FakeGtol(["position", "0.2", "A"]))
        self.assertEqual(g.symbol, "position")
        self.assertAlmostEqual(g.zone_value, 0.0002)
        self.assertEqual([d.letter for d in g.datum_refs], ["A"])

    def test_non_gtol_object_returns_none(self):
        # No GetFrameValues2 member → guarded read yields [] → None.
        self.assertIsNone(read_geometric_tolerance(object()))

    def test_read_error_returns_none(self):
        self.assertIsNone(read_geometric_tolerance(FakeGtol(None, raise_on_read=True)))


class IterViewGtolsTests(unittest.TestCase):
    def test_walks_chain_and_skips_non_gtols(self):
        flat = FakeAnnotation(specific=FakeGtol(["flatness", "0.05"]))
        non = FakeAnnotation(specific=object(), nxt=flat)          # not a gtol → skipped
        pos = FakeAnnotation(
            specific=FakeGtol(["position", "⌀0.1", "A"]), nxt=non)
        view = FakeView(first_annotation=pos)

        results = list(iter_view_geometric_tolerances(view))
        symbols = [label.symbol for label, _geo in results]
        self.assertEqual(symbols, ["position", "flatness"])
        # No attached entities and a bare view → geometry resolves to None.
        self.assertTrue(all(geo is None for _label, geo in results))

    def test_empty_view_yields_nothing(self):
        self.assertEqual(list(iter_view_geometric_tolerances(FakeView())), [])


class HarvestGeometricRecordTests(unittest.TestCase):
    def test_record_shape(self):
        from harvest import _build_geometric_record
        from sw_tolerance.models import GeometryContext

        geo = GeometryContext(feature_kind="hole_clearance", surface_type="cylinder",
                              nominal_diameter=0.006, is_internal=True)
        label = GeometricTolerance(symbol="position", zone_value=0.0002,
                                   diameter_zone=True, datum_refs=(DatumRef("A"),))
        rec = _build_geometric_record(geo, label, "View1", "Sheet1", "/d/x.SLDDRW")
        self.assertEqual(rec["label_type"], "geometric")
        self.assertEqual(rec["source_file"], "/d/x.SLDDRW")
        self.assertEqual(rec["feature"]["view_name"], "View1")
        self.assertEqual(rec["feature"]["geometry"]["feature_kind"], "hole_clearance")
        self.assertEqual(rec["label"]["symbol"], "position")
        self.assertEqual(rec["label"]["datum_refs"][0]["letter"], "A")

    def test_record_tolerates_missing_geometry(self):
        from harvest import _build_geometric_record

        label = GeometricTolerance(symbol="flatness", zone_value=0.00005)
        rec = _build_geometric_record(None, label, "View1", "Sheet1", "x")
        self.assertIsNone(rec["feature"]["geometry"])


if __name__ == "__main__":
    unittest.main()
