from __future__ import annotations

from core.models import IndicatorMetric, IndicatorValueSummary
from filters.chop import evaluate_chop


def build_indicators(adx_value: float | None) -> IndicatorValueSummary:
    return IndicatorValueSummary(
        metrics=(
            IndicatorMetric(name="ema", value=1.0, source="talib"),
            IndicatorMetric(name="adx", value=adx_value, source="talib"),
        )
    )


def test_chop_filter_passes_when_adx_is_above_threshold() -> None:
    result = evaluate_chop(build_indicators(25.0))

    assert result.status == "PASS"
    assert result.reason == "adx_above_threshold"
    assert result.metric_name == "adx"
    assert result.metric_value == 25.0


def test_chop_filter_warns_when_adx_is_near_threshold() -> None:
    result = evaluate_chop(build_indicators(17.5))

    assert result.status == "CAUTION"
    assert result.reason == "adx_near_threshold"


def test_chop_filter_rejects_when_adx_is_below_threshold() -> None:
    result = evaluate_chop(build_indicators(10.0))

    assert result.status == "REJECT"
    assert result.reason == "adx_below_threshold"


def test_chop_filter_returns_caution_when_adx_is_unavailable() -> None:
    missing_metric_result = evaluate_chop(IndicatorValueSummary())
    none_metric_result = evaluate_chop(build_indicators(None))

    assert missing_metric_result.status == "CAUTION"
    assert missing_metric_result.reason == "adx_unavailable"
    assert none_metric_result.status == "CAUTION"
    assert none_metric_result.reason == "adx_unavailable"


def test_chop_filter_is_repeatable_for_identical_input() -> None:
    indicators = build_indicators(22.0)

    first = evaluate_chop(indicators)
    second = evaluate_chop(indicators)

    assert first.model_dump() == second.model_dump()
