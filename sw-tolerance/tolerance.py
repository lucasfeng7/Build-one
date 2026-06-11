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
from sw_tolerance.apply import (
    SaveFailed,
    rebuild_and_save,
    write_geometric_tolerance,
    write_tolerance,
)
from sw_tolerance.decide import decide_for, set_predictor
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
    geo_applied = geo_failed = 0
    config_name = get_active_config_name(model)

    with report_path.open("w", encoding="utf-8") as fh:
        _write_header(fh, config_name, input_path, policy)

        for disp_dim, _idim, tol_obj, feat in iter_dimensions(model):
            decision = decide_for(feat)
            tolerance = decision.dimensional
            rationale = decision.rationale

            # The dimensional ± write (action), tracked as before.
            if tolerance is None:
                dim_action, dim_error = "skipped", None
                skipped += 1
            else:
                try:
                    write_tolerance(tol_obj, tolerance)
                    dim_action, dim_error = "applied", None
                    applied += 1
                except Exception as e:
                    dim_action, dim_error = "failed", str(e)
                    failed += 1

            # Each proposed GD&T frame is written independently and recorded with
            # its own outcome, so one bad frame neither blocks the others nor the
            # dimensional write.
            geo_results = []
            for g in decision.geometric:
                try:
                    write_geometric_tolerance(model, disp_dim, g)
                    geo_results.append((g, "applied", None))
                    geo_applied += 1
                except Exception as e:
                    geo_results.append((g, "failed", str(e)))
                    geo_failed += 1

            _write_record(fh, feat, action=dim_action, tolerance=tolerance,
                          error=dim_error, rationale=rationale, geometric=geo_results)

    rebuild_and_save(model, out_path)

    print(
        f"policy={policy} applied={applied} skipped={skipped} failed={failed} "
        f"gtol_applied={geo_applied} gtol_failed={geo_failed}",
        file=sys.stderr,
    )
    print(f"report: {report_path}", file=sys.stderr)
    return EXIT_PARTIAL_FAILURE if (failed > 0 or geo_failed > 0) else EXIT_OK


def _write_header(fh, config_name: str, input_path: str, policy: str) -> None:
    fh.write(json.dumps({
        # v3: Feature gained is_reference / text_prefix / text_suffix, so each
        # record's embedded `feature` shape changed.
        # v4: Feature gained nested `geometry` (GeometryContext) — 3D-model
        # context resolved from the part behind the drawing.
        # v5: each record gained a `geometric` list — proposed GD&T feature
        # control frames (applied to the drawing in Phase 4).
        # v6: each `geometric` entry became an object {tolerance, action, error}
        # recording the per-frame write outcome (InsertGtol now runs).
        "schema_version": 6,
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
    geometric: tuple = (),
) -> None:
    fh.write(json.dumps({
        "feature": asdict(feat),
        "action": action,
        "tolerance": asdict(tolerance) if tolerance else None,
        # One entry per GD&T frame (empty list when none): the proposed frame
        # plus its per-frame write outcome.
        "geometric": [
            {"tolerance": asdict(g), "action": g_action, "error": g_error}
            for g, g_action, g_error in geometric
        ],
        "error": error,
        "rationale": rationale,
    }) + "\n")


if __name__ == "__main__":
    sys.exit(main())
