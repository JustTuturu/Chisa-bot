"""Discord-facing tools callable by the LLM, plus their JSON schemas."""

from __future__ import annotations

import contextlib
from typing import Any

import discord
from discord import PartialEmoji

from tools.base import ToolContext, ToolDef, err, ok, safe_mentions
from utils.messages import split_message

VALID_AUTO_ARCHIVE = (60, 1440, 4320, 10080)


def _is_dm(channel: Any) -> bool:
    return getattr(channel, "guild", None) is None


def _bot_perms(channel: Any) -> Any | None:
    """The bot's permissions in a channel, or None when they cannot be read."""
    member = getattr(getattr(channel, "guild", None), "me", None)
    if member is None:
        return None
    try:
        return channel.permissions_for(member)
    except Exception:
        return None


def _perm_error(channel: Any, permission: str, label: str | None = None) -> str | None:
    """Return an error string if the bot lacks `permission` in `channel`."""
    if _is_dm(channel):
        return None
    perms = _bot_perms(channel)
    if perms is None:
        return None  # cannot read them here; Discord answers with 403 if truly missing
    if not getattr(perms, permission, False):
        name = getattr(channel, "name", str(getattr(channel, "id", "?")))
        return f'I am missing the "{label or permission}" permission in #{name}.'
    return None


async def _resolve_channel(ctx: ToolContext, channel_id: Any) -> Any:
    """Resolve channel_id (int | "<#123>" | "current" | None) to a channel."""
    if channel_id is None or str(channel_id).strip().lower() in ("current", "here", ""):
        return ctx.channel
    raw = str(channel_id).strip()
    if raw.startswith("<#") and raw.endswith(">"):
        raw = raw[2:-1]
    if not raw.lstrip("-").isdigit():
        return None
    cid = int(raw)
    channel = ctx.bot.get_channel(cid)
    if channel is None:
        try:
            channel = await ctx.bot.fetch_channel(cid)
        except (discord.NotFound, discord.Forbidden):
            return None
    return channel


async def _resolve_thread(ctx: ToolContext, ref: Any) -> tuple[Any | None, str | None]:
    """Resolve a thread by id, <#mention> or name (searched in this channel).

    Returns (thread, None) or (None, readable problem).
    """
    raw = str(ref or "").strip()
    is_id = (raw.startswith("<#") and raw.endswith(">")) or raw.lstrip("-").isdigit()
    if is_id or raw.lower() in ("current", "here", ""):
        channel = await _resolve_channel(ctx, ref)
        if channel is None:
            names = await _thread_names(ctx)
            return None, (
                f"I can't see {ref!r}: it was deleted, or it is a private thread I am not "
                "a member of. Give me Manage Threads in that channel, or add me to the "
                f"thread. Threads in this channel: {names}."
            )
        if not isinstance(channel, discord.Thread):
            names = await _thread_names(ctx)
            return None, (
                f"{ref or 'This channel'!r} is not a thread. Threads in this channel: {names}. "
                "Give me one of those names or ids."
            )
        return channel, None
    # by name: accept "#name" too, and match case-insensitively
    name = raw[1:].strip() if raw.startswith("#") else raw
    matches = [t for t in await _threads_of(ctx.channel) if t.name.casefold() == name.casefold()]
    if not matches:
        return (
            None,
            f"No thread named {name!r} here. Threads in this channel: {await _thread_names(ctx)}.",
        )
    if len(matches) > 1:
        listing = ", ".join(f"{t.name} (id {t.id})" for t in matches[:10])
        return None, f"Several threads match {name!r}: {listing}. Use one of those ids."
    return matches[0], None


async def _thread_names(ctx: ToolContext) -> str:
    """Comma-separated names of the threads in this channel (or "none")."""
    names = [t.name for t in await _threads_of(ctx.channel) if t.name]
    return ", ".join(names[:10]) or "none"


async def _threads_of(channel: Any) -> list[Any]:
    """Active threads of a channel, plus archived ones (best effort)."""
    parent = channel.parent if isinstance(channel, discord.Thread) else channel
    if parent is None:
        return []
    found: dict[int, Any] = {}
    for thread in getattr(parent, "threads", None) or ():
        found[thread.id] = thread
    for thread in getattr(getattr(parent, "guild", None), "threads", None) or ():
        if getattr(thread, "parent_id", None) == getattr(parent, "id", None):
            found[thread.id] = thread
    with contextlib.suppress(Exception):  # archived threads need Manage Threads
        async for thread in parent.archived_threads(limit=50):
            found[thread.id] = thread
    return list(found.values())


async def _resolve_message(channel: Any, message_id: Any) -> discord.Message | None:
    try:
        mid = int(str(message_id).strip())
    except (TypeError, ValueError):
        return None
    try:
        return await channel.fetch_message(mid)
    except (discord.NotFound, discord.Forbidden):
        return None


def _deny_check(ctx: ToolContext, channel: Any) -> str | None:
    """Channel-allowlist gate (item 5c): refuse disallowed targets."""
    from channels import is_channel_allowed

    cfg = getattr(ctx.bot, "config", None)
    if cfg is None or not getattr(cfg, "allowed_channel_ids", set()):
        return None  # no config (bare test stubs) or empty list = allow-all
    if is_channel_allowed(channel, cfg):
        return None
    from channels import allowlist_mentions

    cid = getattr(channel, "id", channel)
    return (
        f"I am not allowed to act in channel <#{cid}>: it is not in "
        f"ALLOWED_CHANNEL_IDS. Allowed channels: {allowlist_mentions(cfg)}."
    )


def _check_send_target(channel: Any) -> str | None:
    if not isinstance(
        channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.DMChannel)
    ):
        return (
            f"{getattr(channel, 'name', channel)} is a {getattr(channel, 'type', channel)} "
            "and cannot receive normal messages."
        )
    if err_ := _perm_error(channel, "send_messages", "Send Messages"):
        return err_
    if isinstance(channel, discord.Thread) and (
        err_ := _perm_error(channel, "send_messages_in_threads", "Send Messages in Threads")
    ):
        return err_
    return None


async def _send_text(
    channel: Any,
    content: str,
    reference: discord.Message | None = None,
) -> list[int]:
    """Send text split into <=2000 char chunks; returns the message ids."""
    ids: list[int] = []
    parts = split_message(str(content))
    if not parts:
        parts = ["(empty message)"]
    for i, part in enumerate(parts):
        kwargs: dict[str, Any] = {"allowed_mentions": safe_mentions()}
        if i == 0 and reference is not None:
            kwargs["reference"] = reference
        msg = await channel.send(part, **kwargs)
        ids.append(msg.id)
    return ids


def _parse_color(color: Any) -> discord.Colour | None:
    if color in (None, ""):
        return discord.Colour.blurple()
    try:
        return discord.Colour.from_str(str(color))
    except Exception:
        return None


def _parse_emoji(emoji: str) -> str | None:
    """Return an error string if `emoji` cannot be a real Discord emoji."""
    try:
        pe = PartialEmoji.from_str(emoji.strip())
    except Exception:
        return f"Could not parse emoji {emoji!r}."
    if pe.id is None:
        name = pe.name or ""
        if not name or name.isalnum():
            return (
                f"{emoji!r} is not a valid emoji. Use a unicode emoji (e.g. 👍) "
                "or a custom emoji like <:name:1234567890>."
            )
        if len(name) > 32:
            return f"{emoji!r} is not a valid emoji."
    return None


async def send_message(
    ctx: ToolContext,
    channel_id: Any = None,
    content: str = "",
    **_: Any,
) -> dict:
    """Send a normal (non-reply) message to a channel."""
    channel = await _resolve_channel(ctx, channel_id)
    if channel is None:
        return err(f"Channel {channel_id!r} not found or not visible to me.")
    if problem := _deny_check(ctx, channel):
        return err(problem)
    if problem := _check_send_target(channel):
        return err(problem)
    ids = await _send_text(channel, content)
    return ok(channel_id=channel.id, message_ids=ids, parts=len(ids))


async def reply_to_message(
    ctx: ToolContext,
    message_id: int,
    content: str = "",
    **_: Any,
) -> dict:
    """Reply (with a message reference) to a message in the current channel."""
    if problem := _deny_check(ctx, ctx.channel):
        return err(problem)
    target = await _resolve_message(ctx.channel, message_id)
    if target is None:
        return err(f"Message {message_id} not found in the current channel.")
    if problem := _check_send_target(ctx.channel):
        return err(problem)
    ids = await _send_text(ctx.channel, content, reference=target)
    return ok(replied_to=message_id, message_ids=ids)


async def add_reaction(ctx: ToolContext, message_id: int, emoji: str, **_: Any) -> dict:
    if problem := _deny_check(ctx, ctx.channel):
        return err(problem)
    if problem := _parse_emoji(str(emoji)):
        return err(problem)
    channel = ctx.channel
    if problem := _perm_error(channel, "add_reactions", "Add Reactions"):
        return err(problem)
    target = await _resolve_message(channel, message_id)
    if target is None:
        return err(f"Message {message_id} not found in the current channel.")
    await target.add_reaction(PartialEmoji.from_str(str(emoji).strip()))
    return ok(message_id=message_id, emoji=str(emoji))


async def remove_reaction(ctx: ToolContext, message_id: int, emoji: str, **_: Any) -> dict:
    if problem := _deny_check(ctx, ctx.channel):
        return err(problem)
    if problem := _parse_emoji(str(emoji)):
        return err(problem)
    channel = ctx.channel
    target = await _resolve_message(channel, message_id)
    if target is None:
        return err(f"Message {message_id} not found in the current channel.")
    pe = PartialEmoji.from_str(str(emoji).strip())
    # Safety: the bot only ever removes its own reactions.
    await target.remove_reaction(pe, ctx.bot.user)
    return ok(message_id=message_id, emoji=str(emoji))


async def create_thread(
    ctx: ToolContext,
    name: str = "",
    channel_id: Any = None,
    message_id: int | None = None,
    auto_archive_duration: int = 1440,
    first_message: str | None = None,
    **_: Any,
) -> dict:
    if _is_dm(ctx.channel):
        return err("Threads do not exist in DMs.")
    channel = await _resolve_channel(ctx, channel_id)
    if channel is None:
        return err(f"Channel {channel_id!r} not found or not visible to me.")
    if problem := _deny_check(ctx, channel):
        return err(problem)
    if not isinstance(channel, discord.TextChannel):
        return err("Threads can only be created in a text channel (not inside threads or DMs).")
    name = str(name).strip()
    if not (1 <= len(name) <= 100):
        return err("Thread name must be 1-100 characters.")
    if auto_archive_duration not in VALID_AUTO_ARCHIVE:
        return err(f"auto_archive_duration must be one of {list(VALID_AUTO_ARCHIVE)} minutes.")
    if problem := _perm_error(channel, "create_public_threads", "Create Public Threads"):
        return err(problem)

    if message_id is not None:
        target = await _resolve_message(channel, message_id)
        if target is None:
            return err(f"Message {message_id} not found in that channel.")
        # message-anchored threads: Discord derives the type from the source
        thread = await target.create_thread(name=name, auto_archive_duration=auto_archive_duration)
    else:
        thread_type = (
            discord.ChannelType.news_thread
            if getattr(channel, "type", None) == discord.ChannelType.news
            else discord.ChannelType.public_thread
        )
        thread = await channel.create_thread(
            name=name,
            auto_archive_duration=auto_archive_duration,
            type=thread_type,
        )
    if first_message:
        if problem := _perm_error(thread, "send_messages_in_threads", "Send Messages in Threads"):
            return ok(thread_id=thread.id, thread_mention=f"<#{thread.id}>", warning=problem)
        await thread.send(str(first_message), allowed_mentions=safe_mentions())
    return ok(thread_id=thread.id, thread_mention=f"<#{thread.id}>")


async def send_in_thread(
    ctx: ToolContext, thread_id: Any = None, content: str = "", **_: Any
) -> dict:
    thread, problem = await _resolve_thread(ctx, thread_id)
    if problem:
        return err(problem)
    if problem := _deny_check(ctx, thread):
        return err(problem)
    if not isinstance(thread, discord.Thread):
        return err(f"Channel {thread_id!r} is not a thread.")
    if problem := _perm_error(thread, "send_messages_in_threads", "Send Messages in Threads"):
        return err(problem)
    ids = await _send_text(thread, content)
    return ok(thread_id=thread.id, message_ids=ids)


async def archive_thread(ctx: ToolContext, thread_id: Any = None, **_: Any) -> dict:
    thread, problem = await _resolve_thread(ctx, thread_id)
    if problem:
        return err(problem)
    if problem := _deny_check(ctx, thread):
        return err(problem)
    if not isinstance(thread, discord.Thread):
        return err(f"Channel {thread_id!r} is not a thread.")
    if problem := _perm_error(thread, "manage_threads", "Manage Threads"):
        return err(problem)
    await thread.edit(archived=True)
    return ok(thread_id=thread.id, archived=True)


async def delete_thread(ctx: ToolContext, thread_id: Any = None, **_: Any) -> dict:
    thread, problem = await _resolve_thread(ctx, thread_id)
    if problem:
        return err(problem)
    if problem := _deny_check(ctx, thread):
        return err(problem)
    if not isinstance(thread, discord.Thread):
        return err(f"Channel {thread_id!r} is not a thread.")
    if problem := _perm_error(thread, "manage_threads", "Manage Threads"):
        return err(problem)
    await thread.delete()
    return ok(thread_id=thread.id, deleted=True)


async def edit_message(ctx: ToolContext, message_id: int, content: str = "", **_: Any) -> dict:
    if problem := _deny_check(ctx, ctx.channel):
        return err(problem)
    target = await _resolve_message(ctx.channel, message_id)
    if target is None:
        return err(f"Message {message_id} not found in the current channel.")
    # Hard safety rule: only the bot's own messages may be edited.
    if target.author.id != ctx.bot.user.id:
        return err("I may only edit my own messages (this one was written by someone else).")
    if len(content) > 2000:
        return err("Discord limits edits to 2000 characters; use send_message for longer text.")
    await target.edit(content=str(content), allowed_mentions=safe_mentions())
    return ok(message_id=message_id, edited=True)


async def delete_message(ctx: ToolContext, message_id: int, **_: Any) -> dict:
    if problem := _deny_check(ctx, ctx.channel):
        return err(problem)
    target = await _resolve_message(ctx.channel, message_id)
    if target is None:
        return err(f"Message {message_id} not found in the current channel.")
    # Hard safety rule: only the bot's own messages may be deleted.
    if target.author.id != ctx.bot.user.id:
        return err("I may only delete my own messages (this one was written by someone else).")
    await target.delete()
    return ok(message_id=message_id, deleted=True)


async def fetch_recent_messages(
    ctx: ToolContext,
    channel_id: Any = None,
    limit: int = 20,
    **_: Any,
) -> dict:
    channel = await _resolve_channel(ctx, channel_id)
    if channel is None:
        return err(f"Channel {channel_id!r} not found or not visible to me.")
    if problem := _deny_check(ctx, channel):
        return err(problem)
    if problem := _perm_error(channel, "read_message_history", "Read Message History"):
        return err(problem)
    fetched = [message async for message in channel.history(limit=int(limit), oldest_first=False)]
    items = []
    for message in reversed(fetched):  # oldest -> newest for readable order
        items.append(
            {
                "id": message.id,
                "author": str(message.author),
                "bot": message.author.bot,
                "content": (message.content or "(no text)")[:500],
                "created_at": message.created_at.isoformat(),
            }
        )
    return ok(channel_id=channel.id, count=len(items), messages=items)


async def pin_message(ctx: ToolContext, message_id: int, **_: Any) -> dict:
    if problem := _deny_check(ctx, ctx.channel):
        return err(problem)
    if _is_dm(ctx.channel):
        return err("Pinning is not available in DMs.")
    if problem := _perm_error(ctx.channel, "pin_messages", "Pin Messages"):
        return err(problem)
    target = await _resolve_message(ctx.channel, message_id)
    if target is None:
        return err(f"Message {message_id} not found in the current channel.")
    try:
        await target.pin()
    except discord.ClientException as exc:  # e.g. already pinned / too many pins
        return err(f"Could not pin message: {exc}")
    return ok(message_id=message_id, pinned=True)


async def send_embed(
    ctx: ToolContext,
    title: str | None = None,
    description: str | None = None,
    fields: list[dict] | None = None,
    color: str | None = None,
    channel_id: Any = None,
    **_: Any,
) -> dict:
    channel = await _resolve_channel(ctx, channel_id)
    if channel is None:
        return err(f"Channel {channel_id!r} not found or not visible to me.")
    if problem := _deny_check(ctx, channel):
        return err(problem)
    if not title and not description and not fields:
        return err("Embed needs at least one of: title, description, fields.")
    colour = _parse_color(color)
    if colour is None:
        return err(f"Could not parse color {color!r}; use e.g. '#ff5500' or an integer.")
    if not _is_dm(channel):
        if problem := _perm_error(channel, "embed_links", "Embed Links"):
            return err(problem)
        if problem := _perm_error(channel, "send_messages", "Send Messages"):
            return err(problem)

    embed = discord.Embed(title=(title or None), description=(description or None), colour=colour)
    for field in fields or []:
        embed.add_field(
            name=str(field.get("name", ""))[:256],
            value=str(field.get("value", ""))[:1024],
            inline=True,
        )
    message = await channel.send(embed=embed, allowed_mentions=safe_mentions())
    return ok(channel_id=channel.id, message_id=message.id)


async def list_channels(ctx: ToolContext, **_: Any) -> dict:
    # after untrusted data, expose only the origin channel
    if ctx.web_used:
        name = getattr(ctx.channel, "name", None) or "direct-message"
        chan_id = getattr(ctx.channel, "id", None)
        return ok(
            channels=[
                {
                    "id": chan_id,
                    "mention": f"<#{chan_id}>"
                    if chan_id is not None and ctx.guild is not None
                    else None,
                    "name": name,
                    "type": "current (restricted)",
                }
            ],
            count=1,
            restricted=True,
        )
    if ctx.guild is None:
        chan_id = getattr(ctx.channel, "id", None)
        return ok(
            channels=[{"id": chan_id, "mention": None, "name": "direct-message", "type": "dm"}],
            count=1,
        )
    items: list[dict] = []
    cfg = getattr(ctx.bot, "config", None)
    from channels import is_channel_id_allowed, is_obfuscated

    def _allowed(channel_id: int, parent_id: int | None = None) -> bool:
        if cfg is None:  # bare test stubs: no allowlist in play
            return True
        return is_channel_id_allowed(channel_id, parent_id=parent_id, config=cfg)

    for channel in ctx.guild.text_channels:
        # CHANNEL_OBFUSCATED: never expose the name/topic of such a channel
        if is_obfuscated(channel) or not _allowed(channel.id):
            continue
        items.append(
            {
                "id": channel.id,
                "mention": f"<#{channel.id}>",
                "name": channel.name,
                "type": "text",
                "category": channel.category.name if channel.category else None,
                "topic": (channel.topic or "")[:100],
            }
        )
    for thread in ctx.guild.threads:
        # a thread is listable only when its parent channel is allowed
        if is_obfuscated(thread) or not _allowed(thread.id, getattr(thread, "parent_id", None)):
            continue
        parent = getattr(thread, "parent", None)
        parent_name = parent.name if parent is not None and not is_obfuscated(parent) else None
        items.append(
            {
                "id": thread.id,
                "mention": f"<#{thread.id}>",
                "name": thread.name,
                "type": "thread",
                "category": "parent: " + parent_name if parent_name else None,
                "topic": None,
            }
        )
    count = len(items)
    return ok(
        channels=items[:100], count=count, note=("showing first 100" if count > 100 else None)
    )


async def get_channel_info(ctx: ToolContext, channel_id: Any = None, **_: Any) -> dict:
    channel = await _resolve_channel(ctx, channel_id)
    if channel is None:
        return err(f"Channel {channel_id!r} not found or not visible to me.")
    if problem := _deny_check(ctx, channel):
        return err(problem)
    from channels import is_obfuscated

    obfuscated = is_obfuscated(channel)

    created = getattr(channel, "created_at", None)
    info: dict[str, Any] = {
        "id": channel.id,
        # never expose the name/topic of a CHANNEL_OBFUSCATED channel (2026-11-16)
        "name": "(hidden: not visible to bots)"
        if obfuscated
        else (getattr(channel, "name", None) or "direct-message"),
        "type": getattr(channel.type, "name", None) or "dm",
        "created_at": created.isoformat() if created else None,
    }
    if obfuscated:
        return ok(**info, obfuscated=True)
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        info["topic"] = getattr(channel, "topic", None) or None
        info["nsfw"] = bool(getattr(channel, "nsfw", False))
        info["category"] = channel.category.name if channel.category else None
        threads = await _threads_of(channel)
        if threads:
            info["threads"] = [
                {"id": t.id, "name": t.name, "archived": bool(getattr(t, "archived", False))}
                for t in threads[:50]
            ]
    if isinstance(channel, discord.Thread):
        info["parent_id"] = channel.parent_id
        info["archived"] = channel.archived
        info["owner_id"] = channel.owner_id
        info["auto_archive_duration"] = channel.auto_archive_duration
    if _is_dm(channel):
        info["note"] = "This is a DM; most channel concepts do not apply."
    else:
        perms = _bot_perms(channel)
        info["bot_permissions"] = (
            {
                name: bool(getattr(perms, name, False))
                for name in (
                    "send_messages",
                    "send_messages_in_threads",
                    "add_reactions",
                    "read_message_history",
                    "create_public_threads",
                    "manage_threads",
                    "manage_messages",
                    "embed_links",
                )
            }
            if perms is not None
            else None
        )
    return ok(**info)


_ID_SCHEMA = {
    "description": (
        'Channel or thread ID (int), a <#123> mention, or "current" for this conversation\'s '
        "channel."
    ),
    "type": ["integer", "string"],
}

_THREAD_SCHEMA = {
    "description": (
        "Thread id (int), a <#123> mention, or the thread's name (looked up in this "
        "channel). get_channel_info lists the threads here if you are unsure."
    ),
    "type": ["integer", "string"],
}


def build_tool_defs() -> list[ToolDef]:
    return [
        ToolDef(
            name="send_message",
            description=(
                "Send a normal message to a channel. Long content is split into several "
                "Discord messages automatically. Mentions are disabled (nobody gets pinged)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel_id": _ID_SCHEMA,
                    "content": {
                        "type": "string",
                        "description": "Message text (Discord markdown allowed).",
                        "minLength": 1,
                        "maxLength": 6000,
                    },
                },
                "required": ["content"],
            },
            handler=send_message,
        ),
        ToolDef(
            name="reply_to_message",
            description=(
                "Reply to a specific message in the current channel, with a visible reply "
                "reference."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": ["integer", "string"],
                        "description": "ID of the message to reply to.",
                    },
                    "content": {"type": "string", "minLength": 1, "maxLength": 6000},
                },
                "required": ["message_id", "content"],
            },
            handler=reply_to_message,
        ),
        ToolDef(
            name="add_reaction",
            description=(
                "Add a reaction to a message in the current channel. Unicode emoji (👍) or "
                "custom emoji (<:name:id>)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message_id": {"type": ["integer", "string"]},
                    "emoji": {
                        "type": "string",
                        "maxLength": 64,
                        "description": "Unicode or custom emoji.",
                    },
                },
                "required": ["message_id", "emoji"],
            },
            handler=add_reaction,
        ),
        ToolDef(
            name="remove_reaction",
            description="Remove the bot's own reaction from a message in the current channel.",
            parameters={
                "type": "object",
                "properties": {
                    "message_id": {"type": ["integer", "string"]},
                    "emoji": {"type": "string", "maxLength": 64},
                },
                "required": ["message_id", "emoji"],
            },
            handler=remove_reaction,
        ),
        ToolDef(
            name="create_thread",
            description=(
                "Create a (public) thread, either standalone in a channel or started from a "
                "message. Optionally send a first message into it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel_id": _ID_SCHEMA,
                    "name": {"type": "string", "minLength": 1, "maxLength": 100},
                    "message_id": {
                        "type": ["integer", "string"],
                        "description": "If given, the thread is started from this message.",
                    },
                    "auto_archive_duration": {
                        "type": "integer",
                        "enum": list(VALID_AUTO_ARCHIVE),
                        "description": "Minutes of inactivity before auto-archive.",
                    },
                    "first_message": {"type": "string", "maxLength": 2000},
                },
                "required": ["name"],
            },
            handler=create_thread,
        ),
        ToolDef(
            name="send_in_thread",
            description="Send a message into a thread (by id, <#mention> or name).",
            parameters={
                "type": "object",
                "properties": {
                    "thread_id": _THREAD_SCHEMA,
                    "content": {"type": "string", "minLength": 1, "maxLength": 6000},
                },
                "required": ["thread_id", "content"],
            },
            handler=send_in_thread,
        ),
        ToolDef(
            name="archive_thread",
            description=(
                "Archive a thread (needs Manage Threads). Give its id, <#mention> or name."
            ),
            parameters={
                "type": "object",
                "properties": {"thread_id": _THREAD_SCHEMA},
                "required": ["thread_id"],
            },
            handler=archive_thread,
        ),
        ToolDef(
            name="delete_thread",
            description=(
                "Permanently delete a thread and everything in it (needs Manage Threads). "
                "Give its id, <#mention> or name. Cannot be undone — prefer archive_thread "
                "unless deletion was asked for."
            ),
            parameters={
                "type": "object",
                "properties": {"thread_id": _THREAD_SCHEMA},
                "required": ["thread_id"],
            },
            handler=delete_thread,
        ),
        ToolDef(
            name="edit_message",
            description=(
                "Edit one of the bot's own messages in the current channel. Editing messages "
                "written by other people is not allowed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message_id": {"type": ["integer", "string"]},
                    "content": {"type": "string", "minLength": 1, "maxLength": 2000},
                },
                "required": ["message_id", "content"],
            },
            handler=edit_message,
        ),
        ToolDef(
            name="delete_message",
            description=(
                "Delete one of the bot's own messages in the current channel. Deleting "
                "messages written by other people is not allowed."
            ),
            parameters={
                "type": "object",
                "properties": {"message_id": {"type": ["integer", "string"]}},
                "required": ["message_id"],
            },
            handler=delete_message,
        ),
        ToolDef(
            name="fetch_recent_messages",
            description=(
                "Read the most recent messages of a channel (oldest first). Useful for "
                "summaries and context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel_id": _ID_SCHEMA,
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "How many messages (default 20, max 50).",
                    },
                },
            },
            handler=fetch_recent_messages,
        ),
        ToolDef(
            name="pin_message",
            description="Pin a message in the current channel (needs Manage Messages).",
            parameters={
                "type": "object",
                "properties": {"message_id": {"type": ["integer", "string"]}},
                "required": ["message_id"],
            },
            handler=pin_message,
        ),
        ToolDef(
            name="send_embed",
            description=(
                "Send a rich embed (title/description/fields/color). Requires the Embed Links "
                "permission in servers."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel_id": _ID_SCHEMA,
                    "title": {"type": "string", "maxLength": 256},
                    "description": {"type": "string", "maxLength": 4096},
                    "fields": {
                        "type": "array",
                        "maxItems": 25,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "maxLength": 256},
                                "value": {"type": "string", "maxLength": 1024},
                            },
                            "required": ["name", "value"],
                        },
                    },
                    "color": {
                        "type": "string",
                        "maxLength": 16,
                        "description": "Hex color like '#ff5500'.",
                    },
                },
            },
            handler=send_embed,
        ),
        ToolDef(
            name="list_channels",
            description=(
                "List the text channels and active threads of this server (id + mention + name)."
            ),
            parameters={"type": "object", "properties": {}},
            handler=list_channels,
        ),
        ToolDef(
            name="get_channel_info",
            description=(
                "Get details about a channel: type, topic, category, the bot's permissions "
                "there, and the threads it contains (id + name)."
            ),
            parameters={
                "type": "object",
                "properties": {"channel_id": _ID_SCHEMA},
            },
            handler=get_channel_info,
        ),
    ]
