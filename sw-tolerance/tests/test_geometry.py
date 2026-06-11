"""Unit tests for geometry.resolve_geometry and its helpers — COM-free.

These exercise the pure classification logic (surface → type, feature type-name →
kind/internal, path → basename) and the *guarding* (each COM read degrades to a
safe default, a total failure returns None) using fake COM objects whose no-arg
members are plain methods, which is how com.call invokes them. The real COM
chain is verified on Windows. Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import unittest

from sw_tolerance import geometry
from sw_tolerance.geometry import (
    _as_list,
    _basename,
    _classify_feature_typename,
    _classify_surface,
    _cylinder_diameter,
    _safe_feature,
    _safe_referenced_model,
    _safe_surface,
    resolve_geometry,
)
from sw_tolerance.models import (
    FEATURE_KIND_VALUES,
    SURFACE_TYPE_VALUES,
    GeometryContext,
)

# swSelectType_e codes the module keys on.
SEL_EDGES = 1
SEL_FACES = 2


# --- fakes ------------------------------------------------------------------

class FakeSurface:
    def __init__(self, kind=None, radius=None, raise_on=()):
        self._kind = kind  # "cylinder" | "plane" | "cone" | None
        self._radius = radius
        self._raise_on = set(raise_on)

    def _flag(self, name, kind):
        if name in self._raise_on:
            raise RuntimeError(f"{name} unavailable")
        return self._kind == kind

    def IsCylinder(self):
        return self._flag("IsCylinder", "cylinder")

    def IsPlane(self):
        return self._flag("IsPlane", "plane")

    def IsCone(self):
        return self._flag("IsCone", "cone")

    def CylinderParams(self):
        if self._radius is None:
            raise RuntimeError("CylinderParams unavailable")
        return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0, self._radius)


class FakeDefinition:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)


class FakeFeature:
    def __init__(self, type_name="", definition=None):
        self._type_name = type_name
        self._definition = definition

    def GetTypeName2(self):
        return self._type_name

    def GetDefinition(self):
        if self._definition is None:
            raise RuntimeError("no definition")
        return self._definition


class FakeFace:
    """A face that can also stand in as an attached entity (GetType == FACES)."""

    def __init__(self, surface=None, feature=None,
                 raise_surface=False, raise_feature=False, etype=SEL_FACES):
        self._surface = surface
        self._feature = feature
        self._raise_surface = raise_surface
        self._raise_feature = raise_feature
        self._etype = etype

    def GetType(self):
        return self._etype

    def GetSurface(self):
        if self._raise_surface:
            raise RuntimeError("GetSurface unavailable")
        return self._surface

    def GetFeature(self):
        if self._raise_feature:
            raise RuntimeError("GetFeature unavailable")
        return self._feature


class FakeEdge:
    def __init__(self, adjacent=None, etype=SEL_EDGES):
        self._adjacent = adjacent
        self._etype = etype

    def GetType(self):
        return self._etype

    def GetTwoAdjacentFaces2(self):
        return self._adjacent


class FakeAnnotation:
    def __init__(self, entities):
        self._entities = entities

    def GetAttachedEntities3(self):
        return self._entities


class FakeDispDim:
    def __init__(self, annotation=None):
        self._annotation = annotation

    def GetAnnotation(self):
        if self._annotation is None:
            raise RuntimeError("no annotation")
        return self._annotation


class FakeDoc:
    def __init__(self, path):
        self._path = path

    def GetPathName(self):
        return self._path


class FakeView:
    def __init__(self, ref_doc=None, model_name=None):
        if ref_doc is not None:
            self.ReferencedDocument = ref_doc
        self._model_name = model_name

    def GetReferencedModelName(self):
        if self._model_name is None:
            raise RuntimeError("no referenced model")
        return self._model_name


# --- pure helpers -----------------------------------------------------------

class BasenameTests(unittest.TestCase):
    def test_windows_and_posix_separators(self):
        self.assertEqual(_basename(r"C:\\drawings\\bracket.SLDPRT"), "bracket.SLDPRT")
        self.assertEqual(_basename("/home/u/bracket.SLDPRT"), "bracket.SLDPRT")

    def test_bare_name_unchanged(self):
        self.assertEqual(_basename("bracket.SLDPRT"), "bracket.SLDPRT")


class AsListTests(unittest.TestCase):
    def test_none_tuple_single(self):
        self.assertEqual(_as_list(None), [])
        self.assertEqual(_as_list((1, 2)), [1, 2])
        self.assertEqual(_as_list(7), [7])


class ClassifyFeatureTypenameTests(unittest.TestCase):
    def test_hole_wizard_is_internal_clearance(self):
        self.assertEqual(_classify_feature_typename("HoleWzd"), ("hole_clearance", True))

    def test_cut_is_internal_unknown_kind(self):
        self.assertEqual(_classify_feature_typename("CutExtrude"), ("unknown", True))

    def test_boss_is_external(self):
        self.assertEqual(_classify_feature_typename("BossExtrude"), ("boss", False))

    def test_fillet_and_chamfer_have_no_sense(self):
        self.assertEqual(_classify_feature_typename("Fillet"), ("fillet", None))
        self.assertEqual(_classify_feature_typename("Chamfer"), ("chamfer", None))

    def test_unknown_and_empty(self):
        self.assertEqual(_classify_feature_typename("Sketch"), ("unknown", None))
        self.assertEqual(_classify_feature_typename(""), ("unknown", None))


# --- surface classification -------------------------------------------------

class SurfaceTests(unittest.TestCase):
    def test_cylinder_with_diameter(self):
        kind, dia = _classify_surface(FakeSurface(kind="cylinder", radius=0.003))
        self.assertEqual(kind, "cylinder")
        self.assertAlmostEqual(dia, 0.006)

    def test_plane_and_cone_have_no_diameter(self):
        self.assertEqual(_classify_surface(FakeSurface(kind="plane")), ("plane", None))
        self.assertEqual(_classify_surface(FakeSurface(kind="cone")), ("cone", None))

    def test_unrecognised_surface_is_other(self):
        self.assertEqual(_classify_surface(FakeSurface(kind=None)), ("other", None))

    def test_flag_failure_degrades_not_raises(self):
        # IsCylinder raising must not crash — falls through to the next check.
        surf = FakeSurface(kind="plane", raise_on=("IsCylinder",))
        self.assertEqual(_classify_surface(surf), ("plane", None))

    def test_cylinder_without_params_has_no_diameter(self):
        self.assertIsNone(_cylinder_diameter(FakeSurface(kind="cylinder", radius=None)))

    def test_nonpositive_radius_rejected(self):
        self.assertIsNone(_cylinder_diameter(FakeSurface(kind="cylinder", radius=0.0)))

    def test_safe_surface_none_face(self):
        self.assertEqual(_safe_surface(None), ("unknown", None))


# --- referenced model -------------------------------------------------------

class ReferencedModelTests(unittest.TestCase):
    def test_from_referenced_document_path(self):
        view = FakeView(ref_doc=FakeDoc(r"C:\\parts\\bracket.SLDPRT"))
        self.assertEqual(_safe_referenced_model(view), "bracket.SLDPRT")

    def test_falls_back_to_model_name(self):
        view = FakeView(model_name="lever.SLDPRT")
        self.assertEqual(_safe_referenced_model(view), "lever.SLDPRT")

    def test_degrades_to_empty(self):
        self.assertEqual(_safe_referenced_model(object()), "")


# --- feature resolution -----------------------------------------------------

class FeatureResolutionTests(unittest.TestCase):
    def test_hole_wizard_reads_standard(self):
        feat = FakeFeature("HoleWzd", FakeDefinition(Standard="M6"))
        face = FakeFace(feature=feat)
        self.assertEqual(_safe_feature(face), ("hole_clearance", "M6", True))

    def test_boss_has_no_hole_standard(self):
        face = FakeFace(feature=FakeFeature("BossExtrude"))
        self.assertEqual(_safe_feature(face), ("boss", "", False))

    def test_feature_read_failure_degrades(self):
        self.assertEqual(_safe_feature(FakeFace(raise_feature=True)), ("unknown", "", None))

    def test_none_face(self):
        self.assertEqual(_safe_feature(None), ("unknown", "", None))


# --- end-to-end resolve_geometry -------------------------------------------

class ResolveGeometryTests(unittest.TestCase):
    def _hole_dim(self):
        surface = FakeSurface(kind="cylinder", radius=0.0033)
        feature = FakeFeature("HoleWzd", FakeDefinition(Standard="M6"))
        face = FakeFace(surface=surface, feature=feature)
        dim = FakeDispDim(annotation=FakeAnnotation([face]))
        view = FakeView(ref_doc=FakeDoc(r"C:\\parts\\plate.SLDPRT"))
        return dim, view

    def test_full_hole_resolves(self):
        dim, view = self._hole_dim()
        ctx = resolve_geometry(dim, view)
        self.assertIsInstance(ctx, GeometryContext)
        self.assertEqual(ctx.feature_kind, "hole_clearance")
        self.assertEqual(ctx.surface_type, "cylinder")
        self.assertAlmostEqual(ctx.nominal_diameter, 0.0066)
        self.assertTrue(ctx.is_internal)
        self.assertEqual(ctx.hole_standard, "M6")
        self.assertEqual(ctx.referenced_model, "plate.SLDPRT")

    def test_edge_attachment_resolves_adjacent_face(self):
        face = FakeFace(surface=FakeSurface(kind="plane"),
                        feature=FakeFeature("BossExtrude"))
        edge = FakeEdge(adjacent=[face])
        dim = FakeDispDim(annotation=FakeAnnotation([edge]))
        ctx = resolve_geometry(dim, FakeView(model_name="x.SLDPRT"))
        self.assertEqual(ctx.surface_type, "plane")
        self.assertEqual(ctx.feature_kind, "boss")

    def test_cylinder_without_feature_falls_back_to_surface_kind(self):
        face = FakeFace(surface=FakeSurface(kind="cylinder", radius=0.005),
                        raise_feature=True)
        dim = FakeDispDim(annotation=FakeAnnotation([face]))
        ctx = resolve_geometry(dim, FakeView(model_name="x.SLDPRT"))
        self.assertEqual(ctx.feature_kind, "cylindrical_face")
        self.assertEqual(ctx.surface_type, "cylinder")

    def test_nothing_resolvable_returns_none(self):
        self.assertIsNone(resolve_geometry(object(), object()))

    def test_outputs_stay_within_declared_taxonomies(self):
        dim, view = self._hole_dim()
        ctx = resolve_geometry(dim, view)
        self.assertIn(ctx.feature_kind, FEATURE_KIND_VALUES)
        self.assertIn(ctx.surface_type, SURFACE_TYPE_VALUES)


if __name__ == "__main__":
    unittest.main()
