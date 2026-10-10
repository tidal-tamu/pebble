"""Officer-only live registration counts from Harp."""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from checks import is_officer
from errors import respond_error
from harp import HarpClient, RegistrationStats


def stats_embed(stats: RegistrationStats) -> discord.Embed:
    embed = discord.Embed(
        title="Registration stats",
        description=f"**{stats.total_submitted:,} applications submitted**\nIncludes every non-draft status.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Applications started", value=f"{stats.total_started:,} (including drafts)", inline=False)
    embed.add_field(
        name="By status",
        value="\n".join(f"{status.title()}: **{stats.by_status[status]:,}**" for status in ("draft", "submitted", "accepted", "rejected", "waitlisted")),
        inline=False,
    )
    return embed


class Registration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.harp = HarpClient(bot.settings.harp_bot_api_key)

    @app_commands.command(name="registration-stats", description="Show live registration counts from Harp in this channel.")
    @is_officer()
    async def registration_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        stats = await asyncio.to_thread(self.harp.registration_stats)
        await interaction.followup.send(embed=stats_embed(stats), allowed_mentions=discord.AllowedMentions.none())

    @registration_stats.error
    async def registration_stats_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await respond_error(interaction, error)


async def setup(bot):
    await bot.add_cog(Registration(bot))
