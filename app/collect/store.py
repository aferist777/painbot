"""Ingest fetched items into raw_items, dropping near-duplicates."""
import json
import logging
import sqlite3
from typing import Any

from app.collect.dedup import find_duplicate, simhash
from app.db.base import x
from app.db.repo import now

log = logging.getLogger("painbot.collect.store")


def ingest(source_id: int, items: list[dict[str, Any]]) -> tuple[int, int]:
    """Returns (stored, skipped)."""
    stored = skipped = 0
    for item in items:
        if not item.get("title") and not item.get("body"):
            skipped += 1
            continue

        fingerprint = simhash(str(item.get("title", "")) + " " + str(item.get("body", "")))
        state = "duplicate" if find_duplicate(fingerprint) else "new"
        if state == "duplicate":
            skipped += 1

        try:
            x(
                "INSERT INTO raw_items(source_id, ext_id, url, title, body, author, "
                "score, comments, created_utc, fetched_at, simhash, state, raw_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                source_id,
                item["ext_id"],
                item.get("url"),
                item.get("title"),
                item.get("body"),
                item.get("author"),
                item.get("score", 0),
                item.get("comments", 0),
                item.get("created_utc", 0),
                now(),
                fingerprint,
                state,
                json.dumps({"era": item.get("era", "fresh")}, ensure_ascii=False),
            )
            if state == "new":
                stored += 1
        except sqlite3.IntegrityError:
            skipped += 1
    return stored, skipped
