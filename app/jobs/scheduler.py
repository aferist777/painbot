"""Nightly sweep: collect, screen, then a short digest to the owner."""
import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db.repo import sget_int
from app.jobs import queue

log = logging.getLogger("painbot.scheduler")

# Weekly, not nightly: a sweep brings in a few hundred posts and the inbox
# outlives a week easily. The digest stays daily — it costs nothing.
from app.admin.state import tune  # noqa: E402  (kept next to what it tunes)

COLLECT_DAY = "mon"
COLLECT_HOUR = 4      # local time
DIGEST_HOUR = 9


async def weekly_collect(bot: Bot) -> None:
    owner = sget_int("owner_id")
    if owner is None:
        log.info("no owner yet, skipping nightly collect")
        return
    message = await bot.send_message(owner, "🌙 Ночной сбор\n\nставлю в очередь…")
    queue.enqueue(
        "collect.run", {}, chat_id=message.chat.id, message_id=message.message_id
    )


async def morning_digest(bot: Bot) -> None:
    from app.db.base import q, q1

    owner = sget_int("owner_id")
    if owner is None:
        return
    rows = q(
        "SELECT id, title_ru, score, era FROM pains WHERE state='inbox' "
        "ORDER BY score DESC LIMIT 5"
    )
    if not rows:
        return
    total = q1("SELECT COUNT(*) AS n FROM pains WHERE state='inbox'")
    lines = [f"☀️ <b>В инбоксе {total['n']}</b>. Топ-5:", ""]
    for row in rows:
        mark = "🕰 " if row["era"] == "vintage" else ""
        lines.append(f"<b>{row['score']}</b> · {mark}{row['title_ru']}")
    lines.append("\nЖми /start → «Следующая боль».")
    await bot.send_message(owner, "\n".join(lines))


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        weekly_collect, "cron", day_of_week=tune("plan.collect_day", COLLECT_DAY),
        hour=tune("plan.collect_hour", COLLECT_HOUR),
        minute=0, args=[bot], id="collect",
    )
    scheduler.add_job(
        morning_digest, "cron", hour=tune("plan.digest_hour", DIGEST_HOUR),
        minute=0, args=[bot], id="digest"
    )
    scheduler.start()
    log.info(
        "scheduler up: collect %s %02d:00, digest daily %02d:00",
        tune("plan.collect_day", COLLECT_DAY), tune("plan.collect_hour", COLLECT_HOUR),
        tune("plan.digest_hour", DIGEST_HOUR),
    )
    return scheduler
