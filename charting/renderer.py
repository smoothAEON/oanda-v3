"""Stage 12 chart rendering, overlay composition, and ephemeral artifacts."""

from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from config.settings import Settings, get_settings
from core.candle_policy import get_timeframe_delta, validate_candle_df
from core.instrument_registry import get_instrument_spec, normalize_instrument
from core.market_state import MarketStateStore
from core.enums import ChartMode, ChartRenderStyle
from core.logging_setup import get_logger, log_failure
from core.models import PendingOrder, TimeframeSnapshot, TradeRecord
from journal.trade_repository import TradeRepository
from orchestration.scan_orchestrator import ScanOrchestrator
from providers.account_client import OandaAccountClient
from providers.base import MarketDataProvider
from providers.oanda import OandaMarketDataProvider

SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15", "M30", "H1", "H4", "D")
SMC_SELECTOR_KEYS: tuple[str, ...] = ("orderblocks", "structure", "liquidity")
TRADE_SELECTOR_KEYS: tuple[str, ...] = ("positions", "orders", "sl", "tp", "gslo")
INDICATOR_SELECTOR_KEYS: tuple[str, ...] = ("ema", "bollinger", "vwap", "rsi", "macd")
DEFAULT_OVERLAY_KEYS: tuple[str, ...] = (
    "orderblocks",
    "positions",
    "orders",
    "sl",
    "tp",
    "gslo",
)
OVERLAY_PRESETS: dict[str, tuple[str, ...]] = {
    "clean": (),
    "smc": SMC_SELECTOR_KEYS,
    "indicators": INDICATOR_SELECTOR_KEYS,
}


class ChartingModel(BaseModel):
    """Base model for Stage 12 request validation."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ResolvedChartSelection:
    """Resolved overlay keys used by payload composition and rendering."""

    keys: tuple[str, ...]
    smc: tuple[str, ...] = ()
    trade: tuple[str, ...] = ()
    indicator: tuple[str, ...] = ()


class ChartRequest(ChartingModel):
    """Validated `/chart` request contract."""

    instrument: str
    timeframe: str
    chat_id: int | None = None
    style: ChartRenderStyle = ChartRenderStyle.CANDLESTICK
    mode: ChartMode = ChartMode.BALANCED
    count: int = Field(default=500, ge=2, le=5000)
    overlays: tuple[str, ...] = ()
    smc: tuple[str, ...] = ()
    trade: tuple[str, ...] = ()
    indicator: tuple[str, ...] = ()
    overlay_selection: ResolvedChartSelection | None = None

    @model_validator(mode="before")
    @classmethod
    def unpack_selection_payloads(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        for key in ("selection", "overlay_selection"):
            payload = normalized.pop(key, None)
            if not isinstance(payload, dict):
                continue
            for family in ("smc", "trade", "indicator"):
                if family in payload and family not in normalized:
                    normalized[family] = payload[family]

        overlays = normalized.get("overlays")
        if isinstance(overlays, dict):
            normalized.pop("overlays", None)
            for family in ("smc", "trade", "indicator"):
                if family in overlays and family not in normalized:
                    normalized[family] = overlays[family]

        return normalized

    @field_validator("instrument", mode="before")
    @classmethod
    def normalize_request_instrument(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return normalize_instrument(value)

    @field_validator("instrument")
    @classmethod
    def validate_request_instrument(cls, value: str) -> str:
        try:
            get_instrument_spec(value)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("timeframe", mode="before")
    @classmethod
    def normalize_request_timeframe(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().upper()

    @field_validator("timeframe")
    @classmethod
    def validate_request_timeframe(cls, value: str) -> str:
        if value not in SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"Unsupported timeframe '{value}'. Supported values: {list(SUPPORTED_TIMEFRAMES)}."
            )
        get_timeframe_delta(value)
        return value

    @field_validator("style", mode="before")
    @classmethod
    def normalize_request_style(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().lower()

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_request_mode(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().lower()

    @field_validator("overlays", "smc", "trade", "indicator", mode="before")
    @classmethod
    def coerce_selector_family(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _coerce_selector_values(value, field_name=info.field_name)

    @model_validator(mode="after")
    def resolve_overlay_selection(self) -> "ChartRequest":
        explicit_selectors_present = any(
            family for family in (self.smc, self.trade, self.indicator)
        )
        selection = _resolve_overlay_selection(
            presets=() if explicit_selectors_present else self.overlays,
            smc=self.smc,
            trade=self.trade,
            indicator=self.indicator,
            mode=self.mode,
            use_default_bundle=not explicit_selectors_present and not self.overlays,
        )
        self.overlay_selection = selection
        return self

    @property
    def selection(self) -> ResolvedChartSelection:
        assert self.overlay_selection is not None
        return self.overlay_selection

    @property
    def overlay_keys(self) -> tuple[str, ...]:
        return self.selection.keys


def _coerce_selector_values(value: object, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()

    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError(f"{field_name} selectors must be strings.")
            items.extend(part.strip() for part in item.split(","))
    else:
        raise TypeError(f"{field_name} selectors must be a string or collection of strings.")

    normalized = tuple(item.lower() for item in items if item)
    return normalized


def _resolve_overlay_selection(
    *,
    presets: tuple[str, ...],
    smc: tuple[str, ...],
    trade: tuple[str, ...],
    indicator: tuple[str, ...],
    mode: ChartMode,
    use_default_bundle: bool,
) -> ResolvedChartSelection:
    resolved_smc = _validate_selector_subset("smc", smc, SMC_SELECTOR_KEYS)
    resolved_trade = _validate_selector_subset("trade", trade, TRADE_SELECTOR_KEYS)
    resolved_indicator = _validate_selector_subset("indicator", indicator, INDICATOR_SELECTOR_KEYS)

    if any((resolved_smc, resolved_trade, resolved_indicator)):
        keys = _merge_unique(resolved_smc, resolved_trade, resolved_indicator)
        return ResolvedChartSelection(
            keys=keys,
            smc=resolved_smc,
            trade=resolved_trade,
            indicator=resolved_indicator,
        )

    if use_default_bundle:
        return _default_selection_for_mode(mode)

    preset_keys: list[str] = []
    for preset in presets:
        resolved_preset = preset.lower()
        if resolved_preset not in OVERLAY_PRESETS:
            raise ValueError(
                f"Unsupported overlay preset '{preset}'. Supported values: {sorted(OVERLAY_PRESETS)}."
            )
        preset_keys.extend(OVERLAY_PRESETS[resolved_preset])

    merged = tuple(dict.fromkeys(preset_keys))
    return ResolvedChartSelection(
        keys=merged,
        smc=tuple(key for key in SMC_SELECTOR_KEYS if key in merged),
        trade=tuple(key for key in TRADE_SELECTOR_KEYS if key in merged),
        indicator=tuple(key for key in INDICATOR_SELECTOR_KEYS if key in merged),
    )


def _default_selection_for_mode(mode: ChartMode) -> ResolvedChartSelection:
    if mode == ChartMode.COMPACT:
        return ResolvedChartSelection(
            keys=("orderblocks", "positions"),
            smc=("orderblocks",),
            trade=("positions",),
            indicator=(),
        )
    if mode == ChartMode.FULL:
        merged = _merge_unique(SMC_SELECTOR_KEYS, TRADE_SELECTOR_KEYS, INDICATOR_SELECTOR_KEYS)
        return ResolvedChartSelection(
            keys=merged,
            smc=SMC_SELECTOR_KEYS,
            trade=TRADE_SELECTOR_KEYS,
            indicator=INDICATOR_SELECTOR_KEYS,
        )
    return ResolvedChartSelection(
        keys=DEFAULT_OVERLAY_KEYS,
        smc=("orderblocks",),
        trade=("positions", "orders", "sl", "tp", "gslo"),
        indicator=(),
    )


def _validate_selector_subset(
    family: str,
    values: tuple[str, ...],
    supported: tuple[str, ...],
) -> tuple[str, ...]:
    if not values:
        return ()

    unknown = [value for value in values if value not in supported]
    if unknown:
        raise ValueError(
            f"Unsupported {family} selector(s): {unknown}. Supported values: {list(supported)}."
        )

    return tuple(candidate for candidate in supported if candidate in values)


def _merge_unique(*groups: tuple[str, ...]) -> tuple[str, ...]:
    ordered: dict[str, None] = {}
    for group in groups:
        for key in group:
            ordered.setdefault(key, None)
    return tuple(ordered)


@dataclass(frozen=True)
class OrderBlockAnnotation:
    """Serializable order-block payload used for chart overlays."""

    instrument: str
    direction: str
    upper_price: float
    lower_price: float
    anchor_time: pd.Timestamp | None
    is_mitigated: bool | None = None


@dataclass(frozen=True)
class StructureAnnotation:
    """Serializable structure-break marker."""

    instrument: str
    kind: str
    direction: str
    level: float | None
    occurred_at: pd.Timestamp


@dataclass(frozen=True)
class LiquidityAnnotation:
    """Serializable liquidity-level marker."""

    instrument: str
    side: str
    price: float
    occurred_at: pd.Timestamp | None
    was_swept: bool | None = None


@dataclass(frozen=True)
class TradeOverlay:
    """Serializable open-trade overlay payload."""

    instrument: str
    trade_id: str
    direction: str
    open_price: float
    sl_price: float | None
    tp_price: float | None
    gslo_price: float | None
    opened_at: pd.Timestamp


@dataclass(frozen=True)
class PendingOrderOverlay:
    """Serializable pending-order overlay payload."""

    instrument: str
    order_id: str
    order_type: str
    direction: str | None
    price: float
    stop_loss_price: float | None
    take_profit_price: float | None
    gslo_price: float | None
    created_at: pd.Timestamp


@dataclass(frozen=True)
class ChartRenderPayload:
    """Pure render payload sent to the process worker."""

    instrument: str
    timeframe: str
    mode: ChartMode
    count: int
    overlay_selection: ResolvedChartSelection
    candles: tuple[dict[str, object], ...]
    visible_price_low: float
    visible_price_high: float
    order_block_annotations: tuple[OrderBlockAnnotation, ...]
    structure_annotations: tuple[StructureAnnotation, ...]
    liquidity_annotations: tuple[LiquidityAnnotation, ...]
    trade_overlays: tuple[TradeOverlay, ...]
    order_overlays: tuple[PendingOrderOverlay, ...]
    omitted_layers: tuple[str, ...]
    artifact_path: str
    warning_text: str | None = None
    style: ChartRenderStyle = ChartRenderStyle.CANDLESTICK


@dataclass(frozen=True)
class ChartArtifact:
    """Context-managed chart artifact that deletes its file on close."""

    path: Path

    @property
    def file_path(self) -> Path:
        return self.path

    def close(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def __enter__(self) -> "ChartArtifact":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


@dataclass(frozen=True)
class ChartRenderResult:
    """Chart render result returned to the caller."""

    artifact: ChartArtifact
    omitted_layers: tuple[str, ...]
    overlay_selection: ResolvedChartSelection
    warning_text: str | None = None

    def close(self) -> None:
        self.artifact.close()


@dataclass(frozen=True)
class _WorkerRenderResult:
    """Minimal worker-process response."""

    path: str


def build_indicator_series(candles: pd.DataFrame) -> pd.DataFrame:
    """Build all supported chart-only indicator series from candle data."""

    frame = validate_candle_df(candles)
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    volume = frame["tick_volume"].astype(float)

    result = pd.DataFrame(index=frame.index)
    result["ema20"] = close.ewm(span=20, adjust=False, min_periods=1).mean()
    result["ema50"] = close.ewm(span=50, adjust=False, min_periods=1).mean()

    bollinger_middle = close.rolling(window=20, min_periods=1).mean()
    bollinger_std = close.rolling(window=20, min_periods=1).std(ddof=0).fillna(0.0)
    result["bollinger_upper"] = bollinger_middle + (2.0 * bollinger_std)
    result["bollinger_middle"] = bollinger_middle
    result["bollinger_lower"] = bollinger_middle - (2.0 * bollinger_std)

    typical_price = (high + low + close) / 3.0
    cumulative_volume = volume.cumsum().replace(0.0, pd.NA)
    result["vwap"] = (typical_price * volume).cumsum() / cumulative_volume

    delta = close.diff().fillna(0.0)
    gains = delta.clip(lower=0.0)
    losses = (-delta.clip(upper=0.0)).astype(float)
    average_gain = gains.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    average_loss = losses.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    relative_strength = average_gain / average_loss.replace(0.0, float("nan"))
    result["rsi"] = 100.0 - (100.0 / (1.0 + relative_strength))

    ema12 = close.ewm(span=12, adjust=False, min_periods=1).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=1).mean()
    result["macd"] = ema12 - ema26
    result["macd_signal"] = result["macd"].ewm(span=9, adjust=False, min_periods=1).mean()
    result["macd_hist"] = result["macd"] - result["macd_signal"]
    return result


def _compute_visible_price_range(candles: pd.DataFrame) -> tuple[float, float]:
    low = float(candles["low"].min())
    high = float(candles["high"].max())
    span = max(high - low, max(abs(high), abs(low), 1.0) * 0.002)
    padding = min(max(span * 0.08, 0.01), span * 0.20)
    return low - padding, high + padding


def _normalize_timestamp(value: object) -> pd.Timestamp | None:
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _coerce_chart_request(request: ChartRequest | dict[str, object]) -> ChartRequest:
    if isinstance(request, ChartRequest):
        return request
    return ChartRequest.model_validate(request)


class ChartRenderer:
    """Build chart payloads from published state and render them in a worker process."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        market_state: MarketStateStore | None = None,
        market_data_provider: MarketDataProvider | None = None,
        scan_orchestrator: ScanOrchestrator | None = None,
        trade_repository: TradeRepository | None = None,
        account_client: OandaAccountClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.market_state = market_state or MarketStateStore()
        self.market_data_provider = market_data_provider or OandaMarketDataProvider(
            settings=self.settings
        )
        self.scan_orchestrator = scan_orchestrator or ScanOrchestrator(
            settings=self.settings,
            market_data_provider=self.market_data_provider,
            market_state=self.market_state,
        )
        self.trade_repository = trade_repository or TradeRepository(settings=self.settings)
        self.account_client = account_client
        self.logger = get_logger(__name__)

    def build_render_payload(
        self,
        request: ChartRequest | dict[str, object],
    ) -> ChartRenderPayload:
        """Build a pure render payload from state plus runtime overlays."""

        resolved_request = _coerce_chart_request(request)
        snapshot = self._resolve_snapshot(resolved_request)
        candles, candle_warning = self._get_chart_candles(
            resolved_request.instrument,
            resolved_request.timeframe,
            resolved_request.count,
        )
        visible_price_low, visible_price_high = _compute_visible_price_range(candles)
        order_blocks = self._build_order_block_annotations(snapshot)
        structures = self._build_structure_annotations(snapshot)
        liquidity_levels = self._build_liquidity_annotations(snapshot)
        trades = self._build_trade_overlays(resolved_request.instrument)
        orders = self._build_pending_order_overlays(resolved_request.instrument)

        return ChartRenderPayload(
            instrument=resolved_request.instrument,
            timeframe=resolved_request.timeframe,
            style=resolved_request.style,
            mode=resolved_request.mode,
            count=resolved_request.count,
            overlay_selection=resolved_request.selection,
            candles=tuple(_serialize_candles(candles)),
            visible_price_low=visible_price_low,
            visible_price_high=visible_price_high,
            order_block_annotations=order_blocks,
            structure_annotations=structures,
            liquidity_annotations=liquidity_levels,
            trade_overlays=trades,
            order_overlays=orders,
            omitted_layers=self._build_omitted_layers(
                selection=resolved_request.selection,
                visible_price_low=visible_price_low,
                visible_price_high=visible_price_high,
                order_blocks=order_blocks,
                structures=structures,
                liquidity_levels=liquidity_levels,
                trades=trades,
                orders=orders,
            ),
            artifact_path=str(
                self._allocate_artifact_path(
                    resolved_request.instrument,
                    resolved_request.timeframe,
                )
            ),
            warning_text=self._build_warning_text(snapshot, candle_warning),
        )

    prepare_render_payload = build_render_payload
    _build_render_payload = build_render_payload

    def render(self, request: ChartRequest | dict[str, object]) -> ChartRenderResult:
        """Render one chart to a temporary PNG and return an artifact handle."""

        payload = self.build_render_payload(request)
        artifact_path = Path(payload.artifact_path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_render_chart_payload, payload)
                worker_result = future.result()
        except Exception as exc:
            log_failure(
                self.logger,
                "chart_render_failed",
                exc,
                instrument=payload.instrument,
                timeframe=payload.timeframe,
                artifact_path=str(artifact_path),
            )
            if artifact_path.exists():
                artifact_path.unlink()
            raise

        artifact = ChartArtifact(path=Path(worker_result.path))
        return ChartRenderResult(
            artifact=artifact,
            omitted_layers=payload.omitted_layers,
            overlay_selection=payload.overlay_selection,
            warning_text=payload.warning_text,
        )

    render_chart = render
    _render = render

    def _resolve_snapshot(self, request: ChartRequest) -> TimeframeSnapshot | None:
        snapshot = self.market_state.get_snapshot(request.instrument, request.timeframe)
        if not request.selection.smc:
            return snapshot
        if snapshot is not None and snapshot.freshness.is_fresh:
            return snapshot
        try:
            refreshed = self.scan_orchestrator.refresh_snapshot(request.instrument, request.timeframe)
        except Exception as exc:
            log_failure(
                self.logger,
                "chart_snapshot_refresh_failed",
                exc,
                level="warning",
                instrument=request.instrument,
                timeframe=request.timeframe,
                using_cached_snapshot=snapshot is not None,
            )
            if snapshot is not None:
                return snapshot
            raise
        resolved_snapshot = refreshed or self.market_state.get_snapshot(
            request.instrument,
            request.timeframe,
        )
        if resolved_snapshot is None:
            return None
        if resolved_snapshot.instrument != request.instrument:
            raise RuntimeError("refresh_snapshot returned a snapshot for the wrong instrument.")
        if resolved_snapshot.timeframe != request.timeframe:
            raise RuntimeError("refresh_snapshot returned a snapshot for the wrong timeframe.")
        return resolved_snapshot

    def _get_chart_candles(
        self,
        instrument: str,
        timeframe: str,
        count: int,
    ) -> tuple[pd.DataFrame, str | None]:
        warning_text: str | None = None
        try:
            candles = self.market_data_provider.get_candles(instrument, timeframe, count)
        except RuntimeError as exc:
            log_failure(
                self.logger,
                "chart_candle_fetch_failed",
                exc,
                level="warning",
                instrument=instrument,
                timeframe=timeframe,
                requested_count=count,
            )
            cached = self._load_cached_candles(instrument, timeframe, count)
            if cached is None:
                raise
            self.logger.warning(
                "chart_candle_fetch_fallback_used",
                instrument=instrument,
                timeframe=timeframe,
                requested_count=count,
            )
            candles = cached
            warning_text = "Warning: chart candles use cached fallback data after live fetch failed."
        return validate_candle_df(candles), warning_text

    @staticmethod
    def _build_warning_text(
        snapshot: TimeframeSnapshot | None,
        candle_warning: str | None,
    ) -> str | None:
        warnings: list[str] = []
        if snapshot is not None and not snapshot.freshness.is_fresh:
            warnings.append("Warning: chart overlays use stale snapshot state.")
        if candle_warning is not None:
            warnings.append(candle_warning)
        if not warnings:
            return None
        return "\n".join(warnings)

    def _allocate_artifact_path(self, instrument: str, timeframe: str) -> Path:
        artifact_root = self.settings.tinydb_path.parent / "chart_artifacts"
        return artifact_root / f"{instrument}_{timeframe}_{uuid4().hex}.png"

    def _build_order_block_annotations(
        self,
        snapshot: TimeframeSnapshot | None,
    ) -> tuple[OrderBlockAnnotation, ...]:
        if snapshot is None:
            return ()
        return tuple(
            OrderBlockAnnotation(
                instrument=snapshot.instrument,
                direction=order_block.direction,
                upper_price=float(order_block.upper_price),
                lower_price=float(order_block.lower_price),
                anchor_time=_normalize_timestamp(order_block.created_at),
                is_mitigated=order_block.is_mitigated,
            )
            for order_block in snapshot.zones.order_blocks
        )

    def _build_structure_annotations(
        self,
        snapshot: TimeframeSnapshot | None,
    ) -> tuple[StructureAnnotation, ...]:
        if snapshot is None:
            return ()
        return tuple(
            StructureAnnotation(
                instrument=snapshot.instrument,
                kind=structure.kind,
                direction=structure.direction,
                level=float(structure.level) if structure.level is not None else None,
                occurred_at=_normalize_timestamp(structure.occurred_at),
            )
            for structure in snapshot.structure.recent_breaks
        )

    def _build_liquidity_annotations(
        self,
        snapshot: TimeframeSnapshot | None,
    ) -> tuple[LiquidityAnnotation, ...]:
        if snapshot is None:
            return ()
        return tuple(
            LiquidityAnnotation(
                instrument=snapshot.instrument,
                side=level.side,
                price=float(level.price),
                occurred_at=_normalize_timestamp(level.occurred_at),
                was_swept=level.was_swept,
            )
            for level in snapshot.liquidity.levels
        )

    def _load_cached_candles(
        self,
        instrument: str,
        timeframe: str,
        count: int,
    ) -> pd.DataFrame | None:
        cache = getattr(self.market_data_provider, "cache", None)
        if cache is None:
            return None

        memory_cache = getattr(cache, "_memory_cache", None)
        if isinstance(memory_cache, dict):
            memory_entry = memory_cache.get((instrument, timeframe))
            candles = getattr(memory_entry, "candles", None)
            if isinstance(candles, pd.DataFrame) and not candles.empty:
                return candles.tail(count).reset_index(drop=True)

        csv_store = getattr(cache, "csv_store", None)
        if csv_store is None or not hasattr(csv_store, "load_candles"):
            return None

        candles = csv_store.load_candles(instrument, timeframe)
        if isinstance(candles, pd.DataFrame) and not candles.empty:
            return candles.tail(count).reset_index(drop=True)
        return None

    def _build_trade_overlays(self, instrument: str) -> tuple[TradeOverlay, ...]:
        if self.trade_repository is None:
            return ()
        records = _coerce_trade_records(self.trade_repository.list_open())
        return tuple(
            TradeOverlay(
                instrument=record.instrument,
                trade_id=record.trade_id,
                direction=record.direction,
                open_price=float(record.open_price),
                sl_price=record.sl_price,
                tp_price=record.tp_price,
                gslo_price=record.gslo_price,
                opened_at=_normalize_timestamp(record.opened_at),
            )
            for record in records
            if record.instrument == instrument
        )

    def _build_pending_order_overlays(self, instrument: str) -> tuple[PendingOrderOverlay, ...]:
        if self.account_client is None:
            return ()
        orders = _coerce_pending_orders(_resolve_maybe_awaitable(self.account_client.get_open_orders()))
        return tuple(
            PendingOrderOverlay(
                instrument=order.instrument,
                order_id=order.order_id,
                order_type=order.order_type,
                direction=order.direction,
                price=float(order.price),
                stop_loss_price=order.stop_loss_price,
                take_profit_price=order.take_profit_price,
                gslo_price=order.gslo_price,
                created_at=_normalize_timestamp(order.created_at),
            )
            for order in orders
            if order.instrument == instrument
        )

    def _build_omitted_layers(
        self,
        *,
        selection: ResolvedChartSelection,
        visible_price_low: float,
        visible_price_high: float,
        order_blocks: tuple[OrderBlockAnnotation, ...],
        structures: tuple[StructureAnnotation, ...],
        liquidity_levels: tuple[LiquidityAnnotation, ...],
        trades: tuple[TradeOverlay, ...],
        orders: tuple[PendingOrderOverlay, ...],
    ) -> tuple[str, ...]:
        omitted: list[str] = []

        if "orderblocks" in selection.smc:
            for order_block in order_blocks:
                if not _range_intersects(
                    low=order_block.lower_price,
                    high=order_block.upper_price,
                    visible_low=visible_price_low,
                    visible_high=visible_price_high,
                ):
                    omitted.append(
                        f"orderblock:{order_block.instrument}:{order_block.lower_price}-{order_block.upper_price}"
                    )

        if "structure" in selection.smc:
            for structure in structures:
                if structure.level is None:
                    continue
                if not _price_is_visible(
                    structure.level,
                    visible_low=visible_price_low,
                    visible_high=visible_price_high,
                ):
                    omitted.append(f"struct:{structure.instrument}:{structure.level}")

        if "liquidity" in selection.smc:
            for liquidity_level in liquidity_levels:
                if not _price_is_visible(
                    liquidity_level.price,
                    visible_low=visible_price_low,
                    visible_high=visible_price_high,
                ):
                    omitted.append(f"liquidity:{liquidity_level.instrument}:{liquidity_level.price}")

        for trade in trades:
            for label, price in (
                ("position", trade.open_price),
                ("sl", trade.sl_price),
                ("tp", trade.tp_price),
                ("gslo", trade.gslo_price),
            ):
                if price is None:
                    continue
                if not _price_is_visible(price, visible_low=visible_price_low, visible_high=visible_price_high):
                    omitted.append(f"{label}:{trade.instrument}:{trade.trade_id}:{price}")

        for order in orders:
            for label, price in (
                ("order", order.price),
                ("order_sl", order.stop_loss_price),
                ("order_tp", order.take_profit_price),
                ("order_gslo", order.gslo_price),
            ):
                if price is None:
                    continue
                if not _price_is_visible(price, visible_low=visible_price_low, visible_high=visible_price_high):
                    omitted.append(f"{label}:{order.instrument}:{order.order_id}:{price}")

        return tuple(omitted)


def _serialize_candles(candles: pd.DataFrame) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for row in candles.itertuples(index=False):
        records.append(
            {
                "time": pd.Timestamp(row.time).to_pydatetime(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "tick_volume": int(row.tick_volume),
            }
        )
    return records


def _resolve_maybe_awaitable(value: object) -> Any:
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def _coerce_pending_orders(value: object) -> list[PendingOrder]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("Pending-order reads must return a list.")
    return [item if isinstance(item, PendingOrder) else PendingOrder.model_validate(item) for item in value]


def _coerce_trade_records(value: object) -> list[TradeRecord]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("Trade reads must return a list.")
    return [item if isinstance(item, TradeRecord) else TradeRecord.model_validate(item) for item in value]


def _price_is_visible(price: float, *, visible_low: float, visible_high: float) -> bool:
    return visible_low <= float(price) <= visible_high


def _range_intersects(
    *,
    low: float,
    high: float,
    visible_low: float,
    visible_high: float,
) -> bool:
    return float(high) >= visible_low and float(low) <= visible_high


def _build_default_mpf_style(mpf: Any) -> dict[str, object]:
    """Return the default mplfinance style used by worker renders."""

    marketcolors = mpf.make_marketcolors(
        base_mpf_style="charles",
        up="#22c55e",
        down="#ef4444",
        edge="inherit",
        wick="inherit",
        volume="inherit",
    )
    return mpf.make_mpf_style(
        base_mpf_style="charles",
        marketcolors=marketcolors,
        facecolor="#000000",
        figcolor="#000000",
        edgecolor="#d1d5db",
        gridcolor="#374151",
        gridstyle="--",
        y_on_right=True,
        rc={
            "axes.edgecolor": "#d1d5db",
            "axes.labelcolor": "#e5e7eb",
            "xtick.color": "#e5e7eb",
            "ytick.color": "#e5e7eb",
            "text.color": "#f9fafb",
            "figure.facecolor": "#000000",
            "axes.facecolor": "#000000",
            "savefig.facecolor": "#000000",
            "savefig.edgecolor": "#000000",
            "grid.color": "#374151",
        },
    )


def _render_chart_payload(payload: ChartRenderPayload) -> _WorkerRenderResult:
    """Render a chart in a worker process using mplfinance."""

    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import mplfinance as mpf

    frame = pd.DataFrame(payload.candles)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = validate_candle_df(frame).set_index("time")
    indicator_frame = build_indicator_series(frame.reset_index())
    indicator_frame.index = frame.index

    addplots = []
    oscillator_panels: dict[str, int] = {}
    volume_panel = 1
    next_panel = 2

    if "rsi" in payload.overlay_selection.indicator:
        oscillator_panels["rsi"] = next_panel
        next_panel += 1
    if "macd" in payload.overlay_selection.indicator:
        oscillator_panels["macd"] = next_panel
        next_panel += 1

    if "ema" in payload.overlay_selection.indicator:
        addplots.append(mpf.make_addplot(indicator_frame["ema20"], color="#2563eb"))
        addplots.append(mpf.make_addplot(indicator_frame["ema50"], color="#dc2626"))
    if "bollinger" in payload.overlay_selection.indicator:
        addplots.append(mpf.make_addplot(indicator_frame["bollinger_upper"], color="#475569"))
        addplots.append(mpf.make_addplot(indicator_frame["bollinger_middle"], color="#64748b"))
        addplots.append(mpf.make_addplot(indicator_frame["bollinger_lower"], color="#475569"))
    if "vwap" in payload.overlay_selection.indicator:
        addplots.append(mpf.make_addplot(indicator_frame["vwap"], color="#f59e0b"))
    if "rsi" in oscillator_panels:
        addplots.append(
            mpf.make_addplot(
                indicator_frame["rsi"],
                panel=oscillator_panels["rsi"],
                color="#0891b2",
            )
        )
    if "macd" in oscillator_panels:
        macd_panel = oscillator_panels["macd"]
        addplots.append(
            mpf.make_addplot(
                indicator_frame["macd"],
                panel=macd_panel,
                color="#7c3aed",
            )
        )
        addplots.append(
            mpf.make_addplot(
                indicator_frame["macd_signal"],
                panel=macd_panel,
                color="#16a34a",
            )
        )
        addplots.append(
            mpf.make_addplot(
                indicator_frame["macd_hist"],
                panel=macd_panel,
                type="bar",
                color="#94a3b8",
                alpha=0.5,
            )
        )

    plot_kwargs: dict[str, object] = {
        "type": "line" if payload.style == ChartRenderStyle.LINE else "candle",
        "style": _build_default_mpf_style(mpf),
        "columns": ("open", "high", "low", "close", "tick_volume"),
        "figsize": (13, 7) if payload.mode == ChartMode.COMPACT else (14, 8) if payload.mode == ChartMode.BALANCED else (16, 9),
        "tight_layout": True,
        "returnfig": True,
        "warn_too_much_data": 10000,
    }
    volume_enabled = payload.mode != ChartMode.COMPACT
    plot_kwargs["volume"] = volume_enabled
    if volume_enabled:
        plot_kwargs["volume_panel"] = volume_panel
        plot_kwargs["ylabel_lower"] = "Tick Volume"
    if addplots:
        plot_kwargs["addplot"] = addplots
    total_panels = max(next_panel, (volume_panel + 1) if volume_enabled else 1)
    plot_kwargs["num_panels"] = total_panels
    if volume_enabled:
        plot_kwargs["panel_ratios"] = tuple([8, 2] + [2] * (total_panels - 2))
    else:
        plot_kwargs["panel_ratios"] = tuple([8] + [2] * (total_panels - 1))

    fig, axes = mpf.plot(frame, **plot_kwargs)
    main_ax = axes[0]
    main_ax.set_ylim(payload.visible_price_low, payload.visible_price_high)

    _draw_order_blocks(main_ax, frame, payload)
    _draw_line_overlays(main_ax, payload)

    output_path = Path(payload.artifact_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return _WorkerRenderResult(path=str(output_path))


def _draw_order_blocks(main_ax, frame: pd.DataFrame, payload: ChartRenderPayload) -> None:
    if "orderblocks" not in payload.overlay_selection.smc:
        return
    total_bars = max(len(frame) - 1, 1)
    for annotation in payload.order_block_annotations:
        if not _range_intersects(
            low=annotation.lower_price,
            high=annotation.upper_price,
            visible_low=payload.visible_price_low,
            visible_high=payload.visible_price_high,
        ):
            continue

        start_index = 0
        if annotation.anchor_time is not None:
            start_index = int(frame.index.searchsorted(annotation.anchor_time, side="left"))
            start_index = max(0, min(start_index, len(frame) - 1))

        xmin = start_index / total_bars
        color = "#16a34a" if annotation.direction == "BULLISH" else "#dc2626"
        main_ax.axhspan(
            annotation.lower_price,
            annotation.upper_price,
            xmin=xmin,
            xmax=1.0,
            color=color,
            alpha=0.12,
        )


def _draw_line_overlays(main_ax, payload: ChartRenderPayload) -> None:
    if "structure" in payload.overlay_selection.smc:
        for structure in payload.structure_annotations:
            if structure.level is None:
                continue
            if not _price_is_visible(
                structure.level,
                visible_low=payload.visible_price_low,
                visible_high=payload.visible_price_high,
            ):
                continue
            main_ax.axhline(
                structure.level,
                color="#0f766e",
                linestyle=":",
                linewidth=1.0,
                alpha=0.8,
            )

    if "liquidity" in payload.overlay_selection.smc:
        for level in payload.liquidity_annotations:
            if not _price_is_visible(
                level.price,
                visible_low=payload.visible_price_low,
                visible_high=payload.visible_price_high,
            ):
                continue
            main_ax.axhline(
                level.price,
                color="#7c2d12",
                linestyle="--",
                linewidth=1.0,
                alpha=0.7,
            )

    if any(key in payload.overlay_selection.trade for key in ("positions", "sl", "tp", "gslo")):
        for trade in payload.trade_overlays:
            if "positions" in payload.overlay_selection.trade:
                _draw_visible_line(main_ax, trade.open_price, payload, color="#1d4ed8", linestyle="-")
            if "sl" in payload.overlay_selection.trade and trade.sl_price is not None:
                _draw_visible_line(main_ax, trade.sl_price, payload, color="#b91c1c", linestyle="--")
            if "tp" in payload.overlay_selection.trade and trade.tp_price is not None:
                _draw_visible_line(main_ax, trade.tp_price, payload, color="#15803d", linestyle="--")
            if "gslo" in payload.overlay_selection.trade and trade.gslo_price is not None:
                _draw_visible_line(main_ax, trade.gslo_price, payload, color="#7c3aed", linestyle="-.")

    if "orders" in payload.overlay_selection.trade:
        for order in payload.order_overlays:
            _draw_visible_line(main_ax, order.price, payload, color="#ea580c", linestyle="-.")
            if "sl" in payload.overlay_selection.trade and order.stop_loss_price is not None:
                _draw_visible_line(main_ax, order.stop_loss_price, payload, color="#b91c1c", linestyle="--")
            if "tp" in payload.overlay_selection.trade and order.take_profit_price is not None:
                _draw_visible_line(main_ax, order.take_profit_price, payload, color="#15803d", linestyle="--")
            if "gslo" in payload.overlay_selection.trade and order.gslo_price is not None:
                _draw_visible_line(main_ax, order.gslo_price, payload, color="#7c3aed", linestyle="-.")

def _draw_visible_line(main_ax, price: float, payload: ChartRenderPayload, *, color: str, linestyle: str) -> None:
    if not _price_is_visible(
        price,
        visible_low=payload.visible_price_low,
        visible_high=payload.visible_price_high,
    ):
        return
    main_ax.axhline(price, color=color, linestyle=linestyle, linewidth=1.1, alpha=0.8)


__all__ = [
    "ChartArtifact",
    "ChartRenderPayload",
    "ChartRenderResult",
    "ChartRenderer",
    "ChartRequest",
    "ResolvedChartSelection",
    "build_indicator_series",
]
