"""Unit tests for predict.predict_tolerance — COM-free, network-free, macOS-runnable.

The Gemini client is replaced with a fake (no SDK, no network). Run from the
project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import io
import json
import math
import unittest
from contextlib import redirect_stderr

from sw_tolerance import predict
from sw_tolerance.models import SW_ANGULAR_DIM, SW_LINEAR_DIM, SW_TOL_BILAT, Feature


class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    """Records generate_content() kwargs and returns a programmed JSON response.

    `payload` is either a dict (serialised to JSON), a raw string (returned as
    `resp.text` verbatim — used to exercise the unparseable-reply fallback), or
    a callable invoked per call (lets a test raise).
    """

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        payload = self._payload() if callable(self._payload) else self._payload
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return _FakeResp(text)


class _FakeClient:
    def __init__(self, models):
        self.models = models


def _install(payload=None, *, raises=None):
    """Install a fake client and return its models object for assertions."""
    if raises is not None:
        payload = lambda: (_ for _ in ()).throw(raises)  # noqa: E731
    models = _FakeModels(payload)
    predict._client_box[:] = [_FakeClient(models)]
    return models


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
        tol, rationale = predict.predict_tolerance(_feature(), "length")
        self.assertEqual(tol.tol_type, SW_TOL_BILAT)
        self.assertAlmostEqual(tol.plus_value, 0.0001)   # 0.1 mm → 0.0001 m
        self.assertAlmostEqual(tol.minus_value, 0.0002)  # 0.2 mm → 0.0002 m
        self.assertEqual(rationale, "fit")

    def test_angular_prediction_converts_degrees_to_radians(self):
        _install({"apply": True, "plus": 2.0, "minus": 1.0})
        tol, _ = predict.predict_tolerance(_feature(dim_type=SW_ANGULAR_DIM), "angular")
        self.assertAlmostEqual(tol.plus_value, math.radians(2.0))
        self.assertAlmostEqual(tol.minus_value, math.radians(1.0))

    def test_negative_deviations_are_abs_normalised(self):
        _install({"apply": True, "plus": -0.1, "minus": -0.3})
        tol, _ = predict.predict_tolerance(_feature(), "length")
        self.assertAlmostEqual(tol.plus_value, 0.0001)
        self.assertAlmostEqual(tol.minus_value, 0.0003)

    def test_apply_false_returns_none_with_reason(self):
        _install({"apply": False, "reason": "reference dim"})
        tol, rationale = predict.predict_tolerance(_feature(), "length")
        self.assertIsNone(tol)
        self.assertEqual(rationale, "reference dim")

    def test_prompt_carries_human_units_and_type(self):
        models = _install({"apply": True, "plus": 0.1, "minus": 0.1})
        predict.predict_tolerance(_feature(value=0.025, dim_type=SW_LINEAR_DIM), "length")
        kwargs = models.calls[0]
        user_text = kwargs["contents"]
        self.assertIn("25 mm", user_text)            # 0.025 m → 25 mm, in human units
        self.assertIn(str(SW_LINEAR_DIM), user_text)  # the dim type code
        self.assertIn("length", user_text)
        # The system instruction is sent, and JSON output is requested.
        self.assertEqual(kwargs["config"]["system_instruction"], predict._SYSTEM_PROMPT)
        self.assertEqual(kwargs["config"]["response_mime_type"], "application/json")

    def test_client_error_falls_back_to_constant(self):
        _install(raises=RuntimeError("network down"))
        buf = io.StringIO()
        with redirect_stderr(buf):
            tol, rationale = predict.predict_tolerance(_feature(), "length")
        # Falls back to the flat ±0.5 mm constant policy, with no rationale.
        self.assertAlmostEqual(tol.plus_value, 0.0005)
        self.assertAlmostEqual(tol.minus_value, 0.0005)
        self.assertIsNone(rationale)
        self.assertIn("WARNING", buf.getvalue())

    def test_unparseable_reply_falls_back_to_constant(self):
        _install("this is not json")
        buf = io.StringIO()
        with redirect_stderr(buf):
            tol, _ = predict.predict_tolerance(_feature(), "length")
        self.assertAlmostEqual(tol.plus_value, 0.0005)
        self.assertIn("WARNING", buf.getvalue())

    def test_identical_features_hit_the_cache(self):
        models = _install({"apply": True, "plus": 0.1, "minus": 0.1})
        f = _feature(value=0.01)
        predict.predict_tolerance(f, "length")
        predict.predict_tolerance(_feature(value=0.01), "length")  # identical key
        self.assertEqual(len(models.calls), 1, "second identical dim must be cached")

    def test_distinct_values_are_not_collapsed(self):
        models = _install({"apply": True, "plus": 0.1, "minus": 0.1})
        predict.predict_tolerance(_feature(value=0.01), "length")
        predict.predict_tolerance(_feature(value=0.02), "length")
        self.assertEqual(len(models.calls), 2)


if __name__ == "__main__":
    unittest.main()
