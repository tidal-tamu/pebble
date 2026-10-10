"""Officer-only manual email summaries; disabled unless explicitly configured."""

import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands

from checks import is_officer
from email_summary import EmailSummaryService, summary_pages
from errors import respond_error


class Emails(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.service = EmailSummaryService(bot.settings)
        self._lock = asyncio.Lock()
        self._last_finished = None

    @app_commands.command(name="summarize-emails", description="Privately summarize email conversations from the last 24 hours or 7 days.")
    @app_commands.describe(period="Time window to summarize")
    @app_commands.choices(period=[app_commands.Choice(name="Last 24 hours", value="24h"), app_commands.Choice(name="Last 7 days", value="7d")])
    @is_officer()
    async def summarize_emails(self, interaction: discord.Interaction, period: app_commands.Choice[str]):
        if self.bot.settings.email_summary_mode == "disabled":
            await interaction.response.send_message("Email summaries are not enabled. A maintainer can configure demo or live mode.", ephemeral=True)
            return
        if self._lock.locked():
            await interaction.response.send_message("An email summary is already running. Please wait for it to finish.", ephemeral=True)
            return
        if self._last_finished is not None and time.monotonic() - self._last_finished < 60:
            await interaction.response.send_message("Please wait a minute between email summaries.", ephemeral=True)
            return
        # Acquire before the first network await so simultaneous commands cannot race.
        async with self._lock:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                result = await asyncio.to_thread(self.service.run, period.value)
                pages = summary_pages(result)
                for index, page in enumerate(pages, 1):
                    prefix = "DEMO · " if result.demo else ""
                    embed = discord.Embed(title=f"{prefix}Email summary · {period.name} · {index}/{len(pages)}", description=page, color=discord.Color.blurple())
                    embed.set_footer(text="Only you can see this · " + result.model)
                    await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            finally:
                self._last_finished = time.monotonic()

    @summarize_emails.error
    async def summarize_emails_error(self, interaction, error):
        await respond_error(interaction, error)


async def setup(bot):
    await bot.add_cog(Emails(bot))
