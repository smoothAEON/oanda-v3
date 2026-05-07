# Stage 02 - Settings, Secrets, And Startup Validation

> Historical stage spec. The repo now has implemented code beyond this checklist. Keep this document for design lineage, and use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for current behavior and status.

- Parent Phase: Phase 1 / P0 - Foundation Contracts And Determinism
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S02-F01`, `S02-F02`, `S02-F03`

## Purpose

Define the fail-fast configuration layer so runtime startup is deterministic, secrets are validated at boot, and mutable runtime behavior is limited to explicitly allowed settings. This stage prevents hidden defaults, silent secret issues, and environment drift.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Section 5.1, Sections 2 and 10
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 1
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 1 gate

## Scope

- Define the canonical `Settings` model with Pydantic `BaseSettings`.
- Lock required secrets and startup-fail behavior.
- Document configuration precedence and which keys may be changed at runtime.
- Specify configuration validation for file paths, environment mode, numeric thresholds, and scheduling values.

## Dependencies

- Stage 01 repository and packaging baseline.

## Implementation Checklist

- [x] `S02-F01` Define the required keys from the source plan: `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, `OANDA_ENVIRONMENT`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`, then add the bot-runtime auth keys needed for a usable Telegram bot: `TELEGRAM_BOT_PASSWORD` and operator-facing `TELEGRAM_ADMIN_IDS`.
- [x] `S02-F02` Add startup validation rules for `practice` or `live` environment selection, positive numeric settings, valid file paths, and presence of required secrets before the bot starts.
- [x] `S02-F03` Define configuration precedence as: defaults in code, `.env`, then explicit environment variables; document which values are startup-only and which may be changed at runtime through controlled admin paths.

## Trade Helper Additions

- Extend the merged settings contract with the following trade-helper keys in `config/settings.py` alongside existing analysis keys:
  - `POLL_INTERVAL_SECONDS` — integer, default `30`. Governs account-poller REST check cadence. Startup validation must reject values below `10`.
  - `STREAM_INSTRUMENTS` — comma-separated string, default matches `STREAM_INSTRUMENTS` from `.env`. Instruments subscribed to in the OANDA price stream.
  - `MAE_MFE_MIN_PIP_MOVE` — float, default `0.5`. Minimum pip delta before an excursion sample is written. Startup validation must reject values `<= 0`.
  - `INDICATOR_SCAN_INTERVAL_MINUTES` — integer, default `5`. Scheduler cadence for indicator-alert evaluation.
  - `ACCOUNT_CURRENCY` — string, default `"USD"`. Used for monetary P&L formatting in trade-open and trade-close notifications.
- All new keys follow the same frozen Pydantic model and `.env` precedence rules as existing analysis keys.
- Treat these as startup-validated runtime settings for the read-only trade-helper services. They are operational knobs, not execution controls.
- Validation must keep broker identity and all secrets startup-only while allowing documented interval and threshold defaults to load without hidden environment dependencies.

## Public Interfaces And Contracts

- `Settings` is the single public configuration contract for the codebase.
- Core source-plan required keys remain mandatory, and the full bot runtime additionally requires `TELEGRAM_BOT_PASSWORD` for `/start` authentication. `TELEGRAM_ADMIN_IDS` is the bounded admin allowlist for admin-only commands.
- The following runtime keys must exist with the source-plan defaults unless explicitly overridden:
  - `LOG_LEVEL=INFO`
  - `LOG_JSON=false`
  - `DEFAULT_CANDLE_COUNT=500`
  - `DEFAULT_SWING_LENGTH=10`
  - `RUPTURES_PENALTY=10.0`
  - `SCAN_INTERVAL_MINUTES=5`
  - `CALENDAR_REFRESH_HOURS=1`
  - `MACRO_REFRESH_HOURS=1`
  - `TINYDB_PATH=data/bot.json`
- Runtime mutation is limited to operational knobs such as scan interval or strictness-style settings exposed through approved admin commands. Secrets and provider identity are startup-only.

## Tests And Approval Evidence

- Unit tests for required-key validation and invalid-environment rejection.
- Unit tests for default loading and explicit override precedence.
- Unit tests for invalid numeric and path-like settings failing cleanly.
- Evidence that startup fails before any provider or bot handler is created when required secrets are missing.
- Supports Phase 1 approval by locking configuration determinism before provider, cache, and state work begins.

## Risks And Watchpoints

- Allowing silent defaults for required secrets would undermine every later stage.
- Overexposing mutable runtime config risks configuration drift between live memory and persisted behavior.
- Environment-specific behavior must not fork analysis contracts.

## Exit Criteria

- Required settings and default runtime keys are documented and validated.
- Startup behavior is deterministic and fails fast on misconfiguration.
- The allowed runtime-mutable settings are explicitly listed and everything else is startup-only.

## Explicit Exclusions

- No provider logic, detector logic, or scheduler logic is implemented here beyond settings contracts.
- No direct credential persistence outside `.env` and process environment handling is defined.
- No command-level configuration mutation beyond documenting permitted behavior.
