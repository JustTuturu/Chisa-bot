"""Cog layout: kkrypt0nn/Python-Discord-Bot-Template (Apache-2.0); code original."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from commands import channel_allowed, require_admin
from utils.downloader import DownloadError, clean_url, download_video, status_id

if TYPE_CHECKING:
    from bot import AgentBot

log = logging.getLogger(__name__)


@app_commands.command(name="download", description="Download a video from an X/Twitter link.")
@app_commands.describe(url="An x.com / twitter.com / vxtwitter / fixupx status link")
@channel_allowed()
async def download_command(interaction: discord.Interaction, url: str) -> None:
    if not await require_admin(interaction):
        return
    # public reply: the "thinking" state and the followups are visible in the
    # channel. The link is never echoed or stored, only the video is posted.
    await interaction.response.defer()

    clean = clean_url(url)
    post_id = status_id(url or "")
    if clean is None:
        log.info("download %s: rejected (unsupported link)", post_id or "unknown")
        await interaction.followup.send("That link isn't a supported X/Twitter post.")
        return

    cfg = interaction.client.config  # type: ignore[attr-defined]
    path: Path | None = None
    try:
        try:
            path = await download_video(clean, Path(cfg.download_dir), cfg.download_max_seconds)
        except DownloadError as exc:
            log.warning("download %s: failed", post_id)
            await interaction.followup.send(f"⚠️ {exc}")
            return

        if path.stat().st_size > cfg.download_max_mb * 1024 * 1024:
            log.warning("download %s: too large", post_id)
            await interaction.followup.send(
                f"⚠️ That video is larger than {cfg.download_max_mb} MB."
            )
            return

        await interaction.followup.send(file=discord.File(path))
        log.info("download %s: ok", post_id)
    except discord.HTTPException:
        log.warning("download %s: upload failed", post_id)
        await interaction.followup.send("⚠️ Could not upload the video.")
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


async def setup(bot: AgentBot) -> None:
    bot.tree.add_command(download_command)
