"""
MangaDex API client.

Free, no auth required for reads. Rate limit is roughly 5 requests/second
per IP — poller.py spaces out requests with a small delay rather than
firing everything concurrently, and _get() backs off on 429s using the
Retry-After header MangaDex sends.

English-only by design (see am-track-bot memory: no reliable official
source exists for Japanese-language releases, so that was dropped).
"""

import asyncio
from typing import Any

import aiohttp

BASE_URL = "https://api.mangadex.org"
COVERS_URL = "https://uploads.mangadex.org/covers"

# MangaDex's originalLanguage doesn't map to a clean "type" field the way
# AniList's does — this is a best-effort heuristic, not authoritative.
LANGUAGE_TO_TYPE = {
    "ja": "Manga",
    "ko": "Manhwa",
    "zh": "Manhua",
    "zh-hk": "Manhua",
}


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        for attempt in range(3):
            async with session.get(f"{BASE_URL}{path}", params=params) as resp:
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", "1"))
                    await asyncio.sleep(retry_after)
                    continue
                data = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"MangaDex error {resp.status}: {data}")
                return data
        raise RuntimeError(f"MangaDex rate-limited repeatedly on {path}")


def _cover_url(manga_id: str, relationships: list[dict[str, Any]]) -> str | None:
    for rel in relationships:
        if rel.get("type") == "cover_art" and rel.get("attributes"):
            filename = rel["attributes"].get("fileName")
            if filename:
                return f"{COVERS_URL}/{manga_id}/{filename}.512.jpg"
    return None


def _author_name(relationships: list[dict[str, Any]]) -> str | None:
    for rel in relationships:
        if rel.get("type") == "author" and rel.get("attributes"):
            return rel["attributes"].get("name")
    return None


def _pick_localized(field: dict[str, str] | None) -> str | None:
    """MangaDex titles/descriptions are {lang: text} dicts — prefer en."""
    if not field:
        return None
    return field.get("en") or next(iter(field.values()), None)


async def search_manga(title: str, limit: int = 25) -> list[dict[str, Any]]:
    """Used by /manga-add's and /manga's autocomplete — searches MangaDex's
    full catalog, not just what's tracked in this server."""
    data = await _get(
        "/manga",
        params={
            "title": title,
            "limit": limit,
            "includes[]": "cover_art",
            "order[relevance]": "desc",
        },
    )
    return data.get("data", [])


async def get_manga_by_id(manga_id: str) -> dict[str, Any] | None:
    data = await _get(
        f"/manga/{manga_id}",
        params={"includes[]": ["cover_art", "author"]},
    )
    return data.get("data")


async def get_rating(manga_id: str) -> float | None:
    """MangaDex's bayesian average is already on a 0-10 scale."""
    data = await _get(f"/statistics/manga/{manga_id}")
    stats = data.get("statistics", {}).get(manga_id, {})
    rating = stats.get("rating", {})
    average = rating.get("bayesian") or rating.get("average")
    return round(average, 2) if average is not None else None


async def get_english_chapter_count(manga_id: str) -> int:
    data = await _get(
        f"/manga/{manga_id}/aggregate", params={"translatedLanguage[]": "en"}
    )
    volumes = data.get("volumes") or {}
    # MangaDex returns "volumes": [] (an empty list, not {}) when a title
    # has zero chapters in the requested language — .values() would crash
    # on that, so normalize first.
    if isinstance(volumes, list):
        return 0
    total = 0
    for volume in volumes.values():
        chapters = volume.get("chapters") or {}
        if isinstance(chapters, list):
            continue
        total += len(chapters)
    return total


async def get_latest_english_chapter(manga_id: str) -> dict[str, Any] | None:
    """Most recently published English chapter — used to seed a newly
    tracked manga's baseline so /manga-add doesn't trigger a backfill of
    every past chapter as a 'new' notification."""
    data = await _get(
        "/chapter",
        params={
            "manga": manga_id,
            "translatedLanguage[]": "en",
            "order[publishAt]": "desc",
            "limit": 1,
            "contentRating[]": ["safe", "suggestive", "erotica"],
        },
    )
    results = data.get("data", [])
    return results[0] if results else None


async def get_new_english_chapters(manga_id: str, since_publish_at: str | None) -> list[dict[str, Any]]:
    """Chapters published after since_publish_at (ISO8601), oldest first.
    since_publish_at=None means nothing tracked yet — caller should seed
    via get_latest_english_chapter instead of calling this blind."""
    params = {
        "manga": manga_id,
        "translatedLanguage[]": "en",
        "order[publishAt]": "asc",
        "limit": 20,
        "contentRating[]": ["safe", "suggestive", "erotica"],
    }
    if since_publish_at:
        params["publishAtSince"] = since_publish_at
    data = await _get("/chapter", params=params)
    return data.get("data", [])


async def get_full_manga_details(manga_id: str) -> dict[str, Any] | None:
    """Combines get_manga_by_id + to_db_fields + rating + English chapter
    count in one call — used by both /manga-add and /manga (details) so
    they stay consistent."""
    manga = await get_manga_by_id(manga_id)
    if manga is None:
        return None

    fields = to_db_fields(manga)
    rating, total_chapters = await asyncio.gather(
        get_rating(manga_id), get_english_chapter_count(manga_id)
    )
    fields["rating"] = rating
    fields["total_chapters_en"] = total_chapters
    return fields


async def get_english_chapter_count_safe(manga_id: str, timeout: float = 1.5) -> int | None:
    """Same as get_english_chapter_count but bounded — used in autocomplete
    callbacks, which Discord gives roughly 3 seconds total to respond to.
    Returns None (rather than raising/hanging) on timeout or any error, so
    one slow title can't blank out the whole suggestion list."""
    try:
        return await asyncio.wait_for(get_english_chapter_count(manga_id), timeout=timeout)
    except Exception:
        return None


def to_db_fields(manga: dict[str, Any]) -> dict[str, Any]:
    """Maps a raw MangaDex Manga object to the flat dict database.py expects."""
    attrs = manga["attributes"]
    relationships = manga.get("relationships", [])

    title_en = _pick_localized(attrs.get("title"))
    description = _pick_localized(attrs.get("description"))
    genres = [
        tag["attributes"]["name"].get("en")
        for tag in attrs.get("tags", [])
        if tag.get("attributes", {}).get("group") == "genre"
    ]
    genres = [g for g in genres if g]

    original_lang = attrs.get("originalLanguage")
    manga_type = LANGUAGE_TO_TYPE.get(original_lang, "Comic")

    return {
        "title_english": title_en,
        "description": description,
        "cover_image_url": _cover_url(manga["id"], relationships),
        "genres": genres,
        "creator": _author_name(relationships),
        "manga_type": manga_type,
        "status": attrs.get("status"),
        "mangadex_url": f"https://mangadex.org/title/{manga['id']}",
    }
