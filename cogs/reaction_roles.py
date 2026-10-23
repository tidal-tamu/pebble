"""Persistent, strictly unprivileged Hacker self-selection via raw reactions."""

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands, tasks

from checks import is_officer
from config import ConfigurationError
from errors import respond_error
from reaction_roles import Panel, ReactionRoleStore

logger = logging.getLogger(__name__)


class PanelError(ValueError):
    """An actionable panel configuration or permission failure."""


FAILURES = (PanelError, discord.HTTPException, OSError, sqlite3.Error)


def emoji_key(emoji):
    """Custom emoji names can change; their ID is the stable identity."""
    if isinstance(emoji, str):
        emoji = discord.PartialEmoji.from_str(emoji)
    return str(emoji.id) if emoji.id else emoji.name


class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.store = ReactionRoleStore(bot.settings.reaction_roles_db)
        self._lock = asyncio.Lock()

    async def cog_load(self):
        try:
            await asyncio.to_thread(self.store.initialize)
        except (OSError, sqlite3.Error) as error:
            self._log_failure("initialize storage", None, error)
            raise ConfigurationError(self._failure_hint(error)) from error
        self.reconcile.start()

    async def cog_unload(self):
        self.reconcile.cancel()

    async def _validate_role(self, guild, role_id):
        settings = self.bot.settings
        if not role_id or role_id != settings.hacker_role_id or role_id == settings.officer_role_id:
            raise PanelError("Only HACKER_ROLE_ID may be self-selected; it must differ from OFFICER_ROLE_ID.")
        # Fetch current roles/overwrites: do not rely on stale permission caches.
        role = discord.utils.get(await guild.fetch_roles(), id=role_id)
        if role is None:
            raise PanelError("The Hacker role was deleted. Correct HACKER_ROLE_ID and configure the panel again.")
        if role.is_default() or role.managed or role.permissions.value:
            raise PanelError("Hacker must be an unmanaged role with NO server permissions. Remove its permissions first.")
        for channel in await guild.fetch_channels():
            for target, overwrite in channel.overwrites.items():
                if target.id == role.id and overwrite.pair()[0].value:
                    raise PanelError(f"Remove Hacker's channel permission grants in channel {channel.id} before enabling self-selection.")
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            raise PanelError("Grant Pebble Manage Roles permission in this server.")
        if role >= me.top_role:
            raise PanelError("Move Pebble's highest role above Hacker in Server Settings > Roles.")
        return role

    def _validate_channel(self, channel, *, creating=False):
        permissions = channel.permissions_for(channel.guild.me)
        required = ["view_channel", "read_message_history", "add_reactions"]
        if creating:
            required.append("send_messages")
        missing = [name for name in required if not getattr(permissions, name)]
        if missing:
            raise PanelError(f"Grant Pebble these permissions in channel {channel.id}: {', '.join(missing)}.")

    @app_commands.command(name="reaction-role", description="Create or configure the Hacker reaction-role message.")
    @app_commands.guild_only()
    @app_commands.describe(
        channel="Channel for the Hacker selection message",
        emoji="One Unicode emoji or a custom emoji such as <:hacker:123456789>",
        message_id="Existing message ID in that channel; omit to create a new message",
    )
    @is_officer()
    async def reaction_role(self, interaction: discord.Interaction, channel: discord.TextChannel,
                            emoji: str = "💻", message_id: str | None = None):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self._lock:
                if interaction.guild is None or channel.guild.id != interaction.guild.id:
                    raise PanelError("Choose a text channel in this server.")
                role = await self._validate_role(interaction.guild, self.bot.settings.hacker_role_id)
                self._validate_channel(channel, creating=message_id is None)
                parsed = discord.PartialEmoji.from_str(emoji.strip())
                if not parsed.name or len(emoji) > 100:
                    raise PanelError("Provide one Unicode emoji or a Discord custom emoji.")
                previous = next((p for p in await asyncio.to_thread(self.store.panels)
                                 if p.guild_id == interaction.guild.id), None)
                if previous and previous.role_id != role.id:
                    raise PanelError("This panel tracks a different Hacker role ID. Restore HACKER_ROLE_ID before reconfiguring it.")
                if message_id is not None:
                    if not message_id.isascii() or not message_id.isdigit() or int(message_id) <= 0:
                        raise PanelError("Provide a positive Discord message ID from the selected channel.")
                    message = await channel.fetch_message(int(message_id))
                else:
                    message = await channel.send(
                        f"React with {parsed} to get the Hacker role. Remove your reaction to remove the role.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                # Discord validates the emoji before we persist/replace the active panel.
                await message.add_reaction(parsed)
                panel = Panel(interaction.guild.id, channel.id, message.id, emoji_key(parsed), role.id)
                await asyncio.to_thread(self.store.save, panel)
            await interaction.followup.send(f"Hacker reaction roles configured: {message.jump_url}", ephemeral=True)
            await self._run_panel(panel)
        except FAILURES as error:
            self._log_failure("configuration", interaction.guild_id, error)
            message = str(error) if isinstance(error, PanelError) else self._failure_hint(error)
            await interaction.followup.send(message, ephemeral=True)

    @reaction_role.error
    async def reaction_role_error(self, interaction, error):
        await respond_error(interaction, error)

    @staticmethod
    def _failure_hint(error):
        if isinstance(error, discord.HTTPException) and error.code == 10014:
            return "Use one valid Unicode emoji or a custom emoji that Pebble can use in this server."
        if isinstance(error, discord.NotFound):
            return "The panel message, channel, or member no longer exists. Check the configured IDs."
        if isinstance(error, discord.Forbidden):
            return "Check Pebble's Manage Roles, role hierarchy, View Channel, Read Message History, Add Reactions, and custom emoji access."
        if isinstance(error, (OSError, sqlite3.Error)):
            return "Check that REACTION_ROLES_DB and its parent directory are writable and have free disk space."
        return "Discord request failed. Check connectivity and retry; selections are reconciled every five minutes."

    def _log_failure(self, operation, guild_id, error):
        hint = str(error) if isinstance(error, PanelError) else self._failure_hint(error)
        logger.error("Reaction roles %s guild=%s (%s): %s", operation, guild_id, type(error).__name__, hint)

    @staticmethod
    async def _member_selected(reaction, user_id):
        if reaction is None:
            return False
        # Discord sorts reactors by user ID. Query only the first user at or
        # above this ID, for each reaction type, rather than listing everyone.
        after = discord.Object(id=user_id - 1)
        for kind in (discord.ReactionType.normal, discord.ReactionType.burst):
            async for user in reaction.users(limit=1, after=after, type=kind):
                if user.id == user_id and not user.bot:
                    return True
        return False

    async def _sync_panel(self, panel, user_id=None):
        guild = self.bot.get_guild(panel.guild_id)
        if guild is None:
            raise PanelError("Server unavailable; check that Pebble is still a member and the server is online.")
        role = await self._validate_role(guild, panel.role_id)
        channel = await guild.fetch_channel(panel.channel_id)
        self._validate_channel(channel)
        message = await channel.fetch_message(panel.message_id)
        reaction = next((r for r in message.reactions if emoji_key(r.emoji) == panel.emoji), None)
        if user_id is None:
            # Fully enumerate before removing anything; failed pagination must not remove roles.
            selected = set()
            if reaction:
                for kind in (discord.ReactionType.normal, discord.ReactionType.burst):
                    selected.update({user.id async for user in reaction.users(limit=None, type=kind) if not user.bot})
            tracked = await asyncio.to_thread(self.store.selected, panel.guild_id)
        else:
            # Read current reactions even for removal events: the other reaction
            # type may remain, or a newer event may have overtaken this one.
            selected = {user_id} if await self._member_selected(reaction, user_id) else set()
            tracked = {user_id} if await asyncio.to_thread(self.store.is_tracked, panel.guild_id, user_id) else set()
        for user_id in selected | tracked:
            try:
                # Fetch members for uncached removal events and fresh role membership.
                member = await guild.fetch_member(user_id)
                if member.bot:
                    continue
                if user_id in selected:
                    await asyncio.to_thread(self.store.track, panel.guild_id, user_id)
                    if role not in member.roles:
                        # Recheck just before each grant, including long reconciliations.
                        role = await self._validate_role(guild, panel.role_id)
                        await member.add_roles(role, reason="Hacker reaction-role selection")
                else:
                    if role in member.roles:
                        await member.remove_roles(role, reason="Hacker reaction removed")
                    await asyncio.to_thread(self.store.forget, panel.guild_id, user_id)
            except discord.NotFound:
                await asyncio.to_thread(self.store.forget, panel.guild_id, user_id)
            except discord.HTTPException as error:
                self._log_failure(f"update member={user_id} message={panel.message_id}", panel.guild_id, error)

    async def _run_panel(self, panel, user_id=None):
        async with self._lock:
            try:
                # A command may have replaced the configuration while this event waited.
                if panel not in await asyncio.to_thread(self.store.panels):
                    return
                await self._sync_panel(panel, user_id)
            except FAILURES as error:
                self._log_failure(f"sync message={panel.message_id}", panel.guild_id, error)

    async def _reaction_event(self, payload):
        if not payload.guild_id or getattr(payload, "user_id", None) == self.bot.user.id:
            return
        try:
            panels = await asyncio.to_thread(self.store.panels)
        except (OSError, sqlite3.Error) as error:
            self._log_failure("read configuration", payload.guild_id, error)
            return
        for panel in panels:
            if (panel.guild_id, panel.channel_id, panel.message_id) != (payload.guild_id, payload.channel_id, payload.message_id):
                continue
            if hasattr(payload, "emoji") and emoji_key(payload.emoji) != panel.emoji:
                continue
            await self._run_panel(panel, getattr(payload, "user_id", None))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self._reaction_event(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self._reaction_event(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload):
        await self._reaction_event(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload):
        await self._reaction_event(payload)

    @tasks.loop(minutes=5)
    async def reconcile(self):
        try:
            for panel in await asyncio.to_thread(self.store.panels):
                await self._run_panel(panel)
        except (OSError, sqlite3.Error) as error:
            self._log_failure("read configuration", None, error)

    @reconcile.before_loop
    async def before_reconcile(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_resumed(self):
        # Recover missed changes promptly on gateway reconnects as well as startup.
        await self.reconcile()


async def setup(bot):
    await bot.add_cog(ReactionRoles(bot))
