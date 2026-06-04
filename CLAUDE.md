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

Single project, lives under `sw-tolerance/`. It's a CLI that opens a SolidWorks `.slddrw`, applies a bilateral tolerance to every untoleranced conventional dimension — length-valued dims (linear, diameter, radial, arc-length, ordinate) and angular dims — and writes a new `.slddrw` plus a JSONL report. End-to-end runs only on Windows with SolidWorks installed (uses COM via pywin32); the code is developed on macOS.

The tolerance *magnitude* comes from a pluggable predictor behind `decide.tolerance_for` (see Architecture). There are three predictors on one swap point, in order of where the project is heading: the flat **constant policy** (±0.5 mm / ±1°, the MVP default and universal fallback), an interim **DeepSeek-backed "brain"** (`predict.predict_tolerance` — chosen per dimension, enabled when `DEEPSEEK_API_KEY` is set; DeepSeek exposes an OpenAI-compatible API, reached via the `openai` SDK), and eventually a **trained ML model** (the last thing built; it drops in at the same seam, bootstrapped by the LLM rationales). The long-term goal is the ML-driven policy; the LLM brain is the interim intelligence that lets the tool feel smart before any training data exists.

There is a second, **read-only** CLI: `harvest.py`. It's the inverse of `tolerance.py` — instead of writing a policy, it reads tolerances engineers have *already* applied across a folder of drawings and records them as `(feature, label)` training pairs for the eventual ML model that replaces `decide.tolerance_for`. It never mutates, rebuilds, or saves a drawing. This is the data-collection half of the long-term goal; `tolerance.py` is the apply half.

## Commands

All commands are run from `sw-tolerance/`.

| Task | Command |
|------|---------|
| Install runtime deps | `pip3 install -r requirements.txt` |
| Run CLI (apply) | `python3 tolerance.py <input.slddrw> <output.slddrw>` |
| Run CLI (apply, force constant) | `python3 tolerance.py <input.slddrw> <output.slddrw> --no-llm` |
| Run CLI (harvest) | `python3 harvest.py <input_dir> <output.jsonl>` |
| Run all tests | `python3 -m unittest discover tests` |
| Run a single test | `python3 -m unittest tests.test_decide.DecideTests.test_already_toleranced_is_skipped` |

`tolerance.py` uses the DeepSeek-backed predictor automatically when `DEEPSEEK_API_KEY` is set (unless `--no-llm` is passed); otherwise it runs the constant policy. `SW_TOLERANCE_MODEL` overrides the model id (default `deepseek-chat`). The LLM path needs `openai` installed; the constant path does not (it's lazily imported).

Tests are COM-free and runnable on macOS — they cover `decide.py` (skip rules + constant policy + predictor seam), `predict.py` (with a mocked DeepSeek client — no network), `com.py`, `apply.write_tolerance`, and `tolerance._validate_paths` (everything that doesn't need a live COM object). There is no separate linter/formatter wired up.

## Architecture — the big picture

Single-purpose modules under `sw_tolerance/`. The whole point of the layout is that **`decide.tolerance_for(feature)` is a pure function** from a `Feature` to a `Tolerance | None`. It dispatches to a **pluggable predictor** (the swap point for intelligence) and branches on dimension-type *family* (length vs angular — see "Tolerance policy & dimension-type families" below).

```
extract.iter_dimensions(model)   →  yields (displayDim, idim, tol_obj, Feature)
       │
decide.tolerance_for(feature)    →  Tolerance | None    ← swap point
   └─ decide.decide_with_rationale(feature)             (skip rules, then dispatch)
          └─ active predictor(feature, family) → (Tolerance | None, rationale | None)
                 ├─ decide.constant_tolerance        (default / universal fallback)
                 ├─ predict.predict_tolerance        (DeepSeek brain; CLI installs it)
                 └─ <trained model>                  (eventual final predictor)
       │
apply.write_tolerance(dim, tol)  →  mutates the SW dim via COM
```

**The skip rules (already-toleranced / reference dim / hole callout / unsupported family) live in `decide.decide_with_rationale`, not in the predictors** — single source of truth, so no predictor (including the LLM) can ever re-tolerance a toleranced dim, add a ± to a reference dim, touch a hole callout, or an unsupported family. A predictor only chooses the *magnitude* for a feature that already passed the skip rules (and may still return `None` to leave it untoleranced). Swap predictors with `decide.set_predictor(fn)` / `decide.use_constant()`; the default is the constant policy, which is why the module stays pure and offline-testable. The optional `rationale` is the predictor's free-text justification (the LLM fills it; the constant policy returns `None`) — recorded in the report and useful as future training signal.

`extract` yields a 4-tuple because **Apply needs the live COM handles** (`displayDim`, `idim`, `tol_obj`) to mutate the drawing, while **Decide only needs the pure `Feature`**. Keep this split — don't pass COM objects into `decide`.

## Tolerance policy & dimension-type families

`decide._family_of` groups `swDimensionType_e` values into families by the *unit* SolidWorks stores the value/tolerance in, and the **constant policy** (`constant_tolerance`, the default predictor) applies a per-family default:

- **Length family** (value/tolerance in **metres**) — `LENGTH_DIM_TYPES` in `models.py`: linear (incl. horizontal/vertical), diameter, radial, arc-length, and ordinate (incl. horizontal/vertical). Constant default: bilateral **±0.5 mm** (`0.0005 m`).
- **Angular family** (value/tolerance in **radians**) — `ANGULAR_DIM_TYPES`: angular dims. Constant default: bilateral **±1°** (`math.radians(1.0)`), written in the dim's native unit, not degrees.
- **Everything else is skipped** — chamfer, unknown, and any type in neither family (`_family_of` returns `None`). This skip — along with the already-toleranced (`current_tolerance_type != swTolNONE`), reference-dim (`is_reference`), and hole-callout skips — happens in `decide_with_rationale` *before* any predictor runs (see Architecture), so no predictor ever sees them and the tool never overwrites an engineer's existing tolerance.

Any predictor must emit in the family's unit, not one flat number: because the units differ, emitting `0.0005` for an angular dim would be ~0.03°, not 0.5 mm. The constant policy picks the right per-unit default; the DeepSeek predictor is told mm vs degrees and converts its answer back to metres/radians. `harvest.py` collects training data over the same `LENGTH_DIM_TYPES ∪ ANGULAR_DIM_TYPES` so the dataset matches what the apply path handles.

## What the predictor sees (the `Feature`)

A `Feature` (`models.py`) is the *only* input to `decide.tolerance_for`, so its richness caps prediction quality. Beyond `value` / `dim_type` / `current_tolerance_type` / `view_name` / `sheet_name`, it carries richer per-dimension context that `extract._build_feature` reads from the display dimension:

- **`is_reference`** — the dim is a reference/driven dim (shown in parentheses). `decide` skips these (they report a value controlled elsewhere; adding a ± is wrong). Read from `IDisplayDimension.IsReference`.
- **`text_prefix` / `text_suffix`** — the non-value annotation text around the dim (a leading `⌀`/`M6`, a trailing `TYP`/`MAX`), read via `IDisplayDimension.GetText(swDimensionTextPrefix|Suffix)`. Surfaced in the DeepSeek prompt as an "Annotation text" line; carries intent the bare type/value miss.
- **`is_hole_callout`** — still a placeholder (always `False`; see first-run unknowns).

**Guarded extraction:** the richer reads live in `extract._display_attrs` and each is wrapped *individually* (`_safe_is_reference`, `_safe_text`), so a missing/over-version COM member degrades to the field default instead of dropping a dimension that otherwise extracted fine. The exact COM members are first-run unknowns — confirm on Windows.

**Train/inference parity (invariant #6):** every `Feature` field must be obtainable identically at harvest and apply time, and must not derive from the existing tolerance (no label leak). New fields flow into both the apply report and the harvest dataset automatically via `asdict(feature)`, which is why adding them bumps both schema versions.

These family sets are seeded from the hardcoded `swDimensionType_e` integers in `models.py` and rebuilt from the live type library by `_sync_constants` on the early-binding path (invariant 2). The non-linear integers were originally inferred from the documented enum ordering, then **confirmed against live SolidWorks** (diameter=6, radial=5, arc-length=4, ordinate=1/7/8, angular=3 all apply at the expected value; the angular band reads exactly ±1.0000°, and chamfer=10 is correctly skipped).

## Invariants to preserve

1. **`decide.py` and `models.py` must have zero COM imports.** This is what makes decide unit-testable on macOS and trivially swappable for an ML model. If you find yourself wanting `win32com` in either file, the design has drifted. `predict.py` (the DeepSeek brain) is likewise **zero-COM**, and imports `openai` **lazily inside functions** (same rule as 3 below) so the package still imports on macOS without the SDK; only the LLM code path needs it installed. `decide.py` must not import `predict` at module top either — callers (the CLI) wire it in via `set_predictor`, keeping `decide` import-clean and dependency-free.

2. **`sw_client.connect()` overwrites the `SW_*` int constants in `models.py`** with values from the live SolidWorks type library (`_sync_constants`, which also rebuilds the `LINEAR_DIM_TYPES`/`LENGTH_DIM_TYPES`/`ANGULAR_DIM_TYPES` family sets) — but only on the early-binding path. If `gencache.EnsureDispatch` fails (e.g. first run without makepy), `connect()` falls back to late-binding `Dispatch` and the SW 2020 SDK defaults in `models.py` stay in force; a stderr warning is printed so the degraded mode isn't silent. The hardcoded defaults are best-guesses — they matter on hosts without SolidWorks (tests on macOS) and in the late-binding fallback. Don't trust them in production code paths; trust the runtime-synced values when available.

3. **COM imports are lazy inside functions** (`pythoncom`, `win32com.client`) in `sw_client.py` and `apply.py`. This is why the whole package imports cleanly on macOS for testing. Don't promote them to module-level imports.

   3a. **No-arg COM methods must be invoked via `com.call(obj, "Method")`, never `obj.Method()` directly.** When makepy can't build the type-library cache, `connect()` falls back to late-binding `Dispatch`. Under late binding pywin32 has no type info, so a *no-arg* member (`GetSheetNames`, `GetTitle`, `GetFirstView`, `GetNextView`, `GetNext5`, `GetName2`, `GetActiveConfiguration`, …) is resolved as a property get — `obj.GetSheetNames` already returns the value, and `obj.GetSheetNames()` then raises `'tuple' object is not callable`. `com.call` returns the value as-is under late binding and calls the bound method under early binding. Methods that take **arguments** are unaffected (always real callables) and can be called directly. `com.py` has zero COM imports and is unit-tested on macOS.

4. **Set `tol_obj.Type` BEFORE calling `SetValues2`.** `SetValues2` silently no-ops if `Type` is still `swTolNONE`. This is the single most important SolidWorks API gotcha — `apply.write_tolerance` encodes it and it must stay in that order.

5. **Multi-sheet iteration is explicit** in `extract.py`: it loops `GetSheetNames()` and `ActivateSheet(name)` per sheet. `GetFirstView/GetNextView` only walks the active sheet — don't refactor to a single view walk.

6. **The harvester must not leak the label into the feature.** Every dimension `harvest.py` records is by definition toleranced, but at inference the ML model only sees *untoleranced* dims. So `harvest._build_record` normalises `feature.current_tolerance_type` to `SW_TOL_NONE` (what the model will see at predict time); the real tolerance lives only in `label`. Don't record the raw toleranced state as an input feature — that's a train/inference mismatch that would teach the model nothing. Relatedly, `extract.read_existing_tolerance` `abs()`-normalises both deviations so harvested labels mirror the `Tolerance` shape `decide.tolerance_for` produces and the sign convention `apply.write_tolerance` writes (`apply.py` negates the min).

## The JSONL report

Written to `<output>.report.jsonl` next to the output drawing. First line is a header (`schema_version`, `active_config`, `input`, `tool_version`, `policy`); each subsequent line is one record per dimension (`feature`, `action` ∈ `{applied, skipped, failed}`, `tolerance`, `error`, `rationale`). `policy` is `"constant"` or `"llm:<model-id>"` so a report self-documents which predictor produced it; `rationale` is the predictor's justification string (LLM only, else `null`). The `schema_version` field is the forward-compat hook for using these records as training data later — bump it if you change the record shape. **Current: `schema_version: 3`** (v2 added `policy` to the header and `rationale` to each record in the LLM-predictor change; v3 grew the embedded `feature` with `is_reference`/`text_prefix`/`text_suffix`).

`harvest.py` writes a **separate** JSONL shape — the training dataset, not the apply report. Header: `schema_version`, `kind: "training_dataset"`, `tool_version`, `input_dir`; each record is `{source_file, feature, label}` (the `label` is the harvested `Tolerance`). It carries its own `kind` + `schema_version`; bump that `schema_version` independently if the record shape changes. **Current: `schema_version: 2`** (v2 grew the `feature` with the richer fields above).

## Exit codes (defined in `tolerance.py`)

- `0` — all dimensions handled (any combination of applied + skipped)
- `1` — unrecoverable (SolidWorks unavailable, `OpenDoc6` failed, or `SaveAs3` failed)
- `2` — ran with per-dim apply failures (partial result still saved)
- `3` — validation error (bad path, output already exists, or input == output)

## Known first-run unknowns

- `model.ForceRebuild3(False)` is the assumed pre-save rebuild call for `IDrawingDoc`; if it errors at runtime, swap to `EditRebuild5()`.
- `is_hole_callout` in extracted `Feature`s is currently always `False` — hole-callout failures are caught at apply time via the per-dim `try/except` and logged as `failed`. If a cheap COM property for pre-detection turns up, wire it into `_build_feature`.
- The richer `Feature` reads in `extract._display_attrs` are best-guesses pending a live run: `IDisplayDimension.IsReference` (reference/driven flag) and `IDisplayDimension.GetText(swDimensionTextPrefix|Suffix)` (annotation text). Each is individually guarded, so if a name/signature is wrong on the target SolidWorks build it degrades to the default (`is_reference=False`, empty text) rather than erroring — confirm the members and the `swDimensionTextParts_e` values (assumed prefix=1, suffix=2) on Windows.
- **Deferred richer feature — displayed precision (decimal places).** The number of decimal places a dim is shown to (e.g. `25.00` vs `25`) is a strong intent signal, but the per-dimension COM source is uncertain (it may only be available as the document default `swUnitsLinearDimensionDecimalPlaces`). Deliberately left out of the current richer-extraction pass rather than ship an unverified/fabricated COM call; revisit once it can be confirmed on Windows.
