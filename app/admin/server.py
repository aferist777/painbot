"""The control panel: an aiohttp server inside the bot's own process.

It binds to the loopback address and refuses anything that did not come from
this machine — that is the whole authentication story, and it is the reason key
values are handed out one at a time instead of being baked into the page.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

from app import config
from app.admin import schema, state
from app.db.base import q, q1, x
from app.llm import promptfile

log = logging.getLogger("painbot.admin")

STATIC = Path(__file__).parent / "static"
FONTS = Path(__file__).resolve().parent.parent / "media" / "fonts"

HOST = "127.0.0.1"
PORT = int(os.getenv("ADMIN_PORT") or 8765)
URL = f"http://{HOST}:{PORT}"

LOOPBACK = ("127.0.0.1", "::1", "localhost")

_credits: tuple[float, Optional[float]] = (0.0, None)


# --------------------------------------------------------------------- guard


@web.middleware
async def only_local(request: web.Request, handler: Any) -> web.StreamResponse:
    peer = request.remote or ""
    if peer not in LOOPBACK:
        log.warning("отказал %s — панель только для этой машины", peer)
        raise web.HTTPForbidden(text="панель доступна только с этой машины")
    return await handler(request)


async def _body(request: web.Request) -> dict:
    try:
        return await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}


# ---------------------------------------------------------------- the fields


async def get_state(request: web.Request) -> web.Response:
    return web.json_response({"sections": [s.json() for s in schema.sections()]})


async def set_field(request: web.Request) -> web.Response:
    data = await _body(request)
    item = schema.field(data.get("key", ""))
    if item is None:
        raise web.HTTPNotFound(text="нет такого поля")
    try:
        item.write(data.get("value"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=f"не подходит: {exc}")
    return web.json_response({"value": item.value(), "restart": item.store == "cfg"})


async def reset_field(request: web.Request) -> web.Response:
    data = await _body(request)
    item = schema.field(data.get("key", ""))
    if item is None:
        raise web.HTTPNotFound(text="нет такого поля")
    item.reset()
    return web.json_response({"value": item.value()})


# --------------------------------------------------------------------- keys


def _keys_payload() -> dict:
    keys = []
    for sid, (label, env_name, _) in state.SECRETS.items():
        value = state.secret(sid)
        keys.append({
            "id": sid,
            "label": label,
            "env": env_name,
            "mask": state.mask(value),
            "filled": bool(value),
            "live": sid == "eleven",
        })
    open_keys = [
        {"key": name, "label": label, "hint": hint,
         "value": state.cfg(name) or getattr(config, name, "")}
        for name, (label, hint) in state.OPEN_KEYS.items()
    ]
    return {"keys": keys, "open": open_keys}


async def get_keys(request: web.Request) -> web.Response:
    return web.json_response(_keys_payload())


async def reveal_key(request: web.Request) -> web.Response:
    """One key, asked for explicitly. Nothing here is ever sent with the page."""
    sid = (await _body(request)).get("id", "")
    if sid not in state.SECRETS:
        raise web.HTTPNotFound(text="нет такого ключа")
    log.info("показал ключ %s", sid)
    return web.json_response({"value": state.secret(sid)})


async def set_key(request: web.Request) -> web.Response:
    data = await _body(request)
    sid = data.get("id", "")
    if sid not in state.SECRETS:
        raise web.HTTPNotFound(text="нет такого ключа")
    value = (data.get("value") or "").strip()
    if value:
        state.set_secret(sid, value)
    else:
        state.clear_secret(sid)
    log.info("ключ %s обновлён", sid)
    return web.json_response(_keys_payload())


async def set_open_key(request: web.Request) -> web.Response:
    data = await _body(request)
    name = data.get("key", "")
    if name not in state.OPEN_KEYS:
        raise web.HTTPNotFound(text="нет такого поля")
    state.set_cfg(name, (data.get("value") or "").strip())
    return web.json_response(_keys_payload())


# ------------------------------------------------------------------ prompts


async def get_prompts(request: web.Request) -> web.Response:
    name = request.query.get("name")
    if name:
        return web.json_response({
            "name": name,
            "text": promptfile.load(name),
            "backup": promptfile.has_backup(name),
        })
    return web.json_response({"prompts": [
        {"name": key, "title": title, "size": len(promptfile.load(key)),
         "backup": promptfile.has_backup(key)}
        for key, title in promptfile.TITLES.items()
    ]})


async def save_prompt(request: web.Request) -> web.Response:
    data = await _body(request)
    name = data.get("name", "")
    if name not in promptfile.TITLES:
        raise web.HTTPNotFound(text="нет такого промпта")
    text = data.get("text") or ""
    if not text.strip():
        raise web.HTTPBadRequest(text="пустой промпт не сохраняю")
    promptfile.save(name, text)
    return web.json_response({"size": len(promptfile.load(name)), "backup": True})


async def restore_prompt(request: web.Request) -> web.Response:
    name = (await _body(request)).get("name", "")
    if name not in promptfile.TITLES:
        raise web.HTTPNotFound(text="нет такого промпта")
    if not promptfile.restore(name):
        raise web.HTTPBadRequest(text="сохранённой копии нет")
    return web.json_response({"text": promptfile.load(name)})


# ------------------------------------------------------------------ sources


async def get_sources(request: web.Request) -> web.Response:
    rows = q(
        "SELECT id, kind, name, enabled, tier, stored_total, pains_total, "
        "last_run_at, last_error FROM sources ORDER BY kind, name"
    )
    return web.json_response({"sources": [dict(r) for r in rows]})


async def toggle_source(request: web.Request) -> web.Response:
    data = await _body(request)
    x("UPDATE sources SET enabled=? WHERE id=?",
      1 if data.get("enabled") else 0, int(data.get("id", 0)))
    return await get_sources(request)


async def add_source(request: web.Request) -> web.Response:
    name = (await _body(request)).get("name", "").strip().lstrip("r/").strip()
    if not name:
        raise web.HTTPBadRequest(text="пустое имя")
    if q1("SELECT id FROM sources WHERE kind='reddit' AND name=?", name):
        raise web.HTTPBadRequest(text="такой сабреддит уже есть")
    x(
        "INSERT INTO sources(kind, name, config_json, enabled, tier) "
        "VALUES('reddit', ?, ?, 1, 1)",
        name, json.dumps({"sub": name}, ensure_ascii=False),
    )
    log.info("добавил сабреддит %s", name)
    return await get_sources(request)


# No delete on purpose: raw_items -> pains -> ideas -> scripts all cascade off a
# source row, so removing one subreddit would quietly take months of collected
# material with it. Switching it off stops the sweeps and keeps everything.


# ------------------------------------------------------------------- status


def _count(sql: str) -> int:
    row = q1(sql)
    return int(row["n"]) if row else 0


async def get_status(request: web.Request) -> web.Response:
    global _credits
    stamp, value = _credits
    if time.time() - stamp > 120:
        from app.llm.client import kie_credits

        value = kie_credits() if config.LLM_PROVIDER == "kie" else None
        _credits = (time.time(), value)

    errors = q(
        "SELECT kind, error FROM jobs WHERE status='failed' AND error IS NOT NULL "
        "ORDER BY id DESC LIMIT 3"
    )
    return web.json_response({
        "credits": value,
        "provider": config.LLM_PROVIDER,
        "channel": (q1("SELECT value FROM settings WHERE key='channel_title'") or {})["value"]
        if q1("SELECT value FROM settings WHERE key='channel_title'") else "",
        "counts": {
            "инбокс": _count("SELECT COUNT(*) n FROM pains WHERE state='inbox'"),
            "идеи": _count("SELECT COUNT(*) n FROM ideas"),
            "сценарии": _count("SELECT COUNT(*) n FROM scripts"),
            "ролики": _count("SELECT COUNT(*) n FROM renders WHERE status='done'"),
            "в очереди": _count("SELECT COUNT(*) n FROM jobs WHERE status='queued'"),
        },
        "errors": [{"kind": r["kind"], "error": (r["error"] or "")[:160]} for r in errors],
    })


# --------------------------------------------------------------------- serve


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


def build_app() -> web.Application:
    app = web.Application(middlewares=[only_local])
    app.router.add_get("/", index)
    app.router.add_get("/api/state", get_state)
    app.router.add_post("/api/set", set_field)
    app.router.add_post("/api/reset", reset_field)
    app.router.add_get("/api/keys", get_keys)
    app.router.add_post("/api/keys/reveal", reveal_key)
    app.router.add_post("/api/keys/set", set_key)
    app.router.add_post("/api/keys/open", set_open_key)
    app.router.add_get("/api/prompts", get_prompts)
    app.router.add_post("/api/prompts/save", save_prompt)
    app.router.add_post("/api/prompts/restore", restore_prompt)
    app.router.add_get("/api/sources", get_sources)
    app.router.add_post("/api/sources/toggle", toggle_source)
    app.router.add_post("/api/sources/add", add_source)
    app.router.add_get("/api/status", get_status)
    app.router.add_static("/fonts/", FONTS)
    app.router.add_static("/", STATIC)
    return app


async def start() -> web.AppRunner:
    runner = web.AppRunner(build_app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()
    log.info("админка на %s", URL)
    return runner
