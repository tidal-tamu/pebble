"""Pebble's entry point and Discord connection lifecycle."""

import logging

import discord
from discord.ext import commands

from config import ConfigurationError, Settings

logger = logging.getLogger(__name__)
EXTENSIONS = ("cogs.reminders", "cogs.announcements", "cogs.emails")


class PebbleBot(commands.Bot):
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # A missing feature is a startup failure, rather than a partially working bot.
        for extension in EXTENSIONS:
            await self.load_extension(extension)
            logger.info("Loaded extension: %s", extension)

        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Registered %d commands in the configured guild", len(synced))
        else:
            synced = await self.tree.sync()
            logger.info("Registered %d commands globally", len(synced))

    async def on_ready(self):
        logger.info("Connected as %s (ID: %s)", self.user, self.user.id)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
    except ConfigurationError as error:
        logger.error("Configuration error: %s", error)
        return 1

    logging.getLogger().setLevel(settings.log_level)
    try:
        bot = PebbleBot(settings)
        # Use the root handler above so Discord log messages aren't duplicated.
        bot.run(settings.discord_token, log_handler=None)
    except discord.LoginFailure:
        logger.error("Discord rejected the token. Update DISCORD_TOKEN.")
        return 1
    except discord.PrivilegedIntentsRequired:
        logger.error("Enable Server Members and Message Content intents in the Discord Developer Portal.")
        return 1
    except Exception as error:
        original = getattr(error, "original", error)
        if isinstance(original, ConfigurationError):
            logger.error("Configuration error: %s", original)
        else:
            logger.error("Startup failed (%s). Check configuration, credentials, and network access.", type(original).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
