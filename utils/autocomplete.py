"""Autocomplete callbacks shared across cogs."""

import discord
from discord import app_commands

from db import database as db
from services import anilist


async def tracked_anime_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Nicknames already tracked in this server — used by /anime remove,
    /assign ping role, /subscribe anime, /unsubscribe anime, and /anime."""
    if interaction.guild_id is None:
        return []
    anime_list = await db.list_tracked_anime(str(interaction.guild_id))
    current_lower = current.lower()
    matches = [a for a in anime_list if current_lower in a["nickname"].lower()]
    return [
        app_commands.Choice(name=a["nickname"], value=a["nickname"])
        for a in matches[:25]
    ]


async def anime_details_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /anime — shows English titles per spec, value is still the nickname."""
    if interaction.guild_id is None:
        return []
    anime_list = await db.list_tracked_anime(str(interaction.guild_id))
    current_lower = current.lower()
    matches = [
        a for a in anime_list
        if current_lower in (a.get("title_english") or a["nickname"]).lower()
    ]
    return [
        app_commands.Choice(
            name=a.get("title_english") or a["nickname"], value=a["nickname"]
        )
        for a in matches[:25]
    ]


async def anilist_search_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /anime add — live AniList search restricted to releasing /
    not-yet-released shows, per the spec."""
    if not current or len(current) < 2:
        return []
    try:
        results = await anilist.search_anime(current, limit=25)
    except Exception:
        return []

    choices = []
    for media in results:
        title = media["title"].get("english") or media["title"]["romaji"]
        romaji = media["title"]["romaji"]
        label = title if title == romaji else f"{title} ({romaji})"
        choices.append(app_commands.Choice(name=label[:100], value=str(media["id"])))
    return choices
