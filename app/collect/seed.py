"""Seed the sources table from the registry on first run."""
import json
import logging

from app.collect.registry import seed_rows, tier_of
from app.db.base import q, x

log = logging.getLogger("painbot.collect.seed")


def seed_sources() -> int:
    added = 0
    for kind, name, config, tier in seed_rows():
        if q("SELECT id FROM sources WHERE kind=? AND name=?", kind, name):
            x("UPDATE sources SET tier=? WHERE kind=? AND name=?", tier, kind, name)
            continue
        x(
            "INSERT INTO sources(kind, name, config_json, enabled, tier) "
            "VALUES(?,?,?,1,?)",
            kind,
            name,
            json.dumps(config, ensure_ascii=False),
            tier,
        )
        added += 1
    if added:
        log.info("seeded %s sources", added)
    return added
