"""Chisa-Bot: Discord gateway entrypoint for the LLM agent loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict, deque

import discord
from discord import app_commands

from agent.llm import LLMClient
from agent.loop import AgentLoop
from channels import is_channel_allowed
from config import CONFIG
from db import Database
from tools import discord_tools
from utils.messages import channel_key, split_message

log = logging.getLogger("bot")

RESPONDABLE_TYPES = (discord.MessageType.default, discord.MessageType.reply)


class RateLimiter:
    """Simple sliding-window rate limiter per user."""

    def __init__(self, max_requests: int, window_seconds: int):
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def hit(self, user_id: int) -> bool:
        """Record a request; returns True if allowed, False if rate-limited."""
        now = time.monotonic()
        bucket = self._hits[user_id]
        while bucket and bucket[0] <= now - self._window:
            bucket.popleft()
        if not bucket:
            del self._hits[user_id]  # bounded: keep no empty buckets around
            bucket = None
        if bucket is not None and len(bucket) >= self._max:
            return False
        if bucket is None:
            bucket = self._hits[user_id]
        bucket.append(now)
        return True


def _directed_at_bot(me: discord.ClientUser, message: discord.Message) -> bool:
    """True only when the message mentions the bot or replies to one of its messages."""
    if any(user.id == me.id for user in message.mentions):
        return True
    reference = message.reference
    if reference is not None and reference.resolved is not None:
        resolved = reference.resolved
        return isinstance(resolved, discord.Message) and resolved.author.id == me.id
    channel = message.channel
    return isinstance(channel, discord.Thread) and channel.owner_id == me.id


def should_respond(bot: AgentBot, message: discord.Message) -> bool:
    """Reply when mentioned, replied to, in the bot's own thread, or in DMs."""
    me = bot.user
    if me is None:
        return False
    author = message.author
    if author is None or author.bot or message.type not in RESPONDABLE_TYPES:
        return False
    if message.guild is None:
        return author.id in bot.config.admin_user_ids
    if not is_channel_allowed(message.channel, bot.config):
        return False
    return _directed_at_bot(me, message)


class AgentBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = CONFIG
        self.db = Database(self.config.db_path)
        self.llm = LLMClient(self.config)
        self.agent = AgentLoop(self, self.config)
        self.rate_limiter = RateLimiter(
            self.config.rate_limit_requests, self.config.rate_limit_window_seconds
        )
        self.tree = app_commands.CommandTree(self)
        self.started_at = time.monotonic()
        self._agent_locks: dict[str, asyncio.Lock] = {}
        self._suppress_embeds = not self.config.search_discord_embed_links
        self._allowlist_warned = False

    # setup

    async def setup_hook(self) -> None:
        await self.db.connect(conversation_retention_days=self.config.conversation_retention_days)
        self.agent.register_tools(discord_tools.build_tool_defs())
        if self.config.tavily_api_key:
            from tools.web_tools import TavilyService
            from tools.web_tools import build_tool_defs as build_web_tool_defs

            self.web = TavilyService(self.config)
            self.agent.register_tools(build_web_tool_defs(self.web))
            log.info("Web search enabled (Tavily).")
        else:
            self.web = None
            log.info("TAVILY_API_KEY not set; web search tools not registered.")

        from commands import load_cogs

        await load_cogs(self)

        # sync only when SYNC_COMMANDS=1 (Discord rate-limits registration).
        if self.config.sync_commands:
            try:
                guild_ids = [guild.id async for guild in self.fetch_guilds(limit=200)]
            except Exception:
                guild_ids = []
                log.warning("could not list guilds to clear stale command scopes", exc_info=True)
            for guild_id in guild_ids:
                target = discord.Object(id=guild_id)
                self.tree.clear_commands(guild=target)
                await self.tree.sync(guild=target)
            await self.tree.sync()
            log.info(
                "Slash commands reset: cleared %d guild scope(s), synced globally.",
                len(guild_ids),
            )
        else:
            log.info("SYNC_COMMANDS not set; skipping slash-command sync (commands unchanged).")
        if not self.config.admin_user_ids:
            log.warning(
                "ADMIN_USER_IDS is empty - NO user is treated as admin "
                "(fail-closed). Admin-only commands and DMs are refused for everyone."
            )
        self._log_channel_allowlist_setup()

    def _log_channel_allowlist_setup(self) -> None:
        """Log the ALLOWED_CHANNEL_IDS summary at startup."""
        if self.config.allowed_channel_ids:
            log.info(
                "ALLOWED_CHANNEL_IDS set: %d channel(s); only they (and their threads) are served.",
                len(self.config.allowed_channel_ids),
            )
        else:
            log.info("ALLOWED_CHANNEL_IDS empty: every channel is served.")

    # events

    async def on_ready(self) -> None:
        user = self.user
        if user is None:
            log.warning("on_ready fired before the bot user was cached; skipping banner.")
            return
        log.info(
            "Logged in as %s (%s) in %d guild(s); persona=%s model=%s tz=%s",
            user,
            user.id,
            len(self.guilds),
            getattr(user, "display_name", "?"),
            self.config.llm_model,
            self.config.timezone,
        )
        await self.change_presence(activity=discord.CustomActivity(name="yabe yabe yabe"))

        self._warn_channel_allowlist_visibility()

    def _warn_channel_allowlist_visibility(self) -> None:
        """Warn once about allowlisted channels the bot cannot see."""
        if self._allowlist_warned:
            return
        for cid in self.config.allowed_channel_ids:
            channel = self.get_channel(cid)
            if channel is None:
                log.warning(
                    "ALLOWED_CHANNEL_IDS: channel %s is not visible to the bot "
                    "(unknown id, no access, or not in the cache). It is treated "
                    "as not allowed until the bot can see it.",
                    cid,
                )
            else:
                guild = getattr(channel, "guild", None)
                if guild is None:
                    log.warning(
                        "ALLOWED_CHANNEL_IDS: channel %s has no guild - DMs and "
                        "private channels cannot match the channel filter.",
                        cid,
                    )
        self._allowlist_warned = True

    async def close(self) -> None:
        # guard each step: one failure must not skip the rest of shutdown
        try:
            try:
                # let in-flight background summarizers finish before the DB closes
                await self.agent.aclose()
            finally:
                try:
                    await self.llm.aclose()
                finally:
                    try:
                        web = getattr(self, "web", None)
                        if web is not None:
                            await web.aclose()
                    finally:
                        try:
                            await self.db.close()
                        finally:
                            await super().close()
        except Exception:
            log.exception("Error during shutdown")

    # conversation handling

    async def on_message(self, message: discord.Message) -> None:
        if not should_respond(self, message):
            return
        user = message.author

        if message.guild is not None and not self.rate_limiter.hit(user.id):
            log.warning(
                "Rate limit hit by %s in guild %s; ignoring message.", user.id, message.guild.id
            )
            return

        key = channel_key(message.channel)
        lock = self._agent_locks.get(key)
        if lock is None:
            lock = self._agent_locks[key] = asyncio.Lock()
        user_display = getattr(user, "display_name", None) or str(user)

        async with lock:
            async with message.channel.typing():
                try:
                    run = await self.agent.run(
                        message.channel,
                        user_id=user.id,
                        user_display=user_display,
                        is_admin=self.is_admin(user),
                        prompt=message.clean_content or "(empty message)",
                    )
                except Exception:
                    log.exception("Agent run failed")
                    await self._safe_send(
                        message.channel, "⚠️ Something went wrong while thinking. Try again shortly."
                    )
                    return

            if run.text:
                await self._safe_send(
                    message.channel,
                    run.text,
                    reference=message if message.guild is not None else None,
                )

    async def _safe_send(
        self, channel, content: str, reference: discord.Message | None = None
    ) -> None:
        # SEARCH_DISCORD_EMBED_LINKS off => suppress link embeds
        suppress = self._suppress_embeds
        # fail_if_not_exists=False: the target may be deleted while we think
        ref = reference.to_reference(fail_if_not_exists=False) if reference is not None else None
        try:
            parts = split_message(content) or ["(empty)"]
            for i, part in enumerate(parts):
                await channel.send(
                    part,
                    reference=ref if i == 0 else None,
                    allowed_mentions=discord.AllowedMentions.none(),
                    suppress_embeds=suppress,
                )
        except discord.HTTPException:
            log.exception("Failed to send message to %s", channel)

    def is_admin(self, user) -> bool:
        """Fail-closed admin check: empty ADMIN_USER_IDS means nobody is admin."""
        admins = self.config.admin_user_ids
        if user.id in admins:
            return True
        if not admins:
            return False
        permissions = getattr(user, "guild_permissions", None)
        return bool(permissions and permissions.manage_guild)


def _install_tree_error_handler(bot: AgentBot) -> None:
    @bot.tree.error
    async def on_app_command_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        # unwrap HybridCommandError so our own check failures read clearly
        cause = getattr(error, "original", None) or getattr(error, "__cause__", None)
        # only our own check/permission failures are shown verbatim
        if isinstance(error, app_commands.CheckFailure) or isinstance(cause, discord.Forbidden):
            text = f"⚠️ {str(cause or error) or 'You are not allowed to do that.'}"[:200]
        else:
            text = "⚠️ Something went wrong. Try again shortly."
        if interaction.response.is_done():
            try:
                await interaction.followup.send(text, ephemeral=True)
            except Exception:
                log.exception("Command error and followup failed")
        else:
            await interaction.response.send_message(text, ephemeral=True)
        # exc_info=error: no active exception in this frame
        log.error("Slash command error", exc_info=error)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = AgentBot()
    _install_tree_error_handler(bot)
    with contextlib.suppress(KeyboardInterrupt):  # Ctrl+C in a terminal, not an error
        bot.run(CONFIG.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
