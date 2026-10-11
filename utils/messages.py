"""Helpers: token estimation, channel keys and code-block-aware message splitting."""

from __future__ import annotations

import re
from typing import Any

FENCE_LINE_RE = re.compile(r"^(`{3,})\s*$")  # a *closing* fence: bare backticks


def estimate_tokens(text: str) -> int:
    """Cheap char-based token estimate (~3 chars per token)."""
    if not text:
        return 0
    return max(1, (len(text) + 2) // 3)


def channel_key(channel: Any) -> str:
    """Stable storage key for a channel/thread/DM's conversation memory."""
    guild = getattr(channel, "guild", None)
    if guild is not None:
        return f"guild:{guild.id}:{channel.id}"
    return f"dm:{channel.id}"


def _fence_open_marker(line: str) -> str | None:
    """If the line opens a fence (``` + optional info string), return the marker."""
    if not line.startswith("```"):
        return None
    m = FENCE_LINE_RE.match(line)
    if m:  # bare ``` behaves as a closing fence - not an opener
        return None
    return line


def _is_close_fence(line: str) -> bool:
    return FENCE_LINE_RE.match(line) is not None


def split_message(content: str, limit: int = 1990) -> list[str]:
    """Split content into chunks of at most ``limit`` characters."""
    text = (content or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    lines = text.split("\n")
    reserve = max(0, min(10, limit // 10))  # headroom for fence fixups
    expanded: list[str] = []
    for line in lines:
        if not line.startswith("```") and len(line) + 1 + reserve > limit:
            piece = max(1, limit - reserve)
            expanded.extend(line[i : i + piece] for i in range(0, len(line), piece))
        else:
            expanded.append(line)

    chunks: list[str] = []
    current: list[str] = []
    inside = False
    marker = "```"

    def flush(closed_for_split: bool) -> None:
        """Append the current chunk; closes/reopens fences if mid-block."""
        nonlocal current
        if not current:
            return
        chunk = "\n".join(current)
        if closed_for_split and inside:
            chunk += "\n```"
        chunks.append(chunk)
        current = [marker] if (closed_for_split and inside) else []

    for line in expanded:
        candidate = [*current, line]
        cand_len = sum(len(x) for x in candidate) + len(candidate) - 1
        # a split inside a fence costs 4 extra chars for the reopen
        effective = limit - 4 if inside else limit
        if cand_len > effective and current:
            flush(closed_for_split=True)
        current.append(line)
        if inside and _is_close_fence(line):
            inside = False
        elif not inside:
            open_marker = _fence_open_marker(line)
            if open_marker is not None:
                inside = True
                marker = open_marker

    flush(closed_for_split=False)
    if inside and chunks:
        chunks[-1] += "\n```"

    # Strict safety net: hard-trim any pathological oversize chunk.
    result = [chunk[:limit] for chunk in chunks if chunk != ""]
    return result or [text[:limit]]
