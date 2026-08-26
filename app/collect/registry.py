"""One place that knows every source kind.

Adding a source means writing a fetch function and adding one line here:
seeding, collection and the "collect only this kind" button all read this map.
A fetch function takes no arguments beyond its own config and returns a list of
dicts shaped like {ext_id, url, title, body, author, score, comments,
created_utc} — the same contract for an HTTP API, a local dump or a CSV.
"""
import json
from typing import Any, Callable

from app.collect import github, hn, reddit
from app.collect.defaults import REDDIT_SUBS

Fetcher = Callable[[Any], list[dict]]


def _hn_fresh(_source: Any) -> list[dict]:
    return hn.fetch_fresh()


def _hn_vintage(_source: Any) -> list[dict]:
    return hn.fetch_vintage()


def _github(_source: Any) -> list[dict]:
    return github.fetch()


def _reddit(source: Any) -> list[dict]:
    config = json.loads(source["config_json"] or "{}")
    return reddit.fetch_sub(config.get("sub") or source["name"])


# kind -> (fetcher, seed rows, tier)
# Tier 1 runs first. Tier 2 only runs when tier 1 came back thin or broken —
# Reddit is where people actually describe the chore they live with, the rest
# is a safety net.
SOURCES: dict[str, tuple[Fetcher, list[tuple[str, dict]], int]] = {
    "reddit": (_reddit, [(sub, {"sub": sub}) for sub in REDDIT_SUBS], 1),
    "hn": (_hn_fresh, [("hn-fresh", {})], 2),
    "hn_vintage": (_hn_vintage, [("hn-vintage", {})], 2),
    "github": (_github, [("github-issues", {})], 2),
}


def fetch(source: Any) -> list[dict]:
    entry = SOURCES.get(source["kind"])
    if entry is None:
        raise ValueError(f"неизвестный источник: {source['kind']}")
    return entry[0](source)


def tier_of(kind: str) -> int:
    entry = SOURCES.get(kind)
    return entry[2] if entry else 2


def seed_rows() -> list[tuple[str, str, dict, int]]:
    return [
        (kind, name, config, tier)
        for kind, (_, rows, tier) in SOURCES.items()
        for name, config in rows
    ]
