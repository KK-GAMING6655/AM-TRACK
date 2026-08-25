"""
Embed builders. Every function here takes a plain dict (either a fresh
services/anilist.to_db_fields() result, or a tracked_anime row straight
out of db/database.py — both use the same key names) and returns a
discord.Embed. No Discord API calls happen in this module.
"""

import discord

from utils.time_format import format_countdown, format_release_week, format_full_datetime

BLUE = discord.Color.blue()
SYNOPSIS_LIMIT = 600  # keep spoiler-wrapped synopsis from blowing past embed limits


def _spoiler_synopsis(synopsis: str | None) -> str:
    if not synopsis:
        return "||No synopsis available.||"
    text = synopsis.strip()
    if len(text) > SYNOPSIS_LIMIT:
        text = text[:SYNOPSIS_LIMIT].rsplit(" ", 1)[0] + "..."
    # AniList descriptions can contain literal "||" from source formatting;
    # strip so we don't prematurely close the spoiler tag.
    text = text.replace("||", "")
    return f"||{text}||"


def _fmt_episode_count(aired: int | None, total: int | None) -> str:
    aired_s = str(aired) if aired is not None else "0"
    total_s = str(total) if total is not None else "?"
    return f"{aired_s}/{total_s}"


def _season_label(season: str | None, season_year: int | None) -> str:
    if not season or not season_year:
        return "N/A"
    return f"{season.title()} {season_year}"


def anime_add_confirmation_embed(anime: dict) -> discord.Embed:
    """Posted by /anime add right after a show is tracked."""
    title_en = anime.get("title_english") or anime.get("title_romaji")
    url = anime.get("site_url") or anime.get("anilist_url")
    embed = discord.Embed(
        title=title_en,
        url=url,
        description=_spoiler_synopsis(anime.get("synopsis")),
        color=BLUE,
    )
    thumb = anime.get("cover_image_url")
    banner = anime.get("banner_image_url") or thumb
    if thumb:
        embed.set_thumbnail(url=thumb)
    if banner:
        embed.set_image(url=banner)
    return embed


def anime_details_embed(anime: dict) -> discord.Embed:
    """/anime <nickname> — full detail card."""
    title_en = anime.get("title_english") or anime.get("title_romaji")
    url = anime.get("site_url") or anime.get("anilist_url")
    embed = discord.Embed(
        title=title_en,
        url=url,
        description=_spoiler_synopsis(anime.get("synopsis")),
        color=BLUE,
    )

    genres = anime.get("genres") or []
    embed.add_field(name="Genre", value=", ".join(genres) or "N/A", inline=True)
    embed.add_field(name="Studio", value=anime.get("studio") or "N/A", inline=True)
    embed.add_field(name="Creator", value=anime.get("creator") or "N/A", inline=True)
    embed.add_field(name="Source", value=(anime.get("source_material") or "N/A").replace("_", " ").title(), inline=True)
    embed.add_field(name="Anime status", value=(anime.get("status") or "N/A").replace("_", " ").title(), inline=True)

    score = anime.get("average_score")
    embed.add_field(name="Rating", value=f"{score}/100" if score is not None else "N/A", inline=True)

    embed.add_field(
        name="Total episodes",
        value=_fmt_episode_count(anime.get("last_aired_episode"), anime.get("total_episodes")),
        inline=True,
    )

    next_at = anime.get("next_airing_at")
    if next_at and anime.get("status") in ("RELEASING", "NOT_YET_RELEASED"):
        embed.add_field(name="Release week", value=format_release_week(next_at), inline=True)
        embed.add_field(
            name="Next episode",
            value=f"{format_full_datetime(next_at)} ({format_countdown(next_at)})",
            inline=True,
        )
    else:
        embed.add_field(name="Release week", value="N/A", inline=True)
        embed.add_field(name="Next episode", value="N/A", inline=True)

    banner = anime.get("banner_image_url") or anime.get("cover_image_url")
    if banner:
        embed.set_image(url=banner)
    return embed


def new_episode_embed(anime: dict, episode: int, nickname: str,
                       watchers: list[str] | None = None) -> tuple[str, discord.Embed]:
    """
    Returns (header_text, embed) for the airing-moment notification.
    header_text is the plain-text line sent above the embed
    ("## New episode X of "nickname" has now aired 🔖").
    """
    is_final = (
        anime.get("total_episodes") is not None
        and episode == anime["total_episodes"]
    )
    tag = " (Final episode)" if is_final else ""
    header = f'## New episode {episode} of "{nickname}" has now aired{tag}🔖'

    title_en = anime.get("title_english") or anime.get("title_romaji")
    url = anime.get("site_url") or anime.get("anilist_url")

    embed = discord.Embed(
        title=title_en,
        url=url,
        description=_spoiler_synopsis(anime.get("synopsis")),
        color=BLUE,
    )

    thumb = anime.get("cover_image_url")
    banner = anime.get("banner_image_url") or thumb
    if thumb:
        embed.set_thumbnail(url=thumb)
    if banner:
        embed.set_image(url=banner)

    embed.add_field(
        name="Episodes",
        value=_fmt_episode_count(episode, anime.get("total_episodes")),
        inline=True,
    )
    embed.add_field(name="Season", value=_season_label(anime.get("season"), anime.get("season_year")), inline=True)
    score = anime.get("average_score")
    embed.add_field(name="Rating", value=f"{score}/100" if score is not None else "N/A", inline=True)
    embed.add_field(name="Genre", value=", ".join(anime.get("genres") or []) or "N/A", inline=False)

    next_at = anime.get("next_airing_at")
    next_ep = anime.get("next_airing_episode")
    if next_at and next_ep:
        next_line = f"Episode {next_ep} will air at {format_full_datetime(next_at)} ({format_countdown(next_at)})"
    else:
        next_line = "N/A"
    embed.add_field(name="Next episode", value=next_line, inline=False)

    if watchers:
        mentions = " ".join(f"<@{uid}>" for uid in watchers)
        embed.add_field(name="Watched:", value=mentions, inline=False)

    return header, embed


def reminder_embed(anime: dict, nickname: str, episode: int, days_label: str) -> tuple[str, discord.Embed]:
    """
    days_label is one of 'tomorrow', '7 days', '30 days', '60 days'
    (the header wording differs slightly for 'tomorrow' per spec).
    """
    total = anime.get("total_episodes")
    is_final = total is not None and episode == total
    final_tag = " (Final episode)" if is_final else ""

    if days_label == "tomorrow":
        header = f'New episode of **"{nickname}"** | EP {episode}/{total or "?"} will air tomorrow ⌛'
    else:
        header = f'New episode of **"{nickname}"** | EP {episode}/{total or "?"} will air in {days_label} ⌛{final_tag}'

    banner = anime.get("banner_image_url") or anime.get("cover_image_url")
    embed = discord.Embed(color=BLUE)
    if banner:
        embed.set_image(url=banner)
    embed.set_footer(text=anime.get("title_romaji") or "")

    return header, embed
      
