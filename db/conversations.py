"""Conversation memory: stored messages, the recent window and rolling summaries."""

from __future__ import annotations

import time
from typing import Any

from db.connection import DatabaseBase
from utils.messages import estimate_tokens


class ConversationsMixin(DatabaseBase):
    """Per-channel message history, summaries and history search."""

    # messages

    async def append_message(
        self,
        channel_key: str,
        role: str,
        content: str,
        author_display: str | None = None,
    ) -> None:
        await self._execute(
            "INSERT INTO conversations (channel_key, role, content, author_display, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (channel_key, role, content, author_display, time.time()),
        )

    async def get_history(
        self,
        channel_key: str,
        max_messages: int,
        token_budget: int,
        after_id: int = 0,
    ) -> list[dict[str, str]]:
        """Newest unsummarized messages for a channel, trimmed to count and budget."""
        rows = await self._fetchall(
            "SELECT role, content, author_display FROM conversations"
            " WHERE channel_key = ? AND id > ?"
            " ORDER BY id DESC LIMIT ?",
            (channel_key, after_id, max_messages * 4),
        )
        picked: list[dict[str, str]] = []
        budget = token_budget
        for row in rows:
            if len(picked) >= max_messages:
                break
            cost = estimate_tokens(row["content"])
            if picked and (budget - cost) < 0:
                break
            budget -= cost
            picked.append(
                {
                    "role": row["role"],
                    "content": row["content"],
                    "author_display": row["author_display"] or "",
                }
            )
        picked.reverse()
        return picked

    async def clear_channel(self, channel_key: str) -> int:
        """Wipe one channel's messages and summary; other channels are untouched."""
        cursor = await self._execute(
            "DELETE FROM conversations WHERE channel_key = ?", (channel_key,)
        )
        removed = cursor.rowcount or 0
        await self._execute("DELETE FROM summaries WHERE channel_key = ?", (channel_key,))
        return removed

    # rolling summary

    async def get_summary(self, channel_key: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT summary, last_message_id FROM summaries WHERE channel_key = ?",
            (channel_key,),
        )
        return dict(row) if row else None

    async def save_summary(self, channel_key: str, summary: str, last_message_id: int) -> None:
        await self._execute(
            "INSERT INTO summaries (channel_key, summary, last_message_id, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(channel_key) DO UPDATE SET summary = excluded.summary,"
            " last_message_id = excluded.last_message_id, updated_at = excluded.updated_at",
            (channel_key, summary, int(last_message_id), time.time()),
        )

    async def unsummarized_count(self, channel_key: str, last_message_id: int) -> int:
        """How many messages wait to be folded into the summary."""
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM conversations WHERE channel_key = ? AND id > ?",
            (channel_key, last_message_id),
        )
        return int(row["n"]) if row else 0

    async def window_start_id(self, channel_key: str, after_id: int, window: int) -> int | None:
        """Lowest id that belongs to the recent (still raw) window."""
        row = await self._fetchone(
            "SELECT MIN(id) AS m FROM ("
            " SELECT id FROM conversations WHERE channel_key = ? AND id > ?"
            " ORDER BY id DESC LIMIT ?)",
            (channel_key, after_id, max(1, window)),
        )
        return int(row["m"]) if row and row["m"] is not None else None

    async def rows_between(
        self, channel_key: str, after_id: int, before_id: int
    ) -> list[dict[str, Any]]:
        """Unsummarized rows older than the recent window (oldest first)."""
        rows = await self._fetchall(
            "SELECT id, role, content, author_display FROM conversations"
            " WHERE channel_key = ? AND id > ? AND id < ? ORDER BY id",
            (channel_key, after_id, before_id),
        )
        return [dict(r) for r in rows]
