"""
/anime-add, /anime-remove, /anime, /subscribe-anime, /unsubscribe-anime.

Naming note: Discord doesn't allow a slash command name to be both a
group (e.g. "/anime add", "/anime remove") and a standalone command
("/anime" alone) at the same time — a name is either a group or a
command, not both. Since the spec needs bare "/anime" for details
alongside "/anime add"/"/anime remove", these are implemented as flat
hyphenated commands instead of a nested group. Functionally identical,
just a different name in the Discord command picker.

/anime (details) fetches live from AniList rather than the server's
tracked list — #10: covers every anime (aired, airing, or upcoming),
same pattern /manga already used.
"""

import discord
from discord import app_commands
from discord.ext import commands

from db import database as db
from services import anilist
from utils.autocomplete import (
    anilist_all_search_autocomplete,
    anilist_search_autocomplete,
    subscribed_anime_autocomplete,
    tracked_anime_autocomplete,
    unsubscribed_anime_autocomplete,
)
from utils.embeds import anime_add_confirmation_embed, anime_details_embed
from utils.permissions import is_bot_admin
from views.buttons import SubscribeView


class AnimeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -- /anime-add -------------------------------------------------------

    @app_commands.command(name="anime-add", description="Track a new anime for episode notifications.")
    @app_commands.describe(anime="Search for the anime on AniList", nickname="What this server will call it")
    @app_commands.autocomplete(anime=anilist_search_autocomplete)
    @is_bot_admin()
    async def anime_add(self, interaction: discord.Interaction, anime: str, nickname: str):
        await interaction.response.defer(ephemeral=False)
        await db.ensure_server(str(interaction.guild_id), str(interaction.guild.owner_id))

        try:
            anilist_id = int(anime)
        except ValueError:
            await interaction.followup.send(
                "⚠️ Please pick an anime from the search suggestions.", ephemeral=True
            )
            return

        media = await anilist.get_anime_by_id(anilist_id)
        if media is None:
            await interaction.followup.send("⚠️ Couldn't find that anime on AniList.", ephemeral=True)
            return

        fields = anilist.to_db_fields(media)
        row_id = await db.add_tracked_anime(
            str(interaction.guild_id), anilist_id, nickname, str(interaction.user.id), fields
        )
        if row_id is None:
            await interaction.followup.send(
                f"⚠️ The nickname **{nickname}** is already used in this server — pick another.",
                ephemeral=True,
            )
            return

        embed = anime_add_confirmation_embed(fields)
        await interaction.followup.send(
            "Successfully added this anime 📥", embed=embed, view=SubscribeView()
        )

    # -- /anime-remove ------------------------------------------------------

    @app_commands.command(name="anime-remove", description="Stop tracking an anime in this server.")
    @app_commands.describe(anime="The tracked anime's nickname")
    @app_commands.autocomplete(anime=tracked_anime_autocomplete)
    @is_bot_admin()
    async def anime_remove(self, interaction: discord.Interaction, anime: str):
        removed = await db.remove_tracked_anime(str(interaction.guild_id), anime)
        if removed:
            await interaction.response.send_message(
                f"🗑️ **{anime}** is no longer tracked in this server.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"⚠️ No anime tracked here under the nickname **{anime}**.", ephemeral=True
            )

    # -- /subscribe-anime / /unsubscribe-anime -------------------------------

    @app_commands.command(name="subscribe-anime", description="Get pinged when a tracked anime's new episode airs.")
    @app_commands.describe(anime="The tracked anime's nickname")
    @app_commands.autocomplete(anime=unsubscribed_anime_autocomplete)
    async def subscribe_anime(self, interaction: discord.Interaction, anime: str):
        tracked = await db.get_tracked_anime_by_nickname(str(interaction.guild_id), anime)
        if tracked is None:
            await interaction.response.send_message(
                f"⚠️ No anime tracked here under the nickname **{anime}**.", ephemeral=True
            )
            return

        result = await db.subscribe(tracked["id"], str(interaction.user.id))
        if result == "ok":
            msg = f"✅ Subscribed to **{anime}**."
        elif result == "already":
            msg = f"You're already subscribed to **{anime}**."
        else:
            msg = f"⚠️ **{anime}** has reached its subscriber limit (100)."
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="unsubscribe-anime", description="Stop getting pinged for a tracked anime's new episodes.")
    @app_commands.describe(anime="An anime you're subscribed to")
    @app_commands.autocomplete(anime=subscribed_anime_autocomplete)
    async def unsubscribe_anime(self, interaction: discord.Interaction, anime: str):
        tracked = await db.get_tracked_anime_by_nickname(str(interaction.guild_id), anime)
        if tracked is None:
            await interaction.response.send_message(
                f"⚠️ No anime tracked here under the nickname **{anime}**.", ephemeral=True
            )
            return

        removed = await db.unsubscribe(tracked["id"], str(interaction.user.id))
        msg = f"✅ Unsubscribed from **{anime}**." if removed else f"You weren't subscribed to **{anime}**."
        await interaction.response.send_message(msg, ephemeral=True)

    # -- /anime (details) — searches ALL of AniList, not just tracked -------

    @app_commands.command(name="anime", description="Show details for any anime.")
    @app_commands.describe(anime="Search AniList for any anime")
    @app_commands.autocomplete(anime=anilist_all_search_autocomplete)
    async def anime_details(self, interaction: discord.Interaction, anime: str):
        await interaction.response.defer()
        try:
            anilist_id = int(anime)
        except ValueError:
            await interaction.followup.send(
                "⚠️ Please pick an anime from the search suggestions.", ephemeral=True
            )
            return

        media = await anilist.get_anime_by_id(anilist_id)
        if media is None:
            await interaction.followup.send("⚠️ Couldn't find that anime on AniList.", ephemeral=True)
            return

        fields = anilist.to_db_fields(media)
        await interaction.followup.send(embed=anime_details_embed(fields))


async def setup(bot: commands.Bot):
    await bot.add_cog(AnimeCog(bot))
