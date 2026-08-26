"""
Background polling loop.

Every POLL_INTERVAL_MINUTES, this:
  1. Pulls every distinct AniList ID currently tracked by any server.
  2. Batch-fetches fresh data for all of them from AniList in one pass
     (services/anilist.get_many_anime_by_ids chunks at 50/request, so a
     few hundred tracked shows is still just a handful of requests —
     nowhere near AniList's ~90 req/min limit).
  3. For each show, refreshes every server's cached copy, and if the
     "episodes aired so far" count increased, fires the new-episode
     notification to every server tracking it — concurrently, so it goes
     out to all servers at effectively the same time (per your spec).
  4. Separately checks each tracked_anime's next_airing_at against the
     60d/30d/7d reminder thresholds — but ONLY when the gap to the next
     episode is large enough to be a real hiatus/new-season wait, not a
     normal weekly gap (see LARGE_GAP_THRESHOLD_SECONDS below). The 1-day
     reminder always applies, to every episode.

Every per-title iteration is wrapped in its own try/except: one title
with bad/missing data used to raise and abort the ENTIRE cycle (nothing
for ANY tracked anime would fire that pass) — this was the likely cause
of episodes/reminders silently never firing. Now a bad title is logged
and skipped; everything else still runs.

Started from main.py's on_ready(), stopped automatically when the bot
shuts down (tasks.loop is tied to the bot's event loop).
"""

import asyncio
import logging
import time

from discord.ext import commands, tasks

from db import database as db
from services import anilist, notifier

log = logging.getLogger("amtrack.poller")

POLL_INTERVAL_MINUTES = 20

# A gap smaller than this is treated as normal airing cadence (weekly,
# biweekly, etc.) — only the 1-day reminder applies. A gap larger than
# this is treated as a real hiatus/new-season wait, eligible for the
# 60d/30d/7d milestones too. 14 days comfortably covers weekly/biweekly
# shows without a code change if a title briefly airs every 2 weeks.
LARGE_GAP_THRESHOLD_SECONDS = 14 * 86400

# (threshold_seconds, reminder_state column, label passed to embeds.reminder_embed)
GAP_GATED_MILESTONES = [
    (60 * 86400, "sent_60d", "60 days"),
    (30 * 86400, "sent_30d", "30 days"),
    (7 * 86400, "sent_7d", "7 days"),
]
ALWAYS_ON_MILESTONE = (1 * 86400, "sent_1d", "tomorrow")


def start_poller(bot: commands.Bot) -> None:
    if poll_loop.is_running():
        return
    poll_loop.start(bot)


@tasks.loop(minutes=POLL_INTERVAL_MINUTES)
async def poll_loop(bot: commands.Bot):
    try:
        await _run_poll_cycle(bot)
    except Exception:
        log.exception("Poll cycle failed — will retry next interval.")


@poll_loop.before_loop
async def _before_loop(bot: commands.Bot):
    await bot.wait_until_ready()


async def _run_poll_cycle(bot: commands.Bot) -> None:
    anilist_ids = await db.list_distinct_anilist_ids()
    if not anilist_ids:
        return

    log.info("Polling %d tracked AniList title(s)...", len(anilist_ids))
    media_list = await anilist.get_many_anime_by_ids(anilist_ids)
    media_by_id = {m["id"]: m for m in media_list}

    missing = set(anilist_ids) - set(media_by_id.keys())
    if missing:
        log.warning("AniList returned no data for IDs: %s", missing)

    notify_tasks = []
    reminder_tasks = []

    for anilist_id, media in media_by_id.items():
        try:
            await _process_one_anime(bot, anilist_id, media, notify_tasks, reminder_tasks)
        except Exception:
            # one bad title must never take down the whole cycle
            log.exception("Failed processing AniList id %s — skipping this cycle.", anilist_id)

    # fire every server's new-episode notification at once, per spec
    if notify_tasks:
        await asyncio.gather(*notify_tasks, return_exceptions=True)
    if reminder_tasks:
        await asyncio.gather(*reminder_tasks, return_exceptions=True)

    log.info(
        "Poll cycle done — %d new-episode notification(s), %d reminder check(s) queued.",
        len(notify_tasks), len(reminder_tasks),
    )


async def _process_one_anime(bot: commands.Bot, anilist_id: int, media: dict,
                              notify_tasks: list, reminder_tasks: list) -> None:
    fields = anilist.to_db_fields(media)
    newly_aired = fields["last_aired_episode"]

    rows = await db.list_all_tracked_anime_for_anilist_id(anilist_id)
    for row in rows:
        await db.update_anime_cache(row["id"], fields)
        merged = {**row, **fields}

        effective_last_aired_at = row.get("last_episode_aired_at")

        if newly_aired > row["last_aired_episode"]:
            # covers the rare case a poll cycle is missed and more than
            # one episode lands between checks
            now = int(time.time())
            for ep in range(row["last_aired_episode"] + 1, newly_aired + 1):
                notify_tasks.append(notifier.send_new_episode_notification(bot, merged, ep))
            await db.set_last_aired_episode(row["id"], newly_aired, now)
            effective_last_aired_at = now  # this episode just aired — use "now" as the gap anchor
            log.info(
                "New episode(s) detected for AniList id %s (guild %s): %d -> %d",
                anilist_id, row["guild_id"], row["last_aired_episode"], newly_aired,
            )

        reminder_tasks.append(_check_reminders(bot, row, fields, effective_last_aired_at))


async def _check_reminders(bot: commands.Bot, row: dict, fields: dict,
                            last_episode_aired_at: int | None) -> None:
    next_at = fields.get("next_airing_at")
    next_ep = fields.get("next_airing_episode")
    if not next_at or not next_ep:
        return

    remaining = next_at - time.time()
    if remaining <= 0:
        return  # already covered by the airing-moment notification above

    state = await db.get_reminder_state(row["id"], next_ep)
    merged = {**row, **fields}

    # gap-gating: 60d/30d/7d only apply for a genuine hiatus/new-season
    # wait, not a normal weekly airing cadence (see #2/#3 in the bot's
    # change history) — determined from when the *previous* episode
    # aired, so a weekly show (gap ~7 days) never reaches this branch.
    gap_seconds = None
    if last_episode_aired_at:
        gap_seconds = next_at - last_episode_aired_at

    is_large_gap = gap_seconds is not None and gap_seconds > LARGE_GAP_THRESHOLD_SECONDS
    # if we don't know the previous episode's air time (e.g. a freshly
    # added not-yet-released show), treat it as a large gap so a season
    # premiere's 60/30/7-day countdown still works
    if last_episode_aired_at is None:
        is_large_gap = True

    if is_large_gap:
        for threshold_seconds, column, label in GAP_GATED_MILESTONES:
            if remaining <= threshold_seconds and not state.get(column):
                await notifier.send_reminder_notification(bot, merged, next_ep, label)
                await db.mark_reminder_sent(row["id"], next_ep, column)

    threshold_seconds, column, label = ALWAYS_ON_MILESTONE
    if remaining <= threshold_seconds and not state.get(column):
        await notifier.send_reminder_notification(bot, merged, next_ep, label)
        await db.mark_reminder_sent(row["id"], next_ep, column)
