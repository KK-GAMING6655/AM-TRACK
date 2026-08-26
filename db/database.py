"""
Turso (libSQL) database layer for AM Track.

One shared async client, opened once at bot startup via init_db() and
closed in on_shutdown(). All other modules import `db` from here and
call its methods rather than touching the HTTP client directly.

We talk to Turso's HTTP pipeline API (v2/pipeline) directly via aiohttp
instead of the official `libsql-client` PyPI package. That package is
unmaintained and its 0.3.1 release throws `KeyError: 'result'` against
current Turso servers — a client-side bug, not anything wrong with the
database or credentials. The v2/pipeline HTTP API is simple, stable,
and documented at https://docs.turso.tech/sdk/http/reference — this
wrapper implements just enough of it for our needs.
"""

import base64
import json
import os
from pathlib import Path
from typing import Any

import aiohttp

_client: "TursoHTTPClient | None" = None


class ResultSet:
    """Minimal stand-in for libsql_client's ResultSet — keeps every query
    function below (which reads .columns / .rows) unchanged."""

    def __init__(self, columns: list[str], rows: list[list[Any]]):
        self.columns = columns
        self.rows = rows


class TursoHTTPClient:
    """Thin async wrapper around Turso's /v2/pipeline HTTP endpoint."""

    def __init__(self, url: str, auth_token: str):
        self.base_url = url.rstrip("/")
        self.auth_token = auth_token
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        args = args or []
        payload = {
            "requests": [
                {"type": "execute", "stmt": {"sql": sql, "args": [self._to_arg(a) for a in args]}},
                {"type": "close"},
            ]
        }
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
        }
        session = self._get_session()
        async with session.post(f"{self.base_url}/v2/pipeline", json=payload, headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"Turso HTTP error {resp.status}: {data}")

        result = data["results"][0]
        if result["type"] == "error":
            raise RuntimeError(f"Turso SQL error for statement {sql!r}: {result['error']}")

        exec_result = result["response"]["result"]
        columns = [c["name"] for c in exec_result.get("cols", [])]
        rows = [
            [self._from_cell(cell) for cell in row]
            for row in exec_result.get("rows", [])
        ]
        return ResultSet(columns, rows)

    @staticmethod
    def _to_arg(value: Any) -> dict[str, Any]:
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "integer", "value": str(int(value))}
        if isinstance(value, int):
            return {"type": "integer", "value": str(value)}
        if isinstance(value, float):
            return {"type": "float", "value": value}
        if isinstance(value, (bytes, bytearray)):
            return {"type": "blob", "base64": base64.b64encode(value).decode()}
        return {"type": "text", "value": str(value)}

    @staticmethod
    def _from_cell(cell: dict[str, Any]) -> Any:
        cell_type = cell.get("type")
        if cell_type == "null":
            return None
        if cell_type == "integer":
            return int(cell["value"])
        if cell_type == "float":
            return float(cell["value"])
        if cell_type == "text":
            return cell["value"]
        if cell_type == "blob":
            return base64.b64decode(cell.get("base64", ""))
        return cell.get("value")


async def init_db() -> None:
    """Create the client and apply schema.sql. Call once on bot startup.

    Normalizes libsql:// / wss:// URLs to https:// — Turso's HTTP API
    works universally, whereas the websocket transport some client
    libraries default to gets rejected by some Turso deployments.
    """
    global _client
    url = os.environ["TURSO_DATABASE_URL"]
    auth_token = os.environ["TURSO_AUTH_TOKEN"]

    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    elif url.startswith("wss://"):
        url = "https://" + url[len("wss://"):]
    elif url.startswith("ws://"):
        url = "http://" + url[len("ws://"):]

    _client = TursoHTTPClient(url=url, auth_token=auth_token)

    schema_path = Path(__file__).parent / "schema.sql"
    schema_sql = _strip_sql_comments(schema_path.read_text())
    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
    for stmt in statements:
        await _client.execute(stmt)


def _strip_sql_comments(sql: str) -> str:
    """Removes '-- ...' comments before statement-splitting on ';'.

    schema.sql's comments contain literal semicolons in the prose (e.g.
    "...identifies the show; nickname is per-server..."), which broke a
    naive split(";") on the raw file — it cut mid-comment and sent
    comment-only fragments to Turso as if they were statements. None of
    our comments live inside a quoted string, so a plain '--' search per
    line is safe here.
    """
    lines = []
    for line in sql.splitlines():
        idx = line.find("--")
        if idx != -1:
            line = line[:idx]
        lines.append(line)
    return "\n".join(lines)


async def close_db() -> None:
    if _client is not None:
        await _client.close()


def _client_or_raise() -> TursoHTTPClient:
    if _client is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _client


# ---------------------------------------------------------------------------
# servers / admins
# ---------------------------------------------------------------------------

async def ensure_server(guild_id: str, owner_id: str) -> None:
    """Insert a server row if one doesn't exist yet. Safe to call repeatedly."""
    c = _client_or_raise()
    await c.execute(
        "INSERT INTO servers (guild_id, owner_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO NOTHING",
        [guild_id, owner_id],
    )


async def set_channel(guild_id: str, kind: str, channel_id: str) -> None:
    """kind is 'anime' or 'manga'."""
    c = _client_or_raise()
    column = "anime_channel_id" if kind == "anime" else "manga_channel_id"
    await c.execute(
        f"UPDATE servers SET {column} = ? WHERE guild_id = ?",
        [channel_id, guild_id],
    )


async def get_server(guild_id: str) -> dict[str, Any] | None:
    c = _client_or_raise()
    rs = await c.execute("SELECT * FROM servers WHERE guild_id = ?", [guild_id])
    if not rs.rows:
        return None
    return dict(zip(rs.columns, rs.rows[0]))


async def add_admin(guild_id: str, user_id: str, added_by: str) -> None:
    c = _client_or_raise()
    await c.execute(
        "INSERT INTO admins (guild_id, user_id, added_by) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO NOTHING",
        [guild_id, user_id, added_by],
    )


async def remove_admin(guild_id: str, user_id: str) -> None:
    c = _client_or_raise()
    await c.execute(
        "DELETE FROM admins WHERE guild_id = ? AND user_id = ?",
        [guild_id, user_id],
    )


async def is_admin(guild_id: str, user_id: str) -> bool:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT 1 FROM admins WHERE guild_id = ? AND user_id = ?",
        [guild_id, user_id],
    )
    return len(rs.rows) > 0


async def list_admins(guild_id: str) -> list[str]:
    c = _client_or_raise()
    rs = await c.execute("SELECT user_id FROM admins WHERE guild_id = ?", [guild_id])
    return [row[0] for row in rs.rows]


# ---------------------------------------------------------------------------
# tracked_anime
# ---------------------------------------------------------------------------

async def add_tracked_anime(guild_id: str, anilist_id: int, nickname: str,
                             added_by: str, anime_data: dict[str, Any]) -> int | None:
    """
    anime_data is the cached-field dict produced by services/anilist.py's
    to_db_fields(). Returns the new row id, or None if the nickname is
    already taken on this server.
    """
    c = _client_or_raise()
    existing = await c.execute(
        "SELECT 1 FROM tracked_anime WHERE guild_id = ? AND nickname = ?",
        [guild_id, nickname],
    )
    if existing.rows:
        return None

    rs = await c.execute(
        """
        INSERT INTO tracked_anime (
            guild_id, anilist_id, nickname, added_by,
            title_romaji, title_english, cover_image_url, banner_image_url,
            synopsis, genres, studio, creator, source_material, status,
            average_score, season, season_year, total_episodes,
            anilist_url, mal_url,
            last_aired_episode, next_airing_at, next_airing_episode
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        [
            guild_id, anilist_id, nickname, added_by,
            anime_data.get("title_romaji"), anime_data.get("title_english"),
            anime_data.get("cover_image_url"), anime_data.get("banner_image_url"),
            anime_data.get("synopsis"), json.dumps(anime_data.get("genres", [])),
            anime_data.get("studio"), anime_data.get("creator"),
            anime_data.get("source_material"), anime_data.get("status"),
            anime_data.get("average_score"), anime_data.get("season"),
            anime_data.get("season_year"), anime_data.get("total_episodes"),
            anime_data.get("site_url"), anime_data.get("mal_url"),
            anime_data.get("last_aired_episode", 0),
            anime_data.get("next_airing_at"), anime_data.get("next_airing_episode"),
        ],
    )
    return rs.rows[0][0]


async def remove_tracked_anime(guild_id: str, nickname: str) -> bool:
    c = _client_or_raise()
    rs = await c.execute(
        "DELETE FROM tracked_anime WHERE guild_id = ? AND nickname = ? RETURNING id",
        [guild_id, nickname],
    )
    return len(rs.rows) > 0


async def get_tracked_anime_by_nickname(guild_id: str, nickname: str) -> dict[str, Any] | None:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT * FROM tracked_anime WHERE guild_id = ? AND nickname = ?",
        [guild_id, nickname],
    )
    if not rs.rows:
        return None
    row = dict(zip(rs.columns, rs.rows[0]))
    row["genres"] = json.loads(row["genres"]) if row.get("genres") else []
    return row


async def get_tracked_anime_by_site_url(guild_id: str, anilist_url: str) -> dict[str, Any] | None:
    """Used by the Subscribe button, which only has the embed's AniList
    URL to identify which tracked_anime row it belongs to."""
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT * FROM tracked_anime WHERE guild_id = ? AND anilist_url = ?",
        [guild_id, anilist_url],
    )
    if not rs.rows:
        return None
    row = dict(zip(rs.columns, rs.rows[0]))
    row["genres"] = json.loads(row["genres"]) if row.get("genres") else []
    return row


async def get_tracked_anime_by_id(tracked_anime_id: int) -> dict[str, Any] | None:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT * FROM tracked_anime WHERE id = ?", [tracked_anime_id]
    )
    if not rs.rows:
        return None
    row = dict(zip(rs.columns, rs.rows[0]))
    row["genres"] = json.loads(row["genres"]) if row.get("genres") else []
    return row


async def list_tracked_anime(guild_id: str) -> list[dict[str, Any]]:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT * FROM tracked_anime WHERE guild_id = ? ORDER BY nickname",
        [guild_id],
    )
    out = []
    for row in rs.rows:
        d = dict(zip(rs.columns, row))
        d["genres"] = json.loads(d["genres"]) if d.get("genres") else []
        out.append(d)
    return out


async def list_all_tracked_anime_for_anilist_id(anilist_id: int) -> list[dict[str, Any]]:
    """Every server's tracked_anime row for a given AniList show — used by
    the polling loop to fan a single new-episode event out to every server."""
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT * FROM tracked_anime WHERE anilist_id = ?", [anilist_id]
    )
    out = []
    for row in rs.rows:
        d = dict(zip(rs.columns, row))
        d["genres"] = json.loads(d["genres"]) if d.get("genres") else []
        out.append(d)
    return out


async def list_distinct_anilist_ids() -> list[int]:
    """All AniList IDs currently tracked by at least one server — the set
    the polling loop needs to check each cycle."""
    c = _client_or_raise()
    rs = await c.execute("SELECT DISTINCT anilist_id FROM tracked_anime")
    return [row[0] for row in rs.rows]


async def update_anime_cache(tracked_anime_id: int, anime_data: dict[str, Any]) -> None:
    """Refresh cached AniList fields for one tracked_anime row (called by
    the polling loop after each AniList fetch)."""
    c = _client_or_raise()
    await c.execute(
        """
        UPDATE tracked_anime SET
            title_romaji = ?, title_english = ?, cover_image_url = ?,
            banner_image_url = ?, synopsis = ?, genres = ?, studio = ?,
            creator = ?, source_material = ?, status = ?, average_score = ?,
            season = ?, season_year = ?, total_episodes = ?,
            anilist_url = ?, mal_url = ?,
            next_airing_at = ?, next_airing_episode = ?
        WHERE id = ?
        """,
        [
            anime_data.get("title_romaji"), anime_data.get("title_english"),
            anime_data.get("cover_image_url"), anime_data.get("banner_image_url"),
            anime_data.get("synopsis"), json.dumps(anime_data.get("genres", [])),
            anime_data.get("studio"), anime_data.get("creator"),
            anime_data.get("source_material"), anime_data.get("status"),
            anime_data.get("average_score"), anime_data.get("season"),
            anime_data.get("season_year"), anime_data.get("total_episodes"),
            anime_data.get("site_url"), anime_data.get("mal_url"),
            anime_data.get("next_airing_at"), anime_data.get("next_airing_episode"),
            tracked_anime_id,
        ],
    )


async def set_last_aired_episode(tracked_anime_id: int, episode: int, aired_at: int | None = None) -> None:
    c = _client_or_raise()
    await c.execute(
        "UPDATE tracked_anime SET last_aired_episode = ?, last_episode_aired_at = ? WHERE id = ?",
        [episode, aired_at, tracked_anime_id],
    )

# ---------------------------------------------------------------------------
# subscriptions
# ---------------------------------------------------------------------------

SUBSCRIBER_LIMIT = 100


async def subscribe(tracked_anime_id: int, user_id: str) -> str:
    """Returns 'ok', 'already' or 'full'."""
    c = _client_or_raise()
    existing = await c.execute(
        "SELECT 1 FROM subscriptions WHERE tracked_anime_id = ? AND user_id = ?",
        [tracked_anime_id, user_id],
    )
    if existing.rows:
        return "already"

    count_rs = await c.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE tracked_anime_id = ?",
        [tracked_anime_id],
    )
    if count_rs.rows[0][0] >= SUBSCRIBER_LIMIT:
        return "full"

    await c.execute(
        "INSERT INTO subscriptions (tracked_anime_id, user_id) VALUES (?, ?)",
        [tracked_anime_id, user_id],
    )
    return "ok"


async def unsubscribe(tracked_anime_id: int, user_id: str) -> bool:
    c = _client_or_raise()
    rs = await c.execute(
        "DELETE FROM subscriptions WHERE tracked_anime_id = ? AND user_id = ? "
        "RETURNING user_id",
        [tracked_anime_id, user_id],
    )
    return len(rs.rows) > 0


async def list_subscribers(tracked_anime_id: int) -> list[str]:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT user_id FROM subscriptions WHERE tracked_anime_id = ?",
        [tracked_anime_id],
    )
    return [row[0] for row in rs.rows]


# ---------------------------------------------------------------------------
# role_pings  (max 1 role per anime, enforced here by replace-on-add)
# ---------------------------------------------------------------------------

async def set_role_ping(tracked_anime_id: int, role_id: str, assigned_by: str) -> None:
    c = _client_or_raise()
    await c.execute(
        """
        INSERT INTO role_pings (tracked_anime_id, role_id, assigned_by)
        VALUES (?, ?, ?)
        ON CONFLICT(tracked_anime_id) DO UPDATE SET
            role_id = excluded.role_id, assigned_by = excluded.assigned_by
        """,
        [tracked_anime_id, role_id, assigned_by],
    )


async def remove_role_ping(tracked_anime_id: int, role_id: str) -> bool:
    """Only clears if the stored role matches — so /assign ping role
    Enable:Remove with the wrong role is a no-op the caller can flag."""
    c = _client_or_raise()
    rs = await c.execute(
        "DELETE FROM role_pings WHERE tracked_anime_id = ? AND role_id = ? "
        "RETURNING role_id",
        [tracked_anime_id, role_id],
    )
    return len(rs.rows) > 0


async def get_role_ping(tracked_anime_id: int) -> str | None:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT role_id FROM role_pings WHERE tracked_anime_id = ?",
        [tracked_anime_id],
    )
    return rs.rows[0][0] if rs.rows else None


# ---------------------------------------------------------------------------
# reminder_state
# ---------------------------------------------------------------------------

async def get_reminder_state(tracked_anime_id: int, episode: int) -> dict[str, Any]:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT * FROM reminder_state WHERE tracked_anime_id = ? AND episode = ?",
        [tracked_anime_id, episode],
    )
    if rs.rows:
        return dict(zip(rs.columns, rs.rows[0]))
    await c.execute(
        "INSERT INTO reminder_state (tracked_anime_id, episode) VALUES (?, ?)",
        [tracked_anime_id, episode],
    )
    return {
        "tracked_anime_id": tracked_anime_id, "episode": episode,
        "sent_60d": 0, "sent_30d": 0, "sent_7d": 0, "sent_1d": 0, "sent_airing": 0,
    }


async def mark_reminder_sent(tracked_anime_id: int, episode: int, milestone: str) -> None:
    """milestone is one of: sent_60d, sent_30d, sent_7d, sent_1d, sent_airing."""
    c = _client_or_raise()
    await c.execute(
        f"UPDATE reminder_state SET {milestone} = 1 "
        "WHERE tracked_anime_id = ? AND episode = ?",
        [tracked_anime_id, episode],
    )


# ---------------------------------------------------------------------------
# watched_status
# ---------------------------------------------------------------------------

async def create_watched_row(message_id: str, channel_id: str,
                              tracked_anime_id: int, episode: int) -> None:
    c = _client_or_raise()
    await c.execute(
        "INSERT INTO watched_status (message_id, channel_id, tracked_anime_id, episode) "
        "VALUES (?, ?, ?, ?)",
        [message_id, channel_id, tracked_anime_id, episode],
    )


async def toggle_watched(message_id: str, user_id: str) -> list[str]:
    """Adds user_id if absent, removes if present. Returns the new watcher list."""
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT watchers FROM watched_status WHERE message_id = ?", [message_id]
    )
    if not rs.rows:
        return []
    watchers: list[str] = json.loads(rs.rows[0][0])
    if user_id in watchers:
        watchers.remove(user_id)
    else:
        watchers.append(user_id)
    await c.execute(
        "UPDATE watched_status SET watchers = ? WHERE message_id = ?",
        [json.dumps(watchers), message_id],
    )
    return watchers


async def get_watched(message_id: str) -> dict[str, Any] | None:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT * FROM watched_status WHERE message_id = ?", [message_id]
    )
    if not rs.rows:
        return None
    row = dict(zip(rs.columns, rs.rows[0]))
    row["watchers"] = json.loads(row["watchers"])
    return row
  

# ===========================================================================
# Part 2: Manga (mirrors the anime functions above — see schema.sql for
# the table shapes)
# ===========================================================================

async def add_tracked_manga(guild_id: str, mangadex_id: str, nickname: str,
                             added_by: str, manga_data: dict[str, Any]) -> int | None:
    """manga_data is a merged dict of services/mangadex.to_db_fields() plus
    rating/total_chapters_en/last_chapter_* — see cogs/manga.py's manga_add.
    Returns the new row id, or None if the nickname is already taken."""
    c = _client_or_raise()
    existing = await c.execute(
        "SELECT 1 FROM tracked_manga WHERE guild_id = ? AND nickname = ?",
        [guild_id, nickname],
    )
    if existing.rows:
        return None

    rs = await c.execute(
        """
        INSERT INTO tracked_manga (
            guild_id, mangadex_id, nickname, added_by,
            title_english, cover_image_url, description, genres, creator,
            manga_type, status, rating, total_chapters_en, mangadex_url,
            last_chapter_id, last_chapter_number, last_chapter_published_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        [
            guild_id, mangadex_id, nickname, added_by,
            manga_data.get("title_english"), manga_data.get("cover_image_url"),
            manga_data.get("description"), json.dumps(manga_data.get("genres", [])),
            manga_data.get("creator"), manga_data.get("manga_type"),
            manga_data.get("status"), manga_data.get("rating"),
            manga_data.get("total_chapters_en"), manga_data.get("mangadex_url"),
            manga_data.get("last_chapter_id"), manga_data.get("last_chapter_number"),
            manga_data.get("last_chapter_published_at"),
        ],
    )
    return rs.rows[0][0]


async def remove_tracked_manga(guild_id: str, nickname: str) -> bool:
    c = _client_or_raise()
    rs = await c.execute(
        "DELETE FROM tracked_manga WHERE guild_id = ? AND nickname = ? RETURNING id",
        [guild_id, nickname],
    )
    return len(rs.rows) > 0


def _row_to_manga_dict(columns: list[str], row: list[Any]) -> dict[str, Any]:
    d = dict(zip(columns, row))
    d["genres"] = json.loads(d["genres"]) if d.get("genres") else []
    return d


async def get_tracked_manga_by_nickname(guild_id: str, nickname: str) -> dict[str, Any] | None:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT * FROM tracked_manga WHERE guild_id = ? AND nickname = ?",
        [guild_id, nickname],
    )
    if not rs.rows:
        return None
    return _row_to_manga_dict(rs.columns, rs.rows[0])


async def get_tracked_manga_by_id(tracked_manga_id: int) -> dict[str, Any] | None:
    c = _client_or_raise()
    rs = await c.execute("SELECT * FROM tracked_manga WHERE id = ?", [tracked_manga_id])
    if not rs.rows:
        return None
    return _row_to_manga_dict(rs.columns, rs.rows[0])


async def get_tracked_manga_by_url(guild_id: str, mangadex_url: str) -> dict[str, Any] | None:
    """Used by the manga Subscribe button, same pattern as the anime one."""
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT * FROM tracked_manga WHERE guild_id = ? AND mangadex_url = ?",
        [guild_id, mangadex_url],
    )
    if not rs.rows:
        return None
    return _row_to_manga_dict(rs.columns, rs.rows[0])


async def list_tracked_manga(guild_id: str) -> list[dict[str, Any]]:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT * FROM tracked_manga WHERE guild_id = ? ORDER BY nickname", [guild_id]
    )
    return [_row_to_manga_dict(rs.columns, row) for row in rs.rows]


async def list_all_tracked_manga_for_mangadex_id(mangadex_id: str) -> list[dict[str, Any]]:
    """Every server's tracked_manga row for a given MangaDex title — used by
    the polling loop to fan a new-chapter event out to every server."""
    c = _client_or_raise()
    rs = await c.execute("SELECT * FROM tracked_manga WHERE mangadex_id = ?", [mangadex_id])
    return [_row_to_manga_dict(rs.columns, row) for row in rs.rows]


async def list_distinct_mangadex_ids() -> list[str]:
    c = _client_or_raise()
    rs = await c.execute("SELECT DISTINCT mangadex_id FROM tracked_manga")
    return [row[0] for row in rs.rows]


async def update_manga_cache(tracked_manga_id: int, manga_data: dict[str, Any]) -> None:
    """Refreshes cached MangaDex metadata (title/cover/description/etc) —
    deliberately does NOT touch last_chapter_* (that's the polling loop's
    detection state, updated separately via set_last_chapter)."""
    c = _client_or_raise()
    await c.execute(
        """
        UPDATE tracked_manga SET
            title_english = ?, cover_image_url = ?, description = ?, genres = ?,
            creator = ?, manga_type = ?, status = ?, rating = ?,
            total_chapters_en = ?, mangadex_url = ?
        WHERE id = ?
        """,
        [
            manga_data.get("title_english"), manga_data.get("cover_image_url"),
            manga_data.get("description"), json.dumps(manga_data.get("genres", [])),
            manga_data.get("creator"), manga_data.get("manga_type"),
            manga_data.get("status"), manga_data.get("rating"),
            manga_data.get("total_chapters_en"), manga_data.get("mangadex_url"),
            tracked_manga_id,
        ],
    )


async def set_last_chapter(tracked_manga_id: int, chapter_id: str,
                            chapter_number: str, published_at: str) -> None:
    c = _client_or_raise()
    await c.execute(
        "UPDATE tracked_manga SET last_chapter_id = ?, last_chapter_number = ?, "
        "last_chapter_published_at = ? WHERE id = ?",
        [chapter_id, chapter_number, published_at, tracked_manga_id],
    )


# ---------------------------------------------------------------------------
# manga_subscriptions
# ---------------------------------------------------------------------------

async def subscribe_manga(tracked_manga_id: int, user_id: str) -> str:
    """Returns 'ok', 'already' or 'full' — mirrors subscribe() above."""
    c = _client_or_raise()
    existing = await c.execute(
        "SELECT 1 FROM manga_subscriptions WHERE tracked_manga_id = ? AND user_id = ?",
        [tracked_manga_id, user_id],
    )
    if existing.rows:
        return "already"

    count_rs = await c.execute(
        "SELECT COUNT(*) FROM manga_subscriptions WHERE tracked_manga_id = ?",
        [tracked_manga_id],
    )
    if count_rs.rows[0][0] >= SUBSCRIBER_LIMIT:
        return "full"

    await c.execute(
        "INSERT INTO manga_subscriptions (tracked_manga_id, user_id) VALUES (?, ?)",
        [tracked_manga_id, user_id],
    )
    return "ok"


async def unsubscribe_manga(tracked_manga_id: int, user_id: str) -> bool:
    c = _client_or_raise()
    rs = await c.execute(
        "DELETE FROM manga_subscriptions WHERE tracked_manga_id = ? AND user_id = ? "
        "RETURNING user_id",
        [tracked_manga_id, user_id],
    )
    return len(rs.rows) > 0


async def list_manga_subscribers(tracked_manga_id: int) -> list[str]:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT user_id FROM manga_subscriptions WHERE tracked_manga_id = ?",
        [tracked_manga_id],
    )
    return [row[0] for row in rs.rows]


# ---------------------------------------------------------------------------
# read_status
# ---------------------------------------------------------------------------

async def create_read_row(message_id: str, channel_id: str,
                           tracked_manga_id: int, chapter_number: str) -> None:
    c = _client_or_raise()
    await c.execute(
        "INSERT INTO read_status (message_id, channel_id, tracked_manga_id, chapter_number) "
        "VALUES (?, ?, ?, ?)",
        [message_id, channel_id, tracked_manga_id, chapter_number],
    )


async def toggle_read(message_id: str, user_id: str) -> list[str]:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT readers FROM read_status WHERE message_id = ?", [message_id]
    )
    if not rs.rows:
        return []
    readers: list[str] = json.loads(rs.rows[0][0])
    if user_id in readers:
        readers.remove(user_id)
    else:
        readers.append(user_id)
    await c.execute(
        "UPDATE read_status SET readers = ? WHERE message_id = ?",
        [json.dumps(readers), message_id],
    )
    return readers


async def get_read_status(message_id: str) -> dict[str, Any] | None:
    c = _client_or_raise()
    rs = await c.execute("SELECT * FROM read_status WHERE message_id = ?", [message_id])
    if not rs.rows:
        return None
    row = dict(zip(rs.columns, rs.rows[0]))
    row["readers"] = json.loads(row["readers"])
    return row




# ---------------------------------------------------------------------------
# Subscribe/unsubscribe filtering — #7: exclude already-subscribed anime
# from /subscribe-anime's options, and exclude not-yet-subscribed anime
# from /unsubscribe-anime's options (and the manga equivalents).
# ---------------------------------------------------------------------------

async def list_unsubscribed_anime(guild_id: str, user_id: str) -> list[dict[str, Any]]:
    c = _client_or_raise()
    rs = await c.execute(
        """
        SELECT * FROM tracked_anime
        WHERE guild_id = ? AND id NOT IN (
            SELECT tracked_anime_id FROM subscriptions WHERE user_id = ?
        )
        ORDER BY nickname
        """,
        [guild_id, user_id],
    )
    out = []
    for row in rs.rows:
        d = dict(zip(rs.columns, row))
        d["genres"] = json.loads(d["genres"]) if d.get("genres") else []
        out.append(d)
    return out


async def list_subscribed_anime(guild_id: str, user_id: str) -> list[dict[str, Any]]:
    c = _client_or_raise()
    rs = await c.execute(
        """
        SELECT ta.* FROM tracked_anime ta
        JOIN subscriptions s ON ta.id = s.tracked_anime_id
        WHERE ta.guild_id = ? AND s.user_id = ?
        ORDER BY ta.nickname
        """,
        [guild_id, user_id],
    )
    out = []
    for row in rs.rows:
        d = dict(zip(rs.columns, row))
        d["genres"] = json.loads(d["genres"]) if d.get("genres") else []
        out.append(d)
    return out


async def list_unsubscribed_manga(guild_id: str, user_id: str) -> list[dict[str, Any]]:
    c = _client_or_raise()
    rs = await c.execute(
        """
        SELECT * FROM tracked_manga
        WHERE guild_id = ? AND id NOT IN (
            SELECT tracked_manga_id FROM manga_subscriptions WHERE user_id = ?
        )
        ORDER BY nickname
        """,
        [guild_id, user_id],
    )
    return [_row_to_manga_dict(rs.columns, row) for row in rs.rows]


async def list_subscribed_manga(guild_id: str, user_id: str) -> list[dict[str, Any]]:
    c = _client_or_raise()
    rs = await c.execute(
        """
        SELECT tm.* FROM tracked_manga tm
        JOIN manga_subscriptions s ON tm.id = s.tracked_manga_id
        WHERE tm.guild_id = ? AND s.user_id = ?
        ORDER BY tm.nickname
        """,
        [guild_id, user_id],
    )
    return [_row_to_manga_dict(rs.columns, row) for row in rs.rows]


# ---------------------------------------------------------------------------
# manga_role_pings — #11: /assign-ping-role now supports manga too
# ---------------------------------------------------------------------------

async def set_manga_role_ping(tracked_manga_id: int, role_id: str, assigned_by: str) -> None:
    c = _client_or_raise()
    await c.execute(
        """
        INSERT INTO manga_role_pings (tracked_manga_id, role_id, assigned_by)
        VALUES (?, ?, ?)
        ON CONFLICT(tracked_manga_id) DO UPDATE SET
            role_id = excluded.role_id, assigned_by = excluded.assigned_by
        """,
        [tracked_manga_id, role_id, assigned_by],
    )


async def remove_manga_role_ping(tracked_manga_id: int, role_id: str) -> bool:
    c = _client_or_raise()
    rs = await c.execute(
        "DELETE FROM manga_role_pings WHERE tracked_manga_id = ? AND role_id = ? "
        "RETURNING role_id",
        [tracked_manga_id, role_id],
    )
    return len(rs.rows) > 0


async def get_manga_role_ping(tracked_manga_id: int) -> str | None:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT role_id FROM manga_role_pings WHERE tracked_manga_id = ?",
        [tracked_manga_id],
    )
    return rs.rows[0][0] if rs.rows else None


# ---------------------------------------------------------------------------
# #13: subscriber counts for /animelist and /mangalist
# ---------------------------------------------------------------------------

async def count_subscribers(tracked_anime_id: int) -> int:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE tracked_anime_id = ?", [tracked_anime_id]
    )
    return rs.rows[0][0]


async def count_manga_subscribers(tracked_manga_id: int) -> int:
    c = _client_or_raise()
    rs = await c.execute(
        "SELECT COUNT(*) FROM manga_subscriptions WHERE tracked_manga_id = ?", [tracked_manga_id]
    )
    return rs.rows[0][0]
