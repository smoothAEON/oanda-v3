# V3 Stage Index

> Historical stage-map document. The repo now has implemented code that only partially matches the original stage checklist. Use [tracker.md](./tracker.md) for the current implementation board and [README](../README.md) for the live runtime description.

## Purpose

This document decomposes the V3 source plan into execution-ready stages. It does not replace the governing source documents. It exists to give the implementer a stage-by-stage roadmap, dependency map, traceability matrix, the merged trade-helper lineage, and a single place to navigate the stage set.

## Source Authority

- [V3_PLAN.md](./V3_PLAN.md) is the governing specification.
- [v3_delivery_phases.md](./v3_delivery_phases.md) is the governing phase grouping.
- [v3_approval_test_spec.md](./v3_approval_test_spec.md) is the governing approval path.
- [tracker.md](./tracker.md) is the live progress board for phase, stage, and feature completion.

If any wording in the stage docs is looser than the source plan, the source plan wins.

## Implementation Status Note

- Current reality: foundations through the original Stage 13 surface ship in code.
- Current reality: historical Stage 14 is now the security and admin backlog only. It remains open and non-blocking for the core release path.
- Current reality: historical Stage 15 is retired as an active delivery stage. Its macro and refined market-hours scope now lives in Stage 18.
- Current reality: Stage 16 now owns operator and trader workflow completion. The repo ships the non-security runtime backlog from the older repos: `/dayrange`, `/pdh`, `/pdl`, `/fib`, `/tradeplan`, `/timealert`, `/listtimealerts`, `/cleartimealert`, `/exporttimealerts`, `/importtimealerts`, `/price --live`, chart modes, trade lifecycle pushes, session reminders, and read-only transaction enrichment.
- Current reality: Stage 17 is the open core-release stage for broad test inventory, CI, deployment, backup, runbooks, and release evidence.
- Current reality: Stage 18 additive enrichment now ships bounded VIX/DXY macro status, refined FX/metals market-hours handling, and scheduler refinements without changing snapshot or bundle contracts.
- Use [tracker.md](./tracker.md) for the live status board.

## Document Set

- [tracker.md](./tracker.md)
- [v3_must_not_do.md](./v3_must_not_do.md)
- [Stage 01](./v3_stages/stage_01_platform_bootstrap.md)
- [Stage 02](./v3_stages/stage_02_settings_and_startup.md)
- [Stage 03](./v3_stages/stage_03_candle_schema_and_bar_policy.md)
- [Stage 04](./v3_stages/stage_04_instrument_registry_and_market_data.md)
- [Stage 05](./v3_stages/stage_05_models_snapshots_and_state_store.md)
- [Stage 06](./v3_stages/stage_06_smc_wrapper_and_structure.md)
- [Stage 07](./v3_stages/stage_07_indicator_layer_and_tick_volume.md)
- [Stage 08](./v3_stages/stage_08_signal_gates_and_custom_detectors.md)
- [Stage 09](./v3_stages/stage_09_htf_bias_and_multitimeframe_assembly.md)
- [Stage 10](./v3_stages/stage_10_calendar_and_persistence.md)
- [Stage 11](./v3_stages/stage_11_scheduler_orchestration_and_observability.md)
- [Stage 12](./v3_stages/stage_12_chart_rendering_and_exports.md)
- [Stage 13](./v3_stages/stage_13_telegram_runtime_and_commands.md)
- [Stage 14](./v3_stages/stage_14_alerts_preferences_and_admin_controls.md)
- [Stage 15](./v3_stages/stage_15_macro_context_and_market_hours.md)
- [Stage 16](./v3_stages/stage_16_operator_and_trader_workflow_completion.md)
- [Stage 16 historical placeholder](./v3_stages/stage_16_test_ci_deployment_and_release.md)
- [Stage 17](./v3_stages/stage_17_core_release_readiness.md)
- [Stage 18](./v3_stages/stage_18_macro_context_and_market_hours.md)

## Global Hard Exclusions

These exclusions apply to every stage unless a stage explicitly narrows them further.

- `HX-01`: No FVG detection, no `smc.fvg()` usage, no FVG command, and no FVG field in any model, snapshot, bundle, or alert payload.
- `HX-02`: No execution-analysis coupling. `ExecutionProvider` and account state remain outside analysis modules.
- `HX-03`: No raw DataFrames in public state. Published contracts are typed summaries only.
- `HX-04`: No TTL-only freshness. Candle-boundary freshness remains mandatory.
- `HX-05`: No generic spread fallback. Registry metadata must define spread behavior per instrument.
- `HX-06`: No inline detector execution from command handlers. Commands read state and trigger targeted refresh only when stale or missing.
- `HX-07`: No automated trade execution, no grading, and no confidence scoring. Read-only trade-plan summaries are allowed only when derived from published state with no inline detector execution and no broker-write behavior.

## Phase Map

| Phase | Delivery Doc | Stages | Outcome |
|---|---|---|---|
| Phase 1 / P0 | [Foundation Contracts And Determinism](./v3_delivery_phases.md#phase-1---foundation-contracts-and-determinism-p0) | S01-S05 | Lock analysis and trade-helper foundation contracts, schema, provider boundaries, models, and state publication. |
| Phase 2 / P1 | [Analysis Engine And Signal Gates](./v3_delivery_phases.md#phase-2---analysis-engine-and-signal-gates-p1) | S06-S09 | Add deterministic analysis, gates, custom detectors, and HTF bias. |
| Phase 3 / P2 | [Operational Integration And Consumption](./v3_delivery_phases.md#phase-3---operational-integration-and-consumption-p2) | S10-S16 | Add persistence, scheduler, orchestration, Telegram runtime, journaling, alerts, and operator and trader workflow completion. |
| Phase 4 / P3 | [Release Readiness And Deferred Enrichment](./v3_delivery_phases.md#phase-4---release-readiness-and-deferred-enrichment-p3) | S17-S18 | Close the core release path with CI and operations evidence, then defer additive macro and refined market-hours work. |

## Stage Catalog

| Stage | Phase | Name | Primary Dependencies | Doc |
|---|---|---|---|---|
| S01 | P0 | Platform bootstrap and repo baseline | None | [Stage 01](./v3_stages/stage_01_platform_bootstrap.md) |
| S02 | P0 | Settings, secrets, and startup validation | S01 | [Stage 02](./v3_stages/stage_02_settings_and_startup.md) |
| S03 | P0 | Candle schema and bar policy | S01-S02 | [Stage 03](./v3_stages/stage_03_candle_schema_and_bar_policy.md) |
| S04 | P0 | Instrument registry, market data, and cache | S02-S03 | [Stage 04](./v3_stages/stage_04_instrument_registry_and_market_data.md) |
| S05 | P0 | Models, snapshots, and state store | S03-S04 | [Stage 05](./v3_stages/stage_05_models_snapshots_and_state_store.md) |
| S06 | P1 | SMC wrapper and structure summaries | S03-S05 | [Stage 06](./v3_stages/stage_06_smc_wrapper_and_structure.md) |
| S07 | P1 | Indicator layer and tick-volume semantics | S03-S05 | [Stage 07](./v3_stages/stage_07_indicator_layer_and_tick_volume.md) |
| S08 | P1 | Spread and chop gates plus custom detectors | S04-S07 | [Stage 08](./v3_stages/stage_08_signal_gates_and_custom_detectors.md) |
| S09 | P1 | HTF bias and multi-timeframe assembly | S05-S08 | [Stage 09](./v3_stages/stage_09_htf_bias_and_multitimeframe_assembly.md) |
| S10 | P2 | Calendar and persistence | S05-S09 | [Stage 10](./v3_stages/stage_10_calendar_and_persistence.md) |
| S11 | P2 | Scheduler, orchestration, and observability | S09-S10 | [Stage 11](./v3_stages/stage_11_scheduler_orchestration_and_observability.md) |
| S12 | P2 | Chart rendering and exports | S05-S11 | [Stage 12](./v3_stages/stage_12_chart_rendering_and_exports.md) |
| S13 | P2 | Telegram runtime and command handling | S05-S11 | [Stage 13](./v3_stages/stage_13_telegram_runtime_and_commands.md) |
| S14 | P2 | Historical security and admin backlog | S10-S13 | [Stage 14](./v3_stages/stage_14_alerts_preferences_and_admin_controls.md) |
| S15 | P2 | Retired placeholder for historical references | None | [Stage 15](./v3_stages/stage_15_macro_context_and_market_hours.md) |
| S16 | P2 | Operator and trader workflow completion | S10-S13 | [Stage 16](./v3_stages/stage_16_operator_and_trader_workflow_completion.md) |
| S17 | P3 | Test, CI, deployment, and core release readiness | S01-S16 | [Stage 17](./v3_stages/stage_17_core_release_readiness.md) |
| S18 | P3 | Macro context and market-hours refinement | S10-S11, S17 | [Stage 18](./v3_stages/stage_18_macro_context_and_market_hours.md) |

## Traceability Table

| Stage | Phase | Source Anchors | Approval Gate | Hard Exclusions | Doc |
|---|---|---|---|---|---|
| S01 | P0 | `V3_PLAN` Sections 2, 3, 4, 9, requirements appendix | Phase 1 prerequisite | HX-01 to HX-07 | [Stage 01](./v3_stages/stage_01_platform_bootstrap.md) |
| S02 | P0 | `V3_PLAN` Sections 5.1, 10; Phase 1 delivery doc | [Phase 1 gate](./v3_approval_test_spec.md#phase-1-approval-gate---foundation-contracts-and-determinism-p0) | HX-01 to HX-07 | [Stage 02](./v3_stages/stage_02_settings_and_startup.md) |
| S03 | P0 | `V3_PLAN` Sections 1.2, 1.3, 5.2, 8, 10 | Phase 1 gate | HX-01 to HX-07 | [Stage 03](./v3_stages/stage_03_candle_schema_and_bar_policy.md) |
| S04 | P0 | `V3_PLAN` Sections 1.1, 1.4, 1.5, 5.3, 5.4, 5.5, 8 | Phase 1 gate and Phase 3 provider-cache regression | HX-01 to HX-07 | [Stage 04](./v3_stages/stage_04_instrument_registry_and_market_data.md) |
| S05 | P0 | `V3_PLAN` Sections 1.8, 1.15, 5.6, 6, 8 | Phase 1 gate and Phase 3 publication-flow regression | HX-01 to HX-07 | [Stage 05](./v3_stages/stage_05_models_snapshots_and_state_store.md) |
| S06 | P1 | `V3_PLAN` Sections 1.9, 5.6, 7.1, 10 | [Phase 2 gate](./v3_approval_test_spec.md#phase-2-approval-gate---analysis-engine-and-signal-gates-p1) | HX-01 to HX-07 | [Stage 06](./v3_stages/stage_06_smc_wrapper_and_structure.md) |
| S07 | P1 | `V3_PLAN` Sections 1.6, 5.8, 8 | Phase 2 gate | HX-01 to HX-07 | [Stage 07](./v3_stages/stage_07_indicator_layer_and_tick_volume.md) |
| S08 | P1 | `V3_PLAN` Sections 1.5, 5.9, 7.1, 8 | Phase 2 gate | HX-01 to HX-07 | [Stage 08](./v3_stages/stage_08_signal_gates_and_custom_detectors.md) |
| S09 | P1 | `V3_PLAN` Sections 1.15, 5.7, 6, 7.1, 8 | Phase 2 gate and Phase 3 mixed-freshness regression | HX-01 to HX-07 | [Stage 09](./v3_stages/stage_09_htf_bias_and_multitimeframe_assembly.md) |
| S10 | P2 | `V3_PLAN` Sections 5.10, 5.11, 9, 10 | [Phase 3 gate](./v3_approval_test_spec.md#phase-3-approval-gate---operational-integration-and-consumption-p2) | HX-01 to HX-07 | [Stage 10](./v3_stages/stage_10_calendar_and_persistence.md) |
| S11 | P2 | `V3_PLAN` Sections 1.7, 5.12, 5.13, 7.1, 10 | Phase 3 gate | HX-01 to HX-07 | [Stage 11](./v3_stages/stage_11_scheduler_orchestration_and_observability.md) |
| S12 | P2 | `V3_PLAN` Sections 1.12, 3, 4, 11 | Phase 3 gate | HX-01 to HX-07 | [Stage 12](./v3_stages/stage_12_chart_rendering_and_exports.md) |
| S13 | P2 | `V3_PLAN` Sections 1.13, 7.2, 7.3, 11 | Phase 3 gate plus runtime refresh tests | HX-01 to HX-07 | [Stage 13](./v3_stages/stage_13_telegram_runtime_and_commands.md) |
| S14 | P2 | `V3_PLAN` Sections 5.11, 11; repo-specific deferred security scope | Non-blocking historical backlog | HX-01 to HX-07 | [Stage 14](./v3_stages/stage_14_alerts_preferences_and_admin_controls.md) |
| S15 | P2 | Historical reference only | None | HX-01 to HX-07 | [Stage 15](./v3_stages/stage_15_macro_context_and_market_hours.md) |
| S16 | P2 | Repo runtime completion backlog from the older bots | Phase 3 gate plus Stage 16 feature-local tests | HX-01 to HX-07 | [Stage 16](./v3_stages/stage_16_operator_and_trader_workflow_completion.md) |
| S17 | P3 | `V3_PLAN` Sections 8, 9, 10; final release checklist | Phase 4 gate and core release gate | HX-01 to HX-07 | [Stage 17](./v3_stages/stage_17_core_release_readiness.md) |
| S18 | P3 | `V3_PLAN` Sections 3, 7.1, 9, 10 | Phase 4 additive enrichment gate | HX-01 to HX-07 | [Stage 18](./v3_stages/stage_18_macro_context_and_market_hours.md) |

## Trade Helper Traceability

The original trade-helper framework is preserved here as lineage only. It does not create a second stage tree.

| Trade-Helper Stage | Merged V3 Stage(s) | Scope Carried Forward |
|---|---|---|
| `TH-S00` | `S01`, `S02` | Project scaffold, runtime layout reservations, and settings keys for the read-only trade-helper runtime |
| `TH-S01` | `S05`, `S10` | Trade, excursion, and alert model contracts plus TinyDB collection ownership |
| `TH-S02` | `S10` | Trade journal, excursion, and alert repository CRUD on TinyDB |
| `TH-S03` | `S04`, `S11` | Read-only OANDA trade polling, pricing, candle fetches for alerts, and live price streaming |
| `TH-S04` | `S05`, `S11`, `S13`, `S14` | Journal service, excursion tracking, notifier contracts, and journal-facing commands |
| `TH-S05` | `S10`, `S11`, `S13`, `S14` | Price-alert persistence, fire-once runtime rules, and Telegram command coverage |
| `TH-S06` | `S10`, `S11`, `S13`, `S14` | Indicator-alert persistence, scheduled evaluation, repeat and cooloff behavior |
| `TH-S07` | `S11`, `S13`, `S16`, `S17` | Background supervision, bot assembly, runtime completion, and release-readiness coverage |

## How To Use This Set

1. Start from the phase in [v3_delivery_phases.md](./v3_delivery_phases.md).
2. Open the matching stage doc and implement every checklist item in order.
3. Update [tracker.md](./tracker.md) when a feature, stage, or phase changes state.
4. Use [v3_approval_test_spec.md](./v3_approval_test_spec.md) as the approval authority before closing a stage.
5. Use [v3_must_not_do.md](./v3_must_not_do.md) as the plain-language guardrail summary for forbidden behavior and rejected shortcuts.
6. Reject any implementation that violates the global hard exclusions, even if it appears to move faster.
