"""Resolve 3D-model context for a drawing dimension (parts-only, best-effort).

Given a display dimension and its drawing view, walk back to the part the view
references and the face/feature the dimension measures, producing a pure
``GeometryContext`` (models.py). This is the input enrichment that lets the
predictor reason about *what* a feature is — a clearance hole vs a bore vs a
planar datum face — not just its number, and is the prerequisite that makes
intelligent GD&T selection possible.

Mirrors ``extract._display_attrs``: every COM read is wrapped individually, so a
missing/over-version member — or a dimension whose view references an assembly
(out of scope for this parts-only pass) — degrades to a field default rather
than dropping the dimension. There are no COM imports here; no-arg members go
through ``com.call`` (invariant 3a) and the pure classification helpers keep the
module unit-testable on macOS. The exact COM chain (dimension → attached face →
surface/feature) is a first-run unknown — confirm/adjust on Windows.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .com import call
from .models import GeometryContext

# swSelectType_e — entity-type codes returned by IEntity::GetType. Hardcoded
# (well-known and stable) so this module needs no constant sync; used only to
# pick a face out of a dimension's attached entities.
_SW_SEL_EDGES: int = 1
_SW_SEL_FACES: int = 2


def resolve_geometry(disp_dim, view) -> Optional[GeometryContext]:
    """Best-effort ``GeometryContext`` for a dimension, or None if nothing resolved.

    Never raises. A total failure returns None (so ``Feature.geometry`` stays
    None and the caller can distinguish "no geometry" from "geometry says
    unknown"); partial success returns a context with the resolved fields set and
    the rest left at their ``unknown`` / None defaults.
    """
    return _resolve(_safe_attached_face(disp_dim), view)


def resolve_geometry_from_annotation(ann, view) -> Optional[GeometryContext]:
    """Like ``resolve_geometry`` but starting from an ``IAnnotation`` directly.

    Used by the GD&T harvest path: a geometric tolerance attaches to a feature
    control frame's annotation, not to a display dimension, but the face → surface
    → feature resolution beyond that point is identical.
    """
    return _resolve(_safe_face_from_annotation(ann), view)


def _resolve(face, view) -> Optional[GeometryContext]:
    referenced_model = _safe_referenced_model(view)
    surface_type, nominal_diameter = _safe_surface(face)
    feature_kind, hole_standard, is_internal = _safe_feature(face)

    if (
        not referenced_model
        and face is None
        and surface_type == "unknown"
        and feature_kind == "unknown"
    ):
        return None

    # A bare surface classification is still useful signal when the owning
    # feature couldn't be identified: reflect it in feature_kind.
    if feature_kind == "unknown" and surface_type == "cylinder":
        feature_kind = "cylindrical_face"
    elif feature_kind == "unknown" and surface_type == "plane":
        feature_kind = "planar_face"

    return GeometryContext(
        feature_kind=feature_kind,
        surface_type=surface_type,
        nominal_diameter=nominal_diameter,
        is_internal=is_internal,
        hole_standard=hole_standard,
        referenced_model=referenced_model,
    )


# --- view → referenced part -------------------------------------------------

def _safe_referenced_model(view) -> str:
    """Filename of the part the view references, or "" (parts-only)."""
    try:
        doc = getattr(view, "ReferencedDocument", None)
        if doc is not None:
            path = call(doc, "GetPathName")
            if path:
                return _basename(str(path))
    except Exception:
        pass
    try:
        name = call(view, "GetReferencedModelName")
        return _basename(str(name)) if name else ""
    except Exception:
        return ""


# --- dimension → attached face ----------------------------------------------

def _safe_attached_face(disp_dim):
    """The ``IFace2`` the dimension measures, or None.

    disp_dim → ``IAnnotation`` → attached entities → a face (directly, or the
    adjacent face of an attached edge). The precise chain by which a *drawing*
    dimension reaches *model* topology is a first-run unknown.
    """
    return _safe_face_from_annotation(_safe_annotation(disp_dim))


def _safe_annotation(disp_dim):
    try:
        return call(disp_dim, "GetAnnotation")
    except Exception:
        return None


def _safe_face_from_annotation(ann):
    """A face from an annotation's attached entities (directly, or via an edge)."""
    if ann is None:
        return None
    try:
        entities = call(ann, "GetAttachedEntities3")
    except Exception:
        return None
    return _first_face(_as_list(entities))


def _first_face(entities: List):
    for ent in entities:
        if ent is None:
            continue
        try:
            etype = int(call(ent, "GetType"))
        except Exception:
            continue
        if etype == _SW_SEL_FACES:
            return ent
        if etype == _SW_SEL_EDGES:
            face = _adjacent_face(ent)
            if face is not None:
                return face
    return None


def _adjacent_face(edge):
    try:
        faces = call(edge, "GetTwoAdjacentFaces2")
    except Exception:
        return None
    return next((f for f in _as_list(faces) if f is not None), None)


# --- face → surface ---------------------------------------------------------

def _safe_surface(face) -> Tuple[str, Optional[float]]:
    if face is None:
        return "unknown", None
    try:
        surface = call(face, "GetSurface")
    except Exception:
        return "unknown", None
    if surface is None:
        return "unknown", None
    return _classify_surface(surface)


def _classify_surface(surface) -> Tuple[str, Optional[float]]:
    """Map an ``ISurface`` to (surface_type, nominal_diameter_in_metres)."""
    if _surface_is(surface, "IsCylinder"):
        return "cylinder", _cylinder_diameter(surface)
    if _surface_is(surface, "IsPlane"):
        return "plane", None
    if _surface_is(surface, "IsCone"):
        return "cone", None
    return "other", None


def _surface_is(surface, member: str) -> bool:
    try:
        return bool(call(surface, member))
    except Exception:
        return False


def _cylinder_diameter(surface) -> Optional[float]:
    """Diameter in metres from ``ISurface.CylinderParams`` ([..., radius] at [6])."""
    try:
        params = call(surface, "CylinderParams")
        radius = float(params[6])
    except Exception:
        return None
    return radius * 2.0 if radius > 0 else None


# --- face → owning feature --------------------------------------------------

def _safe_feature(face) -> Tuple[str, str, Optional[bool]]:
    """(feature_kind, hole_standard, is_internal) from the face's owning feature."""
    if face is None:
        return "unknown", "", None
    try:
        feat = call(face, "GetFeature")
        type_name = str(call(feat, "GetTypeName2"))
    except Exception:
        return "unknown", "", None
    kind, is_internal = _classify_feature_typename(type_name)
    hole_standard = _safe_hole_standard(feat) if kind.startswith("hole") else ""
    return kind, hole_standard, is_internal


def _classify_feature_typename(type_name: str) -> Tuple[str, Optional[bool]]:
    """Map ``IFeature.GetTypeName2`` to (feature_kind, is_internal). Pure.

    Type names are stable, non-localized SolidWorks identifiers. Cuts/holes
    remove material (internal); bosses add it (external); fillets/chamfers are
    edge treatments with no inside/outside sense.
    """
    t = (type_name or "").lower()
    if "hole" in t and ("wzd" in t or "wizard" in t):
        # Hole Wizard: clearance vs tapped/counterbore needs the definition,
        # which is refined separately; default to a clearance hole (internal).
        return "hole_clearance", True
    if "cut" in t:
        return "unknown", True
    if "boss" in t:
        return "boss", False
    if "fillet" in t:
        return "fillet", None
    if "chamfer" in t:
        return "chamfer", None
    return "unknown", None


def _safe_hole_standard(feat) -> str:
    """Hole Wizard standard (e.g. "M6"), read-only and best-effort; "" if unavailable.

    Read WITHOUT ``AccessSelections`` (which mutates selection state on a doc we
    may be about to save), so a build that gates these members behind
    AccessSelections degrades to "". The ``IWizardHoleFeatureData2`` member names
    are a first-run unknown — confirm on Windows.
    """
    try:
        defn = call(feat, "GetDefinition")
    except Exception:
        return ""
    for attr in ("Standard", "FastenerType", "Size"):
        try:
            val = getattr(defn, attr)
        except Exception:
            continue
        if val:
            return str(val)
    return ""


# --- helpers ----------------------------------------------------------------

def _as_list(entities) -> List:
    """Normalise a COM return (tuple / single object / None) to a list."""
    if entities is None:
        return []
    if isinstance(entities, (tuple, list)):
        return list(entities)
    return [entities]


def _basename(path: str) -> str:
    """Filename from a path, handling both Windows and POSIX separators."""
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
