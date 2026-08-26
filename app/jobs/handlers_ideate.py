"""Job handler: generate product specs for an approved pain."""
import asyncio
import logging

from aiogram import Bot

from app.db.base import q1
from app.bot.keyboards import close_kb
from app.jobs.progress import Progress
from app.jobs.worker import register
from app.llm.ideate import generate

log = logging.getLogger("painbot.jobs.ideate")


@register("ideate.run")
async def ideate_run(payload: dict, progress: Progress, bot: Bot) -> None:
    pain_id = int(payload["pain_id"])
    extra = bool(payload.get("extra"))
    row = q1("SELECT title_ru FROM pains WHERE id=?", pain_id)
    title = row["title_ru"] if row else f"#{pain_id}"

    progress.title = "💡 Придумываю решение"
    await progress.update(0, 1, note=title[:70], force=True)

    count = await asyncio.to_thread(generate, pain_id, extra, None)

    if not progress.attached:
        return
    if count == 0:
        await progress.done(f"💡 <b>{title}</b>\n\nИдеи уже есть.", close_kb())
        return
    word = "вариант" if count == 1 else "варианта"
    await progress.done(
        f"💡 <b>{title}</b>\n\nГотово: {count} {word}.\n"
        f"Открой «Одобренные» в меню.",
        close_kb(),
    )


@register("ideate.silent")
async def ideate_silent(payload: dict, progress: Progress, bot: Bot) -> None:
    """Fired right after approval so the flow never blocks on generation."""
    pain_id = int(payload["pain_id"])
    await asyncio.to_thread(generate, pain_id, False, None)
