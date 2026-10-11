"""Permission predicates for slash commands + template-style cog loading."""

from __future__ import annotations

import importlib
import logging

import discord
from discord import app_commands

log = logging.getLogger(__name__)


async def require_admin(interaction: discord.Interaction) -> bool:
    """Respond ephemerally and return False unless the caller is an admin."""
    bot = interaction.client
    if bot.is_admin(interaction.user):  # type: ignore[attr-defined]
        return True
    await interaction.response.send_message(
        "🔒 Only server admins (or configured ADMIN_USER_IDS) can do that.",
        ephemeral=True,
    )
    return False


async def channel_allowed_predicate(interaction: discord.Interaction) -> bool:
    """Fail-closed channel gate; raises CheckFailure when the channel is not allowed."""
    from channels import is_channel_allowed, refusal_text

    bot = interaction.client
    cfg = bot.config  # type: ignore[attr-defined]
    if not cfg.allowed_channel_ids:
        return True
    if is_channel_allowed(interaction.channel, cfg):
        return True
    raise app_commands.CheckFailure(refusal_text(cfg))


def channel_allowed():
    """Decorator: refuse slash commands in non-allowlisted channels (ephemeral)."""
    return app_commands.check(channel_allowed_predicate)


COG_MODULES = ("memory", "persona", "admin", "download", "purge")


async def load_cogs(bot) -> None:
    """Load every slash-command cog."""
    for name in COG_MODULES:
        # discord.Client has no load_extension (only commands.Bot does)
        module = importlib.import_module(f"cogs.{name}")
        await module.setup(bot)
        log.info("Loaded cog cogs.%s", name)
