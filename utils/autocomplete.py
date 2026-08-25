"""Autocomplete callbacks shared across cogs."""

import discord
from discord import app_commands

from db import database as db
from services import anilist, mangadex


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



# ---------------------------------------------------------------------------
# Part 2: Manga autocomplete
# ---------------------------------------------------------------------------

async def tracked_manga_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Nicknames tracked in this server — /manga-remove, /subscribe-manga,
    /unsubscribe-manga."""
    if interaction.guild_id is None:
        return []
    manga_list = await db.list_tracked_manga(str(interaction.guild_id))
    current_lower = current.lower()
    matches = [m for m in manga_list if current_lower in m["nickname"].lower()]
    return [
        app_commands.Choice(name=m["nickname"], value=m["nickname"])
        for m in matches[:25]
    ]


async def mangadex_search_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /manga-add — live MangaDex search, any title in their catalog."""
    if not current or len(current) < 2:
        return []
    try:
        results = await mangadex.search_manga(current, limit=25)
    except Exception:
        return []

    choices = []
    for manga in results:
        title = mangadex._pick_localized(manga["attributes"].get("title")) or "Unknown title"
        choices.append(app_commands.Choice(name=title[:100], value=manga["id"]))
    return choices


async def manga_details_search_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /manga — spec wants ANY manga, not just tracked ones, so this
    hits MangaDex live rather than reading from the server's tracked list.
    Value is the MangaDex ID; the command resolves details from that."""
    if not current or len(current) < 2:
        return []
    try:
        results = await mangadex.search_manga(current, limit=25)
    except Exception:
        return []

    choices = []
    for manga in results:
        title = mangadex._pick_localized(manga["attributes"].get("title")) or "Unknown title"
        choices.append(app_commands.Choice(name=title[:100], value=manga["id"]))
    return choices
