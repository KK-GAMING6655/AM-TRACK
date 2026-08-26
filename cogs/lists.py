"""
/animelist and /mangalist — #13: shows every anime/manga tracked in this
server with its subscriber count, as a numbered, linked list embed.
"""

import discord
from discord import app_commands
from discord.ext import commands

from db import database as db
from utils.embeds import build_anime_list_embeds, build_manga_list_embeds


class ListsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="animelist", description="Show every anime tracked in this server.")
    async def animelist(self, interaction: discord.Interaction):
        await interaction.response.defer()
        anime_list = await db.list_tracked_anime(str(interaction.guild_id))
        rows_with_counts = [(a, await db.count_subscribers(a["id"])) for a in anime_list]
        embeds = build_anime_list_embeds(rows_with_counts)

        await interaction.followup.send(embed=embeds[0])
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed)

    @app_commands.command(name="mangalist", description="Show every manga tracked in this server.")
    async def mangalist(self, interaction: discord.Interaction):
        await interaction.response.defer()
        manga_list = await db.list_tracked_manga(str(interaction.guild_id))
        rows_with_counts = [(m, await db.count_manga_subscribers(m["id"])) for m in manga_list]
        embeds = build_manga_list_embeds(rows_with_counts)

        await interaction.followup.send(embed=embeds[0])
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ListsCog(bot))
