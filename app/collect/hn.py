"""Hacker News via the Algolia search API. No key, no auth, 10k req/hour."""
import html
import logging
import random
import re
import time
from typing import Any, Optional

import httpx

from app.collect import defaults as src

log = logging.getLogger("painbot.collect.hn")

SEARCH = "https://hn.algolia.com/api/v1/search"
SEARCH_BY_DATE = "https://hn.algolia.com/api/v1/search_by_date"
VINTAGE_WINDOW_DAYS = 180

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _clean(text: str) -> str:
    """Comments arrive as HTML fragments; the screener should see prose."""
    if not text:
        return ""
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return _SPACE.sub(" ", text).strip()


def _item(hit: dict[str, Any]) -> dict[str, Any]:
    object_id = str(hit.get("objectID"))
    title = hit.get("title") or hit.get("story_title") or ""
    body = hit.get("story_text") or hit.get("comment_text") or ""
    return {
        "ext_id": object_id,
        "url": hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
        "title": _clean(title),
        "body": _clean(body),
        "author": hit.get("author"),
        "score": int(hit.get("points") or 0),
        "comments": int(hit.get("num_comments") or 0),
        "created_utc": int(hit.get("created_at_i") or 0),
    }


def _search(params: dict[str, Any], by_date: bool = False) -> list[dict[str, Any]]:
    url = SEARCH_BY_DATE if by_date else SEARCH
    try:
        response = httpx.get(url, params=params, timeout=20)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("algolia request failed: %s", exc)
        return []
    hits = response.json().get("hits", [])
    return [_item(h) for h in hits if h.get("objectID")]


def fetch_fresh() -> list[dict[str, Any]]:
    """Newest matches, not all-time best — each run should bring new material."""
    items: list[dict[str, Any]] = []
    for spec in src.hn_queries():
        items.extend(_search({"hitsPerPage": src.hn_hits(), **spec}, by_date=True))
        time.sleep(0.3)
    return items


def fetch_vintage(seed: Optional[int] = None) -> list[dict[str, Any]]:
    """The same pain language inside one random historical window."""
    rng = random.Random(seed if seed is not None else int(time.time()))
    start_year, end_year = src.HN_VINTAGE_YEARS
    year = rng.randint(start_year, end_year)
    start = int(time.mktime((year, rng.randint(1, 12), 1, 0, 0, 0, 0, 1, -1)))
    end = start + VINTAGE_WINDOW_DAYS * 86400
    window = f"created_at_i>{start},created_at_i<{end}"

    items: list[dict[str, Any]] = []
    for phrase in src.vintage_phrases():
        items.extend(
            _search(
                {
                    "tags": "comment",
                    "query": phrase,
                    "hitsPerPage": src.hn_hits(),
                    "numericFilters": window,
                }
            )
        )
        time.sleep(0.3)

    log.info("vintage window %s: %s hits", year, len(items))
    for item in items:
        item["era"] = "vintage"
    return items
