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

## 3. Backups (optional maintenance)

Nothing here is needed to run the bot — do it when you want a snapshot, or when you have
to recover. Back up and restore are **alternatives**, not steps to run in sequence.

### Back up (safe while the bot runs)

```bash
VOL=$(podman volume inspect chisa-bot-data --format '{{.Mountpoint}}')
sqlite3 "$VOL/bot.db" ".backup /var/backups/chisa-bot/bot-$(date +%Y%m%d%H%M%S).db"
```

`.backup` uses SQLite's online backup API, so it is consistent even with the bot writing.
That single `.db` file is the whole state — keep it wherever you like (e.g. a weekly
systemd timer).

### Restore (replaces the current data — stop the bot first)

```bash
systemctl --user stop chisa-bot
VOL=$(podman volume inspect chisa-bot-data --format '{{.Mountpoint}}')
sqlite3 "$VOL/bot.db" ".restore /var/backups/chisa-bot/bot-<ts>.db"
rm -f "$VOL/bot.db-wal" "$VOL/bot.db-shm"   # drop stale WAL after the swap
systemctl --user start chisa-bot
```

## 4. Discord setup

Use the [Discord Developer Portal](https://discord.com/developers/applications): create the
app + bot, then

- enable **Message Content** and **Server Members** under **Bot → Privileged Gateway
  Intents**;
- invite the bot with the `bot` and `applications.commands` scopes, and give it the
  permissions the bot actually uses (send/reply/react, threads, Read History, Manage
  Messages, Pin, Embed Links, Attach Files, External Emojis, Slash Commands) — or invite
  with none and grant them on the bot's role in **Server Settings → Roles**.

Details for each step are on the developer portal itself.
