import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands

from bot import EXTENSIONS, PebbleBot
from cogs.reaction_roles import PanelError, ReactionRoles, emoji_key
from reaction_roles import Panel, ReactionRoleStore
from tests.helpers import settings


class StoreTests(unittest.TestCase):
    def test_configuration_and_selections_survive_reopening(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "roles.sqlite3"
            store = ReactionRoleStore(path)
            store.initialize()
            panel = Panel(1, 2, 3, "💻", 4)
            store.save(panel)
            store.track(1, 5)
            store.track(1, 5)
            reopened = ReactionRoleStore(path)
            reopened.initialize()
            self.assertEqual(reopened.panels(), [panel])
            self.assertEqual(reopened.selected(1), {5})
            self.assertTrue(reopened.is_tracked(1, 5))
            self.assertFalse(reopened.is_tracked(1, 6))
            self.assertFalse(reopened.is_tracked(2, 5))
            reopened.save(replace(panel, message_id=6))
            self.assertEqual(reopened.panels(), [replace(panel, message_id=6)])
            reopened.forget(1, 5)
            self.assertEqual(reopened.selected(1), set())
            self.assertFalse(reopened.is_tracked(1, 5))


class ReactionRoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = replace(settings(HACKER_ROLE_ID="1003"), reaction_roles_db=Path(self.directory.name) / "roles.sqlite3")
        self.role = Mock(spec=discord.Role)
        self.role.id = 1003
        self.role.managed = False
        self.role.permissions = discord.Permissions.none()
        self.role.is_default.return_value = False
        self.role.__ge__ = Mock(return_value=False)
        self.me = SimpleNamespace(guild_permissions=discord.Permissions(manage_roles=True), top_role=object())
        self.member = SimpleNamespace(id=222, bot=False, roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
        self.reactors = []
        self.burst_reactors = []

        async def users(**kwargs):
            source = self.burst_reactors if kwargs["type"] == discord.ReactionType.burst else self.reactors
            after = kwargs.get("after")
            result = sorted((user for user in source if after is None or user.id > after.id), key=lambda user: user.id)
            limit = kwargs.get("limit")
            for user in result if limit is None else result[:limit]:
                yield user

        self.reaction = SimpleNamespace(emoji="💻", users=Mock(side_effect=users))
        self.message = SimpleNamespace(id=333, reactions=[self.reaction], add_reaction=AsyncMock(), jump_url="https://discord.com/channels/789/444/333")
        self.permissions = discord.Permissions(view_channel=True, read_message_history=True, add_reactions=True, send_messages=True)
        self.channel = SimpleNamespace(id=444, overwrites={}, fetch_message=AsyncMock(return_value=self.message),
                                       permissions_for=Mock(return_value=self.permissions), send=AsyncMock(return_value=self.message))
        self.guild = SimpleNamespace(id=789, me=self.me, fetch_roles=AsyncMock(return_value=[self.role]),
                                     fetch_channels=AsyncMock(return_value=[self.channel]),
                                     fetch_channel=AsyncMock(return_value=self.channel), fetch_member=AsyncMock(return_value=self.member))
        self.channel.guild = self.guild
        self.bot = SimpleNamespace(settings=self.settings, user=SimpleNamespace(id=999), get_guild=Mock(return_value=self.guild))
        self.cog = ReactionRoles(self.bot)
        self.cog.store.initialize()
        self.panel = Panel(789, 444, 333, "💻", 1003)
        self.cog.store.save(self.panel)
        self.payload = SimpleNamespace(guild_id=789, channel_id=444, message_id=333, user_id=222, emoji=discord.PartialEmoji(name="💻"))
        self.officer = Mock(spec=discord.Member)
        self.officer.roles = [SimpleNamespace(id=123)]
        self.request = SimpleNamespace(guild=self.guild, guild_id=789, user=self.officer, client=self.bot,
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=Mock(return_value=False)),
            followup=SimpleNamespace(send=AsyncMock()), command=self.cog.reaction_role)

    async def test_raw_add_and_remove_on_uncached_message(self):
        self.reactors.append(self.member)
        await self.cog.on_raw_reaction_add(self.payload)
        self.member.add_roles.assert_awaited_once_with(self.role, reason="Hacker reaction-role selection")
        self.assertEqual(self.cog.store.selected(789), {222})
        self.member.roles = [self.role]
        self.reactors.clear()
        await self.cog.on_raw_reaction_remove(self.payload)
        self.member.remove_roles.assert_awaited_once_with(self.role, reason="Hacker reaction removed")
        self.assertEqual(self.cog.store.selected(789), set())
        self.channel.fetch_message.assert_awaited_with(333)

    async def test_restart_reconciles_offline_additions_and_removals(self):
        self.reactors.append(self.member)
        restarted = ReactionRoles(self.bot)
        await restarted.reconcile()
        self.member.add_roles.assert_awaited_once()
        self.member.roles = [self.role]
        self.reactors.clear()
        restarted_again = ReactionRoles(self.bot)
        await restarted_again.reconcile()
        self.member.remove_roles.assert_awaited_once()

    async def test_ignores_wrong_guild_channel_message_emoji_and_bots(self):
        self.reactors.append(self.member)
        for field, value in (("guild_id", None), ("guild_id", 10), ("channel_id", 10),
                             ("message_id", 10), ("emoji", discord.PartialEmoji(name="✅")), ("user_id", 999)):
            payload = SimpleNamespace(**{**vars(self.payload), field: value})
            await self.cog.on_raw_reaction_add(payload)
        self.member.add_roles.assert_not_awaited()
        self.reactors[:] = [SimpleNamespace(id=20, bot=True)]
        await self.cog.on_raw_reaction_add(self.payload)
        self.member.add_roles.assert_not_awaited()

    async def test_duplicate_and_out_of_order_events_follow_current_reactions(self):
        self.member.roles = [self.role]
        self.reactors.append(self.member)
        await self.cog.on_raw_reaction_add(self.payload)
        await self.cog.on_raw_reaction_remove(self.payload)
        self.member.add_roles.assert_not_awaited()
        self.member.remove_roles.assert_not_awaited()

    async def test_individual_events_fetch_only_the_affected_member(self):
        # A large panel must not turn ten events into a thousand member fetches.
        self.reactors.extend(SimpleNamespace(id=user_id, bot=False) for user_id in range(2000, 2100))
        self.reactors.append(self.member)
        self.cog.store.track(789, 555)
        self.member.roles = [self.role]
        with patch.object(self.cog.store, "selected", side_effect=AssertionError("Full scan on individual event")):
            for _ in range(10):
                await self.cog.on_raw_reaction_add(self.payload)
        self.assertEqual(self.guild.fetch_member.await_count, 10)
        self.assertTrue(all(call.args == (222,) for call in self.guild.fetch_member.await_args_list))
        self.assertTrue(all(call.kwargs["limit"] == 1 and call.kwargs["after"].id == 221
                            for call in self.reaction.users.call_args_list))
        self.assertEqual(self.cog.store.selected(789), {222, 555})
        self.member.remove_roles.assert_not_awaited()

    async def test_removal_keeps_role_while_either_reaction_type_remains(self):
        self.member.roles = [self.role]
        self.cog.store.track(789, 222)
        for remaining in (self.reactors, self.burst_reactors):
            remaining.append(self.member)
            await self.cog.on_raw_reaction_remove(self.payload)
            self.member.remove_roles.assert_not_awaited()
            self.assertEqual(self.cog.store.selected(789), {222})
            remaining.clear()
        await self.cog.on_raw_reaction_remove(self.payload)
        self.member.remove_roles.assert_awaited_once()
        self.assertEqual(self.cog.store.selected(789), set())

    async def test_next_reactor_does_not_select_the_affected_member(self):
        self.cog.store.track(789, 222)
        self.member.roles = [self.role]
        self.reactors.extend([SimpleNamespace(id=221, bot=False), SimpleNamespace(id=223, bot=False)])
        await self.cog.on_raw_reaction_remove(self.payload)
        self.member.remove_roles.assert_awaited_once()
        self.member.add_roles.assert_not_awaited()
        self.guild.fetch_member.assert_awaited_once_with(222)

    async def test_member_lookup_uses_bounded_discord_requests_for_both_types(self):
        def user_data(user_id):
            return {"id": str(user_id), "username": "tester", "discriminator": "0", "avatar": None, "bot": False}

        http = SimpleNamespace(get_reaction_users=AsyncMock(side_effect=[[user_data(223)], [user_data(222)]]))
        message = SimpleNamespace(id=333, guild=None, channel=self.channel, _state=SimpleNamespace(http=http))
        reaction = discord.Reaction(message=message, data={"count": 101, "me": True}, emoji="💻")
        self.assertTrue(await self.cog._member_selected(reaction, 222))
        self.assertEqual(http.get_reaction_users.await_count, 2)
        for call, kind in zip(http.get_reaction_users.await_args_list,
                              (discord.ReactionType.normal, discord.ReactionType.burst)):
            self.assertEqual(call.args, (444, 333, "💻", 1))
            self.assertEqual(call.kwargs, {"after": 221, "type": kind.value})

    async def test_untracked_manual_role_is_preserved_on_individual_removal(self):
        self.member.roles = [self.role]
        await self.cog.on_raw_reaction_remove(self.payload)
        self.member.remove_roles.assert_not_awaited()
        self.guild.fetch_member.assert_not_awaited()

    async def test_rapid_queued_events_follow_latest_reactions(self):
        self.cog.store.track(789, 222)
        self.member.roles = [self.role]
        waiting = asyncio.Event()
        original_run = self.cog._run_panel
        started = 0

        async def run_when_unlocked(*args):
            nonlocal started
            started += 1
            if started == 2:
                waiting.set()
            await original_run(*args)

        with patch.object(self.cog, "_run_panel", side_effect=run_when_unlocked):
            async with self.cog._lock:
                pending = [asyncio.create_task(self.cog.on_raw_reaction_add(self.payload)),
                           asyncio.create_task(self.cog.on_raw_reaction_remove(self.payload))]
                await asyncio.wait_for(waiting.wait(), timeout=5)
                # Both handlers must inspect state after acquiring the lock.
                self.burst_reactors.append(self.member)
            await asyncio.gather(*pending)
        self.member.add_roles.assert_not_awaited()
        self.member.remove_roles.assert_not_awaited()
        self.assertEqual(self.cog.store.selected(789), {222})

    async def test_failed_individual_reaction_lookup_preserves_roles_and_tracking(self):
        self.cog.store.track(789, 222)
        self.member.roles = [self.role]

        async def failed_users(**kwargs):
            for user in self.reactors:
                yield user
            if kwargs["type"] == discord.ReactionType.burst:
                raise discord.HTTPException(SimpleNamespace(status=500, reason="Failed"), "failed")

        self.reaction.users.side_effect = failed_users
        with self.assertLogs("cogs.reaction_roles", level="ERROR"):
            await self.cog.on_raw_reaction_remove(self.payload)
        self.member.remove_roles.assert_not_awaited()
        self.assertEqual(self.cog.store.selected(789), {222})

    async def test_recovery_still_updates_all_selected_and_tracked_members(self):
        other = SimpleNamespace(id=555, bot=False, roles=[self.role], add_roles=AsyncMock(), remove_roles=AsyncMock())
        self.guild.fetch_member.side_effect = lambda user_id: {222: self.member, 555: other}[user_id]
        self.reactors.append(self.member)
        self.cog.store.track(789, 555)
        await self.cog.reconcile()
        self.member.add_roles.assert_awaited_once()
        other.remove_roles.assert_awaited_once()
        self.assertEqual(self.cog.store.selected(789), {222})

    async def test_bulk_clear_removes_tracked_roles_only(self):
        self.cog.store.track(789, 222)
        self.member.roles = [self.role]
        self.message.reactions = []
        await self.cog.on_raw_reaction_clear(SimpleNamespace(guild_id=789, channel_id=444, message_id=333))
        self.member.remove_roles.assert_awaited_once()

    async def test_super_reactions_count_and_custom_names_are_stable(self):
        self.burst_reactors.append(self.member)
        await self.cog.on_raw_reaction_add(self.payload)
        self.member.add_roles.assert_awaited_once()
        self.assertEqual(emoji_key(discord.PartialEmoji(name="renamed", id=123456789012345678)), "123456789012345678")
        self.assertEqual(emoji_key("<:old:123456789012345678>"), "123456789012345678")

    async def test_only_hacker_and_never_officer(self):
        for role_id in (None, 123, 1001, 1002):
            with self.subTest(role_id=role_id), self.assertRaises(PanelError):
                await self.cog._validate_role(self.guild, role_id)
        self.bot.settings = replace(self.settings, hacker_role_id=123)
        with self.assertRaises(PanelError):
            await self.cog._validate_role(self.guild, 123)

    async def test_rejects_any_server_permission_managed_and_everyone(self):
        for permissions in (discord.Permissions(administrator=True), discord.Permissions(manage_messages=True),
                            discord.Permissions(view_channel=True)):
            self.role.permissions = permissions
            with self.assertRaises(PanelError):
                await self.cog._validate_role(self.guild, 1003)
        self.role.permissions = discord.Permissions.none()
        self.role.managed = True
        with self.assertRaises(PanelError):
            await self.cog._validate_role(self.guild, 1003)
        self.role.managed = False
        self.role.is_default.return_value = True
        with self.assertRaises(PanelError):
            await self.cog._validate_role(self.guild, 1003)

    async def test_rejects_channel_permission_grants_but_allows_denies(self):
        self.channel.overwrites = {self.role: discord.PermissionOverwrite(manage_messages=True)}
        with self.assertRaisesRegex(PanelError, "channel permission grants"):
            await self.cog._validate_role(self.guild, 1003)
        self.channel.overwrites = {self.role: discord.PermissionOverwrite(manage_messages=False)}
        self.assertIs(await self.cog._validate_role(self.guild, 1003), self.role)
        self.channel.overwrites = {discord.Object(id=1003): discord.PermissionOverwrite(view_channel=True)}
        with self.assertRaises(PanelError):
            await self.cog._validate_role(self.guild, 1003)

    async def test_validates_bot_permissions_hierarchy_and_deleted_role(self):
        self.me.guild_permissions.manage_roles = False
        with self.assertRaisesRegex(PanelError, "Manage Roles"):
            await self.cog._validate_role(self.guild, 1003)
        self.me.guild_permissions.manage_roles = True
        self.role.__ge__.return_value = True
        with self.assertRaisesRegex(PanelError, "highest role"):
            await self.cog._validate_role(self.guild, 1003)
        self.guild.fetch_roles.return_value = []
        with self.assertRaisesRegex(PanelError, "deleted"):
            await self.cog._validate_role(self.guild, 1003)

    async def test_permission_changes_after_setup_block_assignments_and_log_action(self):
        self.reactors.append(self.member)
        self.role.permissions = discord.Permissions(manage_roles=True)
        with self.assertLogs("cogs.reaction_roles", level="ERROR") as logs:
            await self.cog.on_raw_reaction_add(self.payload)
        self.member.add_roles.assert_not_awaited()
        self.assertIn("NO server permissions", logs.output[0])

    async def test_failed_assignments_are_durable_and_retried(self):
        self.reactors.append(self.member)
        self.member.add_roles.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "private body")
        with self.assertLogs("cogs.reaction_roles", level="ERROR") as logs:
            await self.cog.on_raw_reaction_add(self.payload)
        self.assertEqual(self.cog.store.selected(789), {222})
        self.assertIn("Manage Roles", logs.output[0])
        self.assertNotIn("private body", logs.output[0])
        self.member.add_roles.side_effect = None
        await self.cog.reconcile()
        self.assertEqual(self.member.add_roles.await_count, 2)

    async def test_failed_removals_are_retried(self):
        self.cog.store.track(789, 222)
        self.member.roles = [self.role]
        self.member.remove_roles.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "failed")
        with self.assertLogs("cogs.reaction_roles", level="ERROR"):
            await self.cog.reconcile()
        self.assertEqual(self.cog.store.selected(789), {222})
        self.member.remove_roles.side_effect = None
        await self.cog.reconcile()
        self.assertEqual(self.cog.store.selected(789), set())

    async def test_failed_reaction_pagination_does_not_remove_roles(self):
        self.cog.store.track(789, 222)
        self.member.roles = [self.role]

        async def failed_users(**kwargs):
            yield SimpleNamespace(id=555, bot=False)
            raise discord.HTTPException(SimpleNamespace(status=500, reason="Failed"), "failed")

        self.reaction.users.side_effect = failed_users
        with self.assertLogs("cogs.reaction_roles", level="ERROR"):
            await self.cog.reconcile()
        self.member.remove_roles.assert_not_awaited()
        self.assertEqual(self.cog.store.selected(789), {222})

    async def test_setup_creates_or_configures_existing_message_and_persists(self):
        self.cog.store = ReactionRoleStore(self.settings.reaction_roles_db)
        await self.cog.reaction_role.callback(self.cog, self.request, self.channel)
        self.channel.send.assert_awaited_once()
        self.message.add_reaction.assert_awaited_once()
        self.assertEqual(self.cog.store.panels(), [self.panel])
        self.channel.fetch_message.reset_mock()
        await self.cog.reaction_role.callback(self.cog, self.request, self.channel, "💻", "333")
        self.channel.fetch_message.assert_awaited_with(333)
        self.assertEqual(self.channel.send.await_count, 1)

    async def test_setup_requires_officer_and_is_guild_only(self):
        self.assertTrue(self.cog.reaction_role.guild_only)
        self.assertTrue(await self.cog.reaction_role._check_can_run(self.request))
        self.officer.roles = []
        self.assertFalse(await self.cog.reaction_role._check_can_run(self.request))
        self.request.user = SimpleNamespace(id=222)
        self.assertFalse(await self.cog.reaction_role._check_can_run(self.request))
        await self.cog.reaction_role_error(self.request, app_commands.CheckFailure())
        self.request.response.send_message.assert_awaited_once_with("You need the Officer role to use this command.", ephemeral=True)

    async def test_invalid_setup_does_not_replace_persisted_mapping(self):
        for message_id in ("bad", "0", "-5"):
            with self.assertLogs("cogs.reaction_roles", level="ERROR"):
                await self.cog.reaction_role.callback(self.cog, self.request, self.channel, "💻", message_id)
        self.assertEqual(self.cog.store.panels(), [self.panel])
        self.message.add_reaction.assert_not_awaited()

    async def test_missing_channel_permission_has_actionable_reply(self):
        self.permissions.read_message_history = False
        with self.assertLogs("cogs.reaction_roles", level="ERROR"):
            await self.cog.reaction_role.callback(self.cog, self.request, self.channel)
        self.assertIn("read_message_history", self.request.followup.send.call_args.args[0])
        self.channel.send.assert_not_awaited()

    async def test_deleted_message_preserves_tracked_roles_and_logs(self):
        self.cog.store.track(789, 222)
        self.channel.fetch_message.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="Not found"), "private body")
        with self.assertLogs("cogs.reaction_roles", level="ERROR") as logs:
            await self.cog.reconcile()
        self.member.remove_roles.assert_not_awaited()
        self.assertEqual(self.cog.store.selected(789), {222})
        self.assertIn("Check the configured IDs", logs.output[0])

    async def test_privilege_change_between_scan_and_grant_is_rejected(self):
        self.reactors.append(self.member)
        privileged = Mock(spec=discord.Role)
        privileged.id = 1003
        privileged.is_default.return_value = False
        privileged.managed = False
        privileged.permissions = discord.Permissions(administrator=True)
        self.guild.fetch_roles.side_effect = [[self.role], [privileged]]
        with self.assertLogs("cogs.reaction_roles", level="ERROR"):
            await self.cog.reconcile()
        self.member.add_roles.assert_not_awaited()

    async def test_stale_panel_and_arbitrary_persisted_role_are_ignored(self):
        self.reactors.append(self.member)
        await self.cog._run_panel(replace(self.panel, message_id=900))
        self.member.add_roles.assert_not_awaited()
        self.cog.store.save(replace(self.panel, role_id=123))
        with self.assertLogs("cogs.reaction_roles", level="ERROR"):
            await self.cog.reconcile()
        self.member.add_roles.assert_not_awaited()

    async def test_unknown_emoji_does_not_replace_mapping(self):
        self.message.add_reaction.side_effect = discord.HTTPException(
            SimpleNamespace(status=400, reason="Bad Request"), {"code": 10014, "message": "Unknown Emoji"})
        with self.assertLogs("cogs.reaction_roles", level="ERROR"):
            await self.cog.reaction_role.callback(self.cog, self.request, self.channel, "invalid", "333")
        self.assertEqual(self.cog.store.panels(), [self.panel])
        self.assertIn("valid Unicode emoji", self.request.followup.send.call_args.args[0])

    async def test_disk_failures_log_actionable_hints_without_stopping_reconciliation(self):
        with patch.object(self.cog.store, "panels", side_effect=OSError("private path")):
            with self.assertLogs("cogs.reaction_roles", level="ERROR") as logs:
                await self.cog.reconcile()
                await self.cog.on_raw_reaction_add(self.payload)
        self.assertIn("REACTION_ROLES_DB", logs.output[0])
        self.assertNotIn("private path", logs.output[0])

    async def test_cog_lifecycle_and_intents(self):
        self.assertIn("cogs.reaction_roles", EXTENSIONS)
        bot = PebbleBot(self.settings)
        self.assertTrue(bot.intents.reactions)
        await bot.close()
        with patch.object(self.cog.reconcile, "start") as start, patch.object(self.cog.reconcile, "cancel") as cancel:
            await self.cog.cog_load()
            start.assert_called_once()
            await self.cog.cog_unload()
            cancel.assert_called_once()
