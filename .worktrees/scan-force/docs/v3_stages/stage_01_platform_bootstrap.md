# Stage 01 - Platform Bootstrap And Repo Baseline

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 1 / P0 - Foundation Contracts And Determinism
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S01-F01`, `S01-F02`, `S01-F03`

## Purpose

Establish the repository skeleton, dependency policy, packaging baseline, and developer tooling required to build V3 without repeating the unstructured growth of prior versions. This stage exists to make every later stage build on a predictable directory layout, known packages, and reproducible local setup.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 2, 3, 4, 9, `requirements.txt` appendix
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 1
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 1 prerequisite support

## Scope

- Define the V3 directory layout named in the source plan.
- Lock the dependency list, package roles, and pin strategy around the approved stack.
- Define repository-level conventions for tests, docs, and environment bootstrap.
- Add bootstrap instructions for local development on the target workstation.

## Dependencies

- None. This is the initial stage.
- All later stages assume the repository shape and package policy created here.

## Implementation Checklist

- [x] `S01-F01` Create the top-level package structure from the source plan: `config`, `core`, `providers`, `smc`, `indicators`, `filters`, `data`, `charting`, `orchestration`, `bot`, and `tests`, plus the reserved trade-helper namespaces.
- [x] `S01-F02` Create or update packaging files so the approved dependencies from the source plan are installed consistently and unapproved replacements are not introduced.
- [x] `S01-F03` Define the bootstrap workflow: create `.env.example`, document install steps, define test entrypoints, and add import-smoke validation for the new module layout.

## Trade Helper Additions

- Reserve the following top-level packages in the canonical layout alongside the analysis packages: `journal/`, `tracking/`, `alerts/`, `notifications/`, `background/`.
- The canonical repo names remain `data/` and `charting/`; this stage does not rename the repo back to `calendar/`, `storage/`, or `charts/`.
- The Stage 01 bootstrap path documents the split install for `smartmoneyconcepts==0.0.26 --no-deps` because its published pandas metadata conflicts with the approved `pandas-ta` stack on Python 3.13.
- Each reserved package is a placeholder directory with an `__init__.py` only — no logic in this stage. Its presence in the layout prevents future name collisions and makes the intent explicit from the start.
- `test_import_smoke.py` must include these reserved namespaces in the pass list alongside the analysis packages (`config`, `core`, `providers`, `smc`, `indicators`, `filters`). A missing import in smoke tests is a layout regression.
- Keep these additions documentation-only in this stage. They are package-layout and bootstrap commitments, not implementation work.
- Import-smoke and bootstrap notes for this stage should treat the trade-helper namespaces as part of the planned canonical layout rather than as a parallel project tree.

## Public Interfaces And Contracts

- Repository layout must match the structure documented in the source plan closely enough that later stages can place modules without improvising new top-level areas.
- Dependency policy must keep these choices locked:
  - `oandapyV20` for OANDA access
  - `pydantic` and `pydantic-settings` for configuration and models
  - `structlog` for observability
  - `smartmoneyconcepts`, `TA-Lib`, `pandas-ta`, `ruptures` for analysis
  - `TinyDB`, `APScheduler`, `mplfinance`, `python-telegram-bot`, `yfinance`, `pandas_market_calendars` for runtime support
- Direct `matplotlib` chart pipelines, custom HTTP clients for OANDA, and ad hoc persistence layers remain disallowed.

## Tests And Approval Evidence

- Smoke proof that the repo layout can be imported without circular import failures.
- Environment bootstrap notes validated on a clean checkout.
- Dependency list reviewed against the source plan so no off-plan packages are required for core delivery.
- This stage does not close a named approval gate by itself, but it is required before any Phase 1 gate can pass.

## Risks And Watchpoints

- `TA-Lib` installation may vary across machines and should be documented early rather than discovered mid-implementation.
- Uncontrolled package additions here would widen scope and weaken later approval checks.
- If directory boundaries are sloppy, execution and analysis modules will mix again later.

## Exit Criteria

- The repository layout, dependency policy, and bootstrap workflow are documented and accepted.
- An implementer can create a local environment and understand where every later stage belongs.
- No bootstrap choice conflicts with the locked source-plan stack or exclusions.

## Explicit Exclusions

- No application logic, providers, detectors, state models, scheduler jobs, or Telegram commands are implemented in this stage.
- No automated execution, trade planning, grading, or confidence logic is introduced.
- No FVG-related module, placeholder, or backlog item is created.
