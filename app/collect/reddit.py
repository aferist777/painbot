"""Reddit through Arctic Shift — the maintained successor to Pushshift.

Reddit itself is closed without credentials: the JSON endpoints, Jina Reader and
the RSS feeds all answer 403 or rate-limit anonymous callers to zero. Arctic
Shift mirrors the same content, needs no key, and is current to the day.

Its search is token based, not phrase based: "wish there was" returns nothing
while "spreadsheet" returns people describing the workaround they live with. So
the pain vocabulary here is single words, unlike the Hacker News collector.
"""
import logging
import time
from typing import Any, Optional

import httpx

from app.collect import defaults as src

log = logging.getLogger("painbot.collect.reddit")

BASE = "https://arctic-shift.photon-reddit.com/api"
UA = "painbot/0.1 (personal idea feed)"
# Arctic Shift throttles with both 422 ("Timeout. Maybe slow down a bit") and
# 429. Neither is a bad request — both mean wait. Smaller pages, a real gap
# between calls and a growing back-off keep them rare.
LIMIT = 15
THROTTLED = (422, 429)
RETRY_PAUSE = 8.0
PAUSE = 2.0


def _get(path: str, **params: Any) -> Optional[list[dict]]:
    """Arctic Shift answers 422 when asked too fast; back off and retry once."""
    for attempt in range(3):
        try:
            response = httpx.get(
                BASE + path, params=params, headers={"User-Agent": UA}, timeout=60
            )
        except httpx.HTTPError as exc:
            log.warning("arctic shift request failed: %s", exc)
            return None
        if response.status_code in THROTTLED:
            if attempt == 2:
                log.warning(
                    "arctic shift %s: сдался после 3 попыток (%s, троттлинг)",
                    path, response.status_code,
                )
                return None
            time.sleep(RETRY_PAUSE * (attempt + 1))  # 8, then 16 seconds
            continue
        if response.status_code != 200:
            log.warning("arctic shift %s: %s", response.status_code, response.text[:120])
            return None
        return response.json().get("data") or []
    return None


def _post(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "ext_id": data.get("id") or "",
        "url": "https://www.reddit.com" + (data.get("permalink") or ""),
        "title": (data.get("title") or "").strip(),
        "body": (data.get("selftext") or "").strip()[:4000],
        "author": data.get("author"),
        "score": int(data.get("score") or 0),
        "comments": int(data.get("num_comments") or 0),
        "created_utc": int(data.get("created_utc") or 0),
    }


def _comment(data: dict[str, Any]) -> dict[str, Any]:
    body = (data.get("body") or "").strip()
    return {
        "ext_id": "c_" + (data.get("id") or ""),
        "url": "https://www.reddit.com" + (data.get("permalink") or ""),
        "title": (data.get("link_title") or "").strip(),
        "body": body[:4000],
        "author": data.get("author"),
        "score": int(data.get("score") or 0),
        "comments": 0,
        "created_utc": int(data.get("created_utc") or 0),
    }


def words_for_today() -> list[str]:
    """A rotating slice of the vocabulary, so a nightly sweep stays under an hour."""
    import datetime as dt

    day = dt.date.today().toordinal()
    words = src.pain_words()
    size = max(src.words_per_run(), 1)
    start = (day * size) % len(words)
    doubled = words + words
    return doubled[start : start + size]


def fetch_sub(sub: str) -> list[dict[str, Any]]:
    """Sweep one subreddit for today's slice of the pain vocabulary."""
    items: list[dict[str, Any]] = []
    for word in words_for_today():
        posts = _get("/posts/search", subreddit=sub, query=word, limit=LIMIT, sort="desc")
        for row in posts or []:
            item = _post(row)
            if item["ext_id"] and (item["title"] or item["body"]):
                items.append(item)
        time.sleep(PAUSE)

        comments = _get("/comments/search", subreddit=sub, body=word, limit=LIMIT, sort="desc")
        for row in comments or []:
            item = _comment(row)
            if item["ext_id"] and len(item["body"]) > 80:
                items.append(item)
        time.sleep(PAUSE)

    kept = [
        i for i in items
        if i["score"] >= src.reddit_min_score() or i["ext_id"].startswith("c_")
    ]
    log.info("r/%s: %s items, %s kept", sub, len(items), len(kept))
    return kept
