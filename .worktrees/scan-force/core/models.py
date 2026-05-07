"""Frozen public models for Stage 05 state publication."""

from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.candle_policy import get_timeframe_delta
from core.enums import (
    AlertStatus,
    ChartMode,
    ChartRenderStyle,
    CloseReason,
    IndicatorKind,
    PendingOrderType,
    RuntimeConfigKey,
    TimeAlertKind,
    TimeAlertStatus,
    TradeState,
)
from core.instrument_registry import get_instrument_spec

Direction = Literal["BULLISH", "BEARISH", "NEUTRAL"]
BinaryDirection = Literal["BULLISH", "BEARISH"]
StructureKind = Literal["BOS", "CHOCH"]
ChopStatus = Literal["PASS", "CAUTION", "REJECT"]
ImpactLevel = Literal["LOW", "MEDIUM", "HIGH", "HOLIDAY", "UNKNOWN"]
SwingKind = Literal["HIGH", "LOW"]
SessionName = Literal["SYDNEY", "TOKYO", "LONDON", "NEW_YORK"]
OrderBlockStatus = Literal["ACTIVE", "MITIGATED"]
TradeDirection = Literal["LONG", "SHORT"]
AlertDirection = Literal["above", "below"]
IndicatorAlertCondition = Literal["above", "below", "cross_up", "cross_down"]
SchedulerRuntimeState = Literal["STOPPED", "RUNNING", "PAUSED"]
BackgroundRuntimeState = Literal["STOPPED", "RUNNING", "DEGRADED", "FAILED"]
ScanRunKind = Literal["full", "instrument_refresh", "snapshot_refresh"]
TimeAlertSchedule = Literal["once", "daily", "session"]
TimeAlertSessionName = Literal["london", "newyork", "market_open"]
TradeHistoryEventType = Literal["OPEN", "CLOSE", "PARTIAL_CLOSE"]
TradeHistoryView = Literal["all", "opened", "closed"]

OANDA_INSTRUMENT_RE = re.compile(r"^[A-Z0-9]+_[A-Z0-9]+$")


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("Datetime fields must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _to_aware_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("Datetime fields must be timezone-aware.")
    return value


def _normalize_currency(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise ValueError("Currency fields must use 3-letter alphabetic codes.")
    return normalized


def _validate_oanda_instrument_symbol(value: str, *, field_name: str = "instrument") -> str:
    candidate = str(value).strip().upper()
    if not candidate:
        raise ValueError(f"{field_name} must be a non-empty string.")
    if not OANDA_INSTRUMENT_RE.fullmatch(candidate):
        raise ValueError(
            f"{field_name} must look like an OANDA instrument symbol such as XAU_USD or SGD_JPY."
        )
    return candidate


class FrozenModel(BaseModel):
    """Shared frozen Pydantic configuration for public contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotFreshness(FrozenModel):
    """Stage 04 candle freshness metadata carried into public snapshots unchanged."""

    instrument: str
    timeframe: str
    last_completed_candle: datetime | None = None
    fetched_at: datetime | None = None
    source: str | None = None
    candle_count: int = Field(ge=0)
    is_fresh: bool
    staleness_seconds: float | None = Field(default=None, ge=0)

    _normalize_times = field_validator(
        "last_completed_candle",
        "fetched_at",
        mode="after",
    )(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "SnapshotFreshness":
        get_instrument_spec(self.instrument)
        get_timeframe_delta(self.timeframe)

        has_history = self.last_completed_candle is not None
        if has_history != (self.fetched_at is not None):
            raise ValueError(
                "last_completed_candle and fetched_at must either both be set or both be None."
            )

        if not has_history:
            if self.source is not None or self.candle_count != 0:
                raise ValueError(
                    "Empty freshness state cannot declare a source or non-zero candle count."
                )
            if self.is_fresh or self.staleness_seconds is not None:
                raise ValueError(
                    "Empty freshness state must be stale with no staleness_seconds value."
                )
            return self

        if self.source is None:
            raise ValueError("Freshness with candle history must declare a source.")
        return self


class SwingPointSummary(FrozenModel):
    """Latest swing-point context for structure summaries."""

    kind: SwingKind
    level: float
    occurred_at: datetime

    _normalize_times = field_validator("occurred_at", mode="after")(_to_utc)


class StructureBreak(FrozenModel):
    """Compact BOS or CHOCH event summary."""

    kind: StructureKind
    direction: BinaryDirection
    level: float | None = None
    occurred_at: datetime
    context: str | None = None

    _normalize_times = field_validator("occurred_at", mode="after")(_to_utc)


class StructureEventSummary(FrozenModel):
    """Latest structure state without exposing raw detector frames."""

    latest_break: StructureBreak | None = None
    recent_breaks: tuple[StructureBreak, ...] = Field(default_factory=tuple)
    latest_swing_high: SwingPointSummary | None = None
    latest_swing_low: SwingPointSummary | None = None

    @model_validator(mode="after")
    def validate_size(self) -> "StructureEventSummary":
        if len(self.recent_breaks) > 3:
            raise ValueError("StructureEventSummary may contain at most 3 recent breaks.")
        return self


class OrderBlockSummary(FrozenModel):
    """Compact order-block or zone summary."""

    direction: BinaryDirection
    upper_price: float
    lower_price: float
    created_at: datetime | None = None
    distance_pips: float | None = Field(default=None, ge=0)
    is_mitigated: bool | None = None

    _normalize_times = field_validator("created_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_bounds(self) -> "OrderBlockSummary":
        if self.upper_price < self.lower_price:
            raise ValueError("upper_price must be greater than or equal to lower_price.")
        return self


MAX_ACTIVE_ORDER_BLOCKS = 10


class ActiveZoneSummary(FrozenModel):
    """Nearest active zones published into public state."""

    order_blocks: tuple[OrderBlockSummary, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_size(self) -> "ActiveZoneSummary":
        if len(self.order_blocks) > MAX_ACTIVE_ORDER_BLOCKS:
            raise ValueError(
                f"ActiveZoneSummary may contain at most {MAX_ACTIVE_ORDER_BLOCKS} order blocks."
            )
        return self


class LiquidityLevelSummary(FrozenModel):
    """Compact liquidity-level summary."""

    side: Literal["BUY_SIDE", "SELL_SIDE", "UNKNOWN"] = "UNKNOWN"
    price: float
    occurred_at: datetime | None = None
    distance_pips: float | None = Field(default=None, ge=0)
    was_swept: bool | None = None

    _normalize_times = field_validator("occurred_at", mode="after")(_to_utc)


class LiquidityPoolSummary(FrozenModel):
    """Bounded liquidity context for a timeframe snapshot."""

    levels: tuple[LiquidityLevelSummary, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_size(self) -> "LiquidityPoolSummary":
        if len(self.levels) > 3:
            raise ValueError("LiquidityPoolSummary may contain at most 3 levels.")
        return self


class SessionSummary(FrozenModel):
    """Compact summary for a named trading session."""

    name: SessionName
    is_active: bool = False
    window_start: datetime | None = None
    window_end: datetime | None = None
    session_high: float | None = None
    session_low: float | None = None
    last_evaluated_at: datetime

    _normalize_times = field_validator(
        "window_start",
        "window_end",
        "last_evaluated_at",
        mode="after",
    )(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "SessionSummary":
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("Session windows must define both window_start and window_end.")
        if self.window_start is not None and self.window_start > self.window_end:
            raise ValueError("window_start must be less than or equal to window_end.")
        if (self.session_high is None) != (self.session_low is None):
            raise ValueError("SessionSummary must define both session_high and session_low together.")
        if self.session_high is not None and self.session_low is not None:
            if self.session_high < self.session_low:
                raise ValueError("session_high must be greater than or equal to session_low.")
        if self.is_active and self.window_end is None:
            raise ValueError("Active sessions must define a window.")
        return self


class SessionContextSummary(FrozenModel):
    """Bounded session context for a timeframe snapshot."""

    sessions: tuple[SessionSummary, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_size(self) -> "SessionContextSummary":
        if len(self.sessions) > 4:
            raise ValueError("SessionContextSummary may contain at most 4 sessions.")
        names = [session.name for session in self.sessions]
        if len(names) != len(set(names)):
            raise ValueError("SessionContextSummary session names must be unique.")
        return self


class PreviousHighLowSummary(FrozenModel):
    """Daily previous-high and previous-low context."""

    timeframe: Literal["1D"] = "1D"
    previous_high: float | None = None
    previous_low: float | None = None
    broken_high: bool = False
    broken_low: bool = False
    as_of: datetime | None = None

    _normalize_times = field_validator("as_of", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "PreviousHighLowSummary":
        has_levels = self.previous_high is not None or self.previous_low is not None
        if has_levels != (self.previous_high is not None and self.previous_low is not None):
            raise ValueError(
                "PreviousHighLowSummary must define both previous_high and previous_low together."
            )
        if has_levels and self.previous_high < self.previous_low:
            raise ValueError("previous_high must be greater than or equal to previous_low.")
        if not has_levels and (self.broken_high or self.broken_low):
            raise ValueError("Broken flags require previous high and low values.")
        if not has_levels and self.as_of is not None:
            raise ValueError("Empty previous-high/low summaries must not define as_of.")
        return self


class RetracementSummary(FrozenModel):
    """Latest retracement context for the current timeframe."""

    direction: BinaryDirection | None = None
    current_retracement_pct: float | None = None
    deepest_retracement_pct: float | None = None
    as_of: datetime | None = None

    _normalize_times = field_validator("as_of", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "RetracementSummary":
        has_values = (
            self.current_retracement_pct is not None
            or self.deepest_retracement_pct is not None
            or self.as_of is not None
        )
        if self.direction is None:
            if has_values:
                raise ValueError("Retracement values require a direction.")
            return self

        if self.current_retracement_pct is None or self.deepest_retracement_pct is None:
            raise ValueError(
                "RetracementSummary must define both current and deepest retracement values."
            )
        if self.current_retracement_pct < 0 or self.deepest_retracement_pct < 0:
            raise ValueError("Retracement percentages must be non-negative.")
        if self.deepest_retracement_pct < self.current_retracement_pct:
            raise ValueError(
                "deepest_retracement_pct must be greater than or equal to current_retracement_pct."
            )
        if self.as_of is None:
            raise ValueError("RetracementSummary must define as_of when direction is set.")
        return self


class SmcContextSummary(FrozenModel):
    """Additional Stage 06 SMC context published on snapshots."""

    sessions: SessionContextSummary = Field(default_factory=SessionContextSummary)
    previous_high_low: PreviousHighLowSummary | None = None
    retracement: RetracementSummary | None = None


class IndicatorMetric(FrozenModel):
    """Flat indicator value summary."""

    name: str
    value: float | None = None
    signal: str | None = None
    source: Literal["talib", "pandas_ta", "custom"] | None = None


class TickVolumeIndicator(FrozenModel):
    """Tick-volume indicator with explicit OTC caveat."""

    name: str
    value: float
    volume_type: Literal["tick_count"] = "tick_count"
    source: Literal["oanda_otc"] = "oanda_otc"
    caveat: str = (
        "Computed from OANDA tick count, not exchange-traded volume. "
        "Not equivalent to CME/NYSE volume. Reflects broker tick activity only."
    )


class IndicatorValueSummary(FrozenModel):
    """Flattened indicator summary for published snapshots."""

    metrics: tuple[IndicatorMetric, ...] = Field(default_factory=tuple)
    tick_volume_metrics: tuple[TickVolumeIndicator, ...] = Field(default_factory=tuple)


class SpreadResult(FrozenModel):
    """Registry-driven spread evaluation output."""

    instrument: str
    raw_spread: float = Field(ge=0)
    spread_pips: float = Field(ge=0)
    pip_size: float = Field(gt=0)
    typical_spread_pips: float = Field(gt=0)
    max_spread_pips: float = Field(gt=0)
    is_acceptable: bool
    is_spiking: bool
    spread_ratio: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_instrument(self) -> "SpreadResult":
        get_instrument_spec(self.instrument)
        if self.max_spread_pips < self.typical_spread_pips:
            raise ValueError("max_spread_pips must be greater than or equal to typical_spread_pips.")
        return self


class ChopResult(FrozenModel):
    """Narrow chop-gate output contract."""

    status: ChopStatus
    reason: str
    metric_name: str | None = None
    metric_value: float | None = None
    threshold: float | None = None


class SFPResult(FrozenModel):
    """Swing-failure-pattern summary."""

    detected: bool = False
    direction: BinaryDirection | None = None
    reference_level: float | None = None
    reference_time: datetime | None = None
    sweep_price: float | None = None
    close_price: float | None = None
    occurred_at: datetime | None = None

    _normalize_times = field_validator("reference_time", "occurred_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_detected_shape(self) -> "SFPResult":
        required_fields = (
            self.direction,
            self.reference_level,
            self.reference_time,
            self.sweep_price,
            self.close_price,
            self.occurred_at,
        )
        if self.detected and any(field is None for field in required_fields):
            raise ValueError("Detected SFP events must declare a complete payload.")
        if not self.detected and any(field is not None for field in required_fields):
            raise ValueError("Non-detected SFP results must not declare event fields.")
        return self


class TurtleSoupResult(FrozenModel):
    """Turtle Soup reversal-pattern summary."""

    detected: bool = False
    direction: BinaryDirection | None = None
    reference_level: float | None = None
    reference_time: datetime | None = None
    lookback_bars: int | None = Field(default=None, ge=1)
    sweep_price: float | None = None
    close_price: float | None = None
    occurred_at: datetime | None = None

    _normalize_times = field_validator("reference_time", "occurred_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_detected_shape(self) -> "TurtleSoupResult":
        required_fields = (
            self.direction,
            self.reference_level,
            self.reference_time,
            self.lookback_bars,
            self.sweep_price,
            self.close_price,
            self.occurred_at,
        )
        if self.detected and any(field is None for field in required_fields):
            raise ValueError("Detected Turtle Soup events must declare a complete payload.")
        if not self.detected and any(field is not None for field in required_fields):
            raise ValueError("Non-detected Turtle Soup results must not declare event fields.")
        return self


class ORBResult(FrozenModel):
    """Opening-range breakout summary."""

    detected: bool = False
    direction: BinaryDirection | None = None
    session: SessionName | None = None
    range_high: float | None = None
    range_low: float | None = None
    breakout_price: float | None = None
    occurred_at: datetime | None = None

    _normalize_times = field_validator("occurred_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_detected_shape(self) -> "ORBResult":
        required_fields = (
            self.direction,
            self.session,
            self.range_high,
            self.range_low,
            self.breakout_price,
            self.occurred_at,
        )
        if self.detected and any(field is None for field in required_fields):
            raise ValueError("Detected ORB events must declare a complete payload.")
        if not self.detected and any(field is not None for field in required_fields):
            raise ValueError("Non-detected ORB results must not declare event fields.")
        if self.range_high is not None and self.range_low is not None:
            if self.range_high < self.range_low:
                raise ValueError("range_high must be greater than or equal to range_low.")
        return self


class RegimeChangepoint(FrozenModel):
    """Compact changepoint summary produced by HTF bias analysis."""

    timeframe: str
    changepoint_index: int = Field(ge=0)
    changepoint_time: datetime
    method: str
    bars_ago: int | None = Field(default=None, ge=0)

    _normalize_times = field_validator("changepoint_time", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_timeframe(self) -> "RegimeChangepoint":
        get_timeframe_delta(self.timeframe)
        return self


class HTFBiasResult(FrozenModel):
    """Pinned higher-timeframe bias output published at bundle level."""

    direction: Direction = "NEUTRAL"
    alignment_score: float = Field(default=0.0, ge=0, le=1)
    timeframe_votes: dict[str, Direction] = Field(default_factory=dict)
    structure_breaks: tuple[StructureBreak, ...] = Field(default_factory=tuple)
    regime_changepoints: tuple[RegimeChangepoint, ...] = Field(default_factory=tuple)
    is_transitioning: bool = False
    last_changepoint_bars_ago: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_votes(self) -> "HTFBiasResult":
        for timeframe in self.timeframe_votes:
            get_timeframe_delta(timeframe)
        if not self.regime_changepoints and self.last_changepoint_bars_ago is not None:
            raise ValueError(
                "last_changepoint_bars_ago requires at least one regime changepoint."
            )
        return self


class CalendarEvent(FrozenModel):
    """Typed calendar event contract for bundle publication."""

    title: str
    event_time: datetime
    impact: ImpactLevel = "UNKNOWN"
    currency: str | None = None
    country: str | None = None
    is_blackout: bool = False
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None

    _normalize_times = field_validator("event_time", mode="after")(_to_utc)


class OrderBlockRecord(FrozenModel):
    """Instrument-level order-block tracker record."""

    id: str
    instrument: str
    timeframe: str
    direction: BinaryDirection
    upper_price: float
    lower_price: float
    created_at: datetime
    status: OrderBlockStatus
    mitigated_at: datetime | None = None
    source_snapshot_version: int = Field(gt=0)
    last_analyzed_close: float

    _normalize_times = field_validator("created_at", "mitigated_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "OrderBlockRecord":
        get_instrument_spec(self.instrument)
        get_timeframe_delta(self.timeframe)
        if self.upper_price < self.lower_price:
            raise ValueError("upper_price must be greater than or equal to lower_price.")
        if self.status == "ACTIVE" and self.mitigated_at is not None:
            raise ValueError("Active order blocks must not define mitigated_at.")
        if self.status == "MITIGATED" and self.mitigated_at is None:
            raise ValueError("Mitigated order blocks must define mitigated_at.")
        return self


class InstrumentOrderBlockTracker(FrozenModel):
    """Cross-timeframe order-block tracker for a single instrument."""

    instrument: str
    tracker_version: int = Field(default=0, ge=0)
    created_at: datetime
    records: tuple[OrderBlockRecord, ...] = Field(default_factory=tuple)
    source_snapshot_versions: dict[str, int] = Field(default_factory=dict)

    _normalize_times = field_validator("created_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "InstrumentOrderBlockTracker":
        get_instrument_spec(self.instrument)
        for timeframe, version in self.source_snapshot_versions.items():
            get_timeframe_delta(timeframe)
            if version <= 0:
                raise ValueError("source_snapshot_versions must contain positive integers.")

        seen_ids: set[str] = set()
        for record in self.records:
            if record.instrument != self.instrument:
                raise ValueError("Tracker records must match the tracker instrument.")
            if record.id in seen_ids:
                raise ValueError("Tracker records must have unique ids.")
            seen_ids.add(record.id)

            expected_version = self.source_snapshot_versions.get(record.timeframe)
            if expected_version is None:
                raise ValueError(
                    "Every tracker record timeframe must appear in source_snapshot_versions."
                )
            if record.source_snapshot_version != expected_version:
                raise ValueError(
                    "Tracker record snapshot versions must match source_snapshot_versions."
                )
        return self


class TradeRecord(FrozenModel):
    """Typed trade-history contract for the read-only runtime."""

    trade_id: str
    instrument: str
    units: float
    open_price: float = Field(gt=0)
    close_price: float | None = Field(default=None, gt=0)
    sl_price: float | None = Field(default=None, gt=0)
    tp_price: float | None = Field(default=None, gt=0)
    gslo_price: float | None = Field(default=None, gt=0)
    state: TradeState
    close_reason: CloseReason | None = None
    pips: float | None = None
    instrument_pnl: float | None = None
    instrument_pnl_currency: str | None = None
    account_pnl: float | None = None
    account_currency: str | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    notes: str | None = None

    _normalize_times = field_validator("opened_at", "closed_at", mode="after")(_to_utc)
    _normalize_currencies = field_validator(
        "instrument_pnl_currency",
        "account_currency",
        mode="after",
    )(_normalize_currency)

    @property
    def direction(self) -> TradeDirection:
        return "LONG" if self.units > 0 else "SHORT"

    @model_validator(mode="after")
    def validate_contract(self) -> "TradeRecord":
        _validate_oanda_instrument_symbol(self.instrument)

        if not self.trade_id.strip():
            raise ValueError("trade_id must be a non-empty string.")
        if self.units == 0:
            raise ValueError("units must be non-zero.")

        close_only_fields = (
            self.close_price,
            self.close_reason,
            self.pips,
            self.instrument_pnl,
            self.instrument_pnl_currency,
            self.account_pnl,
            self.account_currency,
            self.closed_at,
        )
        close_fields_present = any(value is not None for value in close_only_fields)

        if self.state == TradeState.OPEN:
            if close_fields_present:
                raise ValueError("OPEN trades must not define close-only fields.")
            return self

        required_close_fields = (
            self.close_price,
            self.close_reason,
            self.pips,
            self.instrument_pnl,
            self.instrument_pnl_currency,
            self.account_pnl,
            self.account_currency,
            self.closed_at,
        )
        if any(value is None for value in required_close_fields):
            raise ValueError("CLOSED trades must define close price, close metadata, and P&L.")
        if self.closed_at is not None and self.closed_at < self.opened_at:
            raise ValueError("closed_at cannot be earlier than opened_at.")
        return self


class TradeHistoryEvent(FrozenModel):
    """Normalized transaction-backed trade lifecycle event."""

    event_id: str
    transaction_id: str
    batch_id: str | None = None
    event_type: TradeHistoryEventType
    account_id: str
    instrument: str
    trade_id: str
    order_id: str | None = None
    units: Decimal
    abs_units: Decimal = Field(ge=0)
    side: TradeDirection
    price: Decimal | None = None
    realized_pl: Decimal = Field(default=Decimal("0"))
    financing: Decimal = Field(default=Decimal("0"))
    commission: Decimal = Field(default=Decimal("0"))
    net_realized_pl: Decimal = Field(default=Decimal("0"))
    time_utc: datetime
    time_local: datetime
    reason: str | None = None
    raw_json: str | None = None

    _normalize_times = field_validator("time_utc", mode="after")(_to_utc)
    _preserve_local_time = field_validator("time_local", mode="after")(_to_aware_datetime)

    @model_validator(mode="after")
    def validate_contract(self) -> "TradeHistoryEvent":
        _validate_oanda_instrument_symbol(self.instrument)
        if not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string.")
        if not self.transaction_id.strip():
            raise ValueError("transaction_id must be a non-empty string.")
        if not self.account_id.strip():
            raise ValueError("account_id must be a non-empty string.")
        if not self.trade_id.strip():
            raise ValueError("trade_id must be a non-empty string.")
        if self.units == 0:
            raise ValueError("units must be non-zero.")
        if self.abs_units != abs(self.units):
            raise ValueError("abs_units must equal abs(units).")
        if self.side == "LONG" and self.units <= 0:
            raise ValueError("LONG events must use positive units.")
        if self.side == "SHORT" and self.units >= 0:
            raise ValueError("SHORT events must use negative units.")
        return self


class FinancingEvent(FrozenModel):
    """Normalized daily financing record derived from OANDA transactions."""

    event_id: str
    transaction_id: str
    event_type: Literal["DAILY_FINANCING"] = "DAILY_FINANCING"
    account_id: str
    instrument: str | None = None
    financing: Decimal = Field(default=Decimal("0"))
    time_utc: datetime
    time_local: datetime
    raw_json: str | None = None

    _normalize_times = field_validator("time_utc", mode="after")(_to_utc)
    _preserve_local_time = field_validator("time_local", mode="after")(_to_aware_datetime)

    @model_validator(mode="after")
    def validate_contract(self) -> "FinancingEvent":
        if not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string.")
        if not self.transaction_id.strip():
            raise ValueError("transaction_id must be a non-empty string.")
        if not self.account_id.strip():
            raise ValueError("account_id must be a non-empty string.")
        if self.instrument is not None:
            _validate_oanda_instrument_symbol(self.instrument)
        return self


class TradeHistorySyncState(FrozenModel):
    """Incremental trade-history sync watermark."""

    account_id: str
    last_transaction_id: str
    last_sync_utc: datetime

    _normalize_times = field_validator("last_sync_utc", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "TradeHistorySyncState":
        if not self.account_id.strip():
            raise ValueError("account_id must be a non-empty string.")
        if not self.last_transaction_id.strip():
            raise ValueError("last_transaction_id must be a non-empty string.")
        return self


class RealizedPnLSummary(FrozenModel):
    """Realized PnL aggregate over a resolved trade-history window."""

    period: str
    instrument: str | None = None
    start_utc: datetime
    end_utc: datetime
    start_local: datetime
    end_local: datetime
    gross_realized_pl: Decimal = Field(default=Decimal("0"))
    financing: Decimal = Field(default=Decimal("0"))
    commission: Decimal = Field(default=Decimal("0"))
    net_realized_pl: Decimal = Field(default=Decimal("0"))

    _normalize_times = field_validator("start_utc", "end_utc", mode="after")(_to_utc)
    _preserve_local_times = field_validator(
        "start_local",
        "end_local",
        mode="after",
    )(_to_aware_datetime)

    @model_validator(mode="after")
    def validate_contract(self) -> "RealizedPnLSummary":
        if not self.period.strip():
            raise ValueError("period must be a non-empty string.")
        if self.instrument is not None:
            _validate_oanda_instrument_symbol(self.instrument)
        if self.end_utc < self.start_utc:
            raise ValueError("end_utc must be greater than or equal to start_utc.")
        if self.end_local < self.start_local:
            raise ValueError("end_local must be greater than or equal to start_local.")
        expected = self.gross_realized_pl + self.financing - self.commission
        if self.net_realized_pl != expected:
            raise ValueError("net_realized_pl must equal gross_realized_pl + financing - commission.")
        return self


class TradeHistoryPage(FrozenModel):
    """Paged `/tradehistory` response contract."""

    period: str
    view: TradeHistoryView
    instrument: str | None = None
    window_start_utc: datetime
    window_end_utc: datetime
    window_start_local: datetime
    window_end_local: datetime
    summary: RealizedPnLSummary
    rows: tuple[TradeHistoryEvent, ...] = Field(default_factory=tuple)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_rows: int = Field(ge=0)
    total_pages: int = Field(ge=1)
    stale_warning: str | None = None

    _normalize_times = field_validator("window_start_utc", "window_end_utc", mode="after")(_to_utc)
    _preserve_local_times = field_validator(
        "window_start_local",
        "window_end_local",
        mode="after",
    )(_to_aware_datetime)

    @model_validator(mode="after")
    def validate_contract(self) -> "TradeHistoryPage":
        if not self.period.strip():
            raise ValueError("period must be a non-empty string.")
        if self.instrument is not None:
            _validate_oanda_instrument_symbol(self.instrument)
        if self.window_end_utc < self.window_start_utc:
            raise ValueError("window_end_utc must be greater than or equal to window_start_utc.")
        if self.window_end_local < self.window_start_local:
            raise ValueError("window_end_local must be greater than or equal to window_start_local.")
        if self.total_pages < 1:
            raise ValueError("total_pages must be greater than or equal to 1.")
        return self


class PendingOrder(FrozenModel):
    """Typed pending-order contract for the read-only runtime."""

    order_id: str
    instrument: str | None = None
    units: float | None = None
    price: float = Field(gt=0)
    order_type: PendingOrderType
    state: Literal["PENDING"] = "PENDING"
    time_in_force: str | None = None
    position_fill: str | None = None
    trigger_condition: str | None = None
    trade_id: str | None = None
    stop_loss_price: float | None = Field(default=None, gt=0)
    take_profit_price: float | None = Field(default=None, gt=0)
    gslo_price: float | None = Field(default=None, gt=0)
    created_at: datetime

    _normalize_times = field_validator("created_at", mode="after")(_to_utc)
    _normalize_strings = field_validator(
        "state",
        "order_type",
        "time_in_force",
        "position_fill",
        "trigger_condition",
        mode="before",
    )(lambda value: value.strip().upper() if isinstance(value, str) else value)

    @property
    def direction(self) -> TradeDirection | None:
        if self.units is None:
            return None
        return "LONG" if self.units > 0 else "SHORT"

    @property
    def is_risk_order(self) -> bool:
        return self.order_type in {
            PendingOrderType.TAKE_PROFIT,
            PendingOrderType.STOP_LOSS,
            PendingOrderType.GUARANTEED_STOP_LOSS,
        }

    @model_validator(mode="after")
    def validate_contract(self) -> "PendingOrder":
        if self.instrument is not None:
            get_instrument_spec(self.instrument)
        elif not self.is_risk_order:
            raise ValueError("instrument is required for non-risk orders.")

        if not self.order_id.strip():
            raise ValueError("order_id must be a non-empty string.")
        if self.units == 0:
            raise ValueError("units must be non-zero.")
        if self.time_in_force is not None and not self.time_in_force.strip():
            raise ValueError("time_in_force must be a non-empty string when provided.")
        if self.position_fill is not None and not self.position_fill.strip():
            raise ValueError("position_fill must be a non-empty string when provided.")
        if self.trigger_condition is not None and not self.trigger_condition.strip():
            raise ValueError("trigger_condition must be a non-empty string when provided.")
        return self


class ExcursionSample(FrozenModel):
    """Persisted MAE/MFE sample contract."""

    trade_id: str
    sampled_at: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    adverse_pips: float = Field(ge=0)
    favorable_pips: float = Field(ge=0)

    _normalize_times = field_validator("sampled_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "ExcursionSample":
        if not self.trade_id.strip():
            raise ValueError("trade_id must be a non-empty string.")
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid.")
        return self


class PriceAlert(FrozenModel):
    """Typed fire-once price-alert contract."""

    id: int = Field(gt=0)
    instrument: str
    target_price: float = Field(gt=0)
    direction: AlertDirection
    status: AlertStatus
    armed: bool = False
    chat_id: int
    notes: str | None = None
    created_at: datetime
    fired_at: datetime | None = None

    _normalize_times = field_validator("created_at", "fired_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "PriceAlert":
        get_instrument_spec(self.instrument)
        if self.status == AlertStatus.FIRED and self.fired_at is None:
            raise ValueError("FIRED alerts must define fired_at.")
        if self.status != AlertStatus.FIRED and self.fired_at is not None:
            raise ValueError("Only FIRED alerts may define fired_at.")
        if self.status != AlertStatus.PENDING and self.armed:
            raise ValueError("Only pending alerts may remain armed.")
        return self


class IndicatorAlert(FrozenModel):
    """Typed scheduled indicator-alert contract."""

    id: int = Field(gt=0)
    instrument: str
    granularity: str
    indicator: IndicatorKind
    condition: IndicatorAlertCondition
    threshold: float | None = None
    status: AlertStatus
    repeat: bool = False
    cooloff_minutes: int | None = Field(default=None, gt=0)
    chat_id: int
    notes: str | None = None
    created_at: datetime
    fired_at: datetime | None = None

    _normalize_times = field_validator("created_at", "fired_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "IndicatorAlert":
        get_instrument_spec(self.instrument)
        get_timeframe_delta(self.granularity)

        threshold_required = self.condition in {"above", "below"}
        if threshold_required and self.threshold is None:
            raise ValueError("Threshold alerts must define a threshold.")
        if not threshold_required and self.threshold is not None:
            raise ValueError("Cross alerts must not define a threshold.")
        if self.cooloff_minutes is not None and not self.repeat:
            raise ValueError("cooloff_minutes is only valid when repeat is enabled.")
        if self.status == AlertStatus.FIRED and self.fired_at is None:
            raise ValueError("FIRED alerts must define fired_at.")
        if self.status != AlertStatus.FIRED and self.fired_at is not None:
            raise ValueError("Only FIRED alerts may define fired_at.")
        return self


class IndicatorAlertEvaluationCursor(FrozenModel):
    """Persisted per-(instrument, timeframe) alert evaluation boundary."""

    instrument: str
    granularity: str
    last_evaluated_candle: datetime
    updated_at: datetime

    _normalize_times = field_validator(
        "last_evaluated_candle",
        "updated_at",
        mode="after",
    )(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "IndicatorAlertEvaluationCursor":
        get_instrument_spec(self.instrument)
        get_timeframe_delta(self.granularity)
        if self.updated_at < self.last_evaluated_candle:
            raise ValueError("updated_at must be greater than or equal to last_evaluated_candle.")
        return self


class TimeAlert(FrozenModel):
    """Typed chat-scoped reminder contract."""

    id: int = Field(gt=0)
    chat_id: int
    kind: TimeAlertKind
    status: TimeAlertStatus = TimeAlertStatus.ACTIVE
    schedule: TimeAlertSchedule
    timezone_name: str = "Asia/Singapore"
    local_time: str | None = None
    session_name: TimeAlertSessionName | None = None
    note: str | None = None
    created_at: datetime
    next_fire_at: datetime | None = None
    last_fired_at: datetime | None = None

    _normalize_times = field_validator(
        "created_at",
        "next_fire_at",
        "last_fired_at",
        mode="after",
    )(_to_utc)

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("timezone_name must be a non-empty string.")
        return normalized

    @field_validator("local_time")
    @classmethod
    def validate_local_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if len(normalized) != 5 or normalized[2] != ":":
            raise ValueError("local_time must use HH:MM format.")
        hours_text, minutes_text = normalized.split(":", maxsplit=1)
        if not hours_text.isdigit() or not minutes_text.isdigit():
            raise ValueError("local_time must use HH:MM format.")
        hours = int(hours_text)
        minutes = int(minutes_text)
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            raise ValueError("local_time must use a valid 24-hour HH:MM value.")
        return f"{hours:02d}:{minutes:02d}"

    @model_validator(mode="after")
    def validate_contract(self) -> "TimeAlert":
        if self.kind == TimeAlertKind.FIXED_TIME:
            if self.local_time is None:
                raise ValueError("Fixed-time alerts must define local_time.")
            if self.session_name is not None:
                raise ValueError("Fixed-time alerts must not define session_name.")
            if self.schedule not in {"once", "daily"}:
                raise ValueError("Fixed-time alerts support once or daily schedules only.")
        else:
            if self.session_name is None:
                raise ValueError("Session alerts must define session_name.")
            if self.local_time is not None:
                raise ValueError("Session alerts must not define local_time.")
            if self.schedule != "session":
                raise ValueError("Session alerts must use the session schedule.")

        if self.status == TimeAlertStatus.ACTIVE and self.next_fire_at is None:
            raise ValueError("Active time alerts must define next_fire_at.")
        if self.status == TimeAlertStatus.COMPLETED:
            if self.last_fired_at is None:
                raise ValueError("Completed time alerts must define last_fired_at.")
            if self.next_fire_at is not None:
                raise ValueError("Completed time alerts must not define next_fire_at.")
        return self


class FibLevel(FrozenModel):
    """One bounded Fibonacci level."""

    label: str
    ratio: float = Field(ge=0, le=1)
    price: float = Field(gt=0)


class FibSummary(FrozenModel):
    """Compact Fibonacci ladder built from published swing context."""

    instrument: str
    timeframe: str
    direction: BinaryDirection
    anchor_high: float = Field(gt=0)
    anchor_low: float = Field(gt=0)
    current_price: float | None = Field(default=None, gt=0)
    levels: tuple[FibLevel, ...] = Field(default_factory=tuple)
    as_of: datetime

    _normalize_times = field_validator("as_of", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "FibSummary":
        get_instrument_spec(self.instrument)
        get_timeframe_delta(self.timeframe)
        if self.anchor_high < self.anchor_low:
            raise ValueError("anchor_high must be greater than or equal to anchor_low.")
        if not self.levels:
            raise ValueError("FibSummary must contain at least one level.")
        return self


class TradePlanSummary(FrozenModel):
    """Bounded read-only trade-plan summary derived from published state."""

    instrument: str
    direction: TradeDirection | None = None
    valid: bool = False
    setup: str | None = None
    trigger_timeframe: str | None = None
    entry_low: float | None = Field(default=None, gt=0)
    entry_high: float | None = Field(default=None, gt=0)
    invalidation_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    reward_risk: float | None = Field(default=None, gt=0)
    rationale: tuple[str, ...] = Field(default_factory=tuple)
    rejection_reasons: tuple[str, ...] = Field(default_factory=tuple)
    computed_at: datetime

    _normalize_times = field_validator("computed_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "TradePlanSummary":
        get_instrument_spec(self.instrument)
        if self.valid:
            required = (
                self.direction,
                self.setup,
                self.trigger_timeframe,
                self.entry_low,
                self.entry_high,
                self.invalidation_price,
                self.target_price,
                self.reward_risk,
            )
            if any(value is None for value in required):
                raise ValueError("Valid trade plans must define direction, setup, levels, and RR.")
            if self.entry_high < self.entry_low:
                raise ValueError("entry_high must be greater than or equal to entry_low.")
            if self.rejection_reasons:
                raise ValueError("Valid trade plans must not define rejection_reasons.")
        elif not self.rejection_reasons:
            raise ValueError("Invalid trade plans must define rejection_reasons.")
        return self


class TimeframeSnapshot(FrozenModel):
    """Immutable per-(instrument, timeframe) published state."""

    instrument: str
    timeframe: str
    version: int = Field(default=0, ge=0)
    last_completed_candle: datetime
    computed_at: datetime
    candle_range_start: datetime
    candle_range_end: datetime
    indicators: IndicatorValueSummary
    structure: StructureEventSummary
    zones: ActiveZoneSummary
    liquidity: LiquidityPoolSummary
    smc_context: SmcContextSummary = Field(default_factory=SmcContextSummary)
    spread: SpreadResult
    chop: ChopResult
    sfp: SFPResult = Field(default_factory=SFPResult)
    turtle_soup: TurtleSoupResult = Field(default_factory=TurtleSoupResult)
    orb: ORBResult | None = None
    freshness: SnapshotFreshness

    _normalize_times = field_validator(
        "last_completed_candle",
        "computed_at",
        "candle_range_start",
        "candle_range_end",
        mode="after",
    )(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "TimeframeSnapshot":
        get_instrument_spec(self.instrument)
        get_timeframe_delta(self.timeframe)

        if self.last_completed_candle != self.candle_range_end:
            raise ValueError("last_completed_candle must match candle_range_end.")
        if self.candle_range_start > self.candle_range_end:
            raise ValueError("candle_range_start must be less than or equal to candle_range_end.")
        if self.computed_at < self.last_completed_candle:
            raise ValueError("computed_at cannot be earlier than last_completed_candle.")
        if self.freshness.instrument != self.instrument:
            raise ValueError("Snapshot freshness instrument must match the snapshot instrument.")
        if self.freshness.timeframe != self.timeframe:
            raise ValueError("Snapshot freshness timeframe must match the snapshot timeframe.")
        if self.freshness.last_completed_candle != self.last_completed_candle:
            raise ValueError(
                "Snapshot freshness last_completed_candle must match the snapshot boundary."
            )
        if self.spread.instrument != self.instrument:
            raise ValueError("SpreadResult instrument must match the snapshot instrument.")
        if self.timeframe != "M15" and self.orb is not None:
            raise ValueError("ORB results may only be published on M15 snapshots.")
        return self


class InstrumentBundle(FrozenModel):
    """Immutable bundle that pins exact timeframe snapshot versions."""

    instrument: str
    bundle_version: int = Field(default=0, ge=0)
    created_at: datetime
    members: dict[str, int]
    htf_bias: HTFBiasResult
    calendar: tuple[CalendarEvent, ...] = Field(default_factory=tuple)
    calendar_version: int = Field(default=0, ge=0)
    mixed_freshness: bool
    stalest_timeframe: str | None = None
    stalest_age_seconds: float = Field(ge=0)
    member_freshness: dict[str, SnapshotFreshness]

    _normalize_times = field_validator("created_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "InstrumentBundle":
        get_instrument_spec(self.instrument)
        if not self.members:
            raise ValueError("InstrumentBundle must pin at least one member snapshot.")
        if set(self.members) != set(self.member_freshness):
            raise ValueError("member_freshness keys must match pinned member timeframes.")

        stale_detected = False
        max_staleness = 0.0
        max_timeframe: str | None = None
        for timeframe, version in self.members.items():
            get_timeframe_delta(timeframe)
            if version <= 0:
                raise ValueError("Pinned snapshot versions must be positive integers.")

            freshness = self.member_freshness[timeframe]
            if freshness.instrument != self.instrument:
                raise ValueError("member_freshness instrument must match the bundle instrument.")
            if freshness.timeframe != timeframe:
                raise ValueError("member_freshness timeframe must match its pinned member key.")

            staleness = freshness.staleness_seconds or 0.0
            if not freshness.is_fresh:
                stale_detected = True
            if staleness > max_staleness:
                max_staleness = staleness
                max_timeframe = timeframe

        if self.mixed_freshness != stale_detected:
            raise ValueError("mixed_freshness must reflect member freshness state.")
        if self.stalest_timeframe is not None and self.stalest_timeframe not in self.members:
            raise ValueError("stalest_timeframe must be one of the pinned bundle members.")
        if self.stalest_age_seconds != max_staleness:
            raise ValueError("stalest_age_seconds must match the maximum member staleness.")
        if max_staleness == 0.0 and self.stalest_timeframe is not None:
            raise ValueError("stalest_timeframe must be None when all members are fresh.")
        if max_staleness > 0.0 and self.stalest_timeframe != max_timeframe:
            raise ValueError("stalest_timeframe must identify the stalest pinned member.")
        return self


class SchedulerJobStatus(FrozenModel):
    """Typed health state for one scheduler-managed job."""

    job_id: str
    is_paused: bool = False
    is_running: bool = False
    pending_rerun: bool = False
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    last_succeeded_at: datetime | None = None
    last_failed_at: datetime | None = None
    next_run_at: datetime | None = None
    last_error: str | None = None

    _normalize_times = field_validator(
        "last_started_at",
        "last_completed_at",
        "last_succeeded_at",
        "last_failed_at",
        "next_run_at",
        mode="after",
    )(_to_utc)


class SchedulerStatus(FrozenModel):
    """Typed scheduler lifecycle and per-job state."""

    state: SchedulerRuntimeState
    timezone: str = "UTC"
    started_at: datetime | None = None
    paused_at: datetime | None = None
    jobs: tuple[SchedulerJobStatus, ...] = Field(default_factory=tuple)

    _normalize_times = field_validator("started_at", "paused_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_jobs(self) -> "SchedulerStatus":
        job_ids = [job.job_id for job in self.jobs]
        if len(job_ids) != len(set(job_ids)):
            raise ValueError("SchedulerStatus jobs must have unique ids.")
        if self.state != "PAUSED" and self.paused_at is not None:
            raise ValueError("paused_at may only be set when the scheduler is paused.")
        return self


class ScanCycleStatus(FrozenModel):
    """Typed result for the latest orchestrated scan cycle."""

    run_kind: ScanRunKind
    started_at: datetime | None = None
    completed_at: datetime | None = None
    requested_instruments: tuple[str, ...] = Field(default_factory=tuple)
    scanned_instruments: tuple[str, ...] = Field(default_factory=tuple)
    snapshots_published: int = Field(default=0, ge=0)
    bundles_published: int = Field(default=0, ge=0)
    skipped_reason: str | None = None
    errors: tuple[str, ...] = Field(default_factory=tuple)

    _normalize_times = field_validator("started_at", "completed_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_instruments(self) -> "ScanCycleStatus":
        for instrument in self.requested_instruments:
            get_instrument_spec(instrument)
        for instrument in self.scanned_instruments:
            get_instrument_spec(instrument)
        return self


class CalendarRefreshStatus(FrozenModel):
    """Typed calendar refresh health surface."""

    last_attempted_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    calendar_version: int = Field(default=0, ge=0)
    event_count: int = Field(default=0, ge=0)
    next_high_impact: datetime | None = None
    used_cached: bool = False
    last_error: str | None = None

    _normalize_times = field_validator(
        "last_attempted_at",
        "last_refreshed_at",
        "next_high_impact",
        mode="after",
    )(_to_utc)


class MacroIndicatorStatus(FrozenModel):
    """One bounded macro reading sourced from yfinance."""

    name: str
    symbol: str
    value: float | None = None
    as_of: datetime | None = None

    _normalize_times = field_validator("as_of", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "MacroIndicatorStatus":
        if not self.name.strip():
            raise ValueError("MacroIndicatorStatus.name must be a non-empty string.")
        if not self.symbol.strip():
            raise ValueError("MacroIndicatorStatus.symbol must be a non-empty string.")
        return self


def _default_vix_status() -> MacroIndicatorStatus:
    return MacroIndicatorStatus(name="VIX", symbol="^VIX")


def _default_dxy_status() -> MacroIndicatorStatus:
    return MacroIndicatorStatus(name="DXY", symbol="DX-Y.NYB")


class MacroContextStatus(FrozenModel):
    """Typed bounded macro context used by runtime health surfaces."""

    last_attempted_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    used_cached: bool = False
    last_error: str | None = None
    vix: MacroIndicatorStatus = Field(default_factory=_default_vix_status)
    dxy: MacroIndicatorStatus = Field(default_factory=_default_dxy_status)

    _normalize_times = field_validator(
        "last_attempted_at",
        "last_refreshed_at",
        mode="after",
    )(_to_utc)


class MarketHoursStatus(FrozenModel):
    """Typed market-open status used by the scheduler and orchestrator."""

    checked_at: datetime
    is_market_open: bool
    source: str
    category: str | None = None
    reason: str | None = None
    next_open_at: datetime | None = None
    next_close_at: datetime | None = None

    _normalize_times = field_validator(
        "checked_at",
        "next_open_at",
        "next_close_at",
        mode="after",
    )(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "MarketHoursStatus":
        if self.category is not None and not self.category.strip():
            raise ValueError("MarketHoursStatus.category must be non-empty when provided.")
        return self


class MarketHoursOverview(FrozenModel):
    """Aggregate market-hours status across the supported instrument families."""

    overall: MarketHoursStatus
    fx: MarketHoursStatus
    metals: MarketHoursStatus

    @property
    def checked_at(self) -> datetime:
        return self.overall.checked_at

    @property
    def is_market_open(self) -> bool:
        return self.overall.is_market_open

    @property
    def source(self) -> str:
        return self.overall.source

    @property
    def reason(self) -> str | None:
        return self.overall.reason

    @property
    def next_open_at(self) -> datetime | None:
        return self.overall.next_open_at

    @property
    def next_close_at(self) -> datetime | None:
        return self.overall.next_close_at

    def category_status(self, category: str) -> MarketHoursStatus:
        normalized = category.strip().lower()
        if normalized in {"fx", "major_fx", "minor_fx"}:
            return self.fx
        if normalized in {"metal", "metals"}:
            return self.metals
        raise KeyError(f"Unsupported market-hours category {category!r}.")

    @model_validator(mode="after")
    def validate_contract(self) -> "MarketHoursOverview":
        if self.fx.category not in {None, "fx"}:
            raise ValueError("fx status must use category 'fx'.")
        if self.metals.category not in {None, "metals"}:
            raise ValueError("metals status must use category 'metals'.")
        return self


class QueueDepthStatus(FrozenModel):
    """Typed queue-depth snapshot for background fan-out consumers."""

    name: str
    depth: int = Field(ge=0)


class BackgroundTaskStatus(FrozenModel):
    """Typed health for one supervised background task."""

    name: str
    state: BackgroundRuntimeState
    restart_count: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None

    _normalize_times = field_validator(
        "started_at",
        "last_heartbeat_at",
        "last_error_at",
        mode="after",
    )(_to_utc)


class StreamHealthStatus(FrozenModel):
    """Typed health for the live price-stream producer."""

    state: BackgroundRuntimeState
    started_at: datetime | None = None
    last_tick_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    reconnect_count: int = Field(default=0, ge=0)
    last_error_at: datetime | None = None
    last_error: str | None = None

    _normalize_times = field_validator(
        "started_at",
        "last_tick_at",
        "last_heartbeat_at",
        "last_error_at",
        mode="after",
    )(_to_utc)


class RuntimeHealthStatus(FrozenModel):
    """Aggregate typed runtime health used by later `/status` surfaces."""

    scheduler: SchedulerStatus | None = None
    last_scan: ScanCycleStatus | None = None
    calendar: CalendarRefreshStatus | None = None
    market_hours: MarketHoursOverview | None = None
    macro: MacroContextStatus | None = None
    stream: StreamHealthStatus | None = None
    queues: tuple[QueueDepthStatus, ...] = Field(default_factory=tuple)
    tasks: tuple[BackgroundTaskStatus, ...] = Field(default_factory=tuple)


class BotSessionRecord(FrozenModel):
    """Persistent authenticated Telegram session state."""

    user_id: int = Field(gt=0)
    chat_id: int
    is_admin: bool = False
    username: str | None = None
    first_name: str | None = None
    authenticated_at: datetime
    last_activity_at: datetime

    _normalize_times = field_validator(
        "authenticated_at",
        "last_activity_at",
        mode="after",
    )(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "BotSessionRecord":
        if self.username is not None and not self.username.strip():
            raise ValueError("username must be a non-empty string when provided.")
        if self.first_name is not None and not self.first_name.strip():
            raise ValueError("first_name must be a non-empty string when provided.")
        if self.last_activity_at < self.authenticated_at:
            raise ValueError("last_activity_at must be greater than or equal to authenticated_at.")
        return self


class RuntimeConfigRecord(FrozenModel):
    """One persisted runtime-config override."""

    key: RuntimeConfigKey
    value: float | int | bool | ChartRenderStyle | ChartMode
    updated_at: datetime

    _normalize_times = field_validator("updated_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def validate_contract(self) -> "RuntimeConfigRecord":
        if self.key == RuntimeConfigKey.CHART:
            if not isinstance(self.value, ChartRenderStyle):
                raise ValueError("chart runtime config must use a chart style value.")
            return self

        if self.key == RuntimeConfigKey.CHART_MODE:
            if not isinstance(self.value, ChartMode):
                raise ValueError("chart_mode runtime config must use a chart mode value.")
            return self

        if self.key == RuntimeConfigKey.SCAN_INTERVAL:
            if not isinstance(self.value, int) or self.value <= 0:
                raise ValueError("scan_interval runtime config must be a positive integer.")
            return self

        if self.key in {RuntimeConfigKey.TRADE_PUSH, RuntimeConfigKey.SESSION_ALERTS}:
            if not isinstance(self.value, bool):
                raise ValueError(f"{self.key.value} runtime config must be a boolean.")
            return self

        if isinstance(self.value, bool):
            raise ValueError("numeric runtime config values must not be booleans.")
        if not isinstance(self.value, (int, float)) or float(self.value) <= 0:
            raise ValueError("numeric runtime config values must be positive.")
        return self


class RuntimeConfigSnapshot(FrozenModel):
    """Aggregated runtime-config overrides consumed by handlers and runtime services."""

    tolerance: float | None = Field(default=None, gt=0)
    spread: float | None = Field(default=None, gt=0)
    chop: float | None = Field(default=None, gt=0)
    chart: ChartRenderStyle = ChartRenderStyle.CANDLESTICK
    chart_mode: ChartMode = ChartMode.BALANCED
    scan_interval: int | None = Field(default=None, gt=0)
    trade_push: bool = True
    session_alerts: bool = True
    updated_at: datetime | None = None

    _normalize_times = field_validator("updated_at", mode="after")(_to_utc)


class AccountSummary(FrozenModel):
    """Typed read-only account summary for command replies."""

    account_id: str
    environment: str
    currency: str
    balance: float
    nav: float
    unrealized_pl: float
    realized_pl: float
    margin_used: float
    margin_available: float
    open_trade_count: int = Field(default=0, ge=0)
    open_position_count: int = Field(default=0, ge=0)
    pending_order_count: int = Field(default=0, ge=0)
    alias: str | None = None
    hedging_enabled: bool | None = None
    fetched_at: datetime

    _normalize_times = field_validator("fetched_at", mode="after")(_to_utc)
    _normalize_currency_field = field_validator("currency", mode="after")(_normalize_currency)

    @model_validator(mode="after")
    def validate_contract(self) -> "AccountSummary":
        if not self.account_id.strip():
            raise ValueError("account_id must be a non-empty string.")
        if not self.environment.strip():
            raise ValueError("environment must be a non-empty string.")
        if self.alias is not None and not self.alias.strip():
            raise ValueError("alias must be a non-empty string when provided.")
        return self


class OpenTradePosition(FrozenModel):
    """Typed individual live trade surfaced through `/positions`."""

    trade_id: str
    instrument: str
    units: float
    open_price: float = Field(gt=0)
    unrealized_pl: float | None = None
    realized_pl: float | None = None
    account_currency: str | None = None
    stop_loss_price: float | None = Field(default=None, gt=0)
    take_profit_price: float | None = Field(default=None, gt=0)
    gslo_price: float | None = Field(default=None, gt=0)
    opened_at: datetime

    _normalize_times = field_validator("opened_at", mode="after")(_to_utc)
    _normalize_account_currency = field_validator("account_currency", mode="after")(_normalize_currency)

    @property
    def direction(self) -> TradeDirection:
        return "LONG" if self.units > 0 else "SHORT"

    @model_validator(mode="after")
    def validate_contract(self) -> "OpenTradePosition":
        get_instrument_spec(self.instrument)
        if not self.trade_id.strip():
            raise ValueError("trade_id must be a non-empty string.")
        if self.units == 0:
            raise ValueError("units must be non-zero.")
        return self


__all__ = [
    "ActiveZoneSummary",
    "AlertDirection",
    "BackgroundRuntimeState",
    "BackgroundTaskStatus",
    "BinaryDirection",
    "BotSessionRecord",
    "CalendarRefreshStatus",
    "CalendarEvent",
    "ChartMode",
    "ChartRenderStyle",
    "ChopResult",
    "ChopStatus",
    "Direction",
    "ExcursionSample",
    "FibLevel",
    "FibSummary",
    "HTFBiasResult",
    "ImpactLevel",
    "IndicatorAlert",
    "IndicatorAlertEvaluationCursor",
    "IndicatorAlertCondition",
    "IndicatorMetric",
    "IndicatorValueSummary",
    "InstrumentOrderBlockTracker",
    "InstrumentBundle",
    "LiquidityLevelSummary",
    "LiquidityPoolSummary",
    "MacroContextStatus",
    "MacroIndicatorStatus",
    "MarketHoursOverview",
    "MarketHoursStatus",
    "MAX_ACTIVE_ORDER_BLOCKS",
    "OpenTradePosition",
    "ORBResult",
    "OrderBlockRecord",
    "OrderBlockSummary",
    "PendingOrder",
    "OrderBlockStatus",
    "PreviousHighLowSummary",
    "PriceAlert",
    "QueueDepthStatus",
    "RegimeChangepoint",
    "RetracementSummary",
    "RuntimeConfigKey",
    "RuntimeConfigRecord",
    "RuntimeConfigSnapshot",
    "RuntimeHealthStatus",
    "ScanCycleStatus",
    "ScanRunKind",
    "SchedulerJobStatus",
    "SchedulerRuntimeState",
    "SchedulerStatus",
    "SessionContextSummary",
    "SessionName",
    "SessionSummary",
    "SnapshotFreshness",
    "SmcContextSummary",
    "SFPResult",
    "SpreadResult",
    "StreamHealthStatus",
    "StructureKind",
    "StructureBreak",
    "StructureEventSummary",
    "SwingKind",
    "SwingPointSummary",
    "TickVolumeIndicator",
    "TimeAlert",
    "TimeAlertKind",
    "TimeAlertSchedule",
    "TimeAlertSessionName",
    "TimeAlertStatus",
    "TimeframeSnapshot",
    "AccountSummary",
    "TradeDirection",
    "TradeHistoryEvent",
    "TradeHistoryEventType",
    "TradeHistoryPage",
    "TradeHistorySyncState",
    "TradeHistoryView",
    "TradePlanSummary",
    "TradeRecord",
    "FinancingEvent",
    "RealizedPnLSummary",
    "TurtleSoupResult",
]
