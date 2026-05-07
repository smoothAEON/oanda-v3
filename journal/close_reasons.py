"""Shared close-reason classification helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from core.enums import CloseReason

_EVIDENCE_KEYS = frozenset({"reason", "type", "orderType", "order_type"})


def infer_close_reason(
    *,
    close_price: float,
    sl_price: float | None = None,
    tp_price: float | None = None,
    raw_reason: object = None,
    evidence_sources: Iterable[object] = (),
) -> CloseReason:
    """Infer a trade close reason from raw broker evidence."""

    evidence = list(_collect_evidence(raw_reason))
    for source in evidence_sources:
        evidence.extend(_collect_evidence(source))

    if any("STOP" in item for item in evidence) or _matches_level(close_price, sl_price):
        return CloseReason.SL_HIT
    if any("TAKE" in item or "TP" in item for item in evidence) or _matches_level(close_price, tp_price):
        return CloseReason.TP_HIT
    if any("MARKET_IF_TOUCHED" in item for item in evidence):
        return CloseReason.MIT
    return CloseReason.MANUAL


def _collect_evidence(source: object) -> tuple[str, ...]:
    if source is None:
        return ()
    if isinstance(source, str):
        stripped = source.strip()
        if not stripped:
            return ()
        decoded = _decode_json(stripped)
        if decoded is not None:
            return _collect_evidence(decoded)
        return (stripped.upper(),)
    if isinstance(source, Mapping):
        collected: list[str] = []
        for key, value in source.items():
            key_text = str(key).strip()
            if key_text in _EVIDENCE_KEYS:
                collected.extend(_collect_evidence(value))
            elif isinstance(value, (Mapping, list, tuple, set)):
                collected.extend(_collect_evidence(value))
        return tuple(collected)
    if isinstance(source, (list, tuple, set)):
        collected: list[str] = []
        for item in source:
            collected.extend(_collect_evidence(item))
        return tuple(collected)
    return ()


def _decode_json(value: str) -> Any | None:
    if not value or value[0] not in "{[":
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _matches_level(close_price: float, level: float | None) -> bool:
    if level is None:
        return False
    tolerance = max(abs(close_price) * 1e-6, 1e-6)
    return abs(close_price - level) <= tolerance


__all__ = ["infer_close_reason"]
