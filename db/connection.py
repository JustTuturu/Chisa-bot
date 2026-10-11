"""Connection lifecycle, low-level SQL helpers and the schema loader."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

# aiosqlite's worker thread is a daemon so a crash cannot hang the process
if not getattr(aiosqlite.core, "_daemonized_for_chisa", False):
    _OrigThread = aiosqlite.core.Thread  # pyright: ignore[reportPrivateImportUsage, reportAttributeAccessIssue]

    class _DaemonThread(_OrigThread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.daemon = True  # set before start() - always allowed

    aiosqlite.core.Thread = _DaemonThread  # pyright: ignore[reportPrivateImportUsage, reportAttributeAccessIssue]
    aiosqlite.core._daemonized_for_chisa = True  # pyright: ignore[reportAttributeAccessIssue]

log = logging.getLogger(__name__)

# Every timestamp column in the schema is REAL unix-epoch seconds.
TOOL_LOG_RETENTION_DAYS = 30
SEARCH_USAGE_RETENTION_DAYS = 7
CONVERSATION_RETENTION_DAYS = 90

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _cutoff(days: int) -> float:
    return time.time() - days * 86400


class DatabaseBase:
    """Connection and helpers. Domain methods come from the mixins."""

    def __init__(self, path: str):
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # lifecycle

    async def connect(self, conversation_retention_days: int = CONVERSATION_RETENTION_DAYS) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        await self._prune_retention(conversation_retention_days)
        log.info("Database ready at %s", self._path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        conn = self._conn
        if conn is None:
            raise RuntimeError("database not connected")
        return conn

    # low-level helpers

    async def _execute(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Cursor:
        conn = self._require_conn()
        async with self._lock:
            cursor = await conn.execute(sql, tuple(params))
            await conn.commit()
            return cursor

    async def _fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        conn = self._require_conn()
        async with self._lock:
            cursor = await conn.execute(sql, tuple(params))
            return list(await cursor.fetchall())

    async def _fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        rows = await self._fetchall(sql, params)
        return rows[0] if rows else None

    # retention

    async def _prune_retention(
        self, conversation_retention_days: int = CONVERSATION_RETENTION_DAYS
    ) -> None:
        """Boot-time retention sweep for audit rows and raw conversation history."""
        cutoff = _cutoff(TOOL_LOG_RETENTION_DAYS)
        cursor = await self._execute("DELETE FROM tool_logs WHERE ts < ?", (cutoff,))
        tool_logs_pruned = cursor.rowcount or 0
        cutoff = _cutoff(SEARCH_USAGE_RETENTION_DAYS)
        cursor = await self._execute("DELETE FROM search_usage WHERE ts < ?", (cutoff,))
        search_usage_pruned = cursor.rowcount or 0
        cutoff = _cutoff(conversation_retention_days)
        cursor = await self._execute("DELETE FROM conversations WHERE created_at < ?", (cutoff,))
        conversations_pruned = cursor.rowcount or 0
        cursor = await self._execute("DELETE FROM summaries WHERE updated_at < ?", (cutoff,))
        summaries_pruned = cursor.rowcount or 0
        if tool_logs_pruned or search_usage_pruned or conversations_pruned or summaries_pruned:
            log.info(
                "Retention prune: %d tool_logs (> %dd), %d search_usage (> %dd),"
                " %d conversations (> %dd), %d summaries",
                tool_logs_pruned,
                TOOL_LOG_RETENTION_DAYS,
                search_usage_pruned,
                SEARCH_USAGE_RETENTION_DAYS,
                conversations_pruned,
                conversation_retention_days,
                summaries_pruned,
            )
