"""Small, dependency-free helpers shared across layers."""
from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

UTC = UTC


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime | None, assume_tz=UTC) -> datetime | None:
    """Return a timezone-aware UTC datetime. Naive values are interpreted in ``assume_tz``."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=assume_tz)
    return value.astimezone(UTC)


_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",  # Korean finance boards commonly use this form
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
)


def parse_timestamp(value: Any, assume_tz=UTC) -> datetime | None:
    """Parse datetimes, ISO strings, local-format strings and epoch seconds/millis into UTC.

    Naive inputs are interpreted in ``assume_tz`` (the source's local timezone).
    Returns ``None`` when the value cannot be parsed - callers decide what to do.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, datetime):
        return ensure_utc(value, assume_tz)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if abs(value) > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.replace(".", "", 1).isdigit() and len(text) >= 9:
            return parse_timestamp(float(text), assume_tz)
        try:
            return ensure_utc(datetime.fromisoformat(text.replace("Z", "+00:00")), assume_tz)
        except ValueError:
            pass
        for fmt in _FORMATS:
            try:
                return ensure_utc(datetime.strptime(text, fmt), assume_tz)
            except ValueError:
                continue
    return None


def to_json_safe(obj: Any) -> Any:
    """Convert values to JSON-safe primitives (NaN/inf/NaT -> None, datetimes -> ISO strings)."""
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    try:
        if obj != obj:  # NaN and NaT
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(obj, float):
        return None if math.isinf(obj) else obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_json_safe(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):  # numpy scalars
        try:
            return to_json_safe(obj.item())
        except (ValueError, TypeError):
            pass
    return str(obj)
