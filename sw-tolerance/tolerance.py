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
from sw_tolerance.decide import decide_with_rationale, set_predictor
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
        description=(
            "Apply tolerances to all untoleranced dims. Uses a DeepSeek-backed "
            "policy when DEEPSEEK_API_KEY is set; otherwise (or with --no-llm) "
            "applies the flat ±0.5 mm / ±1° constant policy."
        ),
    )
    parser.add_argument("input", help="Path to input .slddrw")
    parser.add_argument("output", help="Path to write output .slddrw")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Force the constant policy even if a DeepSeek API key is set.",
    )
    args = parser.parse_args(argv)

    rc = _validate_paths(args.input, args.output)
    if rc != EXIT_OK:
        return rc

    policy = _select_policy(use_llm=not args.no_llm)

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
            return _process(model, output_abs, input_abs, report_path, policy)
    except (OpenFailed, SaveFailed) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_UNRECOVERABLE


def _select_policy(*, use_llm: bool) -> str:
    """Install the active predictor and return a policy label for the report.

    The DeepSeek brain is used only when explicitly enabled *and* a key is
    present; otherwise the constant policy (decide's default) stays in force.
    Importing predict is deferred to here so the constant path never needs the
    openai SDK installed.
    """
    from sw_tolerance import predict

    has_key = any(os.environ.get(var) for var in predict.API_KEY_ENV_VARS)
    if use_llm and has_key:
        set_predictor(predict.predict_tolerance)
        model = os.environ.get("SW_TOLERANCE_MODEL", predict.DEFAULT_MODEL)
        return f"llm:{model}"
    return "constant"


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
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    if not os.path.isdir(out_dir):
        # Fail here rather than after a full extract/apply/rebuild that SaveAs
        # would only reject at the very end.
        print(f"ERROR: output directory does not exist: {out_dir}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        print("ERROR: input and output paths must differ", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    return EXIT_OK


def _process(model, out_path: str, input_path: str, report_path: Path, policy: str) -> int:
    applied = skipped = failed = 0
    config_name = get_active_config_name(model)

    with report_path.open("w", encoding="utf-8") as fh:
        _write_header(fh, config_name, input_path, policy)

        for _disp_dim, _idim, tol_obj, feat in iter_dimensions(model):
            tolerance, rationale = decide_with_rationale(feat)
            if tolerance is None:
                skipped += 1
                _write_record(fh, feat, action="skipped", tolerance=None,
                              error=None, rationale=rationale)
                continue
            try:
                write_tolerance(tol_obj, tolerance)
            except Exception as e:
                failed += 1
                _write_record(fh, feat, action="failed", tolerance=tolerance,
                              error=str(e), rationale=rationale)
                continue
            applied += 1
            _write_record(fh, feat, action="applied", tolerance=tolerance,
                          error=None, rationale=rationale)

    rebuild_and_save(model, out_path)

    print(
        f"policy={policy} applied={applied} skipped={skipped} failed={failed}",
        file=sys.stderr,
    )
    print(f"report: {report_path}", file=sys.stderr)
    return EXIT_PARTIAL_FAILURE if failed > 0 else EXIT_OK


def _write_header(fh, config_name: str, input_path: str, policy: str) -> None:
    fh.write(json.dumps({
        "schema_version": 2,
        "active_config": config_name,
        "input": input_path,
        "tool_version": __version__,
        "policy": policy,
    }) + "\n")


def _write_record(
    fh,
    feat: Feature,
    *,
    action: str,
    tolerance: Optional[Tolerance],
    error: Optional[str],
    rationale: Optional[str] = None,
) -> None:
    fh.write(json.dumps({
        "feature": asdict(feat),
        "action": action,
        "tolerance": asdict(tolerance) if tolerance else None,
        "error": error,
        "rationale": rationale,
    }) + "\n")


if __name__ == "__main__":
    sys.exit(main())
