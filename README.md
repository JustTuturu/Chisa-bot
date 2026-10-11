# Chisa-Bot

Private Discord bot on an LLM agent loop: answers when mentioned / replied to / DMed,
calls Discord tools by itself, keeps per-channel memory with rolling summaries, and can
download X/Twitter videos.

Design, file map, safety invariants: **[ARCHITECTURE.md](ARCHITECTURE.md)**

## Run

```bash
cp .env.example .env      # fill in the values
uv run --python 3.12 python bot.py
```

- Enable **Message Content** and **Server Members** intents in the Discord portal.
- Empty `ADMIN_USER_IDS` = nobody is admin (fail-closed).
- Boot once with `SYNC_COMMANDS=1` after changing a command, then unset it.
- Deploy (podman + systemd): [deploy/README-deploy.md](deploy/README-deploy.md).

## Commands (all admin-only)

| Command                     | What                                       |
| --------------------------- | ------------------------------------------ |
| `/status`                   | Uptime, model, memory size                 |
| `/memory clear`             | Wipe this channel's history + summary      |
| `/persona set, view, clear` | Per-server system prompt                   |
| `/download url`             | Download an X/Twitter video (public reply) |
| `/purge count, hours`       | Delete recent messages                     |

## LLM tools (the model calls these itself)

Send/reply/react, threads (create, send, archive, delete), edit/delete its own messages,
fetch recent history, pin, embeds, channel list/info — plus `web_search`, `web_extract`
and `web_research` when `TAVILY_API_KEY` is set. The authoritative list is
`build_tool_defs()` in `tools/*`.

## Config

`.env.example` has the full commented list. Required: `DISCORD_TOKEN`, `LLM_BASE_URL`,
`LLM_API_KEY`, `LLM_MODEL`. Validated at startup — fails fast on missing or placeholder
values.

## Check

```bash
uv run ruff check . && uv run ruff format --check .
uvx pyright
```
