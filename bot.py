import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('discord')

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True # Required to ping specific members reliably

class PebbleBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)

    async def setup_hook(self):
        # Load cogs
        cogs = ['cogs.reminders', 'cogs.announcements']
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"Loaded cog: {cog}")
            except Exception as e:
                logger.error(f"Failed to load cog {cog}: {e}")

        # Sync slash commands
        guild_id = os.getenv('GUILD_ID')
        if guild_id:
            logger.info("Syncing commands to specific guild for faster testing...")
            self.tree.copy_global_to(guild=discord.Object(id=int(guild_id)))
            await self.tree.sync(guild=discord.Object(id=int(guild_id)))
        else:
            logger.info("Syncing commands globally...")
            await self.tree.sync()

    async def on_ready(self):
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info('------')

if __name__ == '__main__':
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        logger.error("No DISCORD_TOKEN found in environment variables!")
        exit(1)
        
    bot = PebbleBot()
    bot.run(TOKEN)
