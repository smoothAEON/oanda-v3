from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

import core.models as models_module
from core.candle_policy import get_timeframe_delta
from core.enums import (
    AlertStatus,
    ChartMode,
    CloseReason,
    IndicatorKind,
    PendingOrderType,
    RuntimeConfigKey,
    TimeAlertKind,
    TimeAlertStatus,
    TradeState,
)
from core.events import Heartbeat, PriceTick, TradeClosedEvent, TradeModifiedEvent, TradeOpenedEvent
from core.instrument_registry import get_instrument_spec
from core.models import (
    ActiveZoneSummary,
    ExcursionSample,
    IndicatorAlert,
    IndicatorValueSummary,
    InstrumentOrderBlockTracker,
    LiquidityLevelSummary,
    LiquidityPoolSummary,
    OrderBlockRecord,
    OrderBlockSummary,
    PendingOrder,
    PreviousHighLowSummary,
    PriceAlert,
    RetracementSummary,
    RuntimeConfigRecord,
    SessionContextSummary,
    SessionSummary,
    SnapshotFreshness,
    SmcContextSummary,
    SpreadResult,
    StructureBreak,
    StructureEventSummary,
    TimeAlert,
    TimeAlertDefinition,
    TimeframeSnapshot,
    TradeRecord,
)


BASE_TIME = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)


def test_models_public_surface_exports_structure_kind() -> None:
    assert "StructureKind" in models_module.__all__


def build_freshness(
    *,
    instrument: str = "EUR_USD",
    timeframe: str = "H1",
    last_completed_candle: datetime = BASE_TIME,
    fetched_at: datetime | None = None,
    is_fresh: bool = True,
    staleness_seconds: float | None = None,
) -> SnapshotFreshness:
    return SnapshotFreshness(
        instrument=instrument,
        timeframe=timeframe,
        last_completed_candle=last_completed_candle,
        fetched_at=fetched_at or (last_completed_candle + timedelta(minutes=5)),
        source="oanda_api",
        candle_count=500,
        is_fresh=is_fresh,
        staleness_seconds=0.0 if staleness_seconds is None else staleness_seconds,
    )


def build_spread(instrument: str = "EUR_USD") -> SpreadResult:
    spec = get_instrument_spec(instrument)
    bid = 1.1000
    ask = bid + (spec.pip_size * 2.0)
    return SpreadResult(
        instrument=instrument,
        bid=bid,
        ask=ask,
        raw_spread=ask - bid,
        spread_pips=2.0,
        pip_size=spec.pip_size,
        fetched_at=BASE_TIME,
    )


def build_snapshot(
    *,
    instrument: str = "EUR_USD",
    timeframe: str = "H1",
    last_completed_candle: datetime = BASE_TIME,
    freshness: SnapshotFreshness | None = None,
) -> TimeframeSnapshot:
    boundary = get_timeframe_delta(timeframe)
    snapshot_freshness = freshness or build_freshness(
        instrument=instrument,
        timeframe=timeframe,
        last_completed_candle=last_completed_candle,
    )
    return TimeframeSnapshot(
        instrument=instrument,
        timeframe=timeframe,
        last_completed_candle=last_completed_candle,
        computed_at=last_completed_candle + timedelta(minutes=1),
        candle_range_start=last_completed_candle - boundary,
        candle_range_end=last_completed_candle,
        indicators=IndicatorValueSummary(),
        structure=StructureEventSummary(),
        zones=ActiveZoneSummary(),
        liquidity=LiquidityPoolSummary(),
        spread=build_spread(instrument),
        freshness=snapshot_freshness,
    )


def test_snapshot_freshness_matches_stage_04_field_shape() -> None:
    freshness = build_freshness()

    assert freshness.model_dump() == {
        "instrument": "EUR_USD",
        "timeframe": "H1",
        "last_completed_candle": BASE_TIME,
        "fetched_at": BASE_TIME + timedelta(minutes=5),
        "source": "oanda_api",
        "candle_count": 500,
        "is_fresh": True,
        "staleness_seconds": 0.0,
    }


def test_timeframe_snapshot_serializes_and_is_frozen() -> None:
    snapshot = build_snapshot()

    dumped = snapshot.model_dump()

    assert dumped["instrument"] == "EUR_USD"
    assert dumped["timeframe"] == "H1"
    assert dumped["version"] == 0
    assert dumped["freshness"]["timeframe"] == "H1"
    assert "sfp" not in dumped
    assert "turtle_soup" not in dumped

    with pytest.raises(ValidationError):
        snapshot.version = 99  # type: ignore[misc]


def test_active_zone_summary_allows_ten_order_blocks_per_mitigation_status() -> None:
    mitigated = OrderBlockSummary(
        direction="BULLISH",
        upper_price=1.1020,
        lower_price=1.1010,
        created_at=BASE_TIME,
        is_mitigated=True,
    )
    unmitigated = OrderBlockSummary(
        direction="BULLISH",
        upper_price=1.1020,
        lower_price=1.1010,
        created_at=BASE_TIME,
        is_mitigated=False,
    )

    summary = ActiveZoneSummary(order_blocks=(mitigated,) * 10 + (unmitigated,) * 10)

    assert len(summary.order_blocks) == 20


def test_active_zone_summary_rejects_more_than_ten_blocks_per_mitigation_status() -> None:
    mitigated = OrderBlockSummary(
        direction="BULLISH",
        upper_price=1.1020,
        lower_price=1.1010,
        created_at=BASE_TIME,
        is_mitigated=True,
    )
    unmitigated = mitigated.model_copy(update={"is_mitigated": False})

    with pytest.raises(ValidationError, match="mitigated order blocks"):
        ActiveZoneSummary(order_blocks=(mitigated,) * 11)

    with pytest.raises(ValidationError):
        ActiveZoneSummary(order_blocks=(unmitigated,) * 11)


def test_stage_06_context_summaries_are_additive_and_bounded() -> None:
    snapshot = build_snapshot()

    assert snapshot.smc_context == SmcContextSummary()

    liquidity = LiquidityLevelSummary(price=1.10, side="BUY_SIDE", occurred_at=BASE_TIME)
    with pytest.raises(ValidationError):
        LiquidityPoolSummary(levels=(liquidity, liquidity, liquidity, liquidity))

    structure_break = StructureBreak(
        kind="BOS",
        direction="BULLISH",
        level=1.1010,
        occurred_at=BASE_TIME,
    )
    with pytest.raises(ValidationError):
        StructureEventSummary(
            recent_breaks=(structure_break, structure_break, structure_break, structure_break)
        )


def test_stage_06_tracker_and_context_models_validate_contracts() -> None:
    session = SessionSummary(
        name="LONDON",
        is_active=True,
        window_start=BASE_TIME,
        window_end=BASE_TIME + timedelta(hours=1),
        session_high=1.11,
        session_low=1.09,
        last_evaluated_at=BASE_TIME + timedelta(hours=1),
    )
    previous_high_low = PreviousHighLowSummary(
        previous_high=1.12,
        previous_low=1.08,
        broken_high=True,
        broken_low=False,
        as_of=BASE_TIME,
    )
    retracement = RetracementSummary(
        direction="BULLISH",
        current_retracement_pct=45.0,
        deepest_retracement_pct=60.0,
        as_of=BASE_TIME,
    )
    smc_context = SmcContextSummary(
        sessions=SessionContextSummary(sessions=(session,)),
        previous_high_low=previous_high_low,
        retracement=retracement,
    )

    snapshot = build_snapshot()
    record = OrderBlockRecord(
        id="ob-1",
        instrument="EUR_USD",
        timeframe="H1",
        direction="BULLISH",
        upper_price=1.1020,
        lower_price=1.1010,
        created_at=BASE_TIME,
        status="MITIGATED",
        mitigated_at=BASE_TIME + timedelta(hours=1),
        source_snapshot_version=1,
        last_analyzed_close=1.1015,
    )
    tracker = InstrumentOrderBlockTracker(
        instrument="EUR_USD",
        created_at=BASE_TIME + timedelta(minutes=2),
        records=(record,),
        source_snapshot_versions={"H1": 1},
    )

    assert smc_context.sessions.sessions[0].name == "LONDON"
    assert tracker.records[0].source_snapshot_version == 1
    assert snapshot.smc_context.sessions.sessions == ()

    with pytest.raises(ValidationError):
        InstrumentOrderBlockTracker(
            instrument="EUR_USD",
            created_at=BASE_TIME + timedelta(minutes=2),
            records=(record,),
            source_snapshot_versions={},
        )

    with pytest.raises(ValidationError):
        RetracementSummary(direction="BULLISH", current_retracement_pct=40.0)


def test_timeframe_snapshot_rejects_mismatched_freshness_and_range_end() -> None:
    with pytest.raises(ValidationError):
        build_snapshot(
            freshness=build_freshness(timeframe="H4"),
        )

    with pytest.raises(ValidationError):
        TimeframeSnapshot(
            instrument="EUR_USD",
            timeframe="H1",
            last_completed_candle=BASE_TIME,
            computed_at=BASE_TIME + timedelta(minutes=1),
            candle_range_start=BASE_TIME - timedelta(hours=1),
            candle_range_end=BASE_TIME - timedelta(hours=1),
            indicators=IndicatorValueSummary(),
            structure=StructureEventSummary(),
            zones=ActiveZoneSummary(),
            liquidity=LiquidityPoolSummary(),
            spread=build_spread(),
            freshness=build_freshness(),
        )


def test_public_models_reject_dataframe_leakage() -> None:
    with pytest.raises(ValidationError):
        TimeframeSnapshot(
            instrument="EUR_USD",
            timeframe="H1",
            last_completed_candle=BASE_TIME,
            computed_at=BASE_TIME + timedelta(minutes=1),
            candle_range_start=BASE_TIME - timedelta(hours=1),
            candle_range_end=BASE_TIME,
            indicators=pd.DataFrame({"value": [1.0]}),  # type: ignore[arg-type]
            structure=StructureEventSummary(),
            zones=ActiveZoneSummary(),
            liquidity=LiquidityPoolSummary(),
            spread=build_spread(),
            freshness=build_freshness(),
        )


def test_neutral_placeholder_models_are_valid() -> None:
    snapshot = build_snapshot()

    assert snapshot.indicators.metrics == ()
    assert snapshot.freshness.is_fresh is True


def test_trade_record_closed_contract_is_frozen_and_computes_direction() -> None:
    trade = TradeRecord(
        trade_id="12345678",
        instrument="SPX500_USD",
        units=1.0,
        open_price=2341.50,
        close_price=2383.50,
        sl_price=2335.00,
        tp_price=2383.50,
        gslo_price=None,
        state=TradeState.CLOSED,
        close_reason=CloseReason.TP_HIT,
        pips=42.0,
        instrument_pnl=4.20,
        instrument_pnl_currency="usd",
        account_pnl=4.20,
        account_currency="usd",
        opened_at=BASE_TIME,
        closed_at=BASE_TIME + timedelta(hours=4),
        notes="asia breakout",
    )

    assert trade.direction == "LONG"
    assert trade.account_currency == "USD"
    assert trade.instrument_pnl_currency == "USD"

    with pytest.raises(ValidationError):
        trade.notes = "changed"  # type: ignore[misc]


def test_trade_record_open_contract_rejects_close_only_fields() -> None:
    with pytest.raises(ValidationError):
        TradeRecord(
            trade_id="12345678",
            instrument="EUR_USD",
            units=-1.0,
            open_price=1.10,
            close_price=1.09,
            state=TradeState.OPEN,
            close_reason=CloseReason.MANUAL,
            pips=10.0,
            instrument_pnl=100.0,
            instrument_pnl_currency="USD",
            account_pnl=100.0,
            account_currency="USD",
            opened_at=BASE_TIME,
            closed_at=BASE_TIME + timedelta(hours=1),
        )


def test_trade_record_closed_contract_requires_split_pnl_fields() -> None:
    with pytest.raises(ValidationError):
        TradeRecord(
            trade_id="12345678",
            instrument="EUR_USD",
            units=-1.0,
            open_price=1.10,
            close_price=1.09,
            state=TradeState.CLOSED,
            close_reason=CloseReason.MANUAL,
            pips=10.0,
            instrument_pnl=100.0,
            instrument_pnl_currency=None,
            account_pnl=100.0,
            account_currency="USD",
            opened_at=BASE_TIME,
            closed_at=BASE_TIME + timedelta(hours=1),
        )


def test_pending_order_contract_is_typed_and_directional() -> None:
    order = PendingOrder(
        order_id="987654",
        instrument="EUR_USD",
        units=-1000.0,
        price=1.1050,
        order_type="stop",
        state="pending",
        time_in_force="gtc",
        position_fill="default",
        trigger_condition="ask",
        trade_id="12345678",
        stop_loss_price=1.0950,
        take_profit_price=1.1200,
        gslo_price=None,
        created_at=BASE_TIME,
    )

    assert order.direction == "SHORT"
    assert order.order_type == PendingOrderType.STOP
    assert order.state == "PENDING"
    assert order.time_in_force == "GTC"
    assert order.position_fill == "DEFAULT"
    assert order.trigger_condition == "ASK"
    assert order.trade_id == "12345678"

    with pytest.raises(ValidationError):
        PendingOrder(
            order_id="",
            instrument="EUR_USD",
            units=1.0,
            price=1.1050,
            order_type="LIMIT",
            created_at=BASE_TIME,
        )


def test_price_and_indicator_alert_contracts_validate_status_and_threshold_rules() -> None:
    alert = PriceAlert(
        id=7,
        instrument="SPX500_USD",
        target_price=2350.50,
        direction="above",
        status=AlertStatus.FIRED,
        chat_id=123,
        notes="NY session high",
        created_at=BASE_TIME,
        fired_at=BASE_TIME + timedelta(minutes=5),
    )
    indicator = IndicatorAlert(
        id=12,
        instrument="SPX500_USD",
        granularity="M30",
        indicator=IndicatorKind.RSI,
        condition="below",
        threshold=30.0,
        status=AlertStatus.PENDING,
        repeat=False,
        cooloff_minutes=None,
        chat_id=123,
        notes="oversold watch",
        created_at=BASE_TIME,
    )

    assert alert.status == AlertStatus.FIRED
    assert indicator.threshold == 30.0

    with pytest.raises(ValidationError):
        PriceAlert(
            id=7,
            instrument="SPX500_USD",
            target_price=2350.50,
            direction="above",
            status=AlertStatus.PENDING,
            chat_id=123,
            created_at=BASE_TIME,
            fired_at=BASE_TIME + timedelta(minutes=5),
        )

    with pytest.raises(ValidationError):
        IndicatorAlert(
            id=12,
            instrument="SPX500_USD",
            granularity="M30",
            indicator=IndicatorKind.MACD,
            condition="cross_up",
            threshold=0.0,
            status=AlertStatus.PENDING,
            repeat=False,
            chat_id=123,
            created_at=BASE_TIME,
        )


def test_time_alert_contract_validates_fixed_and_session_modes() -> None:
    fixed = TimeAlert(
        id=1,
        chat_id=123,
        kind=TimeAlertKind.FIXED_TIME,
        status=TimeAlertStatus.ACTIVE,
        schedule="daily",
        timezone_name="Asia/Singapore",
        local_time="09:30",
        session_name=None,
        note="london prep",
        created_at=BASE_TIME,
        next_fire_at=BASE_TIME + timedelta(hours=1),
        last_fired_at=None,
    )
    session = TimeAlert(
        id=2,
        chat_id=123,
        kind=TimeAlertKind.SESSION,
        status=TimeAlertStatus.ACTIVE,
        schedule="session",
        timezone_name="Asia/Singapore",
        local_time=None,
        session_name="london",
        note=None,
        created_at=BASE_TIME,
        next_fire_at=BASE_TIME + timedelta(hours=1),
        last_fired_at=None,
    )

    assert fixed.local_time == "09:30"
    assert session.session_name == "london"

    dated = TimeAlert(
        id=4,
        chat_id=123,
        kind=TimeAlertKind.FIXED_TIME,
        status=TimeAlertStatus.ACTIVE,
        schedule="once",
        timezone_name="Asia/Singapore",
        local_time="2026-03-21 09:30",
        session_name=None,
        created_at=BASE_TIME,
        next_fire_at=BASE_TIME + timedelta(hours=1),
        last_fired_at=None,
    )

    assert dated.local_time == "2026-03-21 09:30"

    with pytest.raises(ValidationError):
        TimeAlert(
            id=3,
            chat_id=123,
            kind=TimeAlertKind.FIXED_TIME,
            status=TimeAlertStatus.ACTIVE,
            schedule="session",
            timezone_name="Asia/Singapore",
            local_time="09:30",
            session_name=None,
            created_at=BASE_TIME,
            next_fire_at=BASE_TIME + timedelta(hours=1),
        )

    with pytest.raises(ValidationError, match="once schedule"):
        TimeAlertDefinition(
            kind=TimeAlertKind.FIXED_TIME,
            schedule="daily",
            timezone_name="Asia/Singapore",
            local_time="2026-03-21 09:30",
        )


def test_runtime_config_record_accepts_stage16_values() -> None:
    chart_mode = RuntimeConfigRecord(
        key=RuntimeConfigKey.CHART_MODE,
        value=ChartMode.FULL,
        updated_at=BASE_TIME,
    )
    trade_push = RuntimeConfigRecord(
        key=RuntimeConfigKey.TRADE_PUSH,
        value=False,
        updated_at=BASE_TIME,
    )

    assert chart_mode.value == ChartMode.FULL
    assert trade_push.value is False

    with pytest.raises(ValidationError):
        IndicatorAlert(
            id=12,
            instrument="SPX500_USD",
            granularity="M30",
            indicator=IndicatorKind.STOCH,
            condition="above",
            threshold=None,
            status=AlertStatus.PENDING,
            repeat=False,
            cooloff_minutes=5,
            chat_id=123,
            created_at=BASE_TIME,
        )


def test_excursion_sample_rejects_inverted_quotes() -> None:
    with pytest.raises(ValidationError):
        ExcursionSample(
            trade_id="12345678",
            sampled_at=BASE_TIME,
            bid=1.1010,
            ask=1.1000,
            adverse_pips=5.0,
            favorable_pips=10.0,
        )


def test_trade_helper_events_are_frozen_and_timezone_aware() -> None:
    tick = PriceTick(
        instrument="EUR_USD",
        bid=1.1000,
        ask=1.1002,
        time=BASE_TIME,
    )
    heartbeat = Heartbeat(time=BASE_TIME)
    opened = TradeOpenedEvent(
        trade_id="1",
        instrument="EUR_USD",
        units=1.0,
        open_price=1.1000,
        sl=1.0900,
        tp=1.1200,
        gslo=None,
        account_currency="sgd",
        opened_at=BASE_TIME,
    )
    closed = TradeClosedEvent(
        trade_id="1",
        instrument="EUR_USD",
        units=1.0,
        open_price=1.1000,
        close_price=1.1100,
        realized_pnl=100.0,
        close_reason=CloseReason.MANUAL,
        account_currency="usd",
        closed_at=BASE_TIME + timedelta(hours=1),
    )
    modified = TradeModifiedEvent(
        trade_id="1",
        new_sl=1.0950,
        new_tp=1.1250,
        modified_at=BASE_TIME + timedelta(minutes=30),
    )

    assert tick.mid == pytest.approx(1.1001)
    assert heartbeat.time.tzinfo is not None
    assert opened.account_currency == "SGD"
    assert opened.opened_at.tzinfo is not None
    assert closed.account_currency == "USD"
    assert closed.closed_at.tzinfo is not None
    assert modified.modified_at.tzinfo is not None

    with pytest.raises(FrozenInstanceError):
        tick.bid = 1.0  # type: ignore[misc]

    with pytest.raises(ValueError):
        PriceTick(
            instrument="EUR_USD",
            bid=1.1000,
            ask=1.1002,
            time=datetime(2026, 3, 20, 10, 0),
        )
