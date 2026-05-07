"""Thread-safe Stage 05 market state publication store."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from threading import RLock

from core.logging_setup import get_logger
from core.models import (
    InstrumentOrderBlockTracker,
    TimeframeSnapshot,
)

SnapshotKey = tuple[str, str]


class MarketStateStore:
    """Thread-safe two-layer state store with pinned snapshot history."""

    def __init__(self, *, snapshot_history_retention: int = 5) -> None:
        if snapshot_history_retention < 0:
            raise ValueError("snapshot_history_retention must be greater than or equal to zero.")

        self.snapshot_history_retention = snapshot_history_retention
        self._lock = RLock()
        self._latest_snapshots: dict[SnapshotKey, TimeframeSnapshot] = {}
        self._snapshot_history: dict[SnapshotKey, OrderedDict[int, TimeframeSnapshot]] = {}
        self._snapshot_versions: dict[SnapshotKey, int] = {}
        self._order_block_trackers: dict[str, InstrumentOrderBlockTracker] = {}
        self._order_block_tracker_versions: dict[str, int] = {}
        self.logger = get_logger(__name__)

    def publish_snapshot(self, snapshot: TimeframeSnapshot) -> TimeframeSnapshot:
        """Publish a new immutable snapshot version for an instrument/timeframe."""

        key = (snapshot.instrument, snapshot.timeframe)
        with self._lock:
            next_version = self._snapshot_versions.get(key, 0) + 1
            published = self._stamp_snapshot_version(snapshot, next_version)
            stored = self._clone_snapshot(published)

            history = self._snapshot_history.setdefault(key, OrderedDict())
            history[next_version] = stored
            self._latest_snapshots[key] = stored
            self._snapshot_versions[key] = next_version

            max_versions = self.snapshot_history_retention + 1
            while len(history) > max_versions:
                history.popitem(last=False)

            self.logger.info(
                "snapshot_published",
                snapshot_version=published.version,
                instrument=published.instrument,
                timeframe=published.timeframe,
                last_candle=published.last_completed_candle,
                is_stale=not published.freshness.is_fresh,
            )
            return self._clone_snapshot(published)

    def get_snapshot(self, instrument: str, timeframe: str) -> TimeframeSnapshot | None:
        """Return the latest published snapshot for a key."""

        with self._lock:
            snapshot = self._latest_snapshots.get((instrument, timeframe))
            return None if snapshot is None else self._clone_snapshot(snapshot)

    def get_snapshot_version(
        self,
        instrument: str,
        timeframe: str,
        version: int,
    ) -> TimeframeSnapshot | None:
        """Return a historical snapshot by its pinned version."""

        with self._lock:
            history = self._snapshot_history.get((instrument, timeframe))
            if history is None:
                return None
            snapshot = history.get(version)
            return None if snapshot is None else self._clone_snapshot(snapshot)

    def publish_order_block_tracker(
        self,
        tracker: InstrumentOrderBlockTracker,
    ) -> InstrumentOrderBlockTracker:
        """Publish the latest instrument-level order-block tracker."""

        with self._lock:
            return self._publish_order_block_tracker_locked(tracker)

    def get_order_block_tracker(
        self,
        instrument: str,
    ) -> InstrumentOrderBlockTracker | None:
        """Return the latest instrument-level order-block tracker."""

        with self._lock:
            tracker = self._order_block_trackers.get(instrument)
            return None if tracker is None else self._clone_order_block_tracker(tracker)

    def assemble_order_block_tracker(
        self,
        instrument: str,
        records,
        source_snapshot_versions: dict[str, int],
    ) -> InstrumentOrderBlockTracker:
        """Build and publish a tracker from per-timeframe snapshot versions."""

        with self._lock:
            for timeframe, version in source_snapshot_versions.items():
                history = self._snapshot_history.get((instrument, timeframe))
                if history is None or version not in history:
                    raise KeyError(
                        f"Missing pinned snapshot version {instrument} {timeframe} v{version}."
                    )

            tracker = InstrumentOrderBlockTracker(
                instrument=instrument,
                created_at=datetime.now(timezone.utc),
                records=tuple(records),
                source_snapshot_versions=dict(source_snapshot_versions),
            )
            return self._publish_order_block_tracker_locked(tracker)

    def _publish_order_block_tracker_locked(
        self,
        tracker: InstrumentOrderBlockTracker,
    ) -> InstrumentOrderBlockTracker:
        for timeframe, version in tracker.source_snapshot_versions.items():
            history = self._snapshot_history.get((tracker.instrument, timeframe))
            if history is None or version not in history:
                raise KeyError(
                    f"Missing pinned snapshot version {tracker.instrument} {timeframe} v{version}."
                )

        next_version = self._order_block_tracker_versions.get(tracker.instrument, 0) + 1
        published = self._stamp_order_block_tracker_version(tracker, next_version)
        stored = self._clone_order_block_tracker(published)

        self._order_block_trackers[published.instrument] = stored
        self._order_block_tracker_versions[published.instrument] = next_version

        self.logger.info(
            "order_block_tracker_published",
            tracker_version=published.tracker_version,
            instrument=published.instrument,
            record_count=len(published.records),
            timeframes=published.source_snapshot_versions,
        )
        return self._clone_order_block_tracker(published)

    @staticmethod
    def _stamp_snapshot_version(
        snapshot: TimeframeSnapshot,
        version: int,
    ) -> TimeframeSnapshot:
        payload = snapshot.model_dump(mode="python")
        payload["version"] = version
        return TimeframeSnapshot.model_validate(payload)

    @staticmethod
    def _stamp_order_block_tracker_version(
        tracker: InstrumentOrderBlockTracker,
        version: int,
    ) -> InstrumentOrderBlockTracker:
        payload = tracker.model_dump(mode="python")
        payload["tracker_version"] = version
        return InstrumentOrderBlockTracker.model_validate(payload)

    @staticmethod
    def _clone_snapshot(snapshot: TimeframeSnapshot) -> TimeframeSnapshot:
        return snapshot.model_copy(deep=True)

    @staticmethod
    def _clone_order_block_tracker(
        tracker: InstrumentOrderBlockTracker,
    ) -> InstrumentOrderBlockTracker:
        return tracker.model_copy(deep=True)
