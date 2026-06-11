# Build-one

**SolidWorks Auto-Tolerance MVP** — a CLI that applies bilateral ±0.5 mm tolerances to every untoleranced linear dimension in a `.slddrw`.

The MVP exists to de-risk the SolidWorks COM round-trip and to establish a clean three-layer architecture (Extract / Decide / Apply) so a future ML-driven tolerance policy can be dropped in by swapping a single pure function. The Extract layer doubles as a future training-data harvester: it can be run over historical toleranced drawings to build `(feature_dict, tolerance)` pairs.

## What it does

Opens a SolidWorks drawing, walks every sheet and view, and writes a bilateral ±0.5 mm tolerance to each linear dimension that doesn't already carry one. The input file is never modified — the result is written to a separate output path alongside a JSONL report describing what happened to every dimension.

## Requirements

- Windows 10 or 11
- SolidWorks installed and licensed (the CLI talks to it over COM via pywin32)
- Python 3.10+ for Windows (from [python.org](https://www.python.org/downloads/windows/) — tick "Add Python to PATH" during install)

## Install

Open **Command Prompt** or **PowerShell** in the repo root and run:

```
pip install -r sw-tolerance\requirements.txt
```

If `pip` isn't on your PATH, use `py -m pip install -r sw-tolerance\requirements.txt`.

## Run

```
python sw-tolerance\tolerance.py <input.slddrw> <output.slddrw>
```

Example:

```
python sw-tolerance\tolerance.py C:\drawings\bracket.slddrw C:\drawings\bracket_toleranced.slddrw
```

SolidWorks does not need to be open beforehand — the CLI launches it via COM. The first run after installing pywin32 may take a few seconds longer while it builds the SolidWorks type-library cache.

A per-run JSONL report is written to `<output.slddrw>.report.jsonl`. The first line is a header (`schema_version`, `active_config`, `input`, `tool_version`, `policy`); each subsequent line is one record per dimension with `feature`, `action` (`applied` / `skipped` / `failed`), `tolerance`, `geometric` (the GD&T frames written for that feature, each with its own `action`/`error`), `error`, and `rationale`.

## Harvest training data

`harvest.py` is the read-only counterpart to `tolerance.py`. Instead of writing a constant policy onto untoleranced dims, it **reads the tolerances engineers have already applied** across a folder of drawings and records them as `(feature, label)` pairs — the training data for a future ML tolerance model. It never modifies, rebuilds, or saves a drawing.

```
python sw-tolerance\harvest.py <input_dir> <output.jsonl>
```

Example:

```
python sw-tolerance\harvest.py C:\drawings\historical C:\datasets\tolerances.jsonl
```

It walks every `.slddrw` directly in `<input_dir>` and writes one record per existing tolerance, each tagged with a `label_type`: **`"dimensional"`** for a ± band an engineer applied to a dimension (`label` is a `Tolerance`), and **`"geometric"`** for an existing GD&T feature control frame (`label` is a `GeometricTolerance`, and the input `feature` is the 3D geometry the frame attaches to). The first line is a header (`schema_version`, `kind: "training_dataset"`, `tool_version`, `input_dir`). A bad drawing is logged and skipped so the rest of the batch still produces a dataset (exit code 2 signals that some files failed).

The recorded `feature.current_tolerance_type` is normalised to "untoleranced" — i.e. what the model will see at inference time — so harvesting a toleranced dim never leaks the answer into the inputs. The real tolerance lives in `label`.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All dimensions handled (applied + skipped, no failures) |
| 1 | Unrecoverable: SolidWorks unavailable, `OpenDoc6` failed, or `SaveAs3` failed |
| 2 | Ran with per-item write failures — a dimensional ± or a GD&T frame failed (partial result still saved) |
| 3 | Validation error: bad path, output already exists, or input == output |

## Architecture

Three single-purpose modules. `decide` is a pure, COM-free function — the only file that changes when a future ML model lands.

```
extract.iter_dimensions(model)   ->  yields (displayDim, idim, tol, Feature)
       |
decide.tolerance_for(feature)    ->  Tolerance | None    <- swap point
       |
apply.write_tolerance(dim, tol)  ->  mutates the SW dim via COM
```

`decide.py` has zero COM imports so it can be unit-tested without SolidWorks running.

## Tests

```
cd sw-tolerance
python -m unittest discover tests
```

The decide tests cover the policy on all linear variants, hole callouts, already-toleranced dims, and non-linear dim types. They don't touch COM, so they run on any Windows box with Python — no SolidWorks license required.

## Verification

Drop three fixtures into `sw-tolerance\test_drawings\` and run end-to-end against a real SolidWorks install:

- `simple_bracket.slddrw` — single sheet, ~5 linear dims, no existing tolerances. Expected: 5 `applied`, exit 0.
- `already_toleranced.slddrw` — one dim already toleranced. Expected: 4 `applied`, 1 `skipped`, exit 0.
- `multi_sheet.slddrw` — two sheets with dims on each. Proves explicit sheet iteration is correct.

After each run, open the output `.slddrw` in SolidWorks, confirm tolerances render, save and reopen to confirm persistence.

## Layout

```
sw-tolerance\
├── tolerance.py             CLI entry: apply policy, JSONL report
├── harvest.py               CLI entry: read-only training-data harvester
├── requirements.txt
├── sw_tolerance\
│   ├── models.py            Feature, GeometryContext, Tolerance, SW_* constants
│   ├── decide.py            pure policy — the swap point
│   ├── sw_client.py         COM connect + open/close lifecycle
│   ├── extract.py           sheets -> views -> dims; read_existing_tolerance
│   ├── geometry.py          resolve 3D-model context behind each dimension
│   └── apply.py             tolerance write, rebuild + SaveAs3
├── tests\                   decide, com, apply, validate, harvest, read-tol
└── test_drawings\           drop fixture .slddrw files here
```

## Troubleshooting

- **`ImportError: No module named win32com`** — pywin32 didn't install. Re-run `pip install -r sw-tolerance\requirements.txt`, then `python -m pywin32_postinstall -install` if COM still won't bind.
- **`pywintypes.com_error` on startup** — SolidWorks isn't installed, isn't licensed, or is blocked by another COM client. Open SolidWorks once manually so it registers, then retry.
- **Stderr warning about "degraded fallback mode"** — `gencache.EnsureDispatch` failed (no makepy cache yet) and the CLI fell back to late-binding `Dispatch`. The run will still complete using hardcoded SW 2020 SDK constants. To clear it, run `python -m win32com.client.makepy` and pick the SolidWorks type library.
- **`OpenDoc6 failed`** — the input path is wrong, the file is already open in another SolidWorks session, or the drawing references missing parts. Close SolidWorks and retry with an absolute path.

## Known first-run items

- `ForceRebuild3` is the expected pre-save rebuild call for `IDrawingDoc`; fall back to `EditRebuild5` if it errors at runtime.
- `is_hole_callout` detection in `extract.py` is currently always `False` — hole-callout failures are caught at apply-time via the per-dim `try/except` and logged with `action: "failed"`. If a cheap COM property for pre-detection turns up, wire it into `_build_feature`.
