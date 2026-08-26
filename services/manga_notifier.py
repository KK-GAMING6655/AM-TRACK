"""
Per-server manga message sending — mirrors services/notifier.py's pattern
for anime. Kept separate so manga_poller.py's fan-out logic stays readable.
"""

import logging

import discord
from discord.ext import commands

from db import database as db
from utils.embeds import new_chapter_embed
from views.buttons import MangaNotificationView

log = logging.getLogger("amtrack.manga_notifier")

MENTIONS_PER_MESSAGE = 50


async def send_new_chapter_notification(bot: commands.Bot, manga: dict, chapter_number: str) -> None:
    """manga is a tracked_manga row (one server's copy of the title)."""
    server = await db.get_server(manga["guild_id"])
    if server is None or not server.get("manga_channel_id"):
        log.info("Skipping notify for guild %s — no manga channel set.", manga["guild_id"])
        return

    channel = bot.get_channel(int(server["manga_channel_id"]))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(server["manga_channel_id"]))
        except discord.HTTPException:
            log.warning("Couldn't resolve manga channel for guild %s.", manga["guild_id"])
            return

    header, embed = new_chapter_embed(manga, chapter_number, manga["nickname"])
    view = MangaNotificationView(mangadex_url=manga.get("mangadex_url"))

    message = await channel.send(content=header, embed=embed, view=view)
    await db.create_read_row(str(message.id), str(channel.id), manga["id"], chapter_number)

    await _send_ping_message(channel, manga)


async def _send_ping_message(channel: discord.abc.Messageable, manga: dict) -> None:
    subscribers = await db.list_manga_subscribers(manga["id"])
    role_id = await db.get_manga_role_ping(manga["id"])

    mentions = []
    if role_id:
        mentions.append(f"<@&{role_id}>")
    mentions.extend(f"<@{uid}>" for uid in subscribers)

    if not mentions:
        return

    allowed = discord.AllowedMentions(roles=True, users=True, everyone=False)
    for i in range(0, len(mentions), MENTIONS_PER_MESSAGE):
        chunk = mentions[i : i + MENTIONS_PER_MESSAGE]
        await channel.send(content=" ".join(chunk), allowed_mentions=allowed)
    mentions = [f"<@{uid}>" for uid in subscribers]
