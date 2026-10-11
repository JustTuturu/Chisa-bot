"""Shared tool plumbing: context, tool definition, result helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import discord


@dataclass
class ToolContext:
    """Per-run context: the bot, the current channel/guild and who triggered the run."""

    bot: Any  # AgentBot
    channel: Any  # conversation channel ("current")
    guild: discord.Guild | None
    user_id: int
    user_display: str
    is_admin: bool
    # Set to True once a web tool succeeds in this run (prompt-injection guard).
    web_used: bool = False


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[dict]]
    admin_only: bool = False


def ok(**fields) -> dict:
    """Successful tool result."""
    return {"ok": True, **fields}


def err(message: str) -> dict:
    """Failed tool result with a readable error the LLM can act on."""
    return {"ok": False, "error": str(message)[:500]}


def safe_mentions() -> discord.AllowedMentions:
    """Never allow the model (or users via the model) to ping anyone."""
    return discord.AllowedMentions.none()
