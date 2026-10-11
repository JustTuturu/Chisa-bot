# Deploy (podman + systemd)

Rootless container; SQLite on the `chisa-bot-data` volume (`/data`); secrets from
`/etc/chisa-bot/bot.env`; logs to journald. No ports — outbound only.

## 1. Env file

```bash
sudo mkdir -p /etc/chisa-bot
sudo cp .env.example /etc/chisa-bot/bot.env
sudo chmod 600 /etc/chisa-bot/bot.env
sudo chown "$USER" /etc/chisa-bot/bot.env      # rootless podman reads it as you
sudoedit /etc/chisa-bot/bot.env
```

Fill in `DISCORD_TOKEN`, `LLM_BASE_URL` (ends in `/v1`), `LLM_API_KEY`, `LLM_MODEL`,
`ADMIN_USER_IDS`; optionally `ALLOWED_CHANNEL_IDS` and `TAVILY_API_KEY`. Set
`DATA_DIR=/data`.

## 2. Build & run

```bash
uv run ruff check . && uv run ruff format --check . && uvx pyright   # gate first

podman build --format docker -t chisa-bot .
mkdir -p ~/.config/containers/systemd
cp deploy/chisa-bot.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start chisa-bot
journalctl --user -u chisa-bot -f        # "Logged in as ..." = connected
```

Register the slash commands **once**: set `SYNC_COMMANDS=1`, `systemctl --user restart
chisa-bot`, then unset it.

Update: rebuild the image and `systemctl --user restart chisa-bot` (data lives on the
volume). Rollback: retag the old image as `chisa-bot:latest` and restart.

## 3. Backup & restore

```bash
systemctl --user stop chisa-bot
VOL=$(podman volume inspect chisa-bot-data --format '{{.Mountpoint}}')

# backup
sqlite3 "$VOL/bot.db" ".backup /var/backups/chisa-bot/bot-$(date +%Y%m%d%H%M%S).db"

# restore (backup file is the source)
sqlite3 "$VOL/bot.db" ".restore /var/backups/chisa-bot/bot-<ts>.db"
rm -f "$VOL/bot.db-wal" "$VOL/bot.db-shm"

systemctl --user start chisa-bot
```

## 4. Discord setup

Invite with scopes `bot applications.commands` and this permissions integer:

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_APP_ID&permissions=2252128379071552&scope=bot%20applications.commands
```

That is exactly what the code uses (send/reply/react, threads, Manage Threads, Read
History, Manage Messages, Pin, Embed Links, Attach Files, External Emojis, Slash
Commands) — no Administrator, no voice. Simpler: invite with `permissions=0` and tick the
same boxes on the bot's role in **Server Settings → Roles**.

Enable **Bot → Privileged Gateway Intents → Message Content + Server Members**.

## 5. Rotate the token

Discord Portal → Bot → Reset Token → paste into `/etc/chisa-bot/bot.env` →
`systemctl --user restart chisa-bot`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `PrivilegedIntentsRequired` | Enable Message Content + Server Members intents. |
| Service exits at once | `/etc/chisa-bot/bot.env` missing or unreadable (step 1). |
| Placeholder-value error | The env file still has a template value. |
| No slash commands | Boot once with `SYNC_COMMANDS=1`, then unset it. |
| Nothing on a message | It only acts on mention/reply/DM; check `ALLOWED_CHANNEL_IDS`. |
| `403 Forbidden` | The bot's server role is missing that permission (step 4). |

Docker hosts: `docker-compose.yml` is kept for that (same env file and volume).
