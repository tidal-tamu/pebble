import discord
from discord import app_commands
from discord.ext import commands

from checks import is_officer


class Announcements(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="announce", description="Broadcast an announcement to a specific channel.")
    @app_commands.describe(
        channel="The channel to send the announcement to",
        title="Title of the announcement",
        message="The content of the announcement",
        ping_role="Optional role to ping (e.g. @everyone or a specific role id)",
    )
    @is_officer()
    async def announce(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        title: str,
        message: str,
        ping_role: str = None,
    ):
        embed = discord.Embed(
            title=title,
            description=message,
            color=discord.Color.brand_green(),
        )
        embed.set_footer(text=f"Announcement from {interaction.user.display_name}")

        content = ping_role if ping_role else None

        try:
            await channel.send(content=content, embed=embed)
            await interaction.response.send_message(
                f"Announcement sent successfully to {channel.mention}!", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to send messages in that channel.", ephemeral=True
            )

    @announce.error
    async def announce_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "You need the Officer role to use this command.", ephemeral=True
            )
        else:
            raise error


async def setup(bot):
    await bot.add_cog(Announcements(bot))
