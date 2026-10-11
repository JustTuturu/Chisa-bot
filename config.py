"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _csv_ids(name: str) -> set[int]:
    raw = os.getenv(name, "") or ""
    ids: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ValueError(f"{name} contains a non-integer id: {part!r}") from exc
    return ids


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, "") or default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    discord_token: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    # channel allowlist (empty = every channel is served)
    allowed_channel_ids: set[int]
    admin_user_ids: set[int]
    timezone: str
    tz: ZoneInfo
    db_path: str
    memory_max_messages: int
    memory_token_budget: int
    # conversation memory hygiene
    conversation_max_chars: int
    # rolling summary of older messages (kept out of the recent window)
    summary_max_words: int
    summary_model: str  # empty -> use llm_model
    # retention for raw conversation rows + their summaries
    conversation_retention_days: int
    max_agent_iterations: int
    rate_limit_requests: int
    rate_limit_window_seconds: int
    llm_temperature: float
    llm_max_tokens: int
    llm_request_timeout: float
    # web search (Tavily); tools are only registered when tavily_api_key is set
    tavily_api_key: str
    tavily_max_results: int
    tavily_search_depth: str
    search_rate_limit_per_user_per_hour: int
    search_guild_daily_cap: int
    search_cache_ttl_seconds: int
    search_discord_embed_links: bool
    # register slash commands at startup (SYNC_COMMANDS=1); off = no sync
    sync_commands: bool
    # /download
    download_dir: str
    download_max_mb: int
    download_max_seconds: int


def _reject_placeholder(name: str, value: str) -> None:
    placeholders = {
        "changeme",
        "your_token_here",
        "your-discord-bot-token",
        "your-key",
        "yourkey",
        "xxx",
    }
    if value.strip().lower() in placeholders:
        raise RuntimeError(
            f"{name} still contains the placeholder {value.strip()!r}. "
            "Fill in real credentials for THIS bot in .env "
            "(never copy them from another application)."
        )


def load_config() -> Config:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    missing = [
        name
        for name, value in (
            ("DISCORD_TOKEN", token),
            ("LLM_API_KEY", api_key),
            ("LLM_MODEL", model),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill it in."
        )
    _reject_placeholder("DISCORD_TOKEN", token)
    _reject_placeholder("LLM_API_KEY", api_key)
    _reject_placeholder("LLM_MODEL", model)

    base_url = (os.getenv("LLM_BASE_URL", "") or "https://api.openai.com/v1").strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(
            f"Invalid LLM_BASE_URL {base_url!r}: must be a valid http(s) URL "
            "(e.g. http://<host>.<tailnet>.ts.net:20128/v1 - the /v1 suffix is required)."
        )
    # log the host only - never the key
    logging.getLogger(__name__).info("LLM endpoint host: %s", parsed.netloc)

    timezone = os.getenv("TIMEZONE", "UTC").strip() or "UTC"
    try:
        tz = ZoneInfo(timezone)
    except Exception as exc:
        raise RuntimeError(f"Invalid TIMEZONE {timezone!r}: {exc}") from exc

    # DATA_DIR holds every mutable file: SQLite DB, WAL, any caches.
    data_dir = os.getenv("DATA_DIR", "").strip() or "data"
    db_override = os.getenv("DB_PATH", "").strip()
    db_path = db_override if db_override else str(Path(data_dir) / "bot.db")

    return Config(
        discord_token=token,
        llm_base_url=base_url,
        llm_api_key=api_key,
        llm_model=model,
        allowed_channel_ids=_csv_ids("ALLOWED_CHANNEL_IDS"),
        admin_user_ids=_csv_ids("ADMIN_USER_IDS"),
        timezone=timezone,
        tz=tz,
        db_path=db_path,
        memory_max_messages=_int_env("MEMORY_MAX_MESSAGES", 20, 2),
        memory_token_budget=_int_env("MEMORY_TOKEN_BUDGET", 6000, 256),
        conversation_max_chars=_int_env("CONVERSATION_MAX_CHARS", 2000, 200),
        summary_max_words=_int_env("SUMMARY_MAX_WORDS", 200, 50),
        summary_model=(os.getenv("SUMMARY_MODEL", "") or "").strip(),
        conversation_retention_days=_int_env("CONVERSATION_RETENTION_DAYS", 90, 7),
        max_agent_iterations=_int_env("MAX_AGENT_ITERATIONS", 6, 1),
        rate_limit_requests=_int_env("RATE_LIMIT_MAX_REQUESTS", 6, 1),
        rate_limit_window_seconds=_int_env("RATE_LIMIT_WINDOW_SECONDS", 60, 5),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "") or 0.4),
        llm_max_tokens=_int_env("LLM_MAX_TOKENS", 1200, 256),
        llm_request_timeout=float(os.getenv("LLM_REQUEST_TIMEOUT", "") or 120),
        tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
        tavily_max_results=_int_env("TAVILY_MAX_RESULTS", 5, 1),
        tavily_search_depth=(os.getenv("TAVILY_SEARCH_DEPTH", "basic").strip().lower() or "basic"),
        search_rate_limit_per_user_per_hour=_int_env("SEARCH_RATE_LIMIT_PER_USER_PER_HOUR", 20, 1),
        search_guild_daily_cap=_int_env("SEARCH_GUILD_DAILY_CAP", 200, 1),
        search_cache_ttl_seconds=_int_env("SEARCH_CACHE_TTL_SECONDS", 600, 1),
        search_discord_embed_links=os.getenv("SEARCH_DISCORD_EMBED_LINKS", "").strip().lower()
        in ("1", "true", "yes", "on"),
        sync_commands=os.getenv("SYNC_COMMANDS", "").strip().lower() in ("1", "true", "yes", "on"),
        download_dir=os.getenv("DOWNLOAD_DIR", "").strip() or str(Path(data_dir) / "downloads"),
        download_max_mb=_int_env("DOWNLOAD_MAX_MB", 10, 1),
        download_max_seconds=_int_env("DOWNLOAD_MAX_SECONDS", 600, 10),
    )


CONFIG = load_config()
