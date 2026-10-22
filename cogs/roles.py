"""Officer-only assignment of the three configured participant roles."""
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from checks import is_officer
from errors import respond_error


class Roles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="assign-role", description="Assign a judge, mentor, or hacker role to a server member.")
    @app_commands.guild_only()
    @app_commands.describe(user="The server member to assign the role to", role="The participant role to assign")
    @is_officer()
    async def assign_role(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        role: Literal["judge", "mentor", "hacker"],
    ):
        role_ids = {
            "judge": self.bot.settings.judge_role_id,
            "mentor": self.bot.settings.mentor_role_id,
            "hacker": self.bot.settings.hacker_role_id,
        }
        # Validate again at execution time rather than relying only on the UI choices.
        if role not in role_ids:
            await interaction.response.send_message("Choose judge, mentor, or hacker.", ephemeral=True)
            return
        if interaction.guild is None or user.guild.id != interaction.guild.id:
            await interaction.response.send_message("Choose a member of this server.", ephemeral=True)
            return
        role_id = role_ids[role]
        target_role = interaction.guild.get_role(role_id) if role_id else None
        if target_role is None:
            await interaction.response.send_message(
                f"The {role} role is not configured or no longer exists. Ask a bot maintainer to set {role.upper()}_ROLE_ID.",
                ephemeral=True,
            )
            return
        if target_role.id == self.bot.settings.officer_role_id or target_role.is_default() or target_role.managed:
            await interaction.response.send_message("That role cannot be assigned by this command. Contact a bot maintainer.", ephemeral=True)
            return
        if target_role in user.roles:
            await interaction.response.send_message(f"{user.mention} already has the {role} role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await user.add_roles(target_role, reason=f"/assign-role by officer {interaction.user.id}")
        except discord.Forbidden:
            await interaction.followup.send(
                "I couldn't assign that role. Give me Manage Roles permission and place my highest role above the role being assigned.",
                ephemeral=True,
            )
        except discord.HTTPException:
            await interaction.followup.send("Discord couldn't assign the role. Please try again.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"Assigned the {role} role to {user.mention}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @assign_role.error
    async def assign_role_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await respond_error(interaction, error)


async def setup(bot):
    await bot.add_cog(Roles(bot))
