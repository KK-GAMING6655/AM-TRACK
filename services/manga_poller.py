"""
Manga polling loop — mirrors services/poller.py's anime loop, adapted for
MangaDex's per-title feed endpoint (no batched multi-ID lookup like
AniList's id_in, so this polls one title at a time with a small delay to
stay well under MangaDex's ~5 req/sec rate limit).

English-only, per the decision in am-track-bot memory.
"""

import asyncio
import logging

from discord.ext import commands, tasks

from db import database as db
from services import manga_notifier, mangadex

log = logging.getLogger("amtrack.manga_poller")

POLL_INTERVAL_MINUTES = 20
DELAY_BETWEEN_REQUESTS = 0.3  # seconds — keeps us under MangaDex's rate limit


def start_manga_poller(bot: commands.Bot) -> None:
    if manga_poll_loop.is_running():
        return
    manga_poll_loop.start(bot)


@tasks.loop(minutes=POLL_INTERVAL_MINUTES)
async def manga_poll_loop(bot: commands.Bot):
    try:
        await _run_manga_poll_cycle(bot)
    except Exception:
        log.exception("Manga poll cycle failed — will retry next interval.")


@manga_poll_loop.before_loop
async def _before_loop(bot: commands.Bot):
    await bot.wait_until_ready()


async def _run_manga_poll_cycle(bot: commands.Bot) -> None:
    mangadex_ids = await db.list_distinct_mangadex_ids()
    if not mangadex_ids:
        return

    notify_tasks = []

    for mangadex_id in mangadex_ids:
        try:
            await _poll_one_manga(bot, mangadex_id, notify_tasks)
        except Exception:
            log.exception("Failed polling MangaDex title %s — skipping this cycle.", mangadex_id)
        await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

    # fire every server's new-chapter notification at once, per spec
    if notify_tasks:
        await asyncio.gather(*notify_tasks, return_exceptions=True)


async def _poll_one_manga(bot: commands.Bot, mangadex_id: str, notify_tasks: list) -> None:
    rows = await db.list_all_tracked_manga_for_mangadex_id(mangadex_id)
    if not rows:
        return

    # refresh cached metadata (rating, chapter count, status, etc.) once
    # per title, applied to every server tracking it
    fresh_fields = await mangadex.get_full_manga_details(mangadex_id)
    if fresh_fields is not None:
        for row in rows:
            await db.update_manga_cache(row["id"], fresh_fields)

    for row in rows:
        since = row.get("last_chapter_published_at")
        new_chapters = await mangadex.get_new_english_chapters(mangadex_id, since)
        # publishAtSince is inclusive on MangaDex's side — drop anything
        # matching the chapter id we've already notified for
        new_chapters = [c for c in new_chapters if c["id"] != row.get("last_chapter_id")]
        if not new_chapters:
            continue

        merged = {**row, **(fresh_fields or {})}
        for chapter in new_chapters:
            chapter_number = chapter["attributes"].get("chapter") or "?"
            notify_tasks.append(
                manga_notifier.send_new_chapter_notification(bot, merged, chapter_number)
            )

        last = new_chapters[-1]
        await db.set_last_chapter(
            row["id"], last["id"],
            last["attributes"].get("chapter"), last["attributes"].get("publishAt"),
        )
