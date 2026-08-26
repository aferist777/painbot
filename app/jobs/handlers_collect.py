"""Job handlers for the collection pipeline: fetch, then screen."""
import asyncio
import json
import logging
import math
from typing import Any

from aiogram import Bot

from app.collect.registry import fetch as fetch_source
from app.collect import defaults as src
from app.collect.seed import seed_sources
from app.collect.store import ingest
from app.db.base import q, x
from app.db.repo import now
from app.jobs import queue
from app.bot.keyboards import close_kb
from app.jobs.progress import Progress
from app.jobs.worker import register
from app.llm.screen import pending_count, screen

log = logging.getLogger("painbot.collect")

SCREEN_LIMIT = 200


# If the primary tier brings back less than this, the fallback tier runs too.
THIN_HARVEST = 25


# What a source has actually produced counts most; how long it has been waiting
# counts too, so a subreddit that never wins still gets its turn eventually.
PAIN_WEIGHT = 3.0
STORED_WEIGHT = 0.1
STALE_PER_DAY = 2.0
UNTRIED_BONUS = 40.0


def _priority(source: Any) -> float:
    pains = float(source["pains_total"] or 0)
    stored = float(source["stored_total"] or 0)
    last = source["last_run_at"]
    if not last:
        return UNTRIED_BONUS + pains * PAIN_WEIGHT
    days = max(now() - int(last), 0) / 86400.0
    return pains * PAIN_WEIGHT + stored * STORED_WEIGHT + days * STALE_PER_DAY


def _by_yield(sources: list) -> list:
    """Best producers first, with the long-untouched ones climbing over time."""
    return sorted(sources, key=_priority, reverse=True)


async def _sweep(
    sources: list, progress: Progress, base: int, total: int, budget: int
) -> tuple[int, int, int]:
    """Returns (stored, skipped, failed) for one tier. Stops at the budget."""
    stored = skipped = failed = 0
    for index, source in enumerate(sources, start=1):
        if stored >= budget:
            log.info("budget %s reached, stopping the sweep", budget)
            break
        label = source["kind"] + "/" + source["name"]
        try:
            items = await asyncio.to_thread(fetch_source, source)
            got, dropped = await asyncio.to_thread(ingest, source["id"], items)
            stored += got
            skipped += dropped
            x(
                "UPDATE sources SET last_run_at=?, last_error=NULL, "
                "stored_total = COALESCE(stored_total, 0) + ? WHERE id=?",
                now(), got, source["id"],
            )
        except Exception as exc:  # one dead source must not stop the sweep
            failed += 1
            log.warning("source %s failed: %s", label, exc)
            x("UPDATE sources SET last_run_at=?, last_error=? WHERE id=?",
              now(), str(exc)[:500], source["id"])
        await progress.update(base + index, total, note=f"{label} · новых {stored}")
    return stored, skipped, failed


@register("collect.run")
async def collect_run(payload: dict, progress: Progress, bot: Bot) -> None:
    seed_sources()
    progress.title = "📡 Сбор болей"

    only = payload.get("kinds")
    if only:
        rows = q(
            "SELECT * FROM sources WHERE enabled=1 AND kind IN (%s) ORDER BY kind, name"
            % ",".join("?" * len(only)),
            *only,
        )
        primary, fallback = list(rows), []
    else:
        primary = list(q("SELECT * FROM sources WHERE enabled=1 AND tier=1 ORDER BY name"))
        fallback = list(q("SELECT * FROM sources WHERE enabled=1 AND tier>1 ORDER BY kind, name"))

    inbox = q("SELECT COUNT(*) AS n FROM pains WHERE state='inbox'")[0]["n"]
    if inbox >= src.inbox_cap():
        await progress.done(
            f"📥 <b>Инбокс полон</b>\n\n"
            f"В нём {inbox} болей при потолке {src.inbox_cap()}. Разбери часть или "
            f"нажми «Очистить и собрать заново» — тогда соберу свежее.",
            close_kb(),
        )
        return

    primary = _by_yield(primary)
    total = len(primary) + len(fallback)
    await progress.update(
        0, total,
        note=f"основных {len(primary)}, беру до {src.inbox_cap()} постов",
        force=True,
    )

    stored, skipped, failed = await _sweep(primary, progress, 0, total, src.inbox_cap())
    used_fallback = False

    if fallback and (stored < THIN_HARVEST or failed == len(primary)):
        used_fallback = True
        reason = "основные молчат" if failed == len(primary) else f"нашлось мало ({stored})"
        await progress.update(
            len(primary), total, note=f"{reason}, иду в запасные", force=True
        )
        more_stored, more_skipped, _ = await _sweep(
            fallback, progress, len(primary), total, src.inbox_cap() - stored
        )
        stored += more_stored
        skipped += more_skipped
    elif fallback:
        # tier one was enough; do not spend the night on the rest
        await progress.update(total, total, note="запасные не понадобились")

    tail = ""
    if failed:
        tail = f"\nИсточников с ошибкой: {failed}"
    await progress.done(
        f"📡 <b>Сбор завершён</b>\n\n"
        f"Основные (Reddit): {len(primary)}\n"
        f"Запасные: {'подключались' if used_fallback else 'не понадобились'}\n"
        f"Новых: {stored} · дублей и мимо: {skipped}{tail}\n\n"
        f"Скрининг запускается…"
    )
    queue.enqueue(
        "screen.run", {},
        chat_id=progress.chat_id, message_id=progress.message_id,
    )


@register("screen.run")
async def screen_run(payload: dict, progress: Progress, bot: Bot) -> None:
    progress.title = "🧠 Скрининг"

    inbox = q("SELECT COUNT(*) AS n FROM pains WHERE state='inbox'")[0]["n"]
    if inbox >= src.inbox_cap():
        await progress.done(
            f"🧠 <b>Скрининг остановлен</b>\n\n"
            f"В инбоксе уже {inbox} болей — потолок. Остальное подождёт "
            f"следующего раза.",
            close_kb(),
        )
        return

    pending = pending_count()
    if pending == 0:
        await progress.done(
            "🧠 <b>Скрининг</b>\n\nНечего оценивать — новых постов нет.",
            close_kb(),
        )
        return

    planned = min(pending, int(payload.get("limit", SCREEN_LIMIT)))
    total_batches = max(1, math.ceil(planned / src.screen_batch()))
    agg = {"seen": 0, "kept": 0, "rejected": 0}

    await progress.update(0, total_batches, note=f"в очереди {pending} постов", force=True)

    for index in range(1, total_batches + 1):
        stats = await asyncio.to_thread(screen, src.screen_batch(), None)
        if stats["seen"] == 0:
            break
        if inbox + agg["kept"] >= src.inbox_cap():
            log.info("inbox cap %s reached, stopping the screen", src.inbox_cap())
            break
        for key in agg:
            agg[key] += stats[key]
        await progress.update(
            index, total_batches, note=f"в инбокс {agg['kept']} · мимо {agg['rejected']}"
        )

    await progress.done(
        f"🧠 <b>Скрининг завершён</b>\n\n"
        f"Оценено: {agg['seen']}\n"
        f"✅ В инбокс: {agg['kept']}\n"
        f"❌ Отсеяно: {agg['rejected']}\n\n"
        f"Жми «Следующая боль» в меню.",
        close_kb(),
    )
