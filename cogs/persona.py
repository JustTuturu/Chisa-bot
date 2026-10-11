"""Cog layout: kkrypt0nn/Python-Discord-Bot-Template (Apache-2.0); code original."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from commands import channel_allowed, require_admin

if TYPE_CHECKING:
    from bot import AgentBot

log = logging.getLogger(__name__)


class PersonaGroup(app_commands.Group):
    def __init__(self) -> None:
        super().__init__(name="persona", description="Per-server system prompt (admins only).")

    async def _gate(self, interaction: discord.Interaction) -> discord.Guild | None:
        """Guild-only + admin gate; returns the guild when the caller passes."""
        if interaction.guild is None:
            await interaction.response.send_message("Servers only.", ephemeral=True)
            return None
        if not await require_admin(interaction):
            return None
        return interaction.guild

    @app_commands.command(name="set", description="Set this server's system prompt/persona.")
    @app_commands.describe(system_prompt="Persona instructions the agent follows in this server")
    @channel_allowed()
    async def set_(
        self, interaction: discord.Interaction, system_prompt: app_commands.Range[str, 1, 2000]
    ) -> None:
        guild = await self._gate(interaction)
        if guild is None:
            return
        await interaction.client.db.set_persona(  # type: ignore[attr-defined]
            guild.id,
            system_prompt,
            str(interaction.user),  # type: ignore[union-attr]
        )
        await interaction.response.send_message(
            "✅ Persona updated (applies to new runs).", ephemeral=True
        )

    @app_commands.command(name="view", description="Show this server's current persona (admins).")
    @channel_allowed()
    async def view(self, interaction: discord.Interaction) -> None:
        guild = await self._gate(interaction)
        if guild is None:
            return
        persona = await interaction.client.db.persona_for(guild.id)  # type: ignore[attr-defined]
        if persona is None:
            await interaction.response.send_message(
                "No custom persona; using the built-in default.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"**Current persona:**\n{persona[:1900]}", ephemeral=True
            )

    @app_commands.command(name="clear", description="Reset to the built-in default persona.")
    @channel_allowed()
    async def clear(self, interaction: discord.Interaction) -> None:
        guild = await self._gate(interaction)
        if guild is None:
            return
        removed = await interaction.client.db.delete_persona(guild.id)  # type: ignore[attr-defined]
        await interaction.response.send_message(
            "Persona removed." if removed else "No custom persona was set.", ephemeral=True
        )


persona_group = PersonaGroup()


async def setup(bot: AgentBot) -> None:
    # structural container only: AgentBot is a plain discord.Client
    bot.tree.add_command(persona_group)
