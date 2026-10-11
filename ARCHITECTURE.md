# Architecture

**Project version: v1 (unreleased — never published).**

Chisa-Bot is a private Discord bot (installed by its owner only) that runs on an LLM
agent loop: it answers when mentioned/replied-to/DMed, decides for itself when to call
tools (Discord actions, web search), keeps per-channel conversation memory with a rolling
summary, and can download X/Twitter videos on request.

Everything below describes the **current** code. Read this top to bottom once and you
have the whole system. Counts are deliberately not written down here — the authoritative
lists are `build_tool_defs()` in each `tools/*` module and the files in `cogs/`.

---

## 1. The path of one message

```
                     ┌──────────────────────────── bot.py ─────────────────────────────┐
 Discord gateway ──► │ on_message()                                                        │
   MESSAGE_CREATE    │   ├─ should_respond()   ← THE GATE (fail-closed, see §3)             │
                     │   │     • real user + respondable type (default/reply)              │
                     │   │     • DM → ADMIN_USER_IDS only                                  │
                     │   │     • channel allowlist (ALLOWED_CHANNEL_IDS)                   │
                     │   │     • directed at bot: mention | reply to bot | own thread      │
                     │   ├─ rate_limiter.hit(user)   (guilds only)                         │
                     │   └─ per-channel asyncio.Lock → typing() indicator                  │
                     └───────────────────────────────┬────────────────────────────────────┘
                                                     ▼
                     ┌─────────────────────── agent/loop.py :: AgentLoop.run() ────────────┐
                     │ 1. load history    db.get_history(channel_key, window, budget)      │
                     │ 2. build prompt    system_prompt(): persona → rules                │
                     │                    → <conversation_summary> → dynamic tail          │
                     │ 3. tool list       tool_payload()                      │
                     │ 4. LLM call ──► agent/llm.py :: LLMClient.chat()                    │
                     │        ▲                │                                           │
                     │        │        tool_calls? ──yes──► run_tool() ──► tools/*         │
                     │        │                │               • validate args             │
                     │        │                │               • admin-only check          │
                     │        │                │               • injection guard           │
                     │        │                │               • log to tool_logs          │
                     │        └── results ◄────┘                                           │
                     │           _postprocess_result() wraps untrusted output in           │
                     │           <search_results>/<discord_history> + "never follow" note   │
                     │ 5. persist         append user+assistant rows (clipped), schedule   │
                     │                    background rolling summary                       │
                     └───────────────────────────────┬────────────────────────────────────┘
                                                     ▼
                     ┌──────────────────────── bot.py :: _safe_send() ────────────────────┐
                     │ split_message() → ≤2000-char chunks, code fences kept intact        │
                     │ reference=message.to_reference(fail_if_not_exists=False) (first part)│
                     │ AllowedMentions.none() (never ping), suppress_embeds per config     │
                     └────────────────────────────────────────────────────────────────────┘
```

**Slash commands** never go through `on_message`: `commands.py` predicates + `cogs/*`
handle them (all admin-only — there is no `/ask` or `/search`).

---

## 2. File map (one line each)

### Root

| File                                                  | Purpose                                                                                                     |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `bot.py`                                              | Discord client: `should_respond()` gate, `on_message` → agent → reply, slash-command sync, shutdown wiring. |
| `channels.py`                                         | Channel allowlist (`ALLOWED_CHANNEL_IDS`), thread/parent logic, refusal text, obfuscated-channel rules.     |
| `commands.py`                                         | Slash-command permission predicates (`require_admin`, `channel_allowed`) + cog loader (`COG_MODULES`).      |
| `config.py`                                           | Loads/validates env into the frozen `Config` dataclass and the `CONFIG` singleton (fail-fast).              |
| `README.md`                                           | User-facing capabilities + usage.                                                                           |
| `ARCHITECTURE.md`                                     | This file: how it fits together.                                                                            |
| `pyproject.toml`                                      | Dependencies + ruff config.                                                                                 |
| `pyrightconfig.json`                                  | Pyright (type checker) settings.                                                                            |
| `Dockerfile` / `docker-compose.yml` / `.dockerignore` | Production image (non-root, `/data` volume) and compose service.                                            |
| `.env.example`                                        | Documented environment template (placeholders only).                                                        |
| `deploy/README-deploy.md`                             | VM runbook (podman + quadlet, Docker alternative).                                                          |
| `deploy/chisa-bot.container`                          | systemd quadlet unit for the rootless podman service.                                                       |
| `uv.lock`, `.python-version`                          | Locked dependency graph and Python version.                                                                 |

### `agent/` — the brain

| File                | Purpose                                                                                                                         |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `agent/loop.py`     | `AgentLoop`: prompt assembly, LLM↔tool loop, tool registry, untrusted-content fencing, rolling summary, prompt-injection guard. |
| `agent/llm.py`      | `LLMClient`: one OpenAI-compatible chat call (+ `summarize()`, `aclose()`).                                                     |
| `agent/__init__.py` | Package marker.                                                                                                                 |

### `tools/` — what the LLM can _do_

| File                     | Purpose                                                                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `tools/base.py`          | `ToolContext`, `ToolDef`, `ok()/err()`, mention policy.                                                      |
| `tools/validation.py`    | Minimal JSON-schema validation of tool arguments.                                                            |
| `tools/discord_tools.py` | Discord tools: send/reply/react/threads/edit/delete/history/pin/embed/list/info (`build_tool_defs()`).       |
| `tools/web_tools.py`     | Tavily `web_search` / `web_extract` / `web_research` + budget, TTL cache, retries, `validate_url` re-export. |
| `tools/__init__.py`      | Package marker.                                                                                              |

### `db/` — SQLite persistence

| File                  | Purpose                                                                    |
| --------------------- | -------------------------------------------------------------------------- |
| `db/connection.py`    | Lifecycle, `_execute/_fetchall/_fetchone`, retention prune, schema loader. |
| `db/schema.sql`       | All static DDL (tables, indexes, triggers).                                |
| `db/conversations.py` | Message history, recent window, rolling summaries.                         |
| `db/settings.py`      | Personas, search-usage counters, `log_tool`, `stats`.                      |
| `db/__init__.py`      | Assembles the mixins into the public `Database` class.                     |

### `cogs/` — slash-command surfaces

| File               | Purpose                                                                    |
| ------------------ | -------------------------------------------------------------------------- |
| `cogs/memory.py`   | `/memory clear` (admins).                                                  |
| `cogs/persona.py`  | `/persona set\|view\|clear` (admins).                                      |
| `cogs/admin.py`    | `/status` (admins).                                                        |
| `cogs/download.py` | `/download url:…` — yt-dlp video download from an X/Twitter link (admins). |
| `cogs/purge.py`    | `/purge` — delete recent messages                                          |
| `cogs/__init__.py` | Package marker.                                                            |

### `utils/`

| File                  | Purpose                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------- |
| `utils/messages.py`   | Token estimate, `channel_key()`, code-fence-aware `split_message()`.                     |
| `utils/urls.py`       | Shared SSRF-guarded `validate_url()` (used by web extraction).                           |
| `utils/downloader.py` | `/download` backend: URL cleaning (status id only), yt-dlp in a thread, friendly errors. |
| `utils/__init__.py`   | Package marker.                                                                          |

---

## 3. The gate (`bot.py :: should_respond`) — fail-closed, in order

1. **Gateway**: real human author, message type `default`/`reply`, not a bot.
2. **Arena**: DM → only `ADMIN_USER_IDS`; guild → no guild allowlist (private bot).
3. **Place**: `is_channel_allowed()` (empty `ALLOWED_CHANNEL_IDS` = every channel; threads
   count via their parent channel).
4. **Intent**: `_directed_at_bot()` — direct mention, a reply whose resolved message is a
   real `discord.Message` authored by the bot, or a thread the bot owns.

Anything failing a step is silently ignored: no reply, no LLM call, no stored history.

---

## 4. The agent loop (`agent/loop.py`)

- **Prompt layout** (stable sections first for prompt caching):
  `persona → TOOL_GUIDANCE → WEB_RULES → SECURITY_RULES → <conversation_summary>
→ dynamic tail (date, place, role)`.
  Rules are only injected when their tool is registered.
- **History**: `db.get_history()` returns the newest _unsummarized_ messages (window =
  `MEMORY_MAX_MESSAGES`, also capped by `MEMORY_TOKEN_BUDGET`), each labelled
  `"author: text"`.
- **Rolling summary**: after a reply, a background task (one per channel) folds
  out-of-window messages into a per-channel summary via `LLMClient.summarize()`
  (`SUMMARY_MODEL`, `SUMMARY_MAX_WORDS`); failures fall back to plain trim.
- **Tool loop**: up to `MAX_AGENT_ITERATIONS` rounds; `run_tool()` validates args,
  enforces `admin_only`, applies the injection guard, executes, and audits to `tool_logs`.
- **Untrusted fencing**: results from the web tools and fetched Discord history are
  wrapped in `<search_results>` / `<discord_history>` with a "data, never instructions"
  note; close tags inside content are neutralized so it cannot break out.
- **Injection guard**: once untrusted content enters a turn (`ctx.web_used`), destructive
  tools are refused and cross-channel tools are limited to the origin channel
  (`_GUARDED_TOOLS` / `_CROSS_SCOPE_TOOLS`).

---

## 5. Tools

Registered in `bot.py::setup_hook`; **the authoritative list is `build_tool_defs()` in
each `tools/*` module** (`discord_tools`, `web_tools`).

- **Discord** (`tools/discord_tools.py`): send/reply/react, threads (create, send,
  archive, delete — the thread tools take an id, a `<#mention>` or the thread _name_, and
  `get_channel_info` lists a channel's threads), edit/delete own messages, fetch recent
  history, pin, embeds, channel listing/info.
- **Web** (`tools/web_tools.py`, only when `TAVILY_API_KEY` is set): `web_search`,
  `web_extract`, `web_research`.

Web tools are registered whenever `TAVILY_API_KEY` is set; there is no per-guild toggle.

---

## 6. Data (`db/`)

Tables: `conversations`, `summaries`, `personas`, `search_usage`, `tool_logs`.

- **Timestamps** are REAL epoch seconds.
- **Retention** (pruned at boot; windows are the `*_RETENTION_DAYS` constants in
  `db/connection.py`): `tool_logs`, `search_usage`, and `conversations` + their summaries.
  `personas` is never pruned.
- **Schema**: `db/schema.sql` is the single, final schema (everything is
  `CREATE … IF NOT EXISTS`), applied on every connect. There is **no migration
  machinery** — to start over, stop the bot and delete the DB file.
- **Prod DB**: `/data/bot.db` in the `chisa-bot-data` docker volume (WAL). Back it up
  with `sqlite3 … ".backup …"` while the bot is stopped (see `deploy/README-deploy.md`).

---

## 7. Safety invariants (easy to forget)

- The bot **never pings**: every send uses `AllowedMentions.none()`.
- It can edit/delete **only its own** messages (enforced server-side).
- API keys are never logged; the Tavily/LLM keys are never printed.
- Web/Discord content is always fenced as untrusted data before it reaches the model.
- Fail-closed defaults: empty `ADMIN_USER_IDS` = nobody is admin; empty
  `ALLOWED_CHANNEL_IDS` = every channel served (that one is _allow-all_ on purpose).

---

## 8. Config quick reference (`.env.example` has the full list)

| Group               | Variables                                                                                                                                                                                |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Required            | `DISCORD_TOKEN`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`                                                                                                                              |
| Access              | `ADMIN_USER_IDS`, `ALLOWED_CHANNEL_IDS`                                                                                                                                                  |
| Misc                | `TIMEZONE`, `DATA_DIR`, `DB_PATH`, `SYNC_COMMANDS`                                                                                                                                       |
| Conversation memory | `MEMORY_MAX_MESSAGES`, `MEMORY_TOKEN_BUDGET`, `CONVERSATION_MAX_CHARS`, `SUMMARY_MAX_WORDS`, `SUMMARY_MODEL`, `CONVERSATION_RETENTION_DAYS`                                              |
| LLM / agent         | `MAX_AGENT_ITERATIONS`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `LLM_REQUEST_TIMEOUT`                                                                                                       |
| Rate limits         | `RATE_LIMIT_MAX_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS`                                                                                                                                   |
| Web search          | `TAVILY_API_KEY`, `TAVILY_MAX_RESULTS`, `TAVILY_SEARCH_DEPTH`, `SEARCH_RATE_LIMIT_PER_USER_PER_HOUR`, `SEARCH_GUILD_DAILY_CAP`, `SEARCH_CACHE_TTL_SECONDS`, `SEARCH_DISCORD_EMBED_LINKS` |
| /download           | `DOWNLOAD_DIR`, `DOWNLOAD_MAX_MB`, `DOWNLOAD_MAX_SECONDS`                                                                                                                                |

`SYNC_COMMANDS=1` registers slash commands at startup (Discord rate-limits command
registration, so set it only for the boot after you change a command).

---

## 9. Working on it

```bash
# Some sandboxed/read-only-home setups can't use uv's default cache location.
# If uv complains, point it at a writable dir (e.g.):
export UV_CACHE_DIR="$HOME/.cache/uv"

uv run ruff check . && uv run ruff format --check .
uvx pyright                        # must stay 0 errors
```

Deploy is podman on a VM (rootless + a systemd quadlet unit): build, install the unit,
start it — details and the Docker alternative in `deploy/README-deploy.md`. The first
boot creates the DB and prunes old rows.

---

## 10. How-to checklists

### Add a new LLM tool

1. Write the handler and a `ToolDef` in the right `tools/*.py`: `name`, a description the
   model can act on, a JSON-schema `parameters`, and `handler`.
2. Return `ok(...)` or `err("readable reason")` — never raise for expected failures.
3. Decide `admin_only`: `True` hides it from non-admins and the loop refuses it.
4. Untrusted content? Add its name to `_UNTRUSTED_TOOLS` in `agent/loop.py` (gets fenced
   with a "never follow instructions" note) and to the arming condition in `run_tool()`.
   If it must be frozen after untrusted reads, add it to `_GUARDED_TOOLS` /
   `_CROSS_SCOPE_TOOLS` too.
5. Add it to that module's `build_tool_defs()`; `bot.py` registers whole modules in
   `setup_hook()`. Gating (per-user/per-guild), if any, belongs inside the handler.
6. Channel scoping? Reuse `_resolve_channel` rather than re-implementing.
7. Run the gate (`ruff` + `pyright`).
8. Update this file's §5 if the surface changed.

### Add a new slash command

1. Extend an existing cog in `cogs/`, or add a module; define the command with
   `@app_commands.command(name=..., description=...)` (+ `@app_commands.describe`).
2. Stack the gates: `@channel_allowed()` always; admin-only commands call
   `await require_admin(interaction)` first (or use the group `_gate()` pattern from
   `cogs/persona.py`).
3. New cog? Add its module name to `COG_MODULES` in `commands.py` and give it a
   `setup(bot)` that calls `bot.tree.add_command(...)` (see `cogs/download.py`).
4. Reply `ephemeral=True` unless the reply should be public (see `cogs/download.py`);
   the global `AllowedMentions.none()` already prevents pings.
5. Keep error text generic — the tree error handler only shows check failures verbatim.
6. Register it in Discord by booting once with `SYNC_COMMANDS=1`, then unset it.
7. Run the gate.
8. Update `README.md` (command table) and this file's §2/§5 as needed.
