"""
/manga-add, /manga-remove, /subscribe-manga, /unsubscribe-manga, /manga.

Naming note: same reasoning as cogs/anime.py — "/manga" needs to exist
standalone (details command) alongside "/manga add"/"/manga remove" in
the spec, which Discord doesn't allow as a group+command pair sharing a
name. Flat hyphenated names here too.

/manga (details) is deliberately NOT limited to this server's tracked
list — per the spec it searches MangaDex's whole catalog, so it doesn't
use db.list_tracked_manga the way /anime's details command does.
"""

import discord
from discord import app_commands
from discord.ext import commands

from db import database as db
from services import mangadex
from utils.autocomplete import (
    mangadex_search_autocomplete,
    manga_details_search_autocomplete,
    subscribed_manga_autocomplete,
    tracked_manga_autocomplete,
    unsubscribed_manga_autocomplete,
)
from utils.embeds import manga_add_confirmation_embed, manga_details_embed, manga_error_embed
from utils.permissions import is_bot_admin
from views.buttons import MangaSubscribeView


class MangaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -- /manga-add -------------------------------------------------------

    @app_commands.command(name="manga-add", description="Track a new manga for chapter notifications.")
    @app_commands.describe(manga="Search for the manga on MangaDex", nickname="What this server will call it")
    @app_commands.autocomplete(manga=mangadex_search_autocomplete)
    @is_bot_admin()
    async def manga_add(self, interaction: discord.Interaction, manga: str, nickname: str):
        await interaction.response.defer(ephemeral=False)
        await db.ensure_server(str(interaction.guild_id), str(interaction.guild.owner_id))

        fields = await mangadex.get_full_manga_details(manga)
        if fields is None:
            await interaction.followup.send(
                embed=manga_error_embed("⚠️ Couldn't find that manga on MangaDex."), ephemeral=True
            )
            return

        # seed the polling cursor to "now" so /manga-add doesn't fire a
        # notification for every chapter that already exists
        latest = await mangadex.get_latest_english_chapter(manga)
        if latest is not None:
            fields["last_chapter_id"] = latest["id"]
            fields["last_chapter_number"] = latest["attributes"].get("chapter")
            fields["last_chapter_published_at"] = latest["attributes"].get("publishAt")

        row_id = await db.add_tracked_manga(
            str(interaction.guild_id), manga, nickname, str(interaction.user.id), fields
        )
        if row_id is None:
            await interaction.followup.send(
                embed=manga_error_embed(
                    f"⚠️ The nickname **{nickname}** is already used in this server — pick another."
                ),
                ephemeral=True,
            )
            return

        embed = manga_add_confirmation_embed(fields)
        await interaction.followup.send(
            "Successfully added this manga 📥", embed=embed, view=MangaSubscribeView()
        )

    # -- /manga-remove ------------------------------------------------------

    @app_commands.command(name="manga-remove", description="Stop tracking a manga in this server.")
    @app_commands.describe(manga="The tracked manga's nickname")
    @app_commands.autocomplete(manga=tracked_manga_autocomplete)
    @is_bot_admin()
    async def manga_remove(self, interaction: discord.Interaction, manga: str):
        removed = await db.remove_tracked_manga(str(interaction.guild_id), manga)
        if removed:
            await interaction.response.send_message(
                embed=manga_error_embed(f"🗑️ **{manga}** is no longer tracked in this server."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=manga_error_embed(f"⚠️ No manga tracked here under the nickname **{manga}**."),
                ephemeral=True,
            )

    # -- /subscribe-manga / /unsubscribe-manga -------------------------------

    @app_commands.command(name="subscribe-manga", description="Get pinged when a tracked manga's new chapter releases.")
    @app_commands.describe(manga="The tracked manga's nickname")
    @app_commands.autocomplete(manga=unsubscribed_manga_autocomplete)
    async def subscribe_manga(self, interaction: discord.Interaction, manga: str):
        tracked = await db.get_tracked_manga_by_nickname(str(interaction.guild_id), manga)
        if tracked is None:
            await interaction.response.send_message(
                embed=manga_error_embed(f"⚠️ No manga tracked here under the nickname **{manga}**."),
                ephemeral=True,
            )
            return

        result = await db.subscribe_manga(tracked["id"], str(interaction.user.id))
        if result == "ok":
            msg = f"✅ Subscribed to **{manga}**."
        elif result == "already":
            msg = f"You're already subscribed to **{manga}**."
        else:
            msg = f"⚠️ **{manga}** has reached its subscriber limit (100)."
        await interaction.response.send_message(embed=manga_error_embed(msg), ephemeral=True)

    @app_commands.command(name="unsubscribe-manga", description="Stop getting pinged for a tracked manga's new chapters.")
    @app_commands.describe(manga="A manga you're subscribed to")
    @app_commands.autocomplete(manga=subscribed_manga_autocomplete)
    async def unsubscribe_manga(self, interaction: discord.Interaction, manga: str):
        tracked = await db.get_tracked_manga_by_nickname(str(interaction.guild_id), manga)
        if tracked is None:
            await interaction.response.send_message(
                embed=manga_error_embed(f"⚠️ No manga tracked here under the nickname **{manga}**."),
                ephemeral=True,
            )
            return

        removed = await db.unsubscribe_manga(tracked["id"], str(interaction.user.id))
        msg = f"✅ Unsubscribed from **{manga}**." if removed else f"You weren't subscribed to **{manga}**."
        await interaction.response.send_message(embed=manga_error_embed(msg), ephemeral=True)

    # -- /manga (details) — searches ALL of MangaDex, not just tracked ------

    @app_commands.command(name="manga", description="Show details for any manga.")
    @app_commands.describe(manga="Search MangaDex for any manga")
    @app_commands.autocomplete(manga=manga_details_search_autocomplete)
    async def manga_details(self, interaction: discord.Interaction, manga: str):
        await interaction.response.defer()
        fields = await mangadex.get_full_manga_details(manga)
        if fields is None:
            await interaction.followup.send(
                embed=manga_error_embed("⚠️ Couldn't find that manga on MangaDex."), ephemeral=True
            )
            return
        await interaction.followup.send(embed=manga_details_embed(fields))


async def setup(bot: commands.Bot):
    await bot.add_cog(MangaCog(bot))
