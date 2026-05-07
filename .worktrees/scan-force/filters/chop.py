"""Chop gate placeholder reserved for Stage 08."""

from __future__ import annotations

from core.models import ChopResult, IndicatorValueSummary


def evaluate_chop(
    indicators: IndicatorValueSummary,
    *,
    adx_threshold: float = 20.0,
    caution_buffer: float = 5.0,
) -> ChopResult:
    """Evaluate the narrow ADX-only chop gate."""

    if adx_threshold <= 0:
        raise ValueError("adx_threshold must be positive.")
    if caution_buffer < 0:
        raise ValueError("caution_buffer must be greater than or equal to zero.")

    adx_metric = next((metric for metric in indicators.metrics if metric.name == "adx"), None)
    if adx_metric is None or adx_metric.value is None:
        return ChopResult(
            status="CAUTION",
            reason="adx_unavailable",
            metric_name="adx",
            metric_value=None,
            threshold=adx_threshold,
        )

    adx_value = adx_metric.value
    reject_floor = max(0.0, adx_threshold - caution_buffer)
    if adx_value >= adx_threshold:
        return ChopResult(
            status="PASS",
            reason="adx_above_threshold",
            metric_name="adx",
            metric_value=adx_value,
            threshold=adx_threshold,
        )
    if adx_value >= reject_floor:
        return ChopResult(
            status="CAUTION",
            reason="adx_near_threshold",
            metric_name="adx",
            metric_value=adx_value,
            threshold=adx_threshold,
        )
    return ChopResult(
        status="REJECT",
        reason="adx_below_threshold",
        metric_name="adx",
        metric_value=adx_value,
        threshold=adx_threshold,
    )


__all__ = ["evaluate_chop"]
