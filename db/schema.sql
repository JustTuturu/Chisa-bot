-- All static DDL for the bot's SQLite store. Executed on every connect
-- (everything is IF NOT EXISTS, so it is idempotent).
--
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

-- --- conversation memory ---------------------------------------------------

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_key TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    author_display TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_channel
    ON conversations(channel_key, id);

CREATE TABLE IF NOT EXISTS summaries (
    channel_key TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    last_message_id INTEGER NOT NULL DEFAULT 0
        CHECK (last_message_id >= 0),
    updated_at REAL NOT NULL
);

-- --- personas / settings ---------------------------------------------------

CREATE TABLE IF NOT EXISTS personas (
    guild_id TEXT PRIMARY KEY,
    system_prompt TEXT NOT NULL,
    updated_by TEXT,
    updated_at REAL
);

-- --- audit log / usage counters --------------------------------------------

CREATE TABLE IF NOT EXISTS tool_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    guild_id TEXT,
    channel_id TEXT,
    user_id TEXT,
    tool_name TEXT NOT NULL,
    args_json TEXT,
    success INTEGER NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_logs_ts
    ON tool_logs(ts);

CREATE TABLE IF NOT EXISTS search_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    guild_id TEXT,
    user_id TEXT NOT NULL,
    tool_name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_usage_user
    ON search_usage(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_search_usage_guild
    ON search_usage(guild_id, ts);
CREATE INDEX IF NOT EXISTS idx_search_usage_ts
    ON search_usage(ts);

-- --- audit log / usage counters --------------------------------------------
