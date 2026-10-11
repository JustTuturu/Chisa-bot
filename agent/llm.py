"""Thin OpenAI-compatible chat client with native function/tool calling."""

from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, config):
        self._client = AsyncOpenAI(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            max_retries=2,
            timeout=config.llm_request_timeout,
        )
        self._model = config.llm_model
        # optional smaller/cheaper model for background summarization
        self._summary_model = getattr(config, "summary_model", "") or ""
        self._temperature = config.llm_temperature
        self._max_tokens = config.llm_max_tokens

    async def chat(self, messages: list[dict], tools: list[dict] | None) -> dict[str, Any]:
        """One chat-completion round trip."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        choice = resp.choices[0]
        message = choice.message
        return {
            "content": message.content,
            "tool_calls": getattr(message, "tool_calls", None),
            "finish_reason": choice.finish_reason,
        }

    async def aclose(self) -> None:
        """Close the underlying HTTP client (called on bot shutdown)."""
        await self._client.close()

    async def summarize(self, previous: str, transcript: str, *, max_words: int = 200) -> str:
        """Merge a new transcript into the rolling summary of a channel."""
        system = (
            "You compress a chat transcript into a short factual rolling summary. "
            "Keep: people's names, decisions, open questions, user preferences and "
            "anything the user asked to remember. Drop greetings, small talk and "
            f"filler. Reply with ONLY the summary text, no preamble, max {max_words} words."
        )
        user = (
            f"Existing summary (may be empty):\n{previous or '(none)'}\n\n"
            f"New transcript (oldest first):\n{transcript}\n\n"
            "Return the merged, updated summary."
        )
        kwargs: dict[str, Any] = {
            "model": self._summary_model or self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": min(1200, max(200, max_words * 4)),
        }
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"LLM summarize request failed: {exc}") from exc
        return (resp.choices[0].message.content or "").strip()
