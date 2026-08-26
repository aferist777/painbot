"""GitHub issue search — developer pain straight from the people who file it."""
import logging
from typing import Any

import httpx

from app.collect import defaults as src
from app.config import GITHUB_TOKEN

log = logging.getLogger("painbot.collect.github")

SEARCH = "https://api.github.com/search/issues"
PER_PAGE = 40


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "painbot/0.1"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _item(issue: dict[str, Any]) -> dict[str, Any]:
    import datetime as _dt

    created = issue.get("created_at") or ""
    try:
        ts = int(_dt.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp())
    except ValueError:
        ts = 0
    return {
        "ext_id": str(issue.get("id")),
        "url": issue.get("html_url"),
        "title": (issue.get("title") or "").strip(),
        "body": (issue.get("body") or "").strip()[:4000],
        "author": (issue.get("user") or {}).get("login"),
        "score": int(issue.get("reactions", {}).get("total_count") or 0),
        "comments": int(issue.get("comments") or 0),
        "created_utc": ts,
    }


def fetch() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for query in src.github_queries():
        params = {"q": query, "sort": "reactions", "order": "desc", "per_page": PER_PAGE}
        try:
            response = httpx.get(SEARCH, params=params, headers=_headers(), timeout=25)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("github search failed (%s): %s", query, exc)
            continue
        items.extend(_item(i) for i in response.json().get("items", []))
    return [i for i in items if i["ext_id"]]
