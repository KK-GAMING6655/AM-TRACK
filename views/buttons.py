"""
Persistent Views.

Discord button rules that shape this file:
  - A regular (non-link) button needs a fixed custom_id so discord.py can
    route interactions back to it after a bot restart. We register one
    "template" instance of each view (with no link URLs) via
    bot.add_view() in main.py's setup_hook() — that's what makes the
    Mark as Watched button on old messages keep working after a redeploy.
  - A link-style button has a `url` instead of a custom_id, never fires an
    interaction, and needs no persistence — Discord just opens the URL
    client-side. So MAL/AniList buttons are added per-message with the
    real URLs and don't need to match the registered template.
"""

import discord

from db import database as db
from utils.embeds import new_chapter_embed, new_episode_embed


class SubscribeView(discord.ui.View):
    """Attached to the /anime add confirmation embed."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Subscribe", style=discord.ButtonStyle.green,
        custom_id="amtrack:subscribe",
    )
    async def subscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed is None or not embed.url:
            await interaction.response.send_message(
                "Couldn't figure out which anime this button belongs to.", ephemeral=True
            )
            return

        anime = await db.get_tracked_anime_by_site_url(str(interaction.guild_id), embed.url)
        if anime is None:
            await interaction.response.send_message(
                "This anime is no longer tracked in this server.", ephemeral=True
            )
            return

        result = await db.subscribe(anime["id"], str(interaction.user.id))
        if result == "ok":
            await interaction.response.send_message(
                f"Subscribed to **{anime['nickname']}**.", ephemeral=True
            )
        elif result == "already":
            await interaction.response.send_message(
                f"You're already subscribed to **{anime['nickname']}**.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"**{anime['nickname']}** has reached its subscriber limit (100).",
                ephemeral=True,
            )


class NotificationView(discord.ui.View):
    """
    Attached to each new-episode notification message.

    mal_url / anilist_url default to None for the persistent "template"
    instance registered at startup (that instance never actually gets
    rendered to a channel — it exists purely so discord.py has a handler
    for custom_id="amtrack:mark_watched"). Real notifications always pass
    both URLs in.
    """

    def __init__(self, mal_url: str | None = None, anilist_url: str | None = None):
        super().__init__(timeout=None)
        if mal_url:
            self.add_item(discord.ui.Button(label="MyAnimeList", style=discord.ButtonStyle.link, url=mal_url))
        if anilist_url:
            self.add_item(discord.ui.Button(label="AniList", style=discord.ButtonStyle.link, url=anilist_url))

    @discord.ui.button(
        label="Mark as Watched", style=discord.ButtonStyle.green,
        custom_id="amtrack:mark_watched",
    )
    async def mark_watched(self, interaction: discord.Interaction, button: discord.ui.Button):
        message_id = str(interaction.message.id)
        watched_row = await db.get_watched(message_id)
        if watched_row is None:
            await interaction.response.send_message(
                "Couldn't find watch-tracking data for this message.", ephemeral=True
            )
            return

        watchers = await db.toggle_watched(message_id, str(interaction.user.id))
        anime = await db.get_tracked_anime_by_id(watched_row["tracked_anime_id"])

        _, embed = new_episode_embed(
            anime, watched_row["episode"], anime["nickname"], watchers=watchers
        )
        await interaction.response.edit_message(embed=embed, view=self)
              


# ---------------------------------------------------------------------------
# Part 2: Manga views — same persistence pattern as the anime views above.
# ---------------------------------------------------------------------------

class MangaSubscribeView(discord.ui.View):
    """Attached to the /manga-add confirmation embed."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Subscribe", style=discord.ButtonStyle.green,
        custom_id="amtrack:manga_subscribe",
    )
    async def subscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed is None or not embed.url:
            await interaction.response.send_message(
                "Couldn't figure out which manga this button belongs to.", ephemeral=True
            )
            return

        manga = await db.get_tracked_manga_by_url(str(interaction.guild_id), embed.url)
        if manga is None:
            await interaction.response.send_message(
                "This manga is no longer tracked in this server.", ephemeral=True
            )
            return

        result = await db.subscribe_manga(manga["id"], str(interaction.user.id))
        if result == "ok":
            await interaction.response.send_message(
                f"Subscribed to **{manga['nickname']}**.", ephemeral=True
            )
        elif result == "already":
            await interaction.response.send_message(
                f"You're already subscribed to **{manga['nickname']}**.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"**{manga['nickname']}** has reached its subscriber limit (100).",
                ephemeral=True,
            )


class MangaNotificationView(discord.ui.View):
    """
    Attached to each new-chapter notification message. mangadex_url
    defaults to None for the persistent "template" instance registered
    at startup (see main.py's setup_hook) — real notifications always
    pass it in.
    """

    def __init__(self, mangadex_url: str | None = None):
        super().__init__(timeout=None)
        if mangadex_url:
            self.add_item(discord.ui.Button(label="MangaDex", style=discord.ButtonStyle.link, url=mangadex_url))

    @discord.ui.button(
        label="Mark as Read", style=discord.ButtonStyle.green,
        custom_id="amtrack:mark_read",
    )
    async def mark_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        message_id = str(interaction.message.id)
        read_row = await db.get_read_status(message_id)
        if read_row is None:
            await interaction.response.send_message(
                "Couldn't find read-tracking data for this message.", ephemeral=True
            )
            return

        readers = await db.toggle_read(message_id, str(interaction.user.id))
        manga = await db.get_tracked_manga_by_id(read_row["tracked_manga_id"])

        _, embed = new_chapter_embed(
            manga, read_row["chapter_number"], manga["nickname"], readers=readers
        )
        await interaction.response.edit_message(embed=embed, view=self)
