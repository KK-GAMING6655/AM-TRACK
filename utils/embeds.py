"""
Embed builders. Every function here takes a plain dict (either a fresh
services/anilist.to_db_fields() result, or a tracked_anime row straight
out of db/database.py — both use the same key names) and returns a
discord.Embed. No Discord API calls happen in this module.
"""

import re

import discord

from utils.time_format import format_discord_timestamp, format_release_week

BLUE = discord.Color.blue()
SYNOPSIS_LIMIT = 600  # keep spoiler-wrapped synopsis from blowing past embed limits

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_BLANK_LINE_RE = re.compile(r"\n{3,}")


def _clean_text(text: str) -> str:
    """Strips stray HTML fragments (<br>, <i>, </i>, etc.) that AniList's
    asHtml:false and MangaDex's raw descriptions don't always fully clean,
    and collapses the blank lines that removing <br><br> tends to leave
    behind. Plain-text only — never removes real prose."""
    text = _HTML_TAG_RE.sub("", text)
    text = text.replace("&nbsp;", " ")
    text = _MULTI_BLANK_LINE_RE.sub("\n\n", text)
    return text.strip()


def _truncate_at_sentence(text: str, limit: int) -> str:
    """Cuts at the end of the last complete sentence within `limit`
    characters, never mid-word/mid-sentence, and never appends "...".
    If the description is short enough, returns it unchanged."""
    if len(text) <= limit:
        return text

    window = text[:limit]
    last_end = -1
    for punct in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        idx = window.rfind(punct)
        if idx > last_end:
            last_end = idx

    if last_end != -1:
        return text[: last_end + 1].strip()

    # no sentence boundary found within the limit — fall back to the
    # last full paragraph break instead of cutting mid-sentence
    para_break = window.rfind("\n\n")
    if para_break > 0:
        return text[:para_break].strip()

    # last resort: nothing to cleanly break on at all (rare — a single
    # very long run-on description). Better to show the whole thing than
    # cut it off mid-thought.
    return text.strip()


def _spoiler_synopsis(synopsis: str | None) -> str:
    if not synopsis:
        return "||No synopsis available.||"
    text = _clean_text(synopsis)
    text = _truncate_at_sentence(text, SYNOPSIS_LIMIT)
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
            value=format_discord_timestamp(next_at),
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
        next_line = f"Episode {next_ep} will air at {format_discord_timestamp(next_at)}"
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
      


# ---------------------------------------------------------------------------
# Part 2: Manga embeds — same conventions as the anime ones above, pink
# instead of blue per spec.
# ---------------------------------------------------------------------------

PINK = discord.Color.from_rgb(255, 105, 180)


def _spoiler_description(description: str | None) -> str:
    """Manga descriptions now get the same treatment as anime synopses:
    HTML-tag cleanup, sentence-safe truncation, and spoiler-wrapping."""
    if not description:
        return "||No description available.||"
    text = _clean_text(description)
    text = _truncate_at_sentence(text, SYNOPSIS_LIMIT)
    text = text.replace("||", "")
    return f"||{text}||"

def manga_add_confirmation_embed(manga: dict) -> discord.Embed:
    """Posted by /manga-add right after a title is tracked."""
    embed = discord.Embed(
        title=manga.get("title_english"),
        url=manga.get("mangadex_url"),
        description=_spoiler_description(manga.get("description")),
        color=PINK,
    )
    cover = manga.get("cover_image_url")
    if cover:
        embed.set_image(url=cover)
    return embed


def manga_details_embed(manga: dict) -> discord.Embed:
    """/manga <name> — full detail card."""
    embed = discord.Embed(
        title=manga.get("title_english"),
        url=manga.get("mangadex_url"),
        description=_spoiler_description(manga.get("description")),
        color=PINK,
    )

    genres = manga.get("genres") or []
    embed.add_field(name="Genre", value=", ".join(genres) or "N/A", inline=True)
    embed.add_field(name="Creator", value=manga.get("creator") or "N/A", inline=True)
    embed.add_field(name="Type", value=manga.get("manga_type") or "N/A", inline=True)
    embed.add_field(name="Manga status", value=(manga.get("status") or "N/A").title(), inline=True)

    rating = manga.get("rating")
    embed.add_field(name="Rating", value=f"{rating}/10" if rating is not None else "N/A", inline=True)

    total_en = manga.get("total_chapters_en")
    embed.add_field(
        name="Total chapters",
        value=f"English: {total_en if total_en is not None else '?'} chapters",
        inline=True,
    )

    cover = manga.get("cover_image_url")
    if cover:
        embed.set_image(url=cover)
    return embed


def new_chapter_embed(manga: dict, chapter_number: str, nickname: str,
                       readers: list[str] | None = None) -> tuple[str, discord.Embed]:
    """Returns (header_text, embed) for a new-chapter notification."""
    header = f'## New chapter {chapter_number} of "{nickname}" has now aired 🔖'

    embed = discord.Embed(
        title=manga.get("title_english"),
        url=manga.get("mangadex_url"),
        description=_spoiler_description(manga.get("description")),
        color=PINK,
    )

    embed.add_field(name="Chapter", value=str(chapter_number), inline=True)
    embed.add_field(name="Language", value="English", inline=True)

    rating = manga.get("rating")
    embed.add_field(name="Manga rating", value=f"{rating}/10" if rating is not None else "N/A", inline=True)
    embed.add_field(name="Genre", value=", ".join(manga.get("genres") or []) or "N/A", inline=False)

    cover = manga.get("cover_image_url")
    if cover:
        embed.set_image(url=cover)

    if readers:
        mentions = " ".join(f"<@{uid}>" for uid in readers)
        embed.add_field(name="Read:", value=mentions, inline=False)

    return header, embed


def manga_error_embed(message: str) -> discord.Embed:
    """Per Note8 — all messages, including errors, should be embeds for
    the manga side (Part 1's errors stayed plain-text for consistency
    with what was already deployed; this only applies to Part 2)."""
    return discord.Embed(description=message, color=PINK)



# ---------------------------------------------------------------------------
# #13: /animelist and /mangalist
# ---------------------------------------------------------------------------

LIST_EMBED_CHAR_LIMIT = 3900  # stay safely under Discord's 4096 embed-description cap


def build_anime_list_embeds(rows_with_counts: list[tuple[dict, int]]) -> list[discord.Embed]:
    """rows_with_counts: [(tracked_anime_row, subscriber_count), ...].
    Returns one or more embeds (split only if the list is long enough to
    exceed Discord's description limit)."""
    if not rows_with_counts:
        embed = discord.Embed(description="No anime tracked in this server yet.", color=BLUE)
        return [embed]

    lines = []
    for i, (anime, count) in enumerate(rows_with_counts, start=1):
        title = anime.get("title_english") or anime.get("title_romaji") or anime["nickname"]
        url = anime.get("site_url") or anime.get("anilist_url")
        link_text = f"[{title}]({url})" if url else title
        lines.append(f"{i}) {link_text} — {count} subscriber{'s' if count != 1 else ''}")

    return _chunk_lines_into_embeds(lines, title="Tracked Anime", color=BLUE)


def build_manga_list_embeds(rows_with_counts: list[tuple[dict, int]]) -> list[discord.Embed]:
    """Same shape as build_anime_list_embeds, for manga."""
    if not rows_with_counts:
        embed = discord.Embed(description="No manga tracked in this server yet.", color=PINK)
        return [embed]

    lines = []
    for i, (manga, count) in enumerate(rows_with_counts, start=1):
        title = manga.get("title_english") or manga["nickname"]
        url = manga.get("mangadex_url")
        link_text = f"[{title}]({url})" if url else title
        lines.append(f"{i}) {link_text} — {count} subscriber{'s' if count != 1 else ''}")

    return _chunk_lines_into_embeds(lines, title="Tracked Manga", color=PINK)


def _chunk_lines_into_embeds(lines: list[str], title: str, color: discord.Color) -> list[discord.Embed]:
    embeds = []
    current_lines: list[str] = []
    current_len = 0

    for line in lines:
        # +1 for the newline that will join it
        if current_len + len(line) + 1 > LIST_EMBED_CHAR_LIMIT and current_lines:
            embeds.append(discord.Embed(title=title, description="\n".join(current_lines), color=color))
            current_lines = []
            current_len = 0
        current_lines.append(line)
        current_len += len(line) + 1

    if current_lines:
        embeds.append(discord.Embed(title=title, description="\n".join(current_lines), color=color))

    # only the first embed needs the title (avoids repeating it on every page)
    for embed in embeds[1:]:
        embed.title = None

    return embeds
