from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands

from bot import EXTENSIONS
from cogs.roles import Roles
from tests.helpers import settings


class RoleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(settings=settings(JUDGE_ROLE_ID="1001", MENTOR_ROLE_ID="1002", HACKER_ROLE_ID="1003"))
        self.cog = Roles(self.bot)
        self.roles = {
            role_id: SimpleNamespace(id=role_id, managed=False, is_default=lambda: False)
            for role_id in (1001, 1002, 1003)
        }
        self.guild = SimpleNamespace(id=789, get_role=Mock(side_effect=self.roles.get))
        self.member = SimpleNamespace(guild=self.guild, roles=[], mention="<@222>", add_roles=AsyncMock())
        self.officer = Mock(spec=discord.Member)
        self.officer.id = 111
        self.officer.roles = [SimpleNamespace(id=123)]
        self.request = SimpleNamespace(
            client=self.bot, guild=self.guild, user=self.officer,
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), is_done=Mock(return_value=False)),
            followup=SimpleNamespace(send=AsyncMock()),
            command=self.cog.assign_role,
        )

    async def invoke(self, role):
        await self.cog.assign_role.callback(self.cog, self.request, self.member, role)

    async def test_all_three_roles_assign_only_the_configured_role(self):
        for role, role_id in (("judge", 1001), ("mentor", 1002), ("hacker", 1003)):
            with self.subTest(role=role):
                self.member.add_roles.reset_mock()
                await self.invoke(role)
                self.member.add_roles.assert_awaited_once_with(self.roles[role_id], reason="/assign-role by officer 111")
                self.assertTrue(self.request.followup.send.call_args.kwargs["ephemeral"])

    async def test_command_registration_and_choices(self):
        self.assertIn("cogs.roles", EXTENSIONS)
        self.assertTrue(self.cog.assign_role.guild_only)
        parameter = next(p for p in self.cog.assign_role.parameters if p.name == "role")
        self.assertEqual([c.value for c in parameter.choices], ["judge", "mentor", "hacker"])

    async def test_actual_command_check_requires_officer_role(self):
        self.assertTrue(await self.cog.assign_role._check_can_run(self.request))
        self.officer.roles = [SimpleNamespace(id=999)]
        self.assertFalse(await self.cog.assign_role._check_can_run(self.request))
        self.request.user = SimpleNamespace(id=111)
        self.assertFalse(await self.cog.assign_role._check_can_run(self.request))
        self.request.user = self.officer
        self.officer.roles = [SimpleNamespace(id=123)]
        self.request.client = SimpleNamespace()
        self.assertFalse(await self.cog.assign_role._check_can_run(self.request))
        self.member.add_roles.assert_not_awaited()

    async def test_arbitrary_role_is_rejected(self):
        for role in ("officer", "admin", "1001"):
            await self.invoke(role)
        self.member.add_roles.assert_not_awaited()
        self.guild.get_role.assert_not_called()

    async def test_missing_configuration_and_deleted_roles_are_rejected(self):
        self.bot.settings = settings()
        await self.invoke("judge")
        self.bot.settings = settings(JUDGE_ROLE_ID="9000")
        await self.invoke("judge")
        self.member.add_roles.assert_not_awaited()

    async def test_officer_default_and_managed_roles_are_rejected(self):
        self.bot.settings = settings(JUDGE_ROLE_ID="123")
        self.roles[123] = SimpleNamespace(id=123)
        await self.invoke("judge")
        self.bot.settings = settings(JUDGE_ROLE_ID="1001")
        self.roles[1001].managed = True
        await self.invoke("judge")
        self.roles[1001].managed = False
        self.roles[1001].is_default = lambda: True
        await self.invoke("judge")
        self.member.add_roles.assert_not_awaited()

    async def test_existing_role_is_not_added_again(self):
        self.member.roles = [self.roles[1001]]
        await self.invoke("judge")
        self.member.add_roles.assert_not_awaited()
        self.assertIn("already has", self.request.response.send_message.call_args.args[0])

    async def test_dm_and_other_server_member_are_rejected(self):
        self.member.guild = SimpleNamespace(id=999)
        await self.invoke("judge")
        self.request.guild = None
        await self.invoke("judge")
        self.member.add_roles.assert_not_awaited()

    async def test_discord_failures_get_private_responses(self):
        for error_type in (discord.Forbidden, discord.HTTPException):
            self.member.add_roles.side_effect = error_type(SimpleNamespace(status=403, reason="Forbidden"), "failed")
            await self.invoke("judge")
            self.assertTrue(self.request.followup.send.call_args.kwargs["ephemeral"])
            self.assertNotIn("Assigned", self.request.followup.send.call_args.args[0])

    async def test_permission_error_uses_shared_private_reply(self):
        await self.cog.assign_role_error(self.request, app_commands.CheckFailure())
        self.request.response.send_message.assert_awaited_once_with("You need the Officer role to use this command.", ephemeral=True)
