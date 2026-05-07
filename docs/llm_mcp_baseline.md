# LLM-First MCP Baseline

## Purpose

This document defines the baseline direction for a simpler LLM-first version of the repo.
The bot and MCP server should act as an evidence server, not as a deterministic analyst.
The LLM should form the market opinion after inspecting broad context from MCP.

The main problem this baseline fixes is that the current runtime can over-direct the LLM
through precomputed opinions such as HTF bias, deterministic trade plans, setup validity,
target selection, invalidation selection, and reward/risk filtering. Those outputs make
the LLM depend on one narrow system view instead of checking the full picture itself.

This document is the governing design baseline for the current LLM-first MCP surface.

## Baseline Goal

The baseline system should:

- Provide complete, fresh, machine-readable evidence.
- Make missing, stale, skipped, and partial data obvious.
- Preserve read-only account, market, journal, and alert-history visibility.
- Keep compact market-structure facts as evidence, not as conclusions.
- Force the LLM workflow to inspect market-wide context before forming an instrument view.
- Remove deterministic trading opinions from the MCP decision path.

The baseline system should not:

- Tell the LLM that an instrument is bullish, bearish, valid, invalid, tradable, or rejected.
- Produce a trade recommendation, entry, target, invalidation, or reward/risk decision.
- Score a setup or expose confidence language.
- Hide stale data behind successful-looking responses.
- Add broker execution or any broker write path.

## Must Haves

The baseline must keep these evidence surfaces available to the LLM:

- Closed OHLC candles.
- Current bid/ask price reads.
- Spread state and recent spread history.
- Candle freshness and snapshot freshness.
- Market hours and session context.
- Calendar events and macro context.
- Supported instrument metadata.
- Account summary.
- Open positions and open orders.
- Journal trades, trade history, trade stats, transfers, and alert history.
- Compact market-structure facts:
  BOS/CHOCH events, latest swings, order-block or zone facts, liquidity levels,
  previous-day levels, session highs/lows, spread, freshness, and core indicator values.

The baseline must expose enough context for the LLM to reason across:

- Multiple instruments.
- Raw OANDA timeframes from `S5` through `W`, plus published evidence on `D`, `H4`,
  `H1`, and `M15`.
- Account exposure.
- Macro and calendar conditions.
- Data quality and freshness.
- Conflicting evidence.

## Remove From Baseline MCP Decision Path

These are not part of the baseline MCP surface:

- `get_htf_bias`
- `get_trade_plan`
- Deterministic HTF bias output.
- Deterministic setup validity.
- Target, invalidation, and reward/risk recommendations.
- Confidence scores, setup scores, alignment scores, or ranking language.
- Gate-driven final conclusions such as "valid", "invalid", "accepted", or "rejected".
- Pattern/opinion helpers:
  SFP, Turtle Soup, ORB, support/resistance clustering, Fibonacci ladders, and any
  deterministic trade-plan output.

The MCP tool surface removes `get_htf_bias`, `get_trade_plan`, and the related deterministic
helper tools instead of marking them as deprecated.

## Retain As Evidence, Not Opinion

Some evidence is directional by nature. For example, a BOS can break upward and an order
block can be bullish or bearish. The baseline may expose that fact, but it must not expose
a final instrument-level direction.

Allowed evidence field examples:

- `break_side`
- `zone_side`
- `liquidity_side`
- `position_side`
- `price_relation`
- `session_state`

Disallowed final-decision field examples:

- `bias`
- `direction`
- `valid`
- `setup`
- `entry`
- `target`
- `invalidation`
- `reward_risk`
- `score`
- `confidence`
- `recommendation`
- `trade_plan`

If a retained detector currently emits one of the disallowed field names, the MCP adapter
should rename it into evidence-specific language before exposing it to the LLM.
For example, a structure break's current `direction` should become `break_side` inside the
MCP response.

## Proposed MCP Flow

The baseline MCP flow uses small, composable tools. The removed context-pack tools are not
part of the active surface:

- `get_market_context_pack`
- `get_instrument_context_pack`
- `get_historical_bars`

The default sequence is:

1. Use `get_runtime_status` and `get_market_status` for scheduler, stream, market-hours,
   macro, and calendar state.
2. Use `get_candles` or `get_ohlc` for raw closed OANDA candles.
3. Use `get_price` and `get_spread_snapshot` for current bid/ask and raw spread history.
4. Use account, position, order, journal, history, stats, transfer, and alert-history tools
   for operational context.
5. Optionally use sanitized hybrid evidence tools for published scan frames only.

### Raw Candle Tools

`get_candles` and `get_ohlc` are the primary market-evidence tools.

- Instrument scope: any live OANDA account instrument from the broker catalog.
- Supported granularities: `S5`, `S10`, `S15`, `S30`, `M1`, `M2`, `M4`, `M5`, `M10`,
  `M15`, `M30`, `H1`, `H2`, `H3`, `H4`, `H6`, `H8`, `H12`, `D`, `W`.
- Monthly `M` is intentionally not supported.
- Count is capped at OANDA's 5000-candle request maximum.
- MCP raw candle reads are direct, on-demand OANDA REST calls; they do not read or
  update the analysis candle cache, CSV candles, or TinyDB freshness metadata.
- The `force` parameter is compatibility-only for raw candle reads because direct fetch
  is already the default.
- `D` and `W` use OANDA's default alignment semantics:
  `dailyAlignment=17`, `alignmentTimezone=America/New_York`, `weeklyAlignment=Friday`.

### Hybrid Evidence Tools

Sanitized published-analysis tools remain available as optional evidence:

- `get_smc_snapshot`
- `get_structure`
- `get_order_blocks`
- `get_indicators`
- `get_vwap`
- `get_session_context`
- `get_day_range`
- `get_previous_day_levels`

Published snapshot tools stay limited to the scan-published timeframes: `D`, `H4`, `H1`,
and `M15`. Seconds-level and minor raw OANDA frames are for raw candle reads only.

## LLM Workflow Contract

An LLM consuming this baseline MCP surface must follow this sequence:

1. Check runtime health, market-hours state, macro state, calendar events, and account exposure.
2. Pull raw candles/OHLC for the instrument and timeframes required by the user.
3. Check price and spread separately when current execution context matters.
4. Select instruments for deeper review based on user intent and data availability, not on
   repo-generated ranking.
5. Use sanitized hybrid evidence only as supporting context, not as a conclusion.
6. Inspect all requested timeframes before forming an opinion.
7. Separate evidence into supportive, conflicting, missing, and stale categories.
8. State uncertainty explicitly.
9. Give an opinion only after explaining the evidence used.
10. Explain what new evidence would change the opinion.
11. Avoid claiming precision when data is stale, partial, or missing key context.

The LLM answer should use language like:

- "Evidence supporting this view..."
- "Evidence against this view..."
- "Data quality concerns..."
- "I would change this view if..."

The LLM answer should not say:

- "The bot says bias is bullish."
- "The system says this setup is valid."
- "The trade plan is to enter here."
- "The score/confidence says..."

## Implementation Notes

Implementation proceeds in this order:

1. Remove context-pack tools and the redundant `get_historical_bars` alias.
2. Add raw OANDA granularity normalization for MCP candle reads and keep those reads
   direct/no-cache.
3. Keep command, alert, chart, scan, and snapshot timeframes narrow.
4. Expand provider candle policy to support OANDA `S5` through `W` and request alignment
   defaults.
5. Keep sanitized hybrid evidence tools free of final-decision field names.
6. Update active MCP docs to promote the small-tool workflow.

The implementation may keep internal structure analysis if it feeds sanitized evidence. It should
not publish internal analysis as final advice.

## Acceptance Criteria

The MD spec is accepted when it clearly defines:

- Must-have evidence surfaces.
- Opinion and helper surfaces to remove from the baseline MCP decision path.
- Raw `get_candles` and `get_ohlc` workflow.
- Retained evidence fields.
- Disallowed final-decision fields.
- Required LLM workflow.
- Future implementation and test expectations.

Future code implementation is accepted when:

- The MCP tool surface no longer exposes `get_htf_bias`.
- The MCP tool surface no longer exposes `get_trade_plan`.
- The MCP tool surface no longer exposes `get_market_context_pack`.
- The MCP tool surface no longer exposes `get_instrument_context_pack`.
- The MCP tool surface no longer exposes `get_historical_bars`.
- Raw candle tools support OANDA `S5` through `W`, reject monthly `M`, and accept live
  OANDA catalog instruments.
- Published snapshot tools reject raw-only timeframes and stay limited to scan-published frames.
- MCP analysis responses never include final-decision fields:
  `bias`, `direction`, `valid`, `setup`, `entry`, `target`, `invalidation`,
  `reward_risk`, `score`, `confidence`, `recommendation`, or `trade_plan`.
- Existing read-only boundaries remain intact.
- No broker execution path is introduced.
- No FVG support is introduced.
- No forming-candle detector output is introduced into baseline MCP evidence.
- No raw DataFrame is exposed through MCP.

## Boundaries

This baseline remains read-only. It does not place orders, modify orders, close trades,
or write to the broker.

This baseline does not add Fair Value Gap support.

This baseline does not treat alerts, journal state, or account visibility as permission
to mutate broker state.

This baseline does not remove the need for human review. It improves the evidence path so
the LLM can form a broader, better-supported opinion.
