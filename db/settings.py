"""Personas, per-guild settings, usage counters and the tool audit log."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from db.connection import DatabaseBase

log = logging.getLogger(__name__)


class SettingsMixin(DatabaseBase):
    """Personas, guild settings, search usage and audit/stats."""

    # personas

    async def persona_for(self, guild_id: int | str) -> str | None:
        row = await self._fetchone(
            "SELECT system_prompt FROM personas WHERE guild_id = ?", (str(guild_id),)
        )
        return row["system_prompt"] if row else None

    async def set_persona(self, guild_id: int | str, system_prompt: str, updated_by: str) -> None:
        await self._execute(
            "INSERT INTO personas (guild_id, system_prompt, updated_by, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(guild_id) DO UPDATE SET"
            " system_prompt = excluded.system_prompt,"
            " updated_by = excluded.updated_by,"
            " updated_at = excluded.updated_at",
            (str(guild_id), system_prompt, updated_by, time.time()),
        )

    async def delete_persona(self, guild_id: int | str) -> bool:
        cursor = await self._execute("DELETE FROM personas WHERE guild_id = ?", (str(guild_id),))
        return (cursor.rowcount or 0) > 0

    # search usage

    async def record_search_usage(
        self, guild_id: int | str | None, user_id: int | str, tool_name: str
    ) -> None:
        await self._execute(
            "INSERT INTO search_usage (ts, guild_id, user_id, tool_name) VALUES (?, ?, ?, ?)",
            (time.time(), None if guild_id is None else str(guild_id), str(user_id), tool_name),
        )

    async def count_search_usage_user(self, user_id: int | str, since: float) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM search_usage WHERE user_id = ? AND ts >= ?",
            (str(user_id), since),
        )
        return int(row["n"]) if row else 0

    async def count_search_usage_guild(self, guild_id: int | str, since: float) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM search_usage WHERE guild_id = ? AND ts >= ?",
            (str(guild_id), since),
        )
        return int(row["n"]) if row else 0

    # audit log / stats

    async def log_tool(
        self,
        *,
        guild_id: int | str | None,
        channel_id: int | str | None,
        user_id: int | str | None,
        tool_name: str,
        args: dict[str, Any],
        success: bool,
        error: str | None,
    ) -> None:
        try:
            await self._execute(
                "INSERT INTO tool_logs"
                " (ts, guild_id, channel_id, user_id, tool_name, args_json, success, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    None if guild_id is None else str(guild_id),
                    None if channel_id is None else str(channel_id),
                    None if user_id is None else str(user_id),
                    tool_name,
                    json.dumps(args, ensure_ascii=False)[:2000],
                    int(success),
                    (error or "")[:1000],
                ),
            )
        except Exception:  # pragma: no cover - audit log must never break tools
            log.exception("Failed to write tool log")

    async def stats(self) -> dict[str, int]:
        def _count(rows) -> int:
            return int(rows[0]["n"]) if rows else 0

        mem = await self._fetchall("SELECT COUNT(DISTINCT channel_key) AS n FROM conversations")
        msgs = await self._fetchall("SELECT COUNT(*) AS n FROM conversations")
        logs = await self._fetchall("SELECT COUNT(*) AS n FROM tool_logs")
        return {
            "memory_channels": _count(mem),
            "memory_messages": _count(msgs),
            "tool_calls_logged": _count(logs),
        }
