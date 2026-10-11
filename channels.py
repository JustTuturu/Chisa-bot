"""Channel allowlist (ALLOWED_CHANNEL_IDS) — the single source of truth."""

from __future__ import annotations

import discord

from config import CONFIG as _DEFAULT_CONFIG

CHANNEL_OBFUSCATED_FLAG = 1 << 17
_ALL_THREAD_TYPES = (
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.news_thread,
)


def _cfg(config=None):
    return config if config is not None else _DEFAULT_CONFIG


def is_obfuscated(channel) -> bool:
    """True when Discord hides this channel from bots that cannot view it."""
    return bool(getattr(channel, "flags", 0) & CHANNEL_OBFUSCATED_FLAG)


def _is_thread(channel) -> bool:
    """Type-agnostic thread check (works for mocks: threads carry these attrs)."""
    if isinstance(channel, discord.Thread):
        return True
    if getattr(channel, "type", None) in _ALL_THREAD_TYPES:
        return True
    # duck-typing fallback for mocks and future thread classes
    return hasattr(channel, "auto_archive_duration") and hasattr(channel, "parent_id")


def is_channel_id_allowed(channel_id: int, *, parent_id: int | None = None, config=None) -> bool:
    """ID-only variant (used where no channel object is available)."""
    ids = _cfg(config).allowed_channel_ids
    if not ids:
        return True
    if parent_id is not None:
        return parent_id in ids  # thread: only its parent matters
    return channel_id in ids


def is_channel_allowed(channel, config=None) -> bool:
    """THE central gate."""
    if channel is None:
        return False
    guild = getattr(channel, "guild", None)
    if guild is None:
        return True  # DM: the DM policy in should_respond decides, not this list
    cfg = _cfg(config)
    parent_id = getattr(channel, "parent_id", None)
    if _is_thread(channel):
        return is_channel_id_allowed(channel.id, parent_id=parent_id, config=cfg)
    return is_channel_id_allowed(channel.id, parent_id=None, config=cfg)


def allowlist_mentions(config=None, limit: int = 10) -> str:
    """`<#id>` mention list for refusal messages (capped, largest ids first)."""
    ids = sorted(_cfg(config).allowed_channel_ids, reverse=True)
    if not ids:
        return "(none configured)"
    shown = ", ".join(f"<#{i}>" for i in ids[:limit])
    if len(ids) > limit:
        shown += f", … (+{len(ids) - limit} more)"
    return shown


def refusal_text(config=None) -> str:
    return "🔒 This channel is not in ALLOWED_CHANNEL_IDS. Allowed: " + allowlist_mentions(config)
