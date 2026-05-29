"""Unit tests for tolerance._validate_paths — COM-free, macOS-runnable.

tolerance.py imports the sw_tolerance package, whose COM imports are lazy
(inside functions), so importing it for these tests works without pywin32.

Run from the project root with:
    python3 -m unittest discover tests
"""
from __future__ import annotations

import os
import tempfile
import unittest

from tolerance import EXIT_OK, EXIT_VALIDATION_ERROR, _validate_paths


class ValidatePathsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.input = os.path.join(self.tmp, "in.slddrw")
        with open(self.input, "w") as fh:
            fh.write("")  # contents irrelevant; only existence is checked

    def tearDown(self):
        self._tmp.cleanup()

    def test_valid_paths_pass(self):
        out = os.path.join(self.tmp, "out.slddrw")
        self.assertEqual(_validate_paths(self.input, out), EXIT_OK)

    def test_missing_input_is_rejected(self):
        missing = os.path.join(self.tmp, "nope.slddrw")
        out = os.path.join(self.tmp, "out.slddrw")
        self.assertEqual(_validate_paths(missing, out), EXIT_VALIDATION_ERROR)

    def test_non_slddrw_input_is_rejected(self):
        other = os.path.join(self.tmp, "in.txt")
        with open(other, "w") as fh:
            fh.write("")
        out = os.path.join(self.tmp, "out.slddrw")
        self.assertEqual(_validate_paths(other, out), EXIT_VALIDATION_ERROR)

    def test_existing_output_is_rejected(self):
        out = os.path.join(self.tmp, "out.slddrw")
        with open(out, "w") as fh:
            fh.write("")
        self.assertEqual(_validate_paths(self.input, out), EXIT_VALIDATION_ERROR)

    def test_missing_output_directory_is_rejected(self):
        # The fail-fast check: parent dir of the output does not exist.
        out = os.path.join(self.tmp, "does_not_exist", "out.slddrw")
        self.assertEqual(_validate_paths(self.input, out), EXIT_VALIDATION_ERROR)

    def test_bare_filename_output_uses_cwd(self):
        # No directory component → treated as cwd, which exists.
        self.assertEqual(_validate_paths(self.input, "out.slddrw"), EXIT_OK)

    def test_input_equals_output_is_rejected(self):
        self.assertEqual(_validate_paths(self.input, self.input), EXIT_VALIDATION_ERROR)


if __name__ == "__main__":
    unittest.main()
