"""Unit tests for harvest.py pure helpers — COM-free, macOS-runnable.

harvest.py imports the sw_tolerance package, whose COM imports are lazy (inside
functions), so importing it for these tests works without pywin32. Only the
pure parts (_validate_paths, _list_drawings, _build_record) are exercised here;
the COM orchestration is verified end-to-end on Windows.

Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import harvest
from harvest import (
    EXIT_OK,
    EXIT_PARTIAL_FAILURE,
    EXIT_VALIDATION_ERROR,
    _build_record,
    _harvest_all,
    _list_drawings,
    _validate_paths,
)
from sw_tolerance.models import (
    SW_ANGULAR_DIM,
    SW_DIAMETER_DIM,
    SW_LINEAR_DIM,
    SW_TOL_BILAT,
    SW_TOL_NONE,
    Feature,
    Tolerance,
)


class ValidatePathsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_valid_paths_pass(self):
        out = os.path.join(self.tmp, "dataset.jsonl")
        self.assertEqual(_validate_paths(self.tmp, out), EXIT_OK)

    def test_missing_input_dir_is_rejected(self):
        missing = os.path.join(self.tmp, "nope")
        out = os.path.join(self.tmp, "dataset.jsonl")
        self.assertEqual(_validate_paths(missing, out), EXIT_VALIDATION_ERROR)

    def test_input_file_instead_of_dir_is_rejected(self):
        f = os.path.join(self.tmp, "in.slddrw")
        with open(f, "w") as fh:
            fh.write("")
        out = os.path.join(self.tmp, "dataset.jsonl")
        self.assertEqual(_validate_paths(f, out), EXIT_VALIDATION_ERROR)

    def test_existing_output_is_rejected(self):
        out = os.path.join(self.tmp, "dataset.jsonl")
        with open(out, "w") as fh:
            fh.write("")
        self.assertEqual(_validate_paths(self.tmp, out), EXIT_VALIDATION_ERROR)

    def test_missing_output_dir_is_rejected(self):
        out = os.path.join(self.tmp, "missing", "dataset.jsonl")
        self.assertEqual(_validate_paths(self.tmp, out), EXIT_VALIDATION_ERROR)


class ListDrawingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _touch(self, name):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write("")
        return path

    def test_finds_slddrw_case_insensitively_and_sorts(self):
        self._touch("b.SLDDRW")
        self._touch("a.slddrw")
        self._touch("notes.txt")
        self._touch("part.sldprt")
        found = _list_drawings(self.tmp)
        self.assertEqual([os.path.basename(p) for p in found], ["a.slddrw", "b.SLDDRW"])

    def test_empty_dir_returns_empty(self):
        self.assertEqual(_list_drawings(self.tmp), [])


class BuildRecordTests(unittest.TestCase):
    def _feature(self, **overrides):
        defaults = dict(
            value=0.05,
            dim_type=2,
            current_tolerance_type=SW_TOL_BILAT,  # toleranced — the harvest case
            view_name="View1",
            sheet_name="Sheet1",
            is_hole_callout=False,
        )
        defaults.update(overrides)
        return Feature(**defaults)

    def test_label_is_preserved(self):
        label = Tolerance(tol_type=SW_TOL_BILAT, plus_value=0.0005, minus_value=0.0005)
        rec = _build_record(self._feature(), label, "/x/a.slddrw")
        self.assertEqual(rec["label"]["tol_type"], SW_TOL_BILAT)
        self.assertAlmostEqual(rec["label"]["plus_value"], 0.0005)
        self.assertEqual(rec["source_file"], "/x/a.slddrw")

    def test_feature_tolerance_type_is_normalised_to_none(self):
        # The leak guard: a harvested dim is toleranced, but the recorded
        # feature must look like an untoleranced dim (what the model sees at
        # inference). The answer lives only in the label.
        label = Tolerance(tol_type=SW_TOL_BILAT, plus_value=0.0005, minus_value=0.0005)
        rec = _build_record(self._feature(current_tolerance_type=SW_TOL_BILAT), label, "x")
        self.assertEqual(rec["feature"]["current_tolerance_type"], SW_TOL_NONE)

    def test_other_feature_fields_survive(self):
        label = Tolerance(tol_type=SW_TOL_BILAT, plus_value=0.0005, minus_value=0.0005)
        rec = _build_record(self._feature(value=0.123, view_name="VX"), label, "x")
        self.assertAlmostEqual(rec["feature"]["value"], 0.123)
        self.assertEqual(rec["feature"]["view_name"], "VX")


def _feat(dim_type, marker):
    """A fake extracted feature; `marker` is the stand-in tol_obj handle."""
    feat = Feature(
        value=0.05,
        dim_type=dim_type,
        current_tolerance_type=SW_TOL_BILAT,
        view_name="V1",
        sheet_name="S1",
    )
    # iter_dimensions yields (disp_dim, idim, tol_obj, feature)
    return (object(), object(), marker, feat)


_BILAT = Tolerance(tol_type=SW_TOL_BILAT, plus_value=0.0005, minus_value=0.0005)


def _fake_read_tol(marker):
    """Stand-in for read_existing_tolerance keyed on the tol_obj marker."""
    return _BILAT if marker == "has_tol" else None


class HarvestOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _read_dataset(self, path):
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]

    def test_filters_to_toleranced_dims_across_families(self):
        # One drawing mixing every case the filter must handle:
        #   - linear toleranced       → kept (length family)
        #   - diameter toleranced     → kept (length family)
        #   - angular toleranced      → kept (angular family)
        #   - linear untoleranced     → dropped (label None)
        #   - chamfer toleranced (10) → dropped (no family)
        #   - unknown toleranced      → dropped (no family)
        dims = [
            _feat(SW_LINEAR_DIM, "has_tol"),
            _feat(SW_DIAMETER_DIM, "has_tol"),
            _feat(SW_ANGULAR_DIM, "has_tol"),
            _feat(SW_LINEAR_DIM, "no_tol"),
            _feat(10, "has_tol"),
            _feat(999, "has_tol"),
        ]

        @contextmanager
        def fake_open(sw, path):
            yield "model"

        out = os.path.join(self.tmp, "dataset.jsonl")
        with mock.patch.object(harvest, "open_drawing", fake_open), \
             mock.patch.object(harvest, "iter_dimensions", lambda m: iter(dims)), \
             mock.patch.object(harvest, "read_existing_tolerance", _fake_read_tol):
            rc = _harvest_all(None, ["/x/a.slddrw"], "/x", Path(out))

        self.assertEqual(rc, EXIT_OK)
        lines = self._read_dataset(out)
        # header + exactly three kept records (linear, diameter, angular)
        self.assertEqual(lines[0]["kind"], "training_dataset")
        records = lines[1:]
        self.assertEqual(len(records), 3)
        kept_types = sorted(r["feature"]["dim_type"] for r in records)
        self.assertEqual(kept_types, sorted([SW_LINEAR_DIM, SW_DIAMETER_DIM, SW_ANGULAR_DIM]))
        for r in records:
            self.assertEqual(r["source_file"], "/x/a.slddrw")
            self.assertEqual(r["feature"]["current_tolerance_type"], SW_TOL_NONE)
            self.assertAlmostEqual(r["label"]["plus_value"], 0.0005)

    def test_failed_file_is_isolated_and_reported(self):
        # First drawing raises on open; second yields one good record. The batch
        # must still produce a dataset and exit EXIT_PARTIAL_FAILURE.
        @contextmanager
        def fake_open(sw, path):
            if path.endswith("bad.slddrw"):
                raise RuntimeError("OpenDoc6 failed")
            yield "model"

        out = os.path.join(self.tmp, "dataset.jsonl")
        with mock.patch.object(harvest, "open_drawing", fake_open), \
             mock.patch.object(harvest, "iter_dimensions",
                               lambda m: iter([_feat(SW_LINEAR_DIM, "has_tol")])), \
             mock.patch.object(harvest, "read_existing_tolerance", _fake_read_tol):
            rc = _harvest_all(
                None,
                ["/x/bad.slddrw", "/x/good.slddrw"],
                "/x",
                Path(out),
            )

        self.assertEqual(rc, EXIT_PARTIAL_FAILURE)
        records = self._read_dataset(out)[1:]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_file"], "/x/good.slddrw")


if __name__ == "__main__":
    unittest.main()
