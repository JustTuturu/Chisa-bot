"""SQLite persistence (aiosqlite): connection, schema, conversations, settings."""

from __future__ import annotations

from db.connection import DatabaseBase
from db.conversations import ConversationsMixin
from db.settings import SettingsMixin


class Database(
    ConversationsMixin,
    SettingsMixin,
    DatabaseBase,
):
    """The bot's SQLite store: memory, summaries, personas, settings, audit log."""


__all__ = ["Database"]
