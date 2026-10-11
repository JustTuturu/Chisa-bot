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


@app_commands.command(name="status", description="Bot status: uptime, model, memory (admins).")
@channel_allowed()
async def status_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    import time as _time

    if not await require_admin(interaction):
        return
    uptime = _time.monotonic() - bot.started_at  # type: ignore[attr-defined]
    hours, remainder = divmod(int(uptime), 3600)
    minutes = remainder // 60
    stats = await bot.db.stats()  # type: ignore[attr-defined]
    await interaction.response.send_message(
        "🤖 **Status**\n"
        f"• uptime: {hours}h {minutes}m\n"
        f"• model: `{bot.config.llm_model}` @ `{bot.config.llm_base_url}`\n"  # type: ignore[attr-defined]
        f"• memory: {stats['memory_channels']} channel(s), {stats['memory_messages']} message(s)\n"
        f"• tool calls logged: {stats['tool_calls_logged']}\n"
        f"• timezone: {bot.config.timezone}\n",  # type: ignore[attr-defined]
        ephemeral=True,
    )


async def setup(bot: AgentBot) -> None:
    # structural container only: AgentBot is a plain discord.Client
    bot.tree.add_command(status_command)
