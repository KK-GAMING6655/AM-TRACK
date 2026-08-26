-- AM Track (Anime Manga Tracker) — Turso/libSQL schema
-- Part 1: Anime tracking only. Manga tables will be added in Part 2.

-- One row per Discord server using the bot
CREATE TABLE IF NOT EXISTS servers (
    guild_id        TEXT PRIMARY KEY,
    anime_channel_id TEXT,          -- channel for anime notifications (set via /set channel)
    manga_channel_id TEXT,          -- reserved for Part 2
    owner_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Bot-admins per server (separate from Discord's own admin/owner permissions)
CREATE TABLE IF NOT EXISTS admins (
    guild_id   TEXT NOT NULL REFERENCES servers(guild_id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    added_by   TEXT NOT NULL,
    added_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, user_id)
);

-- Anime tracked by a given server. anilist_id identifies the show;
-- nickname is per-server and what members actually type/see in commands.
CREATE TABLE IF NOT EXISTS tracked_anime (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        TEXT NOT NULL REFERENCES servers(guild_id) ON DELETE CASCADE,
    anilist_id      INTEGER NOT NULL,
    nickname        TEXT NOT NULL,
    added_by        TEXT NOT NULL,
    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    -- cached AniList fields so we don't re-fetch on every render;
    -- refreshed by the polling loop
    title_romaji    TEXT,
    title_english   TEXT,
    cover_image_url TEXT,
    banner_image_url TEXT,
    synopsis        TEXT,
    genres          TEXT,           -- JSON array as text
    studio          TEXT,
    creator         TEXT,
    source_material TEXT,
    status          TEXT,           -- RELEASING / FINISHED / NOT_YET_RELEASED / etc.
    average_score   INTEGER,        -- AniList averageScore (0-100)
    season          TEXT,           -- e.g. "FALL"
    season_year     INTEGER,
    total_episodes  INTEGER,        -- null if unknown
    anilist_url     TEXT,
    mal_url         TEXT,
    last_aired_episode INTEGER NOT NULL DEFAULT 0,
    last_episode_aired_at INTEGER,  -- unix ts, approximate — set when the poller detects an increment
    next_airing_at  INTEGER,        -- unix timestamp of next episode, null if none scheduled
    next_airing_episode INTEGER,
    UNIQUE (guild_id, nickname)
);

CREATE INDEX IF NOT EXISTS idx_tracked_anime_anilist_id ON tracked_anime(anilist_id);
CREATE INDEX IF NOT EXISTS idx_tracked_anime_guild ON tracked_anime(guild_id);

-- Member subscriptions to a tracked anime (max 100 enforced in application code)
CREATE TABLE IF NOT EXISTS subscriptions (
    tracked_anime_id INTEGER NOT NULL REFERENCES tracked_anime(id) ON DELETE CASCADE,
    user_id          TEXT NOT NULL,
    subscribed_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tracked_anime_id, user_id)
);

-- Role to ping for a tracked anime (max 1 enforced in application code)
CREATE TABLE IF NOT EXISTS role_pings (
    tracked_anime_id INTEGER NOT NULL REFERENCES tracked_anime(id) ON DELETE CASCADE,
    role_id          TEXT NOT NULL,
    assigned_by      TEXT NOT NULL,
    assigned_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tracked_anime_id)
);

-- Tracks which reminder milestones have already been sent per anime,
-- so the polling loop never double-sends. One row created per tracked_anime
-- per upcoming episode; cleared/reset when next_airing_episode changes.
CREATE TABLE IF NOT EXISTS reminder_state (
    tracked_anime_id INTEGER NOT NULL REFERENCES tracked_anime(id) ON DELETE CASCADE,
    episode          INTEGER NOT NULL,
    sent_60d         INTEGER NOT NULL DEFAULT 0,
    sent_30d         INTEGER NOT NULL DEFAULT 0,
    sent_7d          INTEGER NOT NULL DEFAULT 0,
    sent_1d          INTEGER NOT NULL DEFAULT 0,
    sent_airing      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tracked_anime_id, episode)
);

-- Persisted "Mark as Watched" state per notification message,
-- so the button keeps working across bot restarts/redeploys.
CREATE TABLE IF NOT EXISTS watched_status (
    message_id       TEXT NOT NULL,
    channel_id       TEXT NOT NULL,
    tracked_anime_id INTEGER NOT NULL REFERENCES tracked_anime(id) ON DELETE CASCADE,
    episode          INTEGER NOT NULL,
    watchers         TEXT NOT NULL DEFAULT '[]', -- JSON array of user_ids
    PRIMARY KEY (message_id)
);


-- ===========================================================================
-- Part 2: Manga (MangaDex, English-language chapters only)
-- ===========================================================================

-- Manga tracked by a given server. mangadex_id identifies the title;
-- nickname is per-server, same pattern as tracked_anime.
CREATE TABLE IF NOT EXISTS tracked_manga (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        TEXT NOT NULL REFERENCES servers(guild_id) ON DELETE CASCADE,
    mangadex_id     TEXT NOT NULL,
    nickname        TEXT NOT NULL,
    added_by        TEXT NOT NULL,
    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    -- cached MangaDex fields, refreshed by the manga polling loop
    title_english   TEXT,
    cover_image_url TEXT,
    description     TEXT,
    genres          TEXT,           -- JSON array as text
    creator         TEXT,
    manga_type      TEXT,           -- Manga / Manhwa / Manhua / Comic (heuristic, see services/mangadex.py)
    status          TEXT,           -- ongoing / completed / hiatus / cancelled
    rating          REAL,           -- MangaDex bayesian average, 0-10 scale
    total_chapters_en INTEGER,
    mangadex_url    TEXT,
    last_chapter_id         TEXT,   -- MangaDex chapter UUID of the most recently seen chapter
    last_chapter_number     TEXT,   -- chapter "number" as MangaDex stores it (string — can be non-integer)
    last_chapter_published_at TEXT, -- ISO8601 — polling cursor for /chapter?publishAtSince=
    UNIQUE (guild_id, nickname)
);

CREATE INDEX IF NOT EXISTS idx_tracked_manga_mangadex_id ON tracked_manga(mangadex_id);
CREATE INDEX IF NOT EXISTS idx_tracked_manga_guild ON tracked_manga(guild_id);

-- Member subscriptions (max 100 enforced in application code, same as anime)
CREATE TABLE IF NOT EXISTS manga_subscriptions (
    tracked_manga_id INTEGER NOT NULL REFERENCES tracked_manga(id) ON DELETE CASCADE,
    user_id          TEXT NOT NULL,
    subscribed_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tracked_manga_id, user_id)
);

-- Persisted "Mark as Read" state per notification message.
CREATE TABLE IF NOT EXISTS read_status (
    message_id       TEXT NOT NULL,
    channel_id       TEXT NOT NULL,
    tracked_manga_id INTEGER NOT NULL REFERENCES tracked_manga(id) ON DELETE CASCADE,
    chapter_number   TEXT NOT NULL,
    readers          TEXT NOT NULL DEFAULT '[]', -- JSON array of user_ids
    PRIMARY KEY (message_id)
);


-- Role to ping for a tracked manga (max 1 enforced in application code,
-- mirrors role_pings for anime — added for /assign-ping-role's Type param)
CREATE TABLE IF NOT EXISTS manga_role_pings (
    tracked_manga_id INTEGER NOT NULL REFERENCES tracked_manga(id) ON DELETE CASCADE,
    role_id          TEXT NOT NULL,
    assigned_by      TEXT NOT NULL,
    assigned_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tracked_manga_id)
);
