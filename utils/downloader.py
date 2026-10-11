"""Download a video from an X/Twitter status link with yt-dlp.

Privacy: only the numeric status id is ever logged; the original URL, the
username and the Discord user are never logged or echoed back. Output files are
named with a random token so nothing identifying leaks into the filename.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# hosts we accept (the post is rebuilt as a clean x.com link before use)
ALLOWED_HOSTS = {
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "x.com",
    "www.x.com",
    "vxtwitter.com",
    "www.vxtwitter.com",
    "fixupx.com",
    "www.fixupx.com",
}
_STATUS_ID = re.compile(r"/status/(\d+)")
_FORMAT = "b[height<=720]/bv*[height<=720]+ba/b"


class DownloadError(RuntimeError):
    """A download failed; the message is safe to show to the user."""


class _SilentLogger:
    """Swallow yt-dlp's output: it can include the URL, which must never be logged."""

    def debug(self, msg: str) -> None: ...

    def info(self, msg: str) -> None: ...

    def warning(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def status_id(url: str) -> str | None:
    """The numeric status id in an X/Twitter link, or None."""
    match = _STATUS_ID.search(url or "")
    return match.group(1) if match else None


def clean_url(url: str) -> str | None:
    """A canonical https://x.com/i/status/<id> link, or None if unsupported.

    The username and every query parameter (tracking like ?s=20&t=...) are
    dropped; the id is the only part that reaches the downloader.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    host = re.match(r"^[a-zA-Z]+://([^/?#]+)", raw)
    if host is None or host.group(1).lower() not in ALLOWED_HOSTS:
        return None
    post_id = status_id(raw)
    if post_id is None:
        return None
    return f"https://x.com/i/status/{post_id}"


def _download_sync(clean: str, out_dir: Path, max_seconds: int) -> Path:
    """Blocking yt-dlp download (run in a thread). Returns the file path."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex  # random name: no title/username in the filename
    outtmpl = str(out_dir / f"{token}.%(ext)s")
    opts: dict[str, Any] = {
        "format": _FORMAT,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
        "outtmpl": outtmpl,
    }
    try:
        # yt-dlp's TypedDict params are stricter than the runtime dict it accepts
        with yt_dlp.YoutubeDL(opts) as ydl:  # pyright: ignore[reportArgumentType]
            info = ydl.extract_info(clean, download=False)
            duration = info.get("duration") or 0
            if duration and duration > max_seconds:
                raise DownloadError(f"That video is longer than {max_seconds // 60} minutes.")
            ydl.download([clean])
    except DownloadError:
        raise
    except Exception as exc:  # yt-dlp raises its own DownloadError, plus network errors
        raise DownloadError(_friendly(exc)) from exc

    files = sorted(out_dir.glob(f"{token}.*"))
    if not files:
        raise DownloadError("No video file was produced.")
    return files[0]


def _friendly(exc: Exception) -> str:
    text = str(exc).lower()
    if "no video" in text or "no media" in text:
        return "That post has no video."
    if "private" in text:
        return "That post is private."
    if "age" in text and "restrict" in text:
        return "That post is age-restricted."
    if "404" in text or "not found" in text or "deleted" in text or "unavailable" in text:
        return "That post was deleted or doesn't exist."
    return "The download failed. Try again later."


async def download_video(clean: str, out_dir: Path, max_seconds: int) -> Path:
    """Download ``clean`` in a worker thread. Raises DownloadError."""
    return await asyncio.to_thread(_download_sync, clean, out_dir, max_seconds)
