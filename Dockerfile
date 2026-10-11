# chisa-bot — production image. Canonical deploy is podman on a VM (see
# deploy/README-deploy.md); docker-compose.yml covers Docker hosts.
#
# Multi-stage build: only pyproject.toml + uv.lock enter the builder first, so
# dependency layers cache independently of app code. The gate (ruff + pyright)
# must be run on the HOST before `podman build` / `docker compose build`.
#
# Attribution: deployment layout inspired by kkrypt0nn/Python-Discord-Bot-Template
# (https://github.com/kkrypt0nn/Python-Discord-Bot-Template), Apache-2.0. This

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app

# ffmpeg: /download merges separate video+audio streams into mp4
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY bot.py channels.py commands.py config.py ./
COPY db/ ./db/
COPY agent/ ./agent/
COPY tools/ ./tools/
COPY utils/ ./utils/
COPY cogs/ ./cogs/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --system --create-home --uid 1000 botuser \
    && mkdir -p /data \
    && chown botuser:botuser /data /app
USER botuser

VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD pgrep -f "python bot.py" > /dev/null || exit 1

ENTRYPOINT ["python", "bot.py"]
