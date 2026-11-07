"""Officer-only live application statistics from Harp."""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from checks import is_officer
from errors import respond_error
from harp import HarpClient, HarpError, QuestionStats, RegistrationStats


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


def question_stats_embed(stats: QuestionStats) -> discord.Embed:
    summary = f"**{stats.answered:,} of {stats.total_submitted:,} answered**"
    description = summary + "\n\n"
    omitted = "\n… More choices are available in Harp."
    for option in stats.options:
        label = discord.utils.escape_markdown(" ".join(option.label.split()))
        percent = option.count / stats.answered if stats.answered else 0
        row = f"{label} · {option.count:,} ({percent:.1%})\n"
        if len(description) + len(row) + len(omitted) > 4096:
            description += omitted
            break
        description += row
    if not stats.options:
        description += "No choices returned."
    return discord.Embed(title=stats.label[:256], description=description.rstrip(), color=discord.Color.blurple())


class Registration(commands.Cog):
    appstats = app_commands.Group(name="appstats", description="Show live application question stats from Harp.")

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

    @appstats.command(name="outreach", description="Show how applicants heard about the event.")
    @is_officer()
    async def outreach(self, interaction: discord.Interaction):
        question_id = self.bot.settings.harp_outreach_question_id
        if not question_id:
            raise HarpError("Outreach stats are not configured. Ask a bot maintainer to set HARP_OUTREACH_QUESTION_ID.")
        await interaction.response.defer(thinking=True)
        stats = await asyncio.to_thread(self.harp.question_stats, question_id)
        await interaction.followup.send(embed=question_stats_embed(stats), allowed_mentions=discord.AllowedMentions.none())

    @outreach.error
    async def outreach_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await respond_error(interaction, error)


async def setup(bot):
    await bot.add_cog(Registration(bot))
