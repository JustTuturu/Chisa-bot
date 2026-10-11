"""Cog layout: kkrypt0nn/Python-Discord-Bot-Template (Apache-2.0); code original."""

from __future__ import annotations

import contextlib
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from commands import channel_allowed, require_admin

if TYPE_CHECKING:
    from bot import AgentBot

log = logging.getLogger(__name__)

SCAN_LIMIT = 200  # how far back to look for messages
BULK_MAX_AGE_DAYS = 14  # Discord only bulk-deletes messages younger than this


def _can_manage_messages(channel: Any) -> bool:
    """True when the bot may delete other people's messages here."""
    member = getattr(getattr(channel, "guild", None), "me", None)
    if member is None:
        return False
    try:
        return bool(channel.permissions_for(member).manage_messages)
    except Exception:
        return False


@app_commands.command(name="purge", description="Delete recent messages here (in a DM: my own).")
@app_commands.describe(
    count="How many recent messages to delete (1-100).",
    hours="Only messages newer than this (0 = no limit).",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@channel_allowed()
async def purge_command(
    interaction: discord.Interaction,
    count: app_commands.Range[int, 1, 100] = 50,
    hours: app_commands.Range[int, 0, 720] = 0,
) -> None:
    if not await require_admin(interaction):
        return
    await interaction.response.defer(ephemeral=True)

    channel = interaction.channel
    me = interaction.client.user
    if channel is None or me is None:
        return
    if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.DMChannel)):
        await interaction.followup.send("I can only purge in a channel or thread.", ephemeral=True)
        return
    is_dm = isinstance(channel, discord.DMChannel)
    if not is_dm and not _can_manage_messages(channel):
        await interaction.followup.send(
            "I need the Manage Messages permission to purge here.", ephemeral=True
        )
        return

    cutoff = discord.utils.utcnow() - timedelta(hours=hours) if hours else None
    targets: list[discord.Message] = []
    async for message in channel.history(limit=SCAN_LIMIT):  # newest first
        if cutoff is not None and message.created_at < cutoff:
            break
        if is_dm and message.author.id != me.id:
            continue  # in a DM only my own messages can be deleted
        targets.append(message)
        if len(targets) >= count:
            break

    if not targets:
        await interaction.followup.send("Nothing to delete here.", ephemeral=True)
        return

    now = discord.utils.utcnow()
    recent = [m for m in targets if now - m.created_at < timedelta(days=BULK_MAX_AGE_DAYS)]
    recent_ids = {m.id for m in recent}
    older = [m for m in targets if m.id not in recent_ids]

    deleted = 0
    if recent and not is_dm:
        try:
            await channel.delete_messages(recent)  # one API call
            deleted += len(recent)
        except discord.HTTPException:  # fall back to deleting one by one
            for message in recent:
                with contextlib.suppress(discord.HTTPException):
                    await message.delete()
                    deleted += 1
    else:
        for message in recent:
            with contextlib.suppress(discord.HTTPException):
                await message.delete()
                deleted += 1
    for message in older:
        with contextlib.suppress(discord.HTTPException):
            await message.delete()
            deleted += 1

    log.info("purge: deleted %d message(s) in channel %s", deleted, channel.id)
    await interaction.followup.send(f"🧹 Deleted {deleted} message(s) here.", ephemeral=True)


async def setup(bot: AgentBot) -> None:
    bot.tree.add_command(purge_command)
