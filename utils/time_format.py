"""Countdown + date formatting shared by embeds.py."""

from datetime import datetime, timezone


def format_release_week(target_ts: int) -> str:
    """Day-of-week name from a unix timestamp, e.g. 'Monday'."""
    dt = datetime.fromtimestamp(target_ts, tz=timezone.utc)
    return dt.strftime("%A")


def format_discord_timestamp(target_ts: int) -> str:
    """#12: Discord's native dynamic timestamp markup, e.g.
    '<t:1788102000:f> (<t:1788102000:R>)' — renders in each viewer's own
    timezone and keeps counting down live, unlike a fixed UTC string.

    Note: Discord's actual valid style letters are t/T/d/D/f/F/R only —
    there's no ':s' style, so this uses 'f' (short date/time) for the
    absolute time plus 'R' (relative, e.g. "in 5 days") to match what
    was asked for."""
    return f"<t:{target_ts}:f> (<t:{target_ts}:R>)"
