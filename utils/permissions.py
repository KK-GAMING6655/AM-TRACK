"""
Bot-admin permission check, shared by every admin-only command.

"Admin" here means: the server owner, or a user added via /admin add —
not Discord's own "Administrator" permission, per the spec's own
/admin add /admin remove system.
"""

import discord
from discord import app_commands

from db import database as db


class NotBotAdmin(app_commands.CheckFailure):
    pass


def is_bot_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            raise NotBotAdmin("This command only works in a server.")
        if interaction.user.id == interaction.guild.owner_id:
            return True
        if await db.is_admin(str(interaction.guild_id), str(interaction.user.id)):
            return True
        raise NotBotAdmin("You need to be a bot-admin to use this command.")
    return app_commands.check(predicate)


async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    """Registered once on the tree in main.py. Handles NotBotAdmin plus a
    generic fallback so no command ever fails silently."""
    if isinstance(error, NotBotAdmin):
        message = "🚫 You need to be a server admin (or bot-admin) to use this command."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"⏳ Slow down — try again in {error.retry_after:.1f}s."
    else:
        message = "⚠️ Something went wrong running that command."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
