"""
Turso (libSQL) database layer for AM Track.

One shared async client, opened once at bot startup via init_db() and
closed in on_shutdown(). All other modules import `db` from here and
call its methods rather than touching libsql_client directly.
"""

import json
import os
from pathlib import Path
from typing import Any

import libsql_client

_client: libsql_client.Client | None = None


async def init_db() -> None:
    """Create the client and apply schema.sql. Call once on bot startup.

    Forces the https:// (HTTP-based Hrana) transport rather than the
    libsql:// / wss:// websocket transport — several Turso databases
    (notably ones provisioned on aws-ap-south-1) reject the legacy
    websocket handshake with a 400, even with a valid URL/token. HTTP
    transport is universally supported, so we normalize to it here
    regardless of what scheme is in the env var.
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

    _client = libsql_client.create_client(url=url, auth_token=auth_token)

    schema_path = Path(__file__).parent / "schema.sql"
    schema_sql = schema_path.read_text()
    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
    for stmt in statements:
        await _client.execute(stmt)


async def close_db() -> None:
    if _client is not None:
        await _client.close()


def _client_or_raise() -> libsql_client.Client:
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


async def set_last_aired_episode(tracked_anime_id: int, episode: int) -> None:
    c = _client_or_raise()
    await c.execute(
        "UPDATE tracked_anime SET last_aired_episode = ? WHERE id = ?",
        [episode, tracked_anime_id],
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
  
