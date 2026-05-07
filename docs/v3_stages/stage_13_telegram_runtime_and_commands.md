# Stage 13 - Telegram Runtime And Command Handling

> Historical stage spec. Current repo reality: the Telegram runtime in `bot/` is implemented, `/help` requires authentication, and the live price-alert commands are `/pricealert`, `/listpricealerts`, and `/clearpricealert`. Admin controls from later planning docs are still not implemented. Use [README](../../README.md), [tracker.md](../tracker.md), and [COMMANDS.md](../COMMANDS.md) for the live command surface.

- Parent Phase: Phase 3 / P2 - Operational Integration And Consumption
- Tracker: [tracker.md](../tracker.md)
- Feature IDs: `S13-F01`, `S13-F02`, `S13-F03`, `S13-TH01`

## Purpose

Define the usable Telegram bot runtime on top of the analysis state layer. This stage covers authentication, session handling, command routing, refresh behavior, and async-boundary handling while preserving the rule that commands consume state rather than compute analysis inline.

## Source Anchors

- [V3_PLAN.md](../V3_PLAN.md): Sections 1.13, 7.2, 7.3, 11
- [v3_delivery_phases.md](../v3_delivery_phases.md): Phase 3
- [v3_approval_test_spec.md](../v3_approval_test_spec.md): Phase 3 gate plus runtime refresh scenarios

## Scope

- Define auth and session behavior for bot users.
- Define the command router, command groups, and argument validation rules. The full command surface and signatures are defined in [`COMMANDS.md`](../COMMANDS.md). Symbol aliases and timeframe normalization rules are defined in [`GLOSSARY.md`](../GLOSSARY.md).
- Define stale-state refresh behavior and async or sync boundary handling for provider and storage calls.

## Dependencies

- Stage 05 public state contracts.
- Stage 11 orchestrator and health outputs.
- Stage 12 chart request contract for `/chart`.

## Implementation Checklist

- [ ] `S13-F01` Implement auth and session handling so only `/start` and `/help` are available without an active session and every other command uses a shared auth gate.
- [ ] `S13-F02` Implement the command router for the command groups defined in [`COMMANDS.md`](../COMMANDS.md), using registry validation for instruments and the timeframe list from [`GLOSSARY.md`](../GLOSSARY.md#timeframe-aliases).
- [ ] `S13-F03` Implement command refresh behavior so `/bias`, `/smc`, and related commands read from `MarketStateStore` first and only trigger targeted refresh when the requested bundle or snapshot is stale or missing; all sync I/O must be wrapped honestly with `asyncio.to_thread()` from async handlers.
- [ ] `S13-TH01` Add authenticated command handlers for `/journal [trade_id] [--instrument] [--from] [--to]`, `/label <trade_id> <text>`, `/maemfe [trade_id]`, `/indicatoralert <symbol> <timeframe> <indicator> <condition> [threshold] [note]`, `/listindicators`, and `/clearindicator <id>`; wire each to its backing repository or service via `context.bot_data`; apply the same admin and auth gating as analysis commands; responses must match the canonical formats defined in the Command Response Formats section below.

## Public Interfaces And Contracts

- Command request contract
  Each command must validate auth, arguments, instrument, and timeframe before reading state.
- Command response contract
  Responses must be derived from `TimeframeSnapshot`, `InstrumentBundle`, chart artifacts, or typed operational status objects.
- Runtime settings contract
  Bot runtime must consume the Stage 02 settings model rather than direct environment reads inside handlers.

## Tests And Approval Evidence

- Refresh-path tests must prove `/bias <instrument>` follows the bundle-read then targeted-refresh path from the source walkthrough.
- Refresh-path tests must prove `/smc <instrument> [timeframe]` follows the snapshot-read then targeted-refresh path from the source walkthrough.
- Auth tests must prove only `/start` and `/help` bypass session checks and that blocking sync I/O is not performed directly inside async handlers.

## Command Response Formats

The following verbatim output formats are the required contract for journal and MAE/MFE command responses. Implementations must match these formats. Deviations require a doc update before implementation.

### /journal (list)

```text
📒 Trade Journal (last 10)

#12345678  SPX500_USD  LONG   CLOSED  +42.0 pips  2026-03-21
#12345677  EUR_USD  SHORT  CLOSED  –18.5 pips  2026-03-20
#12345676  SPX500_USD  LONG   OPEN    —           2026-03-20

Filters: none  |  /journal <id> for full detail
```

- P&L column shows `—` for open trades (not yet realised).
- Filters line reflects active `--instrument`, `--from`, `--to` args if passed.
- Maximum 10 rows by default.

### /journal \<trade_id\>

```text
📒 Trade Detail — #12345678

Instrument:   SPX500_USD
Direction:    LONG
Units:        1.00
Entry:        2341.50
Exit:         2383.50
SL:           2335.00
TP:           2383.50 (hit)
GSLO:         None

P&L:          +42.0 pips  |  +$4.20
Duration:     4h 23m
Opened:       2026-03-21 05:00:00 UTC
Closed:       2026-03-21 09:23:00 UTC
Reason:       TP HIT

MAE:          –12.0 pips
MFE:          +47.5 pips

Note:         "asia session breakout"
```

- Exit, P&L, Duration, Closed, and Reason lines are omitted for open trades.
- MAE and MFE lines are omitted if no excursion samples have been recorded yet.
- Note line is omitted if no label was set via `/label`.
- GSLO shows the guaranteed stop price if present, `None` if absent.
- Monetary P&L uses the `ACCOUNT_CURRENCY` setting.

### /maemfe (all open trades)

```text
📉 MAE / MFE — Open Trades

#12345676  SPX500_USD  LONG   MAE: –8.5 pips   MFE: +22.0 pips
#12345675  EUR_USD  SHORT  MAE: –3.0 pips   MFE: +11.5 pips

Updated live from price stream. Use /maemfe <id> for full detail.
```

### /maemfe \<trade_id\>

```text
📉 MAE/MFE — #12345676

Instrument:  SPX500_USD
Direction:   LONG
Entry:       2341.50
Current:     2349.80

MAE (worst):   –8.5 pips  (at 08:14 UTC)
MFE (best):   +22.0 pips  (at 09:01 UTC)

Samples:  47
```

- MAE timestamp is the `sampled_at` of the row with the highest `adverse_pips`.
- MFE timestamp is the `sampled_at` of the row with the highest `favorable_pips`.
- Samples count is the number of stored excursion sample rows for this trade.

## Risks And Watchpoints

- Letting commands compute analysis inline will break consistency with scheduled scans and alerts.
- Mixing sync provider calls directly into async handlers will stall the event loop.
- Weak auth boundaries will leak admin or alert behavior to unauthenticated users.

## Exit Criteria

- Core commands are routed, authenticated, and validated through a shared runtime layer.
- Commands use published state first and only refresh targeted data when needed.
- Async-boundary handling is explicit and honest for sync provider and TinyDB calls.

## Explicit Exclusions

- No automated execution, order placement, or trade management commands.
- No command may bypass the registry, candle policy, or state model.
- No `/fvg` command or other off-plan command surface is introduced.

## Trade Helper Additions

- Add handler coverage for `/journal`, `/label`, `/maemfe`, `/indicatoralert`, `/listindicators`, and `/clearindicator`, while keeping `/pricealert`, `/listalerts`, and `/clearalerts` as the canonical price-alert surface.
- Document `bot_data` dependency injection for journal services, repositories, notifier paths, alert engines, and supervisor-owned background services so handlers do not construct runtime dependencies ad hoc.
- Security rules must make the trade-helper handlers follow the same auth and admin gating as the rest of the Telegram runtime.
