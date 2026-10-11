"""Tavily web-search tools callable by the LLM."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from tools.base import ToolContext, ToolDef, err, ok
from utils.urls import validate_url  # re-exported: SSRF guard lives in utils/urls.py

__all__ = ["TavilyService", "build_tool_defs", "validate_url"]

log = logging.getLogger(__name__)

MAX_EXTRACT_URLS = 3
MAX_SNIPPET_CHARS = 400
MAX_EXTRACT_CHARS = 4000
TOPICS = ("general", "news")
TIME_RANGES = ("day", "week", "month", "year")
DEPTHS = ("basic", "advanced")


class TTLCache:
    """Small in-memory TTL cache (dict-based, clears oldest when oversized)."""

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires, value = entry
        if self._clock() >= expires:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self._max:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (self._clock() + self._ttl, value)

    def clear(self) -> None:
        self._store.clear()


def cache_key(tool: str, params: dict) -> str:
    return tool + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)


class TavilyService:
    def __init__(
        self,
        config,
        cache: TTLCache | None = None,
        client: Any | None = None,
        retry_delay: float = 2.0,
    ):
        if client is None:
            from tavily import AsyncTavilyClient

            client = AsyncTavilyClient(api_key=config.tavily_api_key)
        self._client = client
        self._cache = cache or TTLCache(config.search_cache_ttl_seconds)
        self._default_depth = config.tavily_search_depth
        self._default_max_results = config.tavily_max_results
        self._retry_delay = retry_delay

    async def aclose(self) -> None:
        """Close the Tavily HTTP client (called on bot shutdown)."""
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

    async def peek_search(self, **params: Any) -> dict | None:
        """Return the cached result for this search without hitting Tavily."""
        cached = self._cache.get(cache_key("search", params))
        return {**cached, "cached": True} if cached is not None else None

    async def peek_extract(self, urls: list[str]) -> dict | None:
        cached = self._cache.get(cache_key("extract", {"urls": sorted(urls)}))
        return {**cached, "cached": True} if cached is not None else None

    async def _cached(self, key: str, fn, params: dict) -> dict:
        """Cache-first call: a hit returns at once, otherwise one call (with retry)."""
        cached = self._cache.get(key)
        if cached is not None:
            return {**cached, "cached": True}
        result = await self._call(fn, params)
        self._cache.set(key, result)
        return result

    async def search(self, **params: Any) -> dict:
        return await self._cached(cache_key("search", params), self._client.search, params)

    async def extract(self, urls: list[str]) -> dict:
        key = cache_key("extract", {"urls": sorted(urls)})
        return await self._cached(key, self._client.extract, {"urls": urls})

    async def _call(self, fn, params: dict) -> dict:
        try:
            return await fn(**params)
        except Exception as first:
            log.warning("Tavily call failed (%s); retrying once", type(first).__name__)
            await asyncio.sleep(self._retry_delay)
            try:
                return await fn(**params)
            except Exception as second:
                raise TavilyServiceError(_tavily_error_text(second)) from second


class TavilyServiceError(RuntimeError):
    pass


def _tavily_error_text(exc: Exception) -> str:
    text = str(exc)
    if "401" in text or "unauthorized" in text.lower() or "api key" in text.lower():
        return "Tavily rejected the API key (401). Check TAVILY_API_KEY."
    if "429" in text or "rate" in text.lower():
        return "Tavily rate limit hit (429). Try again in a moment."
    if "timeout" in text.lower():
        return "Tavily request timed out (retried once)."
    return f"Tavily request failed after one retry: {text[:200]}"


async def _check_budget(ctx: ToolContext, units: int) -> str | None:
    """Enforce per-user hourly + per-guild daily search limits WITHOUT recording."""
    cfg = ctx.bot.config
    db = ctx.bot.db
    now = time.time()
    user_count = await db.count_search_usage_user(ctx.user_id, now - 3600)
    if user_count + units > cfg.search_rate_limit_per_user_per_hour:
        return (
            f"Web-search rate limit reached for you "
            f"({cfg.search_rate_limit_per_user_per_hour}/hour). Try again later."
        )
    if ctx.guild is not None:
        guild_count = await db.count_search_usage_guild(ctx.guild.id, now - 86400)
        if guild_count + units > cfg.search_guild_daily_cap:
            return (
                f"This server hit its daily web-search cap "
                f"({cfg.search_guild_daily_cap}/day). Try again tomorrow."
            )
    return None


async def _record_usage(ctx: ToolContext, tool_name: str, units: int) -> None:
    """Record one usage row per Tavily call that was actually made."""
    db = ctx.bot.db
    guild_id = ctx.guild.id if ctx.guild else None
    for _ in range(units):
        await db.record_search_usage(guild_id, ctx.user_id, tool_name)


async def _guarded_call(
    ctx: ToolContext,
    tool_name: str,
    units: int,
    peek: Callable[[], Awaitable[dict | None]],
    call: Callable[[], Awaitable[dict]],
) -> tuple[dict | None, dict | None]:
    """Cache-first budget flow shared by the web-tool handlers."""
    cached = await peek()
    if cached is not None:  # cache hits cost no usage and no Tavily credits
        return cached, None
    if problem := await _check_budget(ctx, units):
        return None, err(problem)
    try:
        response = await call()
    except (ValueError, TavilyServiceError) as exc:
        return None, err(str(exc))
    await _record_usage(ctx, tool_name, units)
    return response, None


def _search_units(depth: str) -> int:
    return 2 if str(depth) == "advanced" else 1


async def web_search(
    ctx: ToolContext,
    query: str = "",
    max_results: int | None = None,
    search_depth: str | None = None,
    topic: str | None = None,
    time_range: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    **_: Any,
) -> dict:
    service: TavilyService = ctx.bot.web
    params: dict[str, Any] = {
        "query": str(query).strip(),
        "max_results": int(max_results or ctx.bot.config.tavily_max_results),
        "search_depth": (search_depth or ctx.bot.config.tavily_search_depth),
    }
    if topic:
        params["topic"] = topic
    if time_range:
        params["time_range"] = time_range
    if include_domains:
        params["include_domains"] = [str(d) for d in include_domains]
    if exclude_domains:
        params["exclude_domains"] = [str(d) for d in exclude_domains]

    units = _search_units(params["search_depth"])
    response, error = await _guarded_call(
        ctx,
        "web_search",
        units,
        lambda: service.peek_search(**params),
        lambda: service.search(**params),
    )
    if response is None:
        return error or err("Search failed.")

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": (r.get("content") or "")[:MAX_SNIPPET_CHARS],
            "score": round(float(r.get("score") or 0), 3),
        }
        for r in (response.get("results") or [])
    ]
    return ok(
        query=params["query"],
        answer=response.get("answer") or None,
        count=len(results),
        results=results,
        cached=bool(response.get("cached")),
    )


async def web_extract(ctx: ToolContext, urls: list[str] | None = None, **_: Any) -> dict:
    service: TavilyService = ctx.bot.web
    url_list = [str(u).strip() for u in (urls or []) if str(u).strip()]
    if not url_list:
        return err("urls must contain at least one URL.")
    if len(url_list) > MAX_EXTRACT_URLS:
        return err(f"Extract at most {MAX_EXTRACT_URLS} URLs per call (got {len(url_list)}).")
    try:
        clean = [validate_url(u) for u in url_list]
    except ValueError as exc:
        return err(str(exc))

    response, error = await _guarded_call(
        ctx,
        "web_extract",
        len(clean),  # 1 call per URL
        lambda: service.peek_extract(clean),
        lambda: service.extract(urls=clean),
    )
    if response is None:
        return error or err("Extraction failed.")

    pages = []
    for r in response.get("results") or []:
        pages.append(
            {
                "url": r.get("url", ""),
                "content": (r.get("raw_content") or "")[:MAX_EXTRACT_CHARS],
                "truncated": len(r.get("raw_content") or "") > MAX_EXTRACT_CHARS,
            }
        )
    failed = response.get("failed_results") or []
    return ok(
        count=len(pages), pages=pages, failed=list(failed), cached=bool(response.get("cached"))
    )


async def web_research(ctx: ToolContext, question: str = "", **_: Any) -> dict:
    """Run 2-3 related searches, merge and deduplicate, return sources."""
    service: TavilyService = ctx.bot.web
    question = str(question).strip()
    if not question:
        return err("question must not be empty.")

    variants = [
        {"query": question},
        {"query": f"{question} overview explanation"},
        {"query": f"{question} latest news", "topic": "news", "time_range": "month"},
    ]
    depth = ctx.bot.config.tavily_search_depth
    if problem := await _check_budget(ctx, 3 * _search_units(depth)):
        return err(problem)  # reserve the worst case (3 plain searches)

    merged: dict[str, dict] = {}
    answers: list[str] = []
    errors: list[str] = []
    made_calls = 0
    for variant in variants:
        vparams = {
            "max_results": ctx.bot.config.tavily_max_results,
            "search_depth": depth,
            **variant,
        }
        try:
            cached = await service.peek_search(**vparams)
        except ValueError:
            cached = None
        if cached is not None:
            response = cached
        else:
            try:
                response = await service.search(**vparams)
            except TavilyServiceError as exc:
                errors.append(str(exc))
                continue
            made_calls += 1
        if response.get("answer"):
            answers.append(response["answer"])
        for r in response.get("results") or []:
            url = r.get("url", "")
            if url and url not in merged:
                merged[url] = {
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": (r.get("content") or "")[:MAX_SNIPPET_CHARS],
                    "score": round(float(r.get("score") or 0), 3),
                }
    if made_calls:
        await _record_usage(ctx, "web_research", made_calls)
    results = sorted(merged.values(), key=lambda r: r["score"], reverse=True)[:12]
    return ok(
        question=question,
        searches_run=len(variants) - len(errors),
        errors=errors or None,
        answers=answers or None,
        count=len(results),
        results=results,
        cached=len(results) > 0 and made_calls == 0,
    )


def build_tool_defs(service: TavilyService) -> list[ToolDef]:
    return [
        ToolDef(
            name="web_search",
            description=(
                "Search the web with Tavily for current information. Use it proactively "
                "whenever a question needs current or external facts (news, prices, "
                "weather, releases, anything past your training cutoff) - you decide, "
                "there is no user-facing search command. Not for casual chat. Returns "
                "titles, URLs, snippets, scores and optionally a short Tavily answer."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 400},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                    "search_depth": {"type": "string", "enum": list(DEPTHS)},
                    "topic": {"type": "string", "enum": list(TOPICS)},
                    "time_range": {
                        "type": "string",
                        "enum": list(TIME_RANGES),
                        "description": "Only results from the last day/week/month/year.",
                    },
                    "include_domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    },
                    "exclude_domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    },
                },
                "required": ["query"],
            },
            handler=web_search,
        ),
        ToolDef(
            name="web_extract",
            description=(
                "Fetch cleaned page content for 1-3 http(s) URLs (public internet "
                "only - private/internal addresses are blocked). Each page is "
                "truncated to ~4000 characters."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_EXTRACT_URLS,
                        "items": {"type": "string", "maxLength": 2048},
                    },
                },
                "required": ["urls"],
            },
            handler=web_extract,
        ),
        ToolDef(
            name="web_research",
            description=(
                "Deeper research on a question: runs 2-3 related searches, merges and "
                "deduplicates results, and returns them with source URLs for you to "
                "synthesize. Costs more searches than web_search - prefer it only "
                "when a single search is not enough."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "minLength": 1, "maxLength": 400},
                },
                "required": ["question"],
            },
            handler=web_research,
        ),
    ]
