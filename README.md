# Build-one

**SolidWorks Auto-Tolerance MVP** — a CLI that applies bilateral ±0.5 mm tolerances to every untoleranced linear dimension in a `.slddrw`.

The MVP exists to de-risk the SolidWorks COM round-trip and to establish a clean three-layer architecture (Extract / Decide / Apply) so a future ML-driven tolerance policy can be dropped in by swapping a single pure function. The Extract layer doubles as a future training-data harvester: it can be run over historical toleranced drawings to build `(feature_dict, tolerance)` pairs.

## What it does

Opens a SolidWorks drawing, walks every sheet and view, and writes a bilateral ±0.5 mm tolerance to each linear dimension that doesn't already carry one. The input file is never modified — the result is written to a separate output path alongside a JSONL report describing what happened to every dimension.

## Requirements

- Windows with SolidWorks installed (uses the COM API via pywin32)
- Python 3.10+

The code is developed on macOS but only runs end-to-end on Windows.

## Install

```
pip3 install -r sw-tolerance/requirements.txt
```

## Run

```
python3 sw-tolerance/tolerance.py <input.slddrw> <output.slddrw>
```

A per-run JSONL report is written to `<output.slddrw>.report.jsonl`. The first line is a header (`schema_version`, `active_config`, `input`, `tool_version`); each subsequent line is one record per dimension with `feature`, `action` (`applied` / `skipped` / `failed`), `tolerance`, and `error`.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All dimensions handled (applied + skipped, no failures) |
| 1 | Unrecoverable: SolidWorks unavailable, `OpenDoc6` failed, or `SaveAs3` failed |
| 2 | Ran with per-dim apply failures (partial result still saved) |
| 3 | Validation error: bad path, output already exists, or input == output |

## Architecture

Three single-purpose modules. `decide` is a pure, COM-free function — the only file that changes when a future ML model lands.

```
extract.iter_dimensions(model)   →  yields (displayDim, idim, tol, Feature)
       │
decide.tolerance_for(feature)    →  Tolerance | None    ← swap point
       │
apply.write_tolerance(dim, tol)  →  mutates the SW dim via COM
```

`decide.py` has zero COM imports so it's unit-testable on macOS.

## Tests

```
cd sw-tolerance && python3 -m unittest discover tests
```

The six decide tests cover the policy on all linear variants, hole callouts, already-toleranced dims, and non-linear dim types — all runnable without SolidWorks.

## Verification on Windows

Drop three fixtures into `sw-tolerance/test_drawings/` and run end-to-end:

- `simple_bracket.slddrw` — single sheet, ~5 linear dims, no existing tolerances. Expected: 5 `applied`, exit 0.
- `already_toleranced.slddrw` — one dim already toleranced. Expected: 4 `applied`, 1 `skipped`, exit 0.
- `multi_sheet.slddrw` — two sheets with dims on each. Proves explicit sheet iteration is correct.

After each run, open the output `.slddrw` in SolidWorks, confirm tolerances render, save and reopen to confirm persistence.

## Layout

```
sw-tolerance/
├── tolerance.py             CLI entry: argparse, orchestration, JSONL report
├── requirements.txt
├── sw_tolerance/
│   ├── models.py            Feature, Tolerance, SW_* constants
│   ├── decide.py            pure policy — the swap point
│   ├── sw_client.py         COM connect + open/close lifecycle
│   ├── extract.py           sheets → views → display dimensions
│   └── apply.py             tolerance write, rebuild + SaveAs3
├── tests/test_decide.py
└── test_drawings/           drop fixture .slddrw files here
```

## Known first-run items

- `IDimensionTolerance::SetValues2` requires all four arguments — `(MinValue, MaxValue, WhichConfigurations, Config_names)`. `apply.write_tolerance` passes a negative min / positive max for the bilateral band, `swSetValue_InThisConfiguration`, and an empty config-name string. SolidWorks has historically returned `False` from `SetValues2` on single-configuration documents; `write_tolerance` surfaces a `False` return as a `failed` record so it's visible in the report rather than silently lost.
- `ForceRebuild3` is the expected pre-save rebuild call for `IDrawingDoc`; fall back to `EditRebuild5` if it errors at runtime.
- `is_hole_callout` detection in `extract.py` is currently always `False` — hole-callout failures are caught at apply-time via the per-dim `try/except` and logged with `action: "failed"`. If a cheap COM property for pre-detection turns up, wire it into `_build_feature`.

## Plan and design notes

The full reviewed implementation plan — context, locked decisions, gotchas, and verification — lives at `/Users/lucasfeng/.claude/plans/this-is-my-plan-drifting-sparrow.md`.
