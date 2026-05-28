# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project shape

Single project, lives under `sw-tolerance/`. It's an MVP CLI that opens a SolidWorks `.slddrw`, applies bilateral ±0.5 mm to every untoleranced linear dimension, and writes a new `.slddrw` plus a JSONL report. End-to-end runs only on Windows with SolidWorks installed (uses COM via pywin32); the code is developed on macOS.

## Commands

All commands are run from `sw-tolerance/`.

| Task | Command |
|------|---------|
| Install runtime deps | `pip3 install -r requirements.txt` |
| Run CLI | `python3 tolerance.py <input.slddrw> <output.slddrw>` |
| Run all tests | `python3 -m unittest discover tests` |
| Run a single test | `python3 -m unittest tests.test_decide.DecideTests.test_already_toleranced_is_skipped` |

Tests are COM-free and runnable on macOS — they cover `decide.py` only. There is no separate linter/formatter wired up.

## Architecture — the big picture

Three single-purpose modules under `sw_tolerance/`. The whole point of the layout is that **`decide.tolerance_for(feature)` is a pure function** from a `Feature` dict to a `Tolerance | None`. Today it's a constant policy; future ML drops in by replacing this one function.

```
extract.iter_dimensions(model)   →  yields (displayDim, idim, tol_obj, Feature)
       │
decide.tolerance_for(feature)    →  Tolerance | None    ← swap point
       │
apply.write_tolerance(dim, tol)  →  mutates the SW dim via COM
```

`extract` yields a 4-tuple because **Apply needs the live COM handles** (`displayDim`, `idim`, `tol_obj`) to mutate the drawing, while **Decide only needs the pure `Feature`**. Keep this split — don't pass COM objects into `decide`.

## Invariants to preserve

1. **`decide.py` and `models.py` must have zero COM imports.** This is what makes decide unit-testable on macOS and trivially swappable for an ML model. If you find yourself wanting `win32com` in either file, the design has drifted.

2. **`sw_client.connect()` overwrites the `SW_*` int constants in `models.py`** with values from the live SolidWorks type library (`_sync_constants`). The hardcoded defaults in `models.py` are best-guesses from the SW 2020 SDK — they only matter on hosts without SolidWorks (i.e. when running tests on macOS). Don't trust them in production code paths; trust the runtime-synced values.

3. **COM imports are lazy inside functions** (`pythoncom`, `win32com.client`) in `sw_client.py` and `apply.py`. This is why the whole package imports cleanly on macOS for testing. Don't promote them to module-level imports.

4. **Set `tol_obj.Type` BEFORE calling `SetValues2`, and call `SetValues2` with all four arguments.** `SetValues2` silently no-ops if `Type` is still `swTolNONE`, and its full signature is `SetValues2(MinValue, MaxValue, WhichConfigurations, Config_names)` — all four are required, so passing only the two values raises a `-2147352561 "Parameter not optional"` COM error. For a bilateral tolerance `MinValue` is the lower deviation (negative) and `MaxValue` the upper deviation (positive); `apply.write_tolerance` signs the stored `Tolerance` magnitudes accordingly and passes `WhichConfigurations = models.SW_SETVALUE_THIS_CONFIG` with `Config_names = ""`. It also treats a `False` return as a failure (`ToleranceWriteFailed`) so a silent no-op is reported as `failed` rather than `applied`. These are the single most important SolidWorks API gotchas — keep the order and the full argument list.

5. **Multi-sheet iteration is explicit** in `extract.py`: it loops `GetSheetNames()` and `ActivateSheet(name)` per sheet. `GetFirstView/GetNextView` only walks the active sheet — don't refactor to a single view walk.

## The JSONL report

Written to `<output>.report.jsonl` next to the output drawing. First line is a header (`schema_version`, `active_config`, `input`, `tool_version`); each subsequent line is one record per dimension (`feature`, `action` ∈ `{applied, skipped, failed}`, `tolerance`, `error`). The `schema_version` field is the forward-compat hook for using these records as training data later — bump it if you change the record shape.

## Exit codes (defined in `tolerance.py`)

- `0` — all dimensions handled (any combination of applied + skipped)
- `1` — unrecoverable (SolidWorks unavailable, `OpenDoc6` failed, or `SaveAs3` failed)
- `2` — ran with per-dim apply failures (partial result still saved)
- `3` — validation error (bad path, output already exists, or input == output)

## Known first-run unknowns

- `model.ForceRebuild3(False)` is the assumed pre-save rebuild call for `IDrawingDoc`; if it errors at runtime, swap to `EditRebuild5()`.
- `is_hole_callout` in extracted `Feature`s is currently always `False` — hole-callout failures are caught at apply time via the per-dim `try/except` and logged as `failed`. If a cheap COM property for pre-detection turns up, wire it into `_build_feature`.
