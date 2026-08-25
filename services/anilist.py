"""
AniList GraphQL client.

AniList's API is free, needs no auth key, and rate-limits at ~90 req/min
(we stay well under that — see poller.py for how batching is used there).
Docs: https://docs.anilist.co
"""

import aiohttp
from typing import Any

ANILIST_URL = "https://graphql.anilist.co"

# Fields shared by every query below — kept in one place so the mapping
# in to_db_fields() always matches what we actually fetched.
MEDIA_FIELDS = """
    id
    idMal
    title { romaji english }
    description(asHtml: false)
    coverImage { extraLarge }
    bannerImage
    genres
    studios(isMain: true) { nodes { name } }
    staff(sort: RELEVANCE, perPage: 1) {
        edges { role node { name { full } } }
    }
    source
    status
    averageScore
    season
    seasonYear
    episodes
    nextAiringEpisode { episode airingAt }
    siteUrl
"""


async def _post(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            ANILIST_URL, json={"query": query, "variables": variables}
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"AniList error {resp.status}: {data}")
            return data["data"]


async def search_anime(search_text: str, limit: int = 25) -> list[dict[str, Any]]:
    """Used by /anime add's autocomplete. Restricted to TV/ONA anime that
    is currently releasing or not-yet-released, per the spec (only
    releasing/upcoming shows should be addable)."""
    query = """
    query ($search: String, $perPage: Int) {
        Page(page: 1, perPage: $perPage) {
            media(
                search: $search
                type: ANIME
                status_in: [RELEASING, NOT_YET_RELEASED]
                sort: POPULARITY_DESC
            ) {
                id
                title { romaji english }
                status
            }
        }
    }
    """
    data = await _post(query, {"search": search_text, "perPage": limit})
    return data["Page"]["media"]


async def get_anime_by_id(anilist_id: int) -> dict[str, Any] | None:
    query = f"""
    query ($id: Int) {{
        Media(id: $id, type: ANIME) {{
            {MEDIA_FIELDS}
        }}
    }}
    """
    data = await _post(query, {"id": anilist_id})
    return data.get("Media")


async def get_many_anime_by_ids(anilist_ids: list[int]) -> list[dict[str, Any]]:
    """Batched lookup for the polling loop — one request covers up to
    ~50 shows via AniList's `id_in` filter, instead of one request each."""
    if not anilist_ids:
        return []
    query = f"""
    query ($ids: [Int]) {{
        Page(page: 1, perPage: 50) {{
            media(id_in: $ids, type: ANIME) {{
                {MEDIA_FIELDS}
            }}
        }}
    }}
    """
    results = []
    # AniList caps id_in-style pages, so chunk defensively at 50 per call
    for i in range(0, len(anilist_ids), 50):
        chunk = anilist_ids[i : i + 50]
        data = await _post(query, {"ids": chunk})
        results.extend(data["Page"]["media"])
    return results


def compute_last_aired_episode(media: dict[str, Any]) -> int:
    """Best-effort 'how many episodes have aired so far' from AniList data.
    AniList doesn't expose this directly — it only gives the *next*
    airing episode, so we derive it: next.episode - 1, or the full
    episode count once the show is FINISHED, or 0 if nothing has aired."""
    next_airing = media.get("nextAiringEpisode")
    if next_airing:
        return max(next_airing["episode"] - 1, 0)
    if media.get("status") == "FINISHED" and media.get("episodes"):
        return media["episodes"]
    return 0


def to_db_fields(media: dict[str, Any]) -> dict[str, Any]:
    """Maps a raw AniList Media object to the flat dict database.py expects."""
    studios = media.get("studios", {}).get("nodes", [])
    studio_name = studios[0]["name"] if studios else None

    creator_name = None
    for edge in media.get("staff", {}).get("edges", []):
        role = (edge.get("role") or "").lower()
        if "original creator" in role or "story" in role or "author" in role:
            creator_name = edge["node"]["name"]["full"]
            break
    if creator_name is None and media.get("staff", {}).get("edges"):
        creator_name = media["staff"]["edges"][0]["node"]["name"]["full"]

    next_airing = media.get("nextAiringEpisode")

    return {
        "title_romaji": media["title"]["romaji"],
        "title_english": media["title"].get("english") or media["title"]["romaji"],
        "cover_image_url": media.get("coverImage", {}).get("extraLarge"),
        "banner_image_url": media.get("bannerImage"),
        "synopsis": media.get("description"),
        "genres": media.get("genres", []),
        "studio": studio_name,
        "creator": creator_name,
        "source_material": media.get("source"),
        "status": media.get("status"),
        "average_score": media.get("averageScore"),
        "season": media.get("season"),
        "season_year": media.get("seasonYear"),
        "total_episodes": media.get("episodes"),
        "next_airing_at": next_airing["airingAt"] if next_airing else None,
        "next_airing_episode": next_airing["episode"] if next_airing else None,
        "site_url": media.get("siteUrl"),
        "mal_url": (
            f"https://myanimelist.net/anime/{media['idMal']}"
            if media.get("idMal") else None
        ),
        "last_aired_episode": compute_last_aired_episode(media),
          }
  
