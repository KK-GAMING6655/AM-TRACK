"""
AM Track — bot entrypoint.

Startup sequence (setup_hook, runs once before the bot connects to the
gateway):
  1. init_db()            — open the Turso client, apply schema.sql
  2. add_view(...)         — register the persistent views' custom_ids so
                              buttons on messages from a *previous* process
                              keep working after this redeploy
  3. load_extension(cog)   — load each cog's slash commands
  4. tree.sync()            — push the slash command list to Discord

The polling loops (services/poller.py, services/manga_poller.py) are
started from on_ready() rather than setup_hook(), since they need
bot.guilds/caches populated.
"""

import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db.database import close_db, init_db
from keep_alive import keep_alive
from services.manga_poller import start_manga_poller
from services.poller import start_poller
from utils.permissions import on_app_command_error
from views.buttons import MangaNotificationView, MangaSubscribeView, NotificationView, SubscribeView

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("amtrack")

INTENTS = discord.Intents.default()
INTENTS.members = True  # needed to resolve users for /admin-add's Member picker and mentions

COGS = ["cogs.admin", "cogs.anime", "cogs.manga"]


class AMTrackBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)

    async def setup_hook(self):
        await init_db()
        log.info("Database ready.")

        # Persistent views: these instances are never sent to a channel
        # themselves — they only exist so discord.py has a handler
        # registered for each custom_id, so buttons on old messages from
        # before this restart keep responding.
        self.add_view(SubscribeView())
        self.add_view(NotificationView())
        self.add_view(MangaSubscribeView())
        self.add_view(MangaNotificationView())

        for cog in COGS:
            await self.load_extension(cog)
            log.info("Loaded %s", cog)

        self.tree.on_error = on_app_command_error

        synced = await self.tree.sync()
        log.info("Synced %d application commands.", len(synced))

    async def on_ready(self):
        log.info("Logged in as %s (%s)", self.user, self.user.id)
        start_poller(self)
        start_manga_poller(self)

    async def close(self):
        await close_db()
        await super().close()


def main():
    token = os.environ["DISCORD_BOT_TOKEN"]
    keep_alive()
    bot = AMTrackBot()
    bot.run(token)


if __name__ == "__main__":
    main()
