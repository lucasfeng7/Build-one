"""CLI: harvest (feature, tolerance) training pairs from toleranced drawings.

Read-only counterpart to tolerance.py. Where tolerance.py *writes* a constant
policy onto untoleranced dims, harvest.py *reads* the tolerances engineers have
already applied and records them as labeled training data for a future ML
tolerance model. It never modifies, rebuilds, or saves a drawing.

Usage: python3 harvest.py <input_dir> <output.jsonl>

It walks every .slddrw in <input_dir>, and for each linear dimension that
already carries a tolerance, writes one (feature, label) record to the output
dataset. The feature's current_tolerance_type is normalised to swTolNONE so the
record matches what the model will see at inference time (untoleranced dims);
the real tolerance lives in `label`, never in the feature.

Exit codes:
  0 — ran; dataset written (zero records is a valid empty dataset)
  1 — unrecoverable (SolidWorks unavailable)
  2 — ran with per-file failures (partial dataset still written)
  3 — validation error (bad input dir, output exists, missing output dir)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Optional

from sw_tolerance import __version__
from sw_tolerance.extract import iter_dimensions, read_existing_tolerance
from sw_tolerance.models import (
    ANGULAR_DIM_TYPES,
    LENGTH_DIM_TYPES,
    SW_TOL_NONE,
    Feature,
    Tolerance,
)

# Collect training data for every dimension family decide.tolerance_for can act
# on, so the dataset matches what the apply path now handles.
HARVEST_DIM_TYPES = LENGTH_DIM_TYPES | ANGULAR_DIM_TYPES
from sw_tolerance.sw_client import (
    SolidWorksUnavailable,
    connect,
    open_drawing,
)

EXIT_OK = 0
EXIT_UNRECOVERABLE = 1
EXIT_PARTIAL_FAILURE = 2
EXIT_VALIDATION_ERROR = 3

# v2: Feature gained richer fields (is_reference, text_prefix, text_suffix),
# so each record's `feature` shape changed.
SCHEMA_VERSION = 2


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Harvest (feature, tolerance) training pairs from toleranced drawings.",
    )
    parser.add_argument("input_dir", help="Directory containing .slddrw files")
    parser.add_argument("output", help="Path to write the .jsonl dataset")
    args = parser.parse_args(argv)

    rc = _validate_paths(args.input_dir, args.output)
    if rc != EXIT_OK:
        return rc

    input_abs = os.path.abspath(args.input_dir)
    output_abs = os.path.abspath(args.output)
    drawings = _list_drawings(input_abs)
    if not drawings:
        print(f"WARN: no .slddrw files found in {input_abs}", file=sys.stderr)

    try:
        sw = connect()
    except SolidWorksUnavailable as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_UNRECOVERABLE

    return _harvest_all(sw, drawings, input_abs, Path(output_abs))


def _validate_paths(input_dir: str, output_path: str) -> int:
    if not os.path.isdir(input_dir):
        print(f"ERROR: input directory does not exist: {input_dir}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    if os.path.exists(output_path):
        print(f"ERROR: output already exists: {output_path}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    if not os.path.isdir(out_dir):
        print(f"ERROR: output directory does not exist: {out_dir}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    return EXIT_OK


def _list_drawings(input_dir: str) -> list[str]:
    """Return sorted absolute paths of .slddrw files directly in input_dir."""
    return sorted(
        str(p)
        for p in Path(input_dir).iterdir()
        if p.is_file() and p.suffix.lower() == ".slddrw"
    )


def _harvest_all(sw, drawings: list[str], input_dir: str, dataset_path: Path) -> int:
    records = failed_files = 0

    with dataset_path.open("w", encoding="utf-8") as fh:
        _write_header(fh, input_dir)
        for path in drawings:
            try:
                records += _harvest_drawing(sw, path, fh)
            except Exception as e:
                # Per-file isolation: a bad drawing (OpenFailed or any COM
                # error) is logged and skipped so the batch still produces a
                # dataset from the rest.
                failed_files += 1
                print(f"WARN: skipping {path}: {e}", file=sys.stderr)

    print(
        f"files={len(drawings)} failed_files={failed_files} records={records}",
        file=sys.stderr,
    )
    print(f"dataset: {dataset_path}", file=sys.stderr)
    return EXIT_PARTIAL_FAILURE if failed_files > 0 else EXIT_OK


def _harvest_drawing(sw, path: str, fh) -> int:
    """Harvest one drawing, writing labeled records. Returns the record count."""
    written = 0
    with open_drawing(sw, path) as model:
        for _disp_dim, _idim, tol_obj, feat in iter_dimensions(model):
            if feat.dim_type not in HARVEST_DIM_TYPES:
                continue
            label = read_existing_tolerance(tol_obj)
            if label is None:
                continue
            fh.write(json.dumps(_build_record(feat, label, path)) + "\n")
            written += 1
    return written


def _build_record(feat: Feature, label: Tolerance, source_file: str) -> dict:
    """Pure: build one dataset record.

    The feature is recorded as the model will see it at inference time —
    current_tolerance_type normalised to swTolNONE — so harvesting a toleranced
    dim does not leak the answer into the inputs. The real tolerance is the
    label, kept separate from the feature.
    """
    inference_feat = replace(feat, current_tolerance_type=SW_TOL_NONE)
    return {
        "source_file": source_file,
        "feature": asdict(inference_feat),
        "label": asdict(label),
    }


def _write_header(fh, input_dir: str) -> None:
    fh.write(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "kind": "training_dataset",
        "tool_version": __version__,
        "input_dir": input_dir,
    }) + "\n")


if __name__ == "__main__":
    sys.exit(main())
