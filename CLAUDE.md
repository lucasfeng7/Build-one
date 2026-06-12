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

There is a second, **read-only** CLI: `harvest.py`. It's the inverse of `tolerance.py` — instead of writing a policy, it reads tolerances engineers have *already* applied across a folder of drawings and records them as training pairs for the eventual ML model that replaces `decide`. It harvests **both** kinds the apply path produces: dimensional ± tolerances (from `read_existing_tolerance`) and geometric GD&T frames (from `iter_geometric_tolerances`/`read_geometric_tolerance`). It never mutates, rebuilds, or saves a drawing. This is the data-collection half of the long-term goal; `tolerance.py` is the apply half.

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

Tests are COM-free and runnable on macOS — they cover `decide.py` (skip rules + constant policy + predictor seam), `predict.py` (with a mocked DeepSeek client — no network), `com.py`, `apply.write_tolerance` + `apply.write_geometric_tolerance` (insert orchestration via fakes), the `gtol_text` codec (frame encoding/decoding + the write↔read round-trip), `geometry.py` (surface/feature classification + guarded-degradation via fake COM objects), the `extract` GD&T annotation walk (via fake IGtol/views), and `tolerance._validate_paths` (everything that doesn't need a live COM object). There is no separate linter/formatter wired up.

## Architecture — the big picture

Single-purpose modules under `sw_tolerance/`. The whole point of the layout is that **`decide.decide_for(feature)` is a pure function** from a `Feature` to a `ToleranceDecision`. It dispatches to a **pluggable predictor** (the swap point for intelligence) and branches on dimension-type *family* (length vs angular — see "Tolerance policy & dimension-type families" below).

A **`ToleranceDecision`** (models.py) carries three things: an optional dimensional `Tolerance` (the ± size band), a tuple of `GeometricTolerance`s (GD&T feature control frames — characteristic · zone · ⌀ · datums · material condition), and an optional `rationale`. One feature can get both a size ± and a position FCF, which is why the seam returns the richer decision rather than a bare `Tolerance`. `tolerance_for(f) → Tolerance | None` is the thin `.dimensional` view for callers that only need the ± band.

```
extract.iter_dimensions(model)   →  yields (displayDim, idim, tol_obj, Feature)
       │   └─ geometry.resolve_geometry(displayDim, view) → Feature.geometry (3D context)
       │
decide.decide_for(feature)       →  ToleranceDecision    ← swap point
   └─ (skip rules, then dispatch)
          └─ active predictor(feature, family) → ToleranceDecision
                 ├─ decide.constant_tolerance        (default / universal fallback; ± only, no GD&T)
                 ├─ predict.predict_tolerance        (DeepSeek brain; ± + optional GD&T; CLI installs it)
                 └─ <trained model>                  (eventual final predictor)
       │
apply.write_tolerance(dim, tol)                  →  mutates the SW dim via COM
apply.write_geometric_tolerance(model, dim, gtol) →  inserts a GD&T frame via COM
```

**The skip rules (already-toleranced / reference dim / hole callout / unsupported family) live in `decide.decide_for`, not in the predictors** — single source of truth, so no predictor (including the LLM) can ever re-tolerance a toleranced dim, add a ± to a reference dim, touch a hole callout, or an unsupported family. A skipped feature returns the empty `ToleranceDecision()` (no ±, no GD&T, no rationale). A predictor only chooses the *decision* for a feature that already passed the skip rules (and may still return an empty decision to leave it untoleranced). Swap predictors with `decide.set_predictor(fn)` / `decide.use_constant()`; the default is the constant policy, which is why the module stays pure and offline-testable. The `rationale` is the predictor's free-text justification (the LLM fills it; the constant policy leaves it `None`) — recorded in the report and useful as future training signal.

`extract` yields a 4-tuple because **Apply needs the live COM handles** (`displayDim`, `idim`, `tol_obj`) to mutate the drawing, while **Decide only needs the pure `Feature`**. Keep this split — don't pass COM objects into `decide`.

## Tolerance policy & dimension-type families

`decide._family_of` groups `swDimensionType_e` values into families by the *unit* SolidWorks stores the value/tolerance in, and the **constant policy** (`constant_tolerance`, the default predictor) applies a per-family default:

- **Length family** (value/tolerance in **metres**) — `LENGTH_DIM_TYPES` in `models.py`: linear (incl. horizontal/vertical), diameter, radial, arc-length, and ordinate (incl. horizontal/vertical). Constant default: bilateral **±0.5 mm** (`0.0005 m`).
- **Angular family** (value/tolerance in **radians**) — `ANGULAR_DIM_TYPES`: angular dims. Constant default: bilateral **±1°** (`math.radians(1.0)`), written in the dim's native unit, not degrees.
- **Everything else is skipped** — chamfer, unknown, and any type in neither family (`_family_of` returns `None`). This skip — along with the already-toleranced (`current_tolerance_type != swTolNONE`), reference-dim (`is_reference`), and hole-callout skips — happens in `decide_for` *before* any predictor runs (see Architecture), so no predictor ever sees them and the tool never overwrites an engineer's existing tolerance.

Any predictor must emit in the family's unit, not one flat number: because the units differ, emitting `0.0005` for an angular dim would be ~0.03°, not 0.5 mm. The constant policy picks the right per-unit default; the DeepSeek predictor is told mm vs degrees and converts its answer back to metres/radians. `harvest.py` collects training data over the same `LENGTH_DIM_TYPES ∪ ANGULAR_DIM_TYPES` so the dataset matches what the apply path handles.

## GD&T (geometric tolerances)

Beyond the dimensional ± band, a predictor may also propose **geometric tolerances** — GD&T feature control frames — in the `geometric` tuple of its `ToleranceDecision`. A **`GeometricTolerance`** (models.py) is `symbol` (the characteristic), `zone_value` (the tolerance zone, a LINEAR distance in **metres** — a geometric zone is always a length, even for an angular feature), `diameter_zone` (a ⌀ cylindrical zone, typical for position), `material_condition` (`RFS`/`MMC`/`LMC` from `MATERIAL_CONDITIONS`), and an ordered `datum_refs` tuple of `DatumRef`s (`letter` + `modifier`). The model is fully general; the **starter set** of characteristics the predictor targets is `GEOMETRIC_SYMBOLS` = flatness, perpendicularity, parallelism, position, concentricity, circular_runout, total_runout. The constant policy proposes none (`geometric=()`); the DeepSeek predictor proposes a frame only when the geometry clearly calls for one (it's told the `Feature.geometry` 3D context and uses it to decide). A frame crosses the COM boundary as a text array `[symbol, tolerance, *datums]`; **the codec for that encoding lives in one place, `gtol_text.py`** (pure, COM-free): `gtol_text.frame_values` encodes (used by apply) and `gtol_text.build_geometric_tolerance` decodes (used by the harvest read), so write↔read round-trips by construction and is verified on macOS. Geometric *application* — `apply.write_geometric_tolerance(model, disp_dim, gtol)` — selects the dimension's geometry, inserts an `IGtol` via the model extension, and populates its first frame from the codec. Each frame is written independently behind a per-frame try/except, so one bad frame blocks neither the others nor the dimensional write. `predict._parse_geometric` is likewise defensive: it drops any frame with an unknown characteristic, a non-numeric zone, or a malformed datum rather than failing the whole prediction (material conditions normalise through `gtol_text.normalize_condition`).

## What the predictor sees (the `Feature`)

A `Feature` (`models.py`) is the *only* input to `decide.tolerance_for`, so its richness caps prediction quality. Beyond `value` / `dim_type` / `current_tolerance_type` / `view_name` / `sheet_name`, it carries richer per-dimension context that `extract._build_feature` reads from the display dimension:

- **`is_reference`** — the dim is a reference/driven dim (shown in parentheses). `decide` skips these (they report a value controlled elsewhere; adding a ± is wrong). Read from `IDisplayDimension.IsReference`.
- **`text_prefix` / `text_suffix`** — the non-value annotation text around the dim (a leading `⌀`/`M6`, a trailing `TYP`/`MAX`), read via `IDisplayDimension.GetText(swDimensionTextPrefix|Suffix)`. Surfaced in the DeepSeek prompt as an "Annotation text" line; carries intent the bare type/value miss.
- **`is_hole_callout`** — still a placeholder (always `False`; see first-run unknowns).
- **`geometry`** — a nested `GeometryContext | None` carrying *3D-model* context resolved from the part behind the drawing (parts-only for now): `feature_kind` (clearance/tapped hole, counterbore, boss, fillet, chamfer, planar/cylindrical face, unknown), `surface_type` (plane/cylinder/cone/other), `nominal_diameter` (metres), `is_internal` (hole vs boss), `hole_standard` (e.g. `M6`), `referenced_model`. This is what lets the predictor tell a precision bore from a rough slot, and is the input that makes intelligent GD&T selection possible. Resolved by `geometry.resolve_geometry(disp_dim, view)`, which walks `disp_dim → IAnnotation → attached entities → IFace2 → ISurface`/owning `IFeature`. `None` when resolution wholly fails (so a dim that extracted fine in 2D is never lost).

**Guarded extraction:** the richer reads live in `extract._display_attrs` (each wrapped *individually* via `_safe_is_reference`, `_safe_text`) and in `geometry.py` (every COM hop wrapped — `referenced_model_of`, `_safe_attached_face`, `_safe_surface`, `_safe_feature`), so a missing/over-version COM member degrades to the field default instead of dropping a dimension that otherwise extracted fine. `geometry.py` holds no COM imports and routes no-arg members through `com.call` (same rules as `extract.py`); its pure classifiers (`_classify_surface`, `_classify_feature_typename`) are unit-tested on macOS. `referenced_model_of(view)` is view-level data: the per-view iterators in `extract.py` resolve it once per view and pass it into `resolve_geometry*`, rather than paying the COM reads per dimension. The exact COM members are first-run unknowns — confirm on Windows.

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

Written to `<output>.report.jsonl` next to the output drawing. First line is a header (`schema_version`, `active_config`, `input`, `tool_version`, `policy`); each subsequent line is one record per dimension (`feature`, `action` ∈ `{applied, skipped, failed}`, `tolerance`, `geometric`, `error`, `rationale`). `action` tracks the *dimensional* ± write; `geometric` is a list of one object per GD&T frame — `{tolerance, action ∈ {applied, failed}, error}` — recording each frame's own write outcome (empty list when none). `policy` is `"constant"` or `"llm:<model-id>"` so a report self-documents which predictor produced it; `rationale` is the predictor's justification string (LLM only, else `null`). The `schema_version` field is the forward-compat hook for using these records as training data later — bump it if you change the record shape. **Current: `schema_version: 6`** (v2 added `policy` to the header and `rationale` to each record in the LLM-predictor change; v3 grew the embedded `feature` with `is_reference`/`text_prefix`/`text_suffix`; v4 grew it with the nested `geometry` 3D-model context; v5 added the `geometric` GD&T list; v6 made each `geometric` entry an object with its per-frame `action`/`error`).

`harvest.py` writes a **separate** JSONL shape — the training dataset, not the apply report. Header: `schema_version`, `kind: "training_dataset"`, `tool_version`, `input_dir`. Each record carries a **`label_type`** discriminating the two kinds it now collects:
- **`"dimensional"`** — `{source_file, label_type, feature, label}` where `feature` is the inference-normalised `Feature` and `label` is the harvested `Tolerance` (the ± an engineer applied to a dimension).
- **`"geometric"`** — `{source_file, label_type, feature, label}` where `label` is a harvested `GeometricTolerance` (an existing GD&T feature control frame) and `feature` is `{view_name, sheet_name, geometry}` — a geometric tolerance attaches to a *face/feature*, not a valued dimension, so its input context is the 3D `geometry`, not a `Feature`. No label leak is possible (geometry is independent of the tolerance).

It carries its own `kind` + `schema_version`; bump that `schema_version` independently if the record shape changes. **Current: `schema_version: 4`** (v2 grew the `feature` with the richer display fields; v3 added the nested `geometry` 3D-model context; v4 added geometric records + the `label_type` discriminator).

## Exit codes (defined in `tolerance.py`)

- `0` — all dimensions handled (any combination of applied + skipped), no write failures
- `1` — unrecoverable (SolidWorks unavailable, `OpenDoc6` failed, or `SaveAs3` failed)
- `2` — ran with per-item write failures — a dimensional ± *or* a GD&T frame failed (partial result still saved)
- `3` — validation error (bad path, output already exists, or input == output)

## Known first-run unknowns

- `model.ForceRebuild3(False)` is the assumed pre-save rebuild call for `IDrawingDoc`; if it errors at runtime, swap to `EditRebuild5()`.
- `is_hole_callout` in extracted `Feature`s is currently always `False` — hole-callout failures are caught at apply time via the per-dim `try/except` and logged as `failed`. If a cheap COM property for pre-detection turns up, wire it into `_build_feature`.
- The richer `Feature` reads in `extract._display_attrs` are best-guesses pending a live run: `IDisplayDimension.IsReference` (reference/driven flag) and `IDisplayDimension.GetText(swDimensionTextPrefix|Suffix)` (annotation text). Each is individually guarded, so if a name/signature is wrong on the target SolidWorks build it degrades to the default (`is_reference=False`, empty text) rather than erroring — confirm the members and the `swDimensionTextParts_e` values (assumed prefix=1, suffix=2) on Windows.
- **Deferred richer feature — displayed precision (decimal places).** The number of decimal places a dim is shown to (e.g. `25.00` vs `25`) is a strong intent signal, but the per-dimension COM source is uncertain (it may only be available as the document default `swUnitsLinearDimensionDecimalPlaces`). Deliberately left out of the current richer-extraction pass rather than ship an unverified/fabricated COM call; revisit once it can be confirmed on Windows.
- **The 3D-geometry COM chain in `geometry.py`** is the largest current first-run unknown. Confirm on Windows: that a drawing `IDisplayDimension` reaches model topology via `GetAnnotation()` → `IAnnotation.GetAttachedEntities3()` (vs. a different member/signature); the `IEntity.GetType` codes (`swSelFACES=2`, `swSelEDGES=1`) and `IEdge.GetTwoAdjacentFaces2`; `IFace2.GetSurface` + `ISurface.IsCylinder/IsPlane/IsCone` and the `CylinderParams[6]` radius slot; `IFace2.GetFeature` → `IFeature.GetTypeName2` strings (e.g. `HoleWzd`, `CutExtrude`, `BossExtrude`); and the `IView.ReferencedDocument`/`GetReferencedModelName` path. Each hop is individually guarded (degrades to a field default / `geometry=None`), so wrong guesses don't crash — but the *value* needs confirming, not the silent default.
- **Hole standard / tapped-vs-clearance detail.** `geometry._safe_hole_standard` reads `IFeature.GetDefinition()` properties (`Standard`/`FastenerType`/`Size`) *without* `AccessSelections` (which would mutate selection state on a doc we may save), so a build that gates those behind AccessSelections yields `hole_standard=""` and a default `hole_clearance` kind. The `IWizardHoleFeatureData2` members and a safe read-only path to counterbore/countersink/tapped classification are unknowns — confirm on Windows. (The `text_prefix`/`text_suffix` annotation, e.g. `M6`, already carries thread signal at the `Feature` level meanwhile.)
- **Assemblies are out of scope (parts-only).** A dimension whose view references an assembly yields a reduced/`unknown` `GeometryContext` rather than an error; assembly mate analysis (a strong functional-surface signal) is a deliberate later expansion.
- **The GD&T read API in `extract.iter_geometric_tolerances`** is a first-run unknown. Confirm on Windows: the view annotation walk (`IView.GetFirstAnnotation2` → `IAnnotation.GetNext2`, `IAnnotation.GetSpecificAnnotation` to get the `IGtol`), and especially **`IGtol.GetFrameValues2(0)`** returning the first frame's text values in the assumed order (symbol, tolerance, datum1, datum2, datum3). All guarded — a wrong member yields no GD&T records rather than crashing — but the member/shape needs confirming. The decoding below the read (the `gtol_text` codec) is pure and unit-tested; only the single COM read is unverified.
- **The GD&T write API in `apply.write_geometric_tolerance`** is the largest Phase-4 first-run unknown. Confirm on Windows: selecting the dimension's geometry (`IDisplayDimension.Select2`, or the annotation's `Select3` fallback), inserting the frame (`IModelDocExtension.InsertGtol`), and populating it (`IGtol.SetFrameValues2(0, values)` — the assumed inverse of the read's `GetFrameValues2`, taking the same `[symbol, tolerance, *datums]` text array). The pure encoder `gtol_text.frame_values` is unit-tested and round-trips against the codec's own decoder, so only the COM insert/select/set members are unverified. A failure raises and the frame is logged `action: "failed"` (per-frame try/except, exit code 2) — it never aborts the dimensional write or the other frames. The text↔frame order assumption mirrors invariant #4's "set before write" discipline; if `SetFrameValues2` proves structured (enum symbol + numeric value + datum objects) rather than text, adapt the `gtol_text` codec in one place so the round-trip property holds.
