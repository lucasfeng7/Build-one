"""CLI: apply bilateral ±0.5 mm tolerances to a SolidWorks drawing.

Usage: python3 tolerance.py <input.slddrw> <output.slddrw>

Exit codes:
  0 — all dimensions handled (any combination of applied + skipped)
  1 — unrecoverable (SolidWorks unavailable, OpenDoc6 failed, SaveAs3 failed)
  2 — ran with per-dim apply failures (partial result still saved)
  3 — validation error (bad paths, output exists, input == output)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from sw_tolerance import __version__
from sw_tolerance.apply import SaveFailed, rebuild_and_save, write_tolerance
from sw_tolerance.decide import tolerance_for
from sw_tolerance.extract import get_active_config_name, iter_dimensions
from sw_tolerance.models import Feature, Tolerance
from sw_tolerance.sw_client import (
    OpenFailed,
    SolidWorksUnavailable,
    connect,
    open_drawing,
)

EXIT_OK = 0
EXIT_UNRECOVERABLE = 1
EXIT_PARTIAL_FAILURE = 2
EXIT_VALIDATION_ERROR = 3


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply bilateral ±0.5 mm tolerances to all untoleranced linear dims.",
    )
    parser.add_argument("input", help="Path to input .slddrw")
    parser.add_argument("output", help="Path to write output .slddrw")
    args = parser.parse_args(argv)

    rc = _validate_paths(args.input, args.output)
    if rc != EXIT_OK:
        return rc

    input_abs = os.path.abspath(args.input)
    output_abs = os.path.abspath(args.output)
    report_path = Path(output_abs + ".report.jsonl")

    try:
        sw = connect()
    except SolidWorksUnavailable as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_UNRECOVERABLE

    try:
        with open_drawing(sw, input_abs) as model:
            return _process(model, output_abs, input_abs, report_path)
    except OpenFailed as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    except SaveFailed as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_UNRECOVERABLE


def _validate_paths(input_path: str, output_path: str) -> int:
    if not os.path.isfile(input_path):
        print(f"ERROR: input does not exist: {input_path}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    if not input_path.lower().endswith(".slddrw"):
        print(f"ERROR: input must end in .slddrw: {input_path}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    if os.path.exists(output_path):
        print(f"ERROR: output already exists: {output_path}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        print("ERROR: input and output paths must differ", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    return EXIT_OK


def _process(model, out_path: str, input_path: str, report_path: Path) -> int:
    applied = skipped = failed = 0
    config_name = get_active_config_name(model)

    with report_path.open("w", encoding="utf-8") as fh:
        _write_header(fh, config_name, input_path)

        for _disp_dim, _idim, tol_obj, feat in iter_dimensions(model):
            tolerance = tolerance_for(feat)
            if tolerance is None:
                skipped += 1
                _write_record(fh, feat, action="skipped", tolerance=None, error=None)
                continue
            try:
                write_tolerance(tol_obj, tolerance)
            except Exception as e:
                failed += 1
                _write_record(fh, feat, action="failed", tolerance=tolerance, error=str(e))
                continue
            applied += 1
            _write_record(fh, feat, action="applied", tolerance=tolerance, error=None)

    rebuild_and_save(model, out_path)

    print(
        f"applied={applied} skipped={skipped} failed={failed}",
        file=sys.stderr,
    )
    print(f"report: {report_path}", file=sys.stderr)
    return EXIT_PARTIAL_FAILURE if failed > 0 else EXIT_OK


def _write_header(fh, config_name: str, input_path: str) -> None:
    fh.write(json.dumps({
        "schema_version": 1,
        "active_config": config_name,
        "input": input_path,
        "tool_version": __version__,
    }) + "\n")


def _write_record(
    fh,
    feat: Feature,
    *,
    action: str,
    tolerance: Optional[Tolerance],
    error: Optional[str],
) -> None:
    fh.write(json.dumps({
        "feature": asdict(feat),
        "action": action,
        "tolerance": asdict(tolerance) if tolerance else None,
        "error": error,
    }) + "\n")


if __name__ == "__main__":
    sys.exit(main())
