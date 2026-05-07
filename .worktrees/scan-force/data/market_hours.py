"""Refined market-hours detection used by the Stage 18 runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from core.instrument_registry import get_instrument_spec
from core.logging_setup import get_logger
from core.models import MarketHoursOverview, MarketHoursStatus


FX_CALENDAR_NAME = "CME_FX"
METALS_CALENDAR_NAME = "CMEGlobex_PreciousMetals"
DEFAULT_CALENDAR_NAMES = {
    "fx": FX_CALENDAR_NAME,
    "metals": METALS_CALENDAR_NAME,
}
_CATEGORY_ALIASES = {
    "fx": "fx",
    "major_fx": "fx",
    "minor_fx": "fx",
    "metals": "metals",
    "metal": "metals",
}
_LOOKBACK_DAYS = 7
_LOOKAHEAD_DAYS = 14


def coerce_market_hours_overview(
    status: MarketHoursOverview | MarketHoursStatus | object,
) -> MarketHoursOverview:
    """Wrap legacy single-status objects into the Stage 18 overview contract."""

    if isinstance(status, MarketHoursOverview):
        return status

    if isinstance(status, MarketHoursStatus):
        base = status
    else:
        base = MarketHoursStatus(
            checked_at=getattr(status, "checked_at", datetime.now(timezone.utc)),
            is_market_open=bool(getattr(status, "is_market_open")),
            source=str(getattr(status, "source", "compat_market_hours")),
            category=getattr(status, "category", None),
            reason=getattr(status, "reason", None),
            next_open_at=getattr(status, "next_open_at", None),
            next_close_at=getattr(status, "next_close_at", None),
        )

    overall = base if base.category == "overall" else base.model_copy(update={"category": "overall"})
    return MarketHoursOverview(
        overall=overall,
        fx=base.model_copy(update={"category": "fx"}),
        metals=base.model_copy(update={"category": "metals"}),
    )


class MarketHoursService:
    """Category-aware pandas-market-calendars-backed market-open checks."""

    def __init__(
        self,
        *,
        calendar_names: dict[str, str] | None = None,
        now_fn=None,
    ) -> None:
        self.calendar_names = dict(DEFAULT_CALENDAR_NAMES)
        if calendar_names is not None:
            for category, calendar_name in calendar_names.items():
                self.calendar_names[self._normalize_category(category)] = calendar_name
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.logger = get_logger(__name__)
        self._calendar_cache: dict[str, object] = {}
        self._last_status: MarketHoursOverview | None = None

    @property
    def last_status(self) -> MarketHoursOverview | None:
        """Return the last computed market-hours overview."""

        return self._last_status

    def is_market_open(
        self,
        *,
        now_utc: datetime | None = None,
        instrument: str | None = None,
        category: str | None = None,
    ) -> bool:
        """Return True when the requested category or instrument is open."""

        if instrument is not None:
            return self.get_instrument_status(instrument, now_utc=now_utc).is_market_open
        if category is not None:
            return self.get_category_status(category, now_utc=now_utc).is_market_open
        return self.get_status(now_utc=now_utc).is_market_open

    def get_status(self, *, now_utc: datetime | None = None) -> MarketHoursOverview:
        """Return the current overview with overall, FX, and metals status."""

        now = self._ensure_utc(now_utc or self._now_fn())
        fx_status = self._compute_category_status("fx", now)
        metals_status = self._compute_category_status("metals", now)
        overview = MarketHoursOverview(
            overall=self._build_overall_status(now, fx_status, metals_status),
            fx=fx_status,
            metals=metals_status,
        )
        self._last_status = overview
        return overview

    def get_category_status(
        self,
        category: str,
        *,
        now_utc: datetime | None = None,
    ) -> MarketHoursStatus:
        """Return the status for one normalized market-hours category."""

        now = self._ensure_utc(now_utc or self._now_fn())
        return self._compute_category_status(self._normalize_category(category), now)

    def get_instrument_status(
        self,
        instrument: str,
        *,
        now_utc: datetime | None = None,
    ) -> MarketHoursStatus:
        """Return the status for the instrument's market-hours category."""

        category = self._instrument_market_category(instrument)
        return self.get_category_status(category, now_utc=now_utc)

    def next_market_open_at(self, *, now_utc: datetime | None = None) -> datetime | None:
        """Return the next market-open time across all supported categories."""

        now = self._ensure_utc(now_utc or self._now_fn())
        overview = self.get_status(now_utc=now)
        if not overview.is_market_open:
            return overview.next_open_at

        candidates: list[datetime] = []
        for status in (overview.fx, overview.metals):
            if status.is_market_open and status.next_close_at is not None:
                follow_up = self._compute_category_status(
                    status.category or "fx",
                    self._ensure_utc(status.next_close_at + timedelta(seconds=1)),
                )
                if follow_up.next_open_at is not None:
                    candidates.append(follow_up.next_open_at)
            elif status.next_open_at is not None:
                candidates.append(status.next_open_at)
        if not candidates:
            return None
        return min(candidates)

    def _build_overall_status(
        self,
        now: datetime,
        fx_status: MarketHoursStatus,
        metals_status: MarketHoursStatus,
    ) -> MarketHoursStatus:
        open_statuses = [status for status in (fx_status, metals_status) if status.is_market_open]
        next_open_candidates = [
            status.next_open_at
            for status in (fx_status, metals_status)
            if status.next_open_at is not None
        ]
        next_close_candidates = [
            status.next_close_at for status in open_statuses if status.next_close_at is not None
        ]

        if open_statuses:
            reason = "open" if len(open_statuses) == 2 else "partial_open"
            return MarketHoursStatus(
                checked_at=now,
                is_market_open=True,
                source="pandas_market_calendars_refined",
                category="overall",
                reason=reason,
                next_open_at=None,
                next_close_at=min(next_close_candidates) if next_close_candidates else None,
            )

        reasons = {status.reason for status in (fx_status, metals_status)}
        return MarketHoursStatus(
            checked_at=now,
            is_market_open=False,
            source="pandas_market_calendars_refined",
            category="overall",
            reason=reasons.pop() if len(reasons) == 1 else "mixed_closed",
            next_open_at=min(next_open_candidates) if next_open_candidates else None,
            next_close_at=None,
        )

    def _compute_category_status(self, category: str, now: datetime) -> MarketHoursStatus:
        calendar_name = self.calendar_names[self._normalize_category(category)]
        calendar = self._load_calendar(calendar_name)
        schedule = calendar.schedule(
            start_date=(now - timedelta(days=_LOOKBACK_DAYS)).date(),
            end_date=(now + timedelta(days=_LOOKAHEAD_DAYS)).date(),
        )

        active_row = None
        next_open = None
        previous_close = None
        for _, row in schedule.iterrows():
            market_open = self._ensure_utc(row["market_open"].to_pydatetime())
            market_close = self._ensure_utc(row["market_close"].to_pydatetime())
            if market_close <= now:
                previous_close = market_close
                continue
            if market_open <= now < market_close:
                active_row = (market_open, market_close)
                break
            if market_open > now and next_open is None:
                next_open = market_open
                break

        if next_open is None:
            next_open = self._find_next_open_after(calendar, now)

        if active_row is not None:
            return MarketHoursStatus(
                checked_at=now,
                is_market_open=True,
                source="pandas_market_calendars_refined",
                category=category,
                reason="open",
                next_open_at=None,
                next_close_at=active_row[1],
            )

        return MarketHoursStatus(
            checked_at=now,
            is_market_open=False,
            source="pandas_market_calendars_refined",
            category=category,
            reason=self._closed_reason(now, previous_close, next_open),
            next_open_at=next_open,
            next_close_at=None,
        )

    def _find_next_open_after(self, calendar, now: datetime) -> datetime | None:
        future_schedule = calendar.schedule(
            start_date=(now + timedelta(seconds=1)).date(),
            end_date=(now + timedelta(days=_LOOKAHEAD_DAYS)).date(),
        )
        for _, row in future_schedule.iterrows():
            market_open = self._ensure_utc(row["market_open"].to_pydatetime())
            if market_open > now:
                return market_open
        return None

    @staticmethod
    def _closed_reason(
        now: datetime,
        previous_close: datetime | None,
        next_open: datetime | None,
    ) -> str:
        if now.weekday() >= 5:
            return "weekend_closed"
        if previous_close is not None and previous_close.date() == now.date():
            return "after_close"
        if next_open is not None and next_open.date() == now.date():
            return "pre_open"
        return "holiday_closed"

    @staticmethod
    def _normalize_category(category: str) -> str:
        try:
            return _CATEGORY_ALIASES[category.strip().lower()]
        except KeyError as exc:
            raise KeyError(f"Unsupported market-hours category {category!r}.") from exc

    def _instrument_market_category(self, instrument: str) -> str:
        spec = get_instrument_spec(instrument)
        return self._normalize_category(spec.category)

    def _load_calendar(self, calendar_name: str):
        cached = self._calendar_cache.get(calendar_name)
        if cached is not None:
            return cached

        try:
            import pandas_market_calendars as mcal
        except ImportError as exc:  # pragma: no cover - import smoke covers availability
            raise RuntimeError(
                "pandas_market_calendars is required for market-hours checks."
            ) from exc

        try:
            calendar = mcal.get_calendar(calendar_name)
        except Exception as exc:  # pragma: no cover - depends on installed calendar set
            raise RuntimeError(
                f"Unable to load market-hours calendar {calendar_name!r}."
            ) from exc

        self._calendar_cache[calendar_name] = calendar
        return calendar

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            return timestamp.tz_localize("UTC").to_pydatetime()
        return timestamp.tz_convert("UTC").to_pydatetime()


__all__ = [
    "DEFAULT_CALENDAR_NAMES",
    "FX_CALENDAR_NAME",
    "METALS_CALENDAR_NAME",
    "MarketHoursService",
    "coerce_market_hours_overview",
]
