"""
/set channel, /admin add, /admin remove, /assign ping role.

All admin-only per the spec — gated by utils/permissions.is_bot_admin().
"""

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from db import database as db
from utils.autocomplete import assign_ping_role_target_autocomplete
from utils.permissions import is_bot_admin


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -- /set channel --------------------------------------------------

    @app_commands.command(name="set-channel", description="Set the notification channel for anime or manga.")
    @app_commands.describe(type="Which kind of notifications this channel receives", channel="The text channel to use")
    @is_bot_admin()
    async def set_channel(
        self, interaction: discord.Interaction,
        type: Literal["Anime", "Manga"], channel: discord.TextChannel,
    ):
        await db.ensure_server(str(interaction.guild_id), str(interaction.guild.owner_id))
        await db.set_channel(str(interaction.guild_id), type.lower(), str(channel.id))
        await interaction.response.send_message(
            f"✅ {type} notifications will now be sent to {channel.mention}.", ephemeral=True
        )

    # -- /admin add / remove --------------------------------------------

    @app_commands.command(name="admin-add", description="Grant a member bot-admin permissions.")
    @app_commands.describe(user="The member to make a bot-admin")
    @is_bot_admin()
    async def admin_add(self, interaction: discord.Interaction, user: discord.Member):
        await db.ensure_server(str(interaction.guild_id), str(interaction.guild.owner_id))
        await db.add_admin(str(interaction.guild_id), str(user.id), str(interaction.user.id))
        await interaction.response.send_message(
            f"✅ {user.mention} is now a bot-admin.", ephemeral=True
        )

    @app_commands.command(name="admin-remove", description="Revoke a member's bot-admin permissions.")
    @app_commands.describe(user="The member to remove as bot-admin")
    @is_bot_admin()
    async def admin_remove(self, interaction: discord.Interaction, user: discord.Member):
        if user.id == interaction.guild.owner_id:
            await interaction.response.send_message(
                "🚫 The server owner can't be removed from admin.", ephemeral=True
            )
            return
        await db.remove_admin(str(interaction.guild_id), str(user.id))
        await interaction.response.send_message(
            f"✅ {user.mention} is no longer a bot-admin.", ephemeral=True
        )

    # -- /assign ping role ------------------------------------------------

    # -- /assign ping role — #11: now covers anime AND manga via Type --------

    @app_commands.command(name="assign-ping-role", description="Set or remove the role pinged for an anime's or manga's new releases.")
    @app_commands.describe(
        enable="Add or remove this role from the ping list",
        type="Whether this targets an anime or a manga",
        target="The tracked anime or manga's nickname",
        role="The role to ping",
    )
    @app_commands.autocomplete(target=assign_ping_role_target_autocomplete)
    @is_bot_admin()
    async def assign_ping_role(
        self, interaction: discord.Interaction,
        enable: Literal["Add", "Remove"], type: Literal["Anime", "Manga"],
        target: str, role: discord.Role,
    ):
        if role.is_default():  # @everyone
            await interaction.response.send_message(
                "🚫 @everyone can't be used as a ping role.", ephemeral=True
            )
            return

        if type == "Manga":
            tracked = await db.get_tracked_manga_by_nickname(str(interaction.guild_id), target)
            if tracked is None:
                await interaction.response.send_message(
                    f"⚠️ No manga tracked here under the nickname **{target}**.", ephemeral=True
                )
                return
            if enable == "Add":
                await db.set_manga_role_ping(tracked["id"], str(role.id), str(interaction.user.id))
                await interaction.response.send_message(
                    f"✅ {role.mention} will now be pinged for **{target}**.", ephemeral=True
                )
            else:
                removed = await db.remove_manga_role_ping(tracked["id"], str(role.id))
                if removed:
                    await interaction.response.send_message(
                        f"✅ {role.mention} removed from **{target}**'s ping list.", ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        f"⚠️ {role.mention} wasn't set as the ping role for **{target}**.", ephemeral=True
                    )
            return

        tracked = await db.get_tracked_anime_by_nickname(str(interaction.guild_id), target)
        if tracked is None:
            await interaction.response.send_message(
                f"⚠️ No anime tracked here under the nickname **{target}**.", ephemeral=True
            )
            return

        if enable == "Add":
            await db.set_role_ping(tracked["id"], str(role.id), str(interaction.user.id))
            await interaction.response.send_message(
                f"✅ {role.mention} will now be pinged for **{target}**.", ephemeral=True
            )
        else:
            removed = await db.remove_role_ping(tracked["id"], str(role.id))
            if removed:
                await interaction.response.send_message(
                    f"✅ {role.mention} removed from **{target}**'s ping list.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"⚠️ {role.mention} wasn't set as the ping role for **{target}**.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
  
