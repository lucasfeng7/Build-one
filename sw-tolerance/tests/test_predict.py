"""Unit tests for predict.predict_tolerance — COM-free, network-free, macOS-runnable.

The DeepSeek client (OpenAI-compatible) is replaced with a fake (no SDK, no
network). Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import io
import json
import math
import unittest
from contextlib import redirect_stderr

from sw_tolerance import predict
from sw_tolerance.models import (
    SW_ANGULAR_DIM,
    SW_LINEAR_DIM,
    SW_TOL_BILAT,
    Feature,
    GeometryContext,
)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """Records create() kwargs and returns a programmed JSON response.

    `payload` is either a dict (serialised to JSON), a raw string (returned as
    the message content verbatim — used to exercise the unparseable-reply
    fallback), or a callable invoked per call (lets a test raise).
    """

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self._payload() if callable(self._payload) else self._payload
        content = payload if isinstance(payload, str) else json.dumps(payload)
        return _FakeResp(content)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


def _install(payload=None, *, raises=None):
    """Install a fake client and return its completions object for assertions."""
    if raises is not None:
        payload = lambda: (_ for _ in ()).throw(raises)  # noqa: E731
    completions = _FakeCompletions(payload)
    predict._client_box[:] = [_FakeClient(completions)]
    return completions


def _feature(**overrides) -> Feature:
    defaults = dict(
        value=0.01,
        dim_type=SW_LINEAR_DIM,
        current_tolerance_type=0,
        view_name="View1",
        sheet_name="Sheet1",
        is_hole_callout=False,
    )
    defaults.update(overrides)
    return Feature(**defaults)


class PredictToleranceTests(unittest.TestCase):
    def setUp(self):
        predict._cache.clear()
        predict._client_box.clear()

    def tearDown(self):
        predict._cache.clear()
        predict._client_box.clear()

    def test_length_prediction_converts_mm_to_metres(self):
        _install({"apply": True, "plus": 0.1, "minus": 0.2, "reason": "fit"})
        decision = predict.predict_tolerance(_feature(), "length")
        tol = decision.dimensional
        self.assertEqual(tol.tol_type, SW_TOL_BILAT)
        self.assertAlmostEqual(tol.plus_value, 0.0001)   # 0.1 mm → 0.0001 m
        self.assertAlmostEqual(tol.minus_value, 0.0002)  # 0.2 mm → 0.0002 m
        self.assertEqual(decision.rationale, "fit")
        self.assertEqual(decision.geometric, ())  # none proposed

    def test_angular_prediction_converts_degrees_to_radians(self):
        _install({"apply": True, "plus": 2.0, "minus": 1.0})
        tol = predict.predict_tolerance(_feature(dim_type=SW_ANGULAR_DIM), "angular").dimensional
        self.assertAlmostEqual(tol.plus_value, math.radians(2.0))
        self.assertAlmostEqual(tol.minus_value, math.radians(1.0))

    def test_negative_deviations_are_abs_normalised(self):
        _install({"apply": True, "plus": -0.1, "minus": -0.3})
        tol = predict.predict_tolerance(_feature(), "length").dimensional
        self.assertAlmostEqual(tol.plus_value, 0.0001)
        self.assertAlmostEqual(tol.minus_value, 0.0003)

    def test_apply_false_returns_none_with_reason(self):
        _install({"apply": False, "reason": "reference dim"})
        decision = predict.predict_tolerance(_feature(), "length")
        self.assertIsNone(decision.dimensional)
        self.assertEqual(decision.rationale, "reference dim")

    def test_prompt_carries_human_units_and_type(self):
        completions = _install({"apply": True, "plus": 0.1, "minus": 0.1})
        predict.predict_tolerance(_feature(value=0.025, dim_type=SW_LINEAR_DIM), "length")
        kwargs = completions.calls[0]
        roles = {m["role"]: m["content"] for m in kwargs["messages"]}
        self.assertIn("25 mm", roles["user"])            # 0.025 m → 25 mm, in human units
        self.assertIn(str(SW_LINEAR_DIM), roles["user"])  # the dim type code
        self.assertIn("length", roles["user"])
        # The system prompt is sent, and JSON output is requested.
        self.assertEqual(roles["system"], predict._SYSTEM_PROMPT)
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        # No annotation text on this feature → no annotation line in the prompt.
        self.assertNotIn("Annotation text", roles["user"])

    def test_annotation_text_surfaced_when_present(self):
        completions = _install({"apply": True, "plus": 0.1, "minus": 0.1})
        predict.predict_tolerance(
            _feature(text_prefix="⌀", text_suffix="MAX"), "length"
        )
        user_text = completions.calls[0]["messages"][1]["content"]
        self.assertIn("Annotation text:", user_text)
        self.assertIn("⌀", user_text)
        self.assertIn("MAX", user_text)

    def test_geometric_proposals_parsed(self):
        _install({
            "apply": True, "plus": 0.05, "minus": 0.05,
            "geometric": [{
                "symbol": "position", "zone": 0.2, "diameter_zone": True,
                "material_condition": "MMC",
                "datums": [{"letter": "A"}, {"letter": "B", "modifier": "MMC"}],
            }],
            "reason": "locating hole",
        })
        decision = predict.predict_tolerance(_feature(), "length")
        self.assertEqual(len(decision.geometric), 1)
        g = decision.geometric[0]
        self.assertEqual(g.symbol, "position")
        self.assertAlmostEqual(g.zone_value, 0.0002)  # 0.2 mm → m
        self.assertTrue(g.diameter_zone)
        self.assertEqual(g.material_condition, "MMC")
        self.assertEqual([d.letter for d in g.datum_refs], ["A", "B"])
        self.assertEqual(g.datum_refs[0].modifier, "RFS")  # defaulted
        self.assertEqual(g.datum_refs[1].modifier, "MMC")

    def test_geometric_only_without_size_tolerance(self):
        _install({"apply": False,
                  "geometric": [{"symbol": "flatness", "zone": 0.1}],
                  "reason": "datum face"})
        decision = predict.predict_tolerance(_feature(), "length")
        self.assertIsNone(decision.dimensional)
        self.assertEqual([g.symbol for g in decision.geometric], ["flatness"])

    def test_invalid_geometric_entries_are_dropped(self):
        _install({"apply": True, "plus": 0.05, "minus": 0.05, "geometric": [
            {"symbol": "bogus", "zone": 0.1},                 # unknown characteristic
            {"symbol": "flatness"},                            # missing zone
            {"symbol": "parallelism", "zone": 0.05, "datums": [{"letter": "A"}]},
        ]})
        decision = predict.predict_tolerance(_feature(), "length")
        self.assertEqual([g.symbol for g in decision.geometric], ["parallelism"])
        self.assertEqual(decision.geometric[0].material_condition, "RFS")  # defaulted

    def test_geometry_context_surfaced_in_prompt(self):
        completions = _install({"apply": True, "plus": 0.1, "minus": 0.1})
        geo = GeometryContext(feature_kind="hole_clearance", surface_type="cylinder",
                              nominal_diameter=0.006, is_internal=True, hole_standard="M6")
        predict.predict_tolerance(_feature(geometry=geo), "length")
        user_text = completions.calls[0]["messages"][1]["content"]
        self.assertIn("Geometry context:", user_text)
        self.assertIn("hole_clearance", user_text)
        self.assertIn("Ø6 mm", user_text)
        self.assertIn("internal", user_text)
        self.assertIn("M6", user_text)

    def test_no_geometry_line_when_absent(self):
        completions = _install({"apply": True, "plus": 0.1, "minus": 0.1})
        predict.predict_tolerance(_feature(), "length")  # geometry=None
        self.assertNotIn("Geometry context", completions.calls[0]["messages"][1]["content"])

    def test_distinct_geometry_not_collapsed(self):
        completions = _install({"apply": True, "plus": 0.1, "minus": 0.1})
        predict.predict_tolerance(_feature(geometry=GeometryContext(feature_kind="boss")), "length")
        predict.predict_tolerance(
            _feature(geometry=GeometryContext(feature_kind="hole_clearance")), "length")
        self.assertEqual(len(completions.calls), 2, "different geometry must not share a cache slot")

    def test_client_error_falls_back_to_constant(self):
        _install(raises=RuntimeError("network down"))
        buf = io.StringIO()
        with redirect_stderr(buf):
            decision = predict.predict_tolerance(_feature(), "length")
        # Falls back to the flat ±0.5 mm constant policy, with no rationale or GD&T.
        self.assertAlmostEqual(decision.dimensional.plus_value, 0.0005)
        self.assertAlmostEqual(decision.dimensional.minus_value, 0.0005)
        self.assertIsNone(decision.rationale)
        self.assertEqual(decision.geometric, ())
        self.assertIn("WARNING", buf.getvalue())

    def test_unparseable_reply_falls_back_to_constant(self):
        _install("this is not json")
        buf = io.StringIO()
        with redirect_stderr(buf):
            tol = predict.predict_tolerance(_feature(), "length").dimensional
        self.assertAlmostEqual(tol.plus_value, 0.0005)
        self.assertIn("WARNING", buf.getvalue())

    def test_identical_features_hit_the_cache(self):
        completions = _install({"apply": True, "plus": 0.1, "minus": 0.1})
        f = _feature(value=0.01)
        predict.predict_tolerance(f, "length")
        predict.predict_tolerance(_feature(value=0.01), "length")  # identical key
        self.assertEqual(len(completions.calls), 1, "second identical dim must be cached")

    def test_distinct_values_are_not_collapsed(self):
        completions = _install({"apply": True, "plus": 0.1, "minus": 0.1})
        predict.predict_tolerance(_feature(value=0.01), "length")
        predict.predict_tolerance(_feature(value=0.02), "length")
        self.assertEqual(len(completions.calls), 2)


if __name__ == "__main__":
    unittest.main()
