"""Countdown + date formatting shared by embeds.py."""

from datetime import datetime, timezone


def format_countdown(target_ts: int, now: datetime | None = None) -> str:
    """'in 3 days', 'in 4 hours', 'in 12 minutes', etc. — coarsest
    non-zero unit only, matching the spec's examples."""
    now = now or datetime.now(timezone.utc)
    target = datetime.fromtimestamp(target_ts, tz=timezone.utc)
    delta = target - now
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "now"

    years, rem = divmod(seconds, 31536000)
    months, rem = divmod(rem, 2592000)
    days, rem = divmod(rem, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    for value, unit in (
        (years, "year"), (months, "month"), (days, "day"),
        (hours, "hour"), (minutes, "minute"), (seconds, "second"),
    ):
        if value > 0:
            return f"in {value} {unit}{'s' if value != 1 else ''}"
    return "now"


def format_release_week(target_ts: int) -> str:
    """Day-of-week name from a unix timestamp, e.g. 'Monday'."""
    dt = datetime.fromtimestamp(target_ts, tz=timezone.utc)
    return dt.strftime("%A")


def format_full_datetime(target_ts: int) -> str:
    """'25 December 2026, 14:00 UTC' — used in the 'will air at' line."""
    dt = datetime.fromtimestamp(target_ts, tz=timezone.utc)
    return dt.strftime("%d %B %Y, %H:%M UTC")
