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
     60d/30d/7d/1d reminder thresholds and fires any that were just
     crossed since the last poll.

Started from main.py's on_ready(), stopped automatically when the bot
shuts down (tasks.loop is tied to the bot's event loop).
"""

import asyncio
import logging

from discord.ext import commands, tasks

from db import database as db
from services import anilist, notifier

log = logging.getLogger("amtrack.poller")

POLL_INTERVAL_MINUTES = 20

# (threshold_seconds, reminder_state column, label passed to embeds.reminder_embed)
REMINDER_MILESTONES = [
    (60 * 86400, "sent_60d", "60 days"),
    (30 * 86400, "sent_30d", "30 days"),
    (7 * 86400, "sent_7d", "7 days"),
    (1 * 86400, "sent_1d", "tomorrow"),
]


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

    media_list = await anilist.get_many_anime_by_ids(anilist_ids)
    media_by_id = {m["id"]: m for m in media_list}

    notify_tasks = []
    reminder_tasks = []

    for anilist_id, media in media_by_id.items():
        fields = anilist.to_db_fields(media)
        newly_aired = fields["last_aired_episode"]

        rows = await db.list_all_tracked_anime_for_anilist_id(anilist_id)
        for row in rows:
            await db.update_anime_cache(row["id"], fields)
            merged = {**row, **fields}

            if newly_aired > row["last_aired_episode"]:
                # covers the rare case a poll cycle is missed and more
                # than one episode lands between checks
                for ep in range(row["last_aired_episode"] + 1, newly_aired + 1):
                    notify_tasks.append(notifier.send_new_episode_notification(bot, merged, ep))
                await db.set_last_aired_episode(row["id"], newly_aired)

            reminder_tasks.append(_check_reminders(bot, row, fields))

    # fire every server's new-episode notification at once, per spec
    if notify_tasks:
        await asyncio.gather(*notify_tasks, return_exceptions=True)
    if reminder_tasks:
        await asyncio.gather(*reminder_tasks, return_exceptions=True)


async def _check_reminders(bot: commands.Bot, row: dict, fields: dict) -> None:
    next_at = fields.get("next_airing_at")
    next_ep = fields.get("next_airing_episode")
    if not next_at or not next_ep:
        return

    import time
    remaining = next_at - time.time()
    if remaining <= 0:
        return  # already covered by the airing-moment notification above

    state = await db.get_reminder_state(row["id"], next_ep)
    merged = {**row, **fields}

    for threshold_seconds, column, label in REMINDER_MILESTONES:
        if remaining <= threshold_seconds and not state.get(column):
            await notifier.send_reminder_notification(bot, merged, next_ep, label)
            await db.mark_reminder_sent(row["id"], next_ep, column)
