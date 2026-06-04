"""Unit tests for extract's richer display-attribute helpers — COM-free.

These exercise the *guarding* logic (each read degrades to a safe default on
failure) and the value mapping, using fake display-dimension objects. The actual
COM members are verified on Windows. Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import unittest

from sw_tolerance.extract import _display_attrs, _safe_is_reference, _safe_text
from sw_tolerance.models import SW_DIM_TEXT_PREFIX, SW_DIM_TEXT_SUFFIX


class FakeDim:
    """Stand-in display dimension with controllable IsReference / GetText."""

    def __init__(self, *, is_reference=False, texts=None,
                 raise_ref=False, raise_text=False):
        self._is_reference = is_reference
        self._texts = texts or {}
        self._raise_ref = raise_ref
        self._raise_text = raise_text

    @property
    def IsReference(self):
        if self._raise_ref:
            raise RuntimeError("IsReference not available")
        return self._is_reference

    def GetText(self, part):
        if self._raise_text:
            raise RuntimeError("GetText not available")
        return self._texts.get(part, "")


class SafeIsReferenceTests(unittest.TestCase):
    def test_true_and_false_pass_through(self):
        self.assertTrue(_safe_is_reference(FakeDim(is_reference=True)))
        self.assertFalse(_safe_is_reference(FakeDim(is_reference=False)))

    def test_defaults_false_when_property_raises(self):
        self.assertFalse(_safe_is_reference(FakeDim(raise_ref=True)))

    def test_defaults_false_when_member_absent(self):
        # A COM object without IsReference at all → AttributeError → False.
        self.assertFalse(_safe_is_reference(object()))


class SafeTextTests(unittest.TestCase):
    def test_returns_prefix_and_suffix_text(self):
        dim = FakeDim(texts={SW_DIM_TEXT_PREFIX: "⌀", SW_DIM_TEXT_SUFFIX: "TYP"})
        self.assertEqual(_safe_text(dim, SW_DIM_TEXT_PREFIX), "⌀")
        self.assertEqual(_safe_text(dim, SW_DIM_TEXT_SUFFIX), "TYP")

    def test_empty_string_when_no_text(self):
        self.assertEqual(_safe_text(FakeDim(), SW_DIM_TEXT_PREFIX), "")

    def test_defaults_empty_when_gettext_raises(self):
        self.assertEqual(_safe_text(FakeDim(raise_text=True), SW_DIM_TEXT_PREFIX), "")

    def test_none_coerced_to_empty(self):
        dim = FakeDim(texts={SW_DIM_TEXT_PREFIX: None})
        self.assertEqual(_safe_text(dim, SW_DIM_TEXT_PREFIX), "")


class DisplayAttrsTests(unittest.TestCase):
    def test_combines_all_three(self):
        dim = FakeDim(
            is_reference=True,
            texts={SW_DIM_TEXT_PREFIX: "M6", SW_DIM_TEXT_SUFFIX: "MAX"},
        )
        self.assertEqual(_display_attrs(dim), (True, "M6", "MAX"))

    def test_all_defaults_when_everything_unavailable(self):
        # A bare object exposes none of the members → every read degrades.
        self.assertEqual(_display_attrs(object()), (False, "", ""))


if __name__ == "__main__":
    unittest.main()
