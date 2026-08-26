"""Autocomplete callbacks shared across cogs."""

import asyncio

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


async def unsubscribed_anime_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /subscribe-anime — #7: excludes anime the user is already
    subscribed to."""
    if interaction.guild_id is None:
        return []
    anime_list = await db.list_unsubscribed_anime(str(interaction.guild_id), str(interaction.user.id))
    current_lower = current.lower()
    matches = [a for a in anime_list if current_lower in a["nickname"].lower()]
    return [app_commands.Choice(name=a["nickname"], value=a["nickname"]) for a in matches[:25]]


async def subscribed_anime_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /unsubscribe-anime — #7: only shows anime the user is
    currently subscribed to."""
    if interaction.guild_id is None:
        return []
    anime_list = await db.list_subscribed_anime(str(interaction.guild_id), str(interaction.user.id))
    current_lower = current.lower()
    matches = [a for a in anime_list if current_lower in a["nickname"].lower()]
    return [app_commands.Choice(name=a["nickname"], value=a["nickname"]) for a in matches[:25]]


async def anilist_all_search_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /anime (details) — #10: spec wants every anime (aired, airing,
    or upcoming), not just what's tracked in this server, matching how
    /manga already works. Unlike /anime-add's autocomplete, this doesn't
    restrict by status."""
    if not current or len(current) < 2:
        return []
    try:
        results = await anilist.search_anime_all_statuses(current, limit=25)
    except Exception:
        return []

    choices = []
    for media in results:
        title = media["title"].get("english") or media["title"]["romaji"]
        romaji = media["title"]["romaji"]
        label = title if title == romaji else f"{title} ({romaji})"
        choices.append(app_commands.Choice(name=label[:100], value=str(media["id"])))
    return choices


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


async def unsubscribed_manga_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /subscribe-manga — #7: excludes manga the user already
    subscribed to."""
    if interaction.guild_id is None:
        return []
    manga_list = await db.list_unsubscribed_manga(str(interaction.guild_id), str(interaction.user.id))
    current_lower = current.lower()
    matches = [m for m in manga_list if current_lower in m["nickname"].lower()]
    return [app_commands.Choice(name=m["nickname"], value=m["nickname"]) for m in matches[:25]]


async def subscribed_manga_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /unsubscribe-manga — #7: only shows manga the user is
    currently subscribed to."""
    if interaction.guild_id is None:
        return []
    manga_list = await db.list_subscribed_manga(str(interaction.guild_id), str(interaction.user.id))
    current_lower = current.lower()
    matches = [m for m in manga_list if current_lower in m["nickname"].lower()]
    return [app_commands.Choice(name=m["nickname"], value=m["nickname"]) for m in matches[:25]]


async def _search_with_chapter_counts(current: str, limit: int) -> list[app_commands.Choice[str]]:
    """Shared by both manga autocompletes below — #9: MangaDex has many
    same-named entries (official series, digital colored editions,
    one-shots, doujin works), so we show each result's English chapter
    count to help tell them apart. Results are capped lower than the
    anime/AniList autocomplete (8 vs 25) and counts are fetched
    concurrently with a short timeout each, since Discord only gives
    autocomplete callbacks ~3 seconds total to respond — a title whose
    count doesn't arrive in time just shows without one rather than
    blocking the whole list."""
    results = await mangadex.search_manga(current, limit=limit)
    if not results:
        return []

    counts = await asyncio.gather(
        *[mangadex.get_english_chapter_count_safe(m["id"]) for m in results]
    )

    choices = []
    for manga, count in zip(results, counts):
        title = mangadex._pick_localized(manga["attributes"].get("title")) or "Unknown title"
        label = f"{title} — {count} ch." if count is not None else title
        choices.append(app_commands.Choice(name=label[:100], value=manga["id"]))
    return choices


async def mangadex_search_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /manga-add — live MangaDex search, any title in their catalog."""
    if not current or len(current) < 2:
        return []
    try:
        return await _search_with_chapter_counts(current, limit=8)
    except Exception:
        return []


async def manga_details_search_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /manga — spec wants ANY manga, not just tracked ones, so this
    hits MangaDex live rather than reading from the server's tracked list.
    Value is the MangaDex ID; the command resolves details from that."""
    if not current or len(current) < 2:
        return []
    try:
        return await _search_with_chapter_counts(current, limit=8)
    except Exception:
        return []


async def assign_ping_role_target_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """For /assign-ping-role's target parameter — #11: the command now
    covers both anime and manga, chosen via the Type parameter. Reads
    the already-filled Type value from the interaction namespace to
    decide which tracked list to search; defaults to anime if Type
    hasn't been filled in yet (Discord doesn't guarantee fill order)."""
    if interaction.guild_id is None:
        return []
    kind = getattr(interaction.namespace, "type", None) or "Anime"
    current_lower = current.lower()

    if kind == "Manga":
        manga_list = await db.list_tracked_manga(str(interaction.guild_id))
        matches = [m for m in manga_list if current_lower in m["nickname"].lower()]
    else:
        anime_list = await db.list_tracked_anime(str(interaction.guild_id))
        matches = [a for a in anime_list if current_lower in a["nickname"].lower()]

    return [app_commands.Choice(name=item["nickname"], value=item["nickname"]) for item in matches[:25]]
