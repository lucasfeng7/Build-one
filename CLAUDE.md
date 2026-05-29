# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working norms

These norms apply to every session, not just feature work.

- **Keep this file alive.** CLAUDE.md is the source of truth for how the project works, so treat it as a living document. Whenever a change alters the architecture, an invariant, the command set, the report schema, exit codes, or resolves a "first-run unknown", update the relevant section in the same change — don't let the docs drift behind the code. When an unknown becomes known, move it out of "Known first-run unknowns" into the section it belongs in.
- **Commit consistently and in small, coherent units.** Make a commit per logical change rather than batching unrelated work. Before starting, `git pull` to stay current; after a unit of work is done and verified, `git push` so nothing lives only on the local machine.
- **Write detailed commit messages.** Use a concise imperative subject line, then a body that explains *what* changed and *why* — the motivation, the trade-offs considered, and any SolidWorks/COM gotcha that drove the approach. A reader skimming `git log` should understand the reasoning without opening the diff.
- **Open pull requests with full descriptions.** When work lands on a branch, open a PR whose description covers the problem, the approach, how it was tested (note that end-to-end needs Windows + SolidWorks, so say what was actually exercised vs. only reasoned about), and any follow-ups or risks. Keep PRs focused enough to review in one sitting.
- **Push and PR only when the user asks** (per the harness rules), but keep the working tree in a state where doing so is a one-step operation: commits clean, message bodies already detailed.

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

2. **`sw_client.connect()` overwrites the `SW_*` int constants in `models.py`** with values from the live SolidWorks type library (`_sync_constants`) — but only on the early-binding path. If `gencache.EnsureDispatch` fails (e.g. first run without makepy), `connect()` falls back to late-binding `Dispatch` and the SW 2020 SDK defaults in `models.py` stay in force; a stderr warning is printed so the degraded mode isn't silent. The hardcoded defaults are best-guesses — they matter on hosts without SolidWorks (tests on macOS) and in the late-binding fallback. Don't trust them in production code paths; trust the runtime-synced values when available.

3. **COM imports are lazy inside functions** (`pythoncom`, `win32com.client`) in `sw_client.py` and `apply.py`. This is why the whole package imports cleanly on macOS for testing. Don't promote them to module-level imports.

   3a. **No-arg COM methods must be invoked via `com.call(obj, "Method")`, never `obj.Method()` directly.** When makepy can't build the type-library cache, `connect()` falls back to late-binding `Dispatch`. Under late binding pywin32 has no type info, so a *no-arg* member (`GetSheetNames`, `GetTitle`, `GetFirstView`, `GetNextView`, `GetNext5`, `GetName2`, `GetActiveConfiguration`, …) is resolved as a property get — `obj.GetSheetNames` already returns the value, and `obj.GetSheetNames()` then raises `'tuple' object is not callable`. `com.call` returns the value as-is under late binding and calls the bound method under early binding. Methods that take **arguments** are unaffected (always real callables) and can be called directly. `com.py` has zero COM imports and is unit-tested on macOS.

4. **Set `tol_obj.Type` BEFORE calling `SetValues2`.** `SetValues2` silently no-ops if `Type` is still `swTolNONE`. This is the single most important SolidWorks API gotcha — `apply.write_tolerance` encodes it and it must stay in that order.

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
