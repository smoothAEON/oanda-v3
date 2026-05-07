"""Runtime-config persistence and effective override helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config.settings import Settings, get_settings
from core.enums import ChartMode, ChartRenderStyle, RuntimeConfigKey
from core.models import RuntimeConfigRecord, RuntimeConfigSnapshot
from data.persistence.trade_store import TradeStore


class RuntimeConfigManager:
    """Own persisted Stage 13 runtime-config overrides."""

    def __init__(
        self,
        *,
        store: TradeStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or TradeStore(settings=self.settings)

    def snapshot(self) -> RuntimeConfigSnapshot:
        """Return the aggregated runtime-config snapshot."""

        payload: dict[str, object] = {
            "chart": ChartRenderStyle.CANDLESTICK,
            "chart_mode": ChartMode.BALANCED,
            "trade_push": True,
            "session_alerts": True,
        }
        updated_at: datetime | None = None
        for record in self.store.list_runtime_configs():
            payload[record.key.value] = record.value
            if updated_at is None or record.updated_at > updated_at:
                updated_at = record.updated_at
        payload["updated_at"] = updated_at
        return RuntimeConfigSnapshot.model_validate(payload)

    def get_record(self, key: RuntimeConfigKey | str) -> RuntimeConfigRecord | None:
        """Return one persisted override record."""

        resolved = _coerce_config_key(key)
        return self.store.get_runtime_config(resolved.value)

    def set_value(self, key: RuntimeConfigKey | str, value: Any) -> RuntimeConfigRecord:
        """Persist one validated override."""

        resolved_key = _coerce_config_key(key)
        normalized_value = _normalize_config_value(resolved_key, value)
        return self.store.upsert_runtime_config(
            RuntimeConfigRecord(
                key=resolved_key,
                value=normalized_value,
                updated_at=datetime.now(timezone.utc),
            )
        )

    def clear_value(self, key: RuntimeConfigKey | str) -> RuntimeConfigRecord | None:
        """Delete one override."""

        resolved = _coerce_config_key(key)
        return self.store.delete_runtime_config(resolved.value)

    def effective_scan_interval_minutes(self) -> int:
        snapshot = self.snapshot()
        return self.settings.scan_interval_minutes if snapshot.scan_interval is None else snapshot.scan_interval

    def effective_chart_style(self) -> ChartRenderStyle:
        return self.snapshot().chart

    def effective_chart_mode(self) -> ChartMode:
        return self.snapshot().chart_mode

    def trade_push_enabled(self) -> bool:
        return self.snapshot().trade_push

    def session_alerts_enabled(self) -> bool:
        return self.snapshot().session_alerts


def _coerce_config_key(key: RuntimeConfigKey | str) -> RuntimeConfigKey:
    if isinstance(key, RuntimeConfigKey):
        return key
    normalized = str(key).strip().lower()
    try:
        return RuntimeConfigKey(normalized)
    except ValueError as exc:
        supported = ", ".join(item.value for item in RuntimeConfigKey)
        raise ValueError(f"Unsupported config key '{key}'. Supported keys: {supported}.") from exc


def _normalize_config_value(
    key: RuntimeConfigKey,
    value: Any,
) -> float | int | bool | ChartRenderStyle | ChartMode:
    if key == RuntimeConfigKey.CHART:
        try:
            return ChartRenderStyle(str(value).strip().lower())
        except ValueError as exc:
            raise ValueError("chart config must be 'line' or 'candlestick'.") from exc

    if key == RuntimeConfigKey.CHART_MODE:
        try:
            return ChartMode(str(value).strip().lower())
        except ValueError as exc:
            raise ValueError("chart_mode config must be 'compact', 'balanced', or 'full'.") from exc

    if key == RuntimeConfigKey.SCAN_INTERVAL:
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("scan_interval config must be a positive integer.") from exc
        if normalized <= 0:
            raise ValueError("scan_interval config must be a positive integer.")
        return normalized

    if key in {RuntimeConfigKey.TRADE_PUSH, RuntimeConfigKey.SESSION_ALERTS}:
        normalized_text = str(value).strip().lower()
        if normalized_text in {"on", "true", "1", "yes"}:
            return True
        if normalized_text in {"off", "false", "0", "no"}:
            return False
        raise ValueError(f"{key.value} config must be on/off.")

    raise ValueError(f"Unsupported config key '{key.value}'.")


__all__ = [
    "RuntimeConfigManager",
]
