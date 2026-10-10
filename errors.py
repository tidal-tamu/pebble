"""Consistent private responses for slash-command failures."""

import logging

from discord import app_commands

from sheets import SheetsError
from gmail import GmailError
from gemini import GeminiError
from harp import HarpError

logger = logging.getLogger(__name__)


async def respond_error(interaction, error):
    original = getattr(error, "original", error)
    if isinstance(original, app_commands.CheckFailure):
        message = "You need the Officer role to use this command."
    elif isinstance(original, (SheetsError, GmailError, GeminiError, HarpError)):
        message = str(original)
    else:
        logger.error("Command %s failed (%s)", getattr(interaction.command, "name", "unknown"), type(original).__name__)
        message = "Couldn't complete that command. Please try again or contact a bot maintainer."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
