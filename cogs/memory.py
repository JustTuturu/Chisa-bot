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


@app_commands.command(name="memory", description="Conversation memory for this channel (admins).")
@app_commands.describe(action="clear = wipe this channel's stored conversation history")
@channel_allowed()
async def memory_command(interaction: discord.Interaction, action: str = "clear") -> None:
    from utils.messages import channel_key

    if not await require_admin(interaction):
        return
    if action.strip().lower() != "clear":
        await interaction.response.send_message(
            "Unknown action; only `clear` is supported.", ephemeral=True
        )
        return
    removed = await interaction.client.db.clear_channel(channel_key(interaction.channel))  # type: ignore[attr-defined,arg-type]
    await interaction.response.send_message(
        f"🧹 Cleared {removed} stored message(s) for this channel.", ephemeral=True
    )


async def setup(bot: AgentBot) -> None:
    # structural container only: AgentBot is a plain discord.Client
    bot.tree.add_command(memory_command)
