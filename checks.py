import discord
from discord import app_commands


def is_officer():
    """
    Slash-command check: caller must be a guild member with the Officer role
    configured via OFFICER_ROLE_ID in .env. Failing the check raises
    app_commands.CheckFailure, which we surface as a friendly ephemeral reply
    via the cog's error handler.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        settings = getattr(interaction.client, 'settings', None)
        role_id = getattr(settings, 'officer_role_id', None)
        if role_id is None:
            # Fail closed: if the role isn't configured, nobody passes.
            # Better to lock the bot than to accidentally leave it open.
            return False

        member = interaction.user
        if not isinstance(member, discord.Member):
            return False

        return any(role.id == role_id for role in member.roles)

    return app_commands.check(predicate)
