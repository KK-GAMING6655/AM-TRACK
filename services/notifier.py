"""
Per-server message sending. poller.py decides *when* something needs to
go out; this module handles *how* it gets sent for one server at a time.
Kept separate so the poller's fan-out logic (asyncio.gather across many
servers) stays readable.
"""

import logging

import discord
from discord.ext import commands

from db import database as db
from utils.embeds import new_episode_embed, reminder_embed
from views.buttons import NotificationView

log = logging.getLogger("amtrack.notifier")

MENTIONS_PER_MESSAGE = 50  # keep ping messages well under Discord's length/mention limits


async def send_new_episode_notification(bot: commands.Bot, anime: dict, episode: int) -> None:
    """anime is a tracked_anime row (one server's copy of the show)."""
    server = await db.get_server(anime["guild_id"])
    if server is None or not server.get("anime_channel_id"):
        log.info("Skipping notify for guild %s — no anime channel set.", anime["guild_id"])
        return

    channel = bot.get_channel(int(server["anime_channel_id"]))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(server["anime_channel_id"]))
        except discord.HTTPException:
            log.warning("Couldn't resolve anime channel for guild %s.", anime["guild_id"])
            return

    header, embed = new_episode_embed(anime, episode, anime["nickname"])
    view = NotificationView(mal_url=anime.get("mal_url"), anilist_url=anime.get("anilist_url"))

    message = await channel.send(content=header, embed=embed, view=view)
    await db.create_watched_row(str(message.id), str(channel.id), anime["id"], episode)

    await _send_ping_message(channel, anime)


async def _send_ping_message(channel: discord.abc.Messageable, anime: dict) -> None:
    subscribers = await db.list_subscribers(anime["id"])
    role_id = await db.get_role_ping(anime["id"])

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


async def send_reminder_notification(bot: commands.Bot, anime: dict, episode: int, days_label: str) -> None:
    """days_label: 'tomorrow', '7 days', '30 days', or '60 days'. No ping — per spec."""
    server = await db.get_server(anime["guild_id"])
    if server is None or not server.get("anime_channel_id"):
        return

    channel = bot.get_channel(int(server["anime_channel_id"]))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(server["anime_channel_id"]))
        except discord.HTTPException:
            log.warning("Couldn't resolve anime channel for guild %s.", anime["guild_id"])
            return

    header, embed = reminder_embed(anime, anime["nickname"], episode, days_label)
    await channel.send(content=header, embed=embed)
